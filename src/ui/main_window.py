"""
main_window.py - Fenetre principale.

Structure reprise du script PowerShell d'origine :
  1. Fenêtre cible (enumeration par HWND, filtre, capture)
  Onglet "Contenu"      : fichier ou saisie directe, envoi ligne par ligne
  Onglet "Clavier cible": disposition de la console distante
  Onglet "Envoi rapide" : entrées + raccourcis globaux + palette flottante
  Barre d'action        : Démarrer / Pause / Stop, progression, journal
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QLabel, QMainWindow, QPlainTextEdit, QPushButton,
    QSizePolicy, QTabWidget, QVBoxLayout, QWidget,
)

from core.hotkeys import (
    ACTION_LABELS, APP_ACTIONS, HotkeyManager,
)
from core.inject_controller import InjectController
from core.models import QuickEntry
from keyboard import backend as w32
from keyboard.engine import METHOD_SCANCODE
from keyboard.layout_converter import LayoutConverter
from security.provider import SecretProvider
from ui.palette import QuickPalette
from ui.window.sending import SendingMixin
from ui.window.storage import StorageMixin
from ui.window.options import OptionsMixin
from ui.window.target import TargetMixin
from ui.window.content import ContentMixin
from ui.window.keyboard_tab import KeyboardTabMixin
from ui.window.quick import QuickSendMixin
from ui.window.actions import ActionsMixin
from ui.theme import LIGHT

APP_TITLE = "SendToConsole"
APP_VERSION = "0.7.3"



class MainWindow(SendingMixin,
                 StorageMixin,
                 OptionsMixin,
                 TargetMixin,
                 ContentMixin,
                 KeyboardTabMixin,
                 QuickSendMixin,
                 ActionsMixin,
                 QMainWindow):
    #: Message court pour le tray, quand la fenetre est cachee (main.py).
    notify = Signal(str)

    def __init__(self, provider: Optional[SecretProvider] = None) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} v{APP_VERSION}")
        self.resize(920, 780)
        self.setMinimumSize(760, 640)

        self.provider = provider
        self.entries: list[QuickEntry] = []
        self.converter = LayoutConverter()
        self.converter.refresh_local()

        self._target_hwnd: int = 0
        self._target_title: str = ""
        self._target_elevation: str = ""    # avertissement « administrateur »
        # Derniere fenetre active n'appartenant pas a l'outil : c'est elle
        # que vise l'envoi rapide quand la palette a malgre tout pris le focus.
        self._last_foreground: int = 0
        self._own_pid = os.getpid()

        # Les reglages persistants sont lus en premier : le controleur et la
        # palette en dependent des leur construction.
        self._settings = QSettings("SendToConsole", "SendToConsole")
        self._acl_warned = False
        self._theme = str(self._settings.value("ui/theme", LIGHT))
        self._altgr_with_ctrl = self._settings.value(
            "send/altgr_with_ctrl", True, type=bool)

        self.controller = InjectController(guard_focus=True)
        self.controller.send_method = str(
            self._settings.value("send/method", METHOD_SCANCODE))
        self.controller.status_changed.connect(self._set_status)
        self.controller.progress_changed.connect(self._on_progress)
        self.controller.failed.connect(self._on_failed)
        self.controller.finished.connect(self._on_finished)
        self.controller.state_changed.connect(lambda _s: self._sync_buttons())
        self.controller.diagnostics.connect(self._on_diagnostics)
        self.controller.modifiers_released.connect(
            lambda names: self._log(
                f"Modificateurs encore enfoncés, relâchés avant de taper : "
                f"{', '.join(names)}."))

        self.hotkeys = HotkeyManager(self)
        self.hotkeys.triggered.connect(self._on_hotkey)
        self.hotkeys.registration_failed.connect(self._on_hotkey_refused)
        self.hotkeys.action_triggered.connect(self._on_action_hotkey)
        self.hotkeys.action_registration_failed.connect(
            lambda action: self._log(
                f"Raccourci « {ACTION_LABELS[action]} » perdu : repris entre-"
                "temps par une autre application.", "WARN"))
        # Raccourcis globaux des actions de l'outil (reglage Options). Ils ne
        # sont enregistres qu'une fois le HWND connu : register_app_hotkeys().
        self._app_hotkeys: dict[str, tuple[int, int]] = {
            action: (
                int(self._settings.value(f"hotkeys/{action}_mods", 0, type=int)),
                int(self._settings.value(f"hotkeys/{action}_vk", 0, type=int)))
            for action in APP_ACTIONS
        }
        # Collage multi-ligne en attente de confirmation : (empreinte HMAC,
        # hwnd, echeance). Cle aleatoire propre a la session : l'empreinte
        # ne permet pas de retrouver le texte hors de ce processus.
        self._paste_pending: Optional[tuple[bytes, int, float]] = None
        self._paste_key = os.urandom(32)

        saved_orientation = self._settings.value("palette/orientation", "vertical")
        self.palette_window = QuickPalette(orientation=str(saved_orientation))
        self.palette_window.entry_clicked.connect(self._on_palette_click)
        self.palette_window.orientation_changed.connect(
            lambda value: self._settings.setValue("palette/orientation", value))
        self.palette_window.visibility_changed.connect(self._sync_palette_button)
        self.palette_window.no_activate_failed.connect(lambda: self._log(
            "Palette : Windows a refusé le mode « sans focus » ; un clic sur "
            "la palette peut sortir le curseur du champ de la console. "
            "Préférez les raccourcis globaux.", "WARN"))

        self._build_ui()
        self._refresh_windows()
        self._load_entries()

        # Suivi continu de la fenetre de travail (equivalent du timer du script PowerShell d'origine).
        self._foreground_timer = QTimer(self)
        # Court : c'est le repli quand la palette a malgre tout pris le focus,
        # et une valeur trop haute vise alors une fenetre deja quittee.
        self._foreground_timer.setInterval(150)
        self._foreground_timer.timeout.connect(self._track_foreground)
        self._foreground_timer.start()

        self._pick_timer = QTimer(self)
        self._pick_timer.setInterval(1000)
        self._pick_timer.timeout.connect(self._pick_tick)
        self._pick_left = 0

        self._log(f"{APP_TITLE} v{APP_VERSION} prêt.")
        if not w32.IS_REAL:
            self._log("Mode simulation (hors Windows) : aucune frappe réelle.", "WARN")

    # ==================================================================== UI --
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        if not w32.IS_REAL:
            banner = QLabel(
                "Mode simulation (hors Windows) : fenêtres d'exemple, "
                "aucune frappe réellement envoyée.")
            banner.setObjectName("simBanner")
            banner.setWordWrap(True)
            root.addWidget(banner)

        # Bandeau stockage volatil : compact, caché quand inutile
        self.storage_banner = QLabel()
        self.storage_banner.setObjectName("hintLabel")
        self.storage_banner.setWordWrap(False)
        self.storage_banner.setVisible(False)
        root.addWidget(self.storage_banner)

        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(False)

        # Roue crantée dans le coin de la barre d'onglets — zéro ligne gaspillée
        self.options_btn = QPushButton()
        self.options_btn.setObjectName("iconButton")
        self.options_btn.setFixedSize(34, 34)
        self.options_btn.setIconSize(QSize(20, 20))
        self.options_btn.setToolTip("Options")
        self.options_btn.setAccessibleName("Options")
        self.options_btn.clicked.connect(self._open_options)
        self.tabs.setCornerWidget(self.options_btn, Qt.Corner.TopRightCorner)
        self._refresh_options_icon()
        self._update_storage_banner()

        self.tabs.addTab(self._build_target_tab(), "Cible")
        self.tabs.addTab(self._build_content_tab(), "Contenu")
        self.tabs.addTab(self._build_keyboard_tab(), "Clavier cible")
        self.tabs.addTab(self._build_quick_tab(), "Envoi rapide")
        self.tabs.addTab(self._build_log_tab(), "Journal")
        self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Expanding)
        root.addWidget(self.tabs, stretch=1)

        root.addWidget(self._build_send_bar())
        self.setCentralWidget(central)
        self._sync_buttons()

    # ------------------------------------------------ reglages persistants --
    #: (cle QSettings, nom de l'attribut, valeur par defaut)
    _DELAY_SETTINGS = (
        ("delays/char_ms", "char_delay_spin", 15),
        ("delays/line_ms", "line_delay_spin", 300),
        ("delays/countdown_s", "countdown_spin", 5),
    )

    def _restore_delays(self) -> None:
        """Relit les délais reglés par l'opérateur. Les valeurs par defaut ne
        conviennent pas a toutes les consoles : les resaisir a chaque
        lancement serait une corvee quotidienne."""
        for key, attr, default in self._DELAY_SETTINGS:
            spin = getattr(self, attr)
            try:
                spin.setValue(int(self._settings.value(key, default)))
            except (TypeError, ValueError):
                spin.setValue(default)
            spin.valueChanged.connect(
                lambda value, k=key: self._settings.setValue(k, value))

    def _build_log_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setObjectName("monoEdit")
        layout.addWidget(self.log_edit)
        return tab

    # ============================================================ fermeture --
    def shutdown(self) -> None:
        """Liberation avant de quitter : les raccourcis globaux sont
        enregistres aupres du système, ne pas les liberer les laisserait
        confisques jusqu'a la fin de la session Windows."""
        self.controller.stop("fermeture")
        self.hotkeys.unregister_all(self.entries)
        self.hotkeys.unregister_all_actions()
        self.palette_window.close()
        if self.provider is not None:
            self.provider.lock()

    def bring_to_front(self) -> None:
        """Relance de l'outil alors qu'il tourne deja (instance unique) :
        on montre la fenetre existante au lieu d'en ouvrir une seconde."""
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        # Fermer la fenetre replie l'outil dans le tray ; seul "Quitter" dans
        # le menu du tray termine l'application (voir main.py).
        event.ignore()
        self.hide()
