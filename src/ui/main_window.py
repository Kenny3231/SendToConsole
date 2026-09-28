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

import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSizePolicy, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from core.hotkeys import (
    ACTION_CAPTURE_TARGET, ACTION_LABELS, ACTION_PASTE_CLIPBOARD,
    ACTION_TOGGLE_PALETTE, APP_ACTIONS, HotkeyManager,
    format_hotkey, hotkey_owner, unsafe_hotkey_reason,
)
from core.inject_controller import InjectController
from core.models import QuickEntry
from core.paste import EMPTY_REASON, PastePlan, prepare_paste
from keyboard import backend as w32
from keyboard.engine import METHOD_SCANCODE, build_tokens
from keyboard.layout_converter import MODE_LAYOUT, MODE_NONE, LayoutConverter
from keyboard.layouts import KNOWN_LAYOUTS
from security.local_encrypted import (
    LocalEncryptedProvider, redirected_store_path)
from security.provider import SecretProvider, SecretProviderError
from ui.options_dialog import OptionsDialog
from ui.palette import QuickPalette
from ui.quick_entry_dialog import QuickEntryDialog
from ui.master_password_dialog import STORE_FILTER
from ui.store_history import (
    LAST_KEY, recent_stores, remember_store, same_path, with_store_suffix)
from ui import icons
from ui.theme import LIGHT, apply_theme, mark_secondary, token as theme_token

APP_TITLE = "SendToConsole"
APP_VERSION = "0.7.1"
#: Délai (s) pour confirmer par un second appui le collage de plusieurs lignes.
PASTE_CONFIRM_S = 5
# Échantillon de l'aperçu de conversion (jamais le contenu réel : secret possible).
PREVIEW_SAMPLE = "azerty AZERTY 0123 @#|\\{}[]~`^¨éèàùç€"



class MainWindow(QMainWindow):
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

    # ------------------------------------------------------- fenetre cible --
    def _build_target_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        row = QHBoxLayout()
        self.window_filter = QLineEdit()
        self.window_filter.setPlaceholderText("Filtrer par processus ou titre...")
        self.window_filter.textChanged.connect(self._refresh_windows)
        row.addWidget(self.window_filter, stretch=1)

        refresh_btn = mark_secondary(QPushButton("Rafraîchir"))
        refresh_btn.clicked.connect(self._refresh_windows)
        row.addWidget(refresh_btn)

        self.capture_btn = QPushButton("Capturer la fenêtre active (3 s)")
        self.capture_btn.clicked.connect(self._start_capture)
        row.addWidget(self.capture_btn)
        layout.addLayout(row)

        self.windows_table = QTableWidget(0, 4)
        self.windows_table.setHorizontalHeaderLabels(["Processus", "PID", "HWND", "Titre"])
        self.windows_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        self.windows_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.windows_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.windows_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.windows_table.itemSelectionChanged.connect(self._on_window_selected)
        layout.addWidget(self.windows_table, stretch=1)

        self.target_label = QLabel("Fenêtre de travail : (aucune)")
        self.target_label.setObjectName("targetLabel")
        layout.addWidget(self.target_label)

        self.target_layout_label = QLabel("")
        self.target_layout_label.setObjectName("hintLabel")
        layout.addWidget(self.target_layout_label)

        # Fenetre lancee en administrateur : SendInput y est bloque en
        # silence (UIPI). Masque tant que la cible est normale.
        self.target_elevation_label = QLabel("")
        self.target_elevation_label.setObjectName("hintLabel")
        self.target_elevation_label.setProperty("warn", True)
        self.target_elevation_label.setWordWrap(True)
        self.target_elevation_label.hide()
        layout.addWidget(self.target_elevation_label)
        return tab

    # ------------------------------------------------------------ contenu --
    # ------------------------------------------------------------ contenu --
    def _build_content_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        row = QHBoxLayout()
        load_btn = mark_secondary(QPushButton("Charger un fichier…"))
        load_btn.clicked.connect(self._load_file)
        clear_btn = mark_secondary(QPushButton("Vider"))
        clear_btn.clicked.connect(lambda: self.content_edit.clear())
        row.addWidget(load_btn)
        row.addWidget(clear_btn)

        self.file_label = QLabel("(ou tape / colle directement ci-dessous)")
        self.file_label.setObjectName("hintLabel")
        row.addWidget(self.file_label, stretch=1)
        layout.addLayout(row)

        self.content_edit = QPlainTextEdit()
        # Minimum volontairement bas : c'est la hauteur MINIMALE des pages qui
        # decide de la taille de la zone a onglets. Si une page en exige plus
        # que les autres, la zone change de hauteur a chaque changement
        # d'onglet et la fenetre entiere sautille.
        self.content_edit.setMinimumHeight(60)
        self.content_edit.setObjectName("monoEdit")
        self.content_edit.setPlaceholderText(
            "Contenu à taper dans la console, ligne par ligne…")
        self.content_edit.textChanged.connect(self._update_estimate)
        layout.addWidget(self.content_edit, stretch=1)

        options = QHBoxLayout()
        self.skip_comments_chk = QCheckBox("Ignorer les lignes # et ;")
        self.skip_empty_chk = QCheckBox("Ignorer les lignes vides")
        self.final_enter_chk = QCheckBox("ENTRÉE après la dernière ligne")
        self.final_enter_chk.setChecked(True)
        options.addWidget(self.skip_comments_chk)
        options.addWidget(self.skip_empty_chk)
        options.addWidget(self.final_enter_chk)
        options.addStretch(1)
        layout.addLayout(options)

        self.estimate_label = QLabel("Aucun contenu.")
        self.estimate_label.setObjectName("hintLabel")
        layout.addWidget(self.estimate_label)
        return tab

    # ------------------------------------------------------ clavier cible --
    def _build_keyboard_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        explain = QLabel(
            "Ce qui arrive dans la console, ce sont des scancodes, pas des "
            "caractères : si la console n'utilise pas la même disposition que "
            "ton poste, les caractères spéciaux sortent faux. Choisis ici la "
            "disposition de la console distante.")
        explain.setWordWrap(True)
        explain.setObjectName("hintLabel")
        layout.addWidget(explain)

        # Formulaire : les deux listes démarrent à la même abscisse.
        form = QFormLayout()
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignVCenter)
        self.conv_mode_combo = QComboBox()
        self.conv_mode_combo.addItems([
            "Aucune - la console a la même disposition que mon poste",
            "Convertir vers la disposition de la console",
        ])
        self.conv_mode_combo.currentIndexChanged.connect(self._on_conv_mode_changed)
        form.addRow("Conversion :", self.conv_mode_combo)

        self.layout_combo = QComboBox()
        self.layout_combo.addItems([name for name, _ in KNOWN_LAYOUTS])
        self.layout_combo.setEnabled(False)
        self.layout_combo.currentIndexChanged.connect(self._on_target_layout_changed)
        form.addRow("Disposition de la console :", self.layout_combo)
        layout.addLayout(form)

        self.local_layout_label = QLabel()
        self.local_layout_label.setObjectName("hintLabel")
        layout.addWidget(self.local_layout_label)

        preview_btn = mark_secondary(QPushButton("Aperçu de la conversion"))
        preview_btn.clicked.connect(self._preview_conversion)
        preview_row = QHBoxLayout()
        preview_row.addWidget(preview_btn)
        preview_row.addStretch(1)
        layout.addLayout(preview_row)

        layout.addStretch(1)
        self._update_local_layout_label()
        return tab

    # ------------------------------------------------------- envoi rapide --
    def _build_quick_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        explain = QLabel(
            "Entrées courtes (mots de passe, licences) associées à un "
            "raccourci clavier GLOBAL : il répond même quand le focus est "
            "dans la console distante. Un même raccourci ne peut pas être "
            "attribué deux fois.")
        explain.setWordWrap(True)
        explain.setObjectName("hintLabel")
        layout.addWidget(explain)

        self.quick_table = QTableWidget(0, 4)
        self.quick_table.setHorizontalHeaderLabels(
            ["Libellé", "Raccourci", "ENTRÉE", "État"])
        self.quick_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.quick_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.quick_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.quick_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.quick_table.doubleClicked.connect(self._edit_entry)
        self.quick_table.itemSelectionChanged.connect(self._sync_buttons)
        # Voir la remarque sur content_edit : sans ce minimum bas, cette page
        # etait la plus exigeante des trois et imposait sa hauteur.
        self.quick_table.setMinimumHeight(60)
        layout.addWidget(self.quick_table, stretch=1)

        row = QHBoxLayout()
        add_btn = QPushButton("Ajouter")
        add_btn.clicked.connect(self._add_entry)
        self.edit_entry_btn = mark_secondary(QPushButton("Modifier"))
        self.edit_entry_btn.clicked.connect(self._edit_entry)
        self.del_entry_btn = mark_secondary(QPushButton("Supprimer"))
        self.del_entry_btn.clicked.connect(self._delete_entry)
        self.send_entry_btn = mark_secondary(
            QPushButton("Envoyer la sélection (3 s)"))
        self.send_entry_btn.clicked.connect(self._send_selected_entry)
        row.addWidget(add_btn)
        row.addWidget(self.edit_entry_btn)
        row.addWidget(self.del_entry_btn)
        row.addWidget(self.send_entry_btn)
        row.addStretch(1)

        self.palette_btn = mark_secondary(QPushButton())
        self.palette_btn.clicked.connect(self._toggle_palette)
        row.addWidget(self.palette_btn)
        self._sync_palette_button(self.palette_window.isVisible())

        self.orient_btn = mark_secondary(QPushButton())
        self.orient_btn.setToolTip(
            "Palette en colonne ou en ligne (aussi possible depuis son bandeau)")
        self.orient_btn.clicked.connect(self.palette_window.toggle_orientation)
        row.addWidget(self.orient_btn)
        layout.addLayout(row)

        self.palette_window.orientation_changed.connect(
            lambda _v: self._update_orient_button())
        self._update_orient_button()
        return tab

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

    # ------------------------------------------------------------ options --
    @property
    def _is_volatile(self) -> bool:
        return getattr(self.provider, "volatile", False)

    def _update_storage_banner(self) -> None:
        if self._is_volatile:
            self.storage_banner.setText(
                "Mode sans enregistrement : les entrées d'envoi rapide "
                "disparaîtront à la fermeture.")
            self.storage_banner.setProperty("warn", True)
            self.storage_banner.setVisible(True)
        else:
            self.storage_banner.setVisible(False)
            self.storage_banner.setProperty("warn", False)
        self.storage_banner.style().unpolish(self.storage_banner)
        self.storage_banner.style().polish(self.storage_banner)

    def _store_paths(self) -> tuple[str, str]:
        """(chemin annonce, chemin réel) - les deux différent quand Windows
        virtualise les écritures du Python du Microsoft Store."""
        path = getattr(self.provider, "path", None)
        if path is None:
            return "(non défini)", ""
        real = redirected_store_path(path)
        return str(path), str(real) if real else ""

    def _open_options(self) -> None:
        store_path, real_path = self._store_paths()
        dialog = OptionsDialog(self._theme, store_path,
                               current_method=self.controller.send_method,
                               altgr_with_ctrl=self._altgr_with_ctrl,
                               volatile=self._is_volatile,
                               real_path=real_path,
                               app_hotkeys=self._app_hotkeys,
                               refused_actions=self._refused_actions(),
                               parent=self)
        self._suspend_during_capture(*dialog.hotkey_edits.values())
        dialog.theme_previewed.connect(self._apply_theme)
        dialog.app_hotkey_changed.connect(
            lambda action, mods, vk: self._on_options_hotkey(
                dialog, action, mods, vk))
        dialog.method_changed.connect(self._apply_send_method)
        dialog.altgr_with_ctrl_changed.connect(self._apply_altgr_with_ctrl)
        dialog.reset_requested.connect(
            lambda: self._reset_master_password(dialog))
        dialog.open_folder_requested.connect(self._open_store_folder)
        dialog.open_store_requested.connect(
            lambda: self._switch_store(dialog))
        dialog.save_as_requested.connect(
            lambda: self._save_store_as(dialog))
        dialog.enable_storage_requested.connect(
            lambda: self._switch_store(dialog))
        try:
            dialog.exec()
        finally:
            self.hotkeys.resume()

    def _open_store_folder(self) -> None:
        store_path, real_path = self._store_paths()
        target = Path(real_path or store_path).parent
        target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _save_store_as(self, parent_dialog=None) -> None:
        """« Enregistrer sous » : jamais sur un fichier existant (le
        dialogue propose alors de l'ouvrir à la place)."""
        owner = parent_dialog or self
        current = getattr(self.provider, "path", None)
        if current is None or self._is_volatile:
            return
        chosen, _filter = QFileDialog.getSaveFileName(
            owner, "Enregistrer le coffre sous", str(current.parent),
            STORE_FILTER, options=QFileDialog.Option.DontConfirmOverwrite)
        if not chosen:
            return
        path = with_store_suffix(Path(chosen))
        if same_path(path, current):
            return
        if path.exists():
            box = QMessageBox(
                QMessageBox.Icon.Question, "Fichier existant",
                f"Un fichier existe déjà :\n{path}\n\n"
                "Il n'est jamais écrasé. Pour utiliser ce coffre, ouvre-le "
                "avec son propre master password.", parent=owner)
            open_btn = box.addButton("Ouvrir ce coffre",
                                     QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is open_btn:
                self._switch_store(parent_dialog, preselect=path)
            return
        if self.set_store_path(path, owner) and parent_dialog is not None:
            parent_dialog._refresh_path_label(*self._store_paths())

    def set_store_path(self, path: Path, owner=None) -> bool:
        """Copie le coffre vers `path` (NOUVEAU fichier) et l'utilise
        désormais, avec le master password courant ; l'ancien fichier est
        laissé en place. Un fichier existant n'est jamais écrasé."""
        owner = owner or self
        save_as = getattr(self.provider, "save_as", None)
        if self.provider is None or self._is_volatile or save_as is None:
            return False
        previous = self.provider.path
        if same_path(path, previous):
            return True
        if path.exists():
            QMessageBox.critical(
                owner, "Emplacement refusé",
                f"Un fichier existe déjà : {path}\nIl n'est jamais écrasé.")
            return False
        self._acl_warned = False        # nouveau fichier : nouvel avertissement
        try:
            # Création exclusive ; le chemin ne change qu'après succès.
            save_as(path, self.entries)
        except (SecretProviderError, OSError) as exc:
            QMessageBox.critical(owner, "Emplacement refusé", str(exc))
            return False
        remember_store(self._settings, path)
        self._log(f"Coffre enregistré sous {path} (l'ancien fichier "
                  f"{previous} est conservé).")
        self._warn_if_acl_not_restricted()
        return True

    def _switch_store(self, parent_dialog=None,
                      preselect: Optional[Path] = None) -> None:
        """Ouvre un autre coffre (existant ou nouveau) et l'utilise. Depuis
        le mode sans enregistrement, les entrées de la session y sont
        ajoutées."""
        owner = parent_dialog or self
        current = None if self._is_volatile else getattr(
            self.provider, "path", None)
        start = preselect or current
        if start is None:
            stored = self._settings.value(LAST_KEY)
            start = Path(str(stored)) if stored else None
        provider = LocalEncryptedProvider(start)
        missing_last = (start if start is not None and preselect is None
                        and current is None and not provider.exists else None)

        from ui.master_password_dialog import MasterPasswordDialog
        dialog = MasterPasswordDialog(
            provider, first_run=not provider.exists, allow_memory_mode=False,
            parent=owner, choose_store=True,
            recent_stores=recent_stores(self._settings),
            missing_last=missing_last, current_store=current)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        chosen = dialog.provider
        dialog.deleteLater()           # ne garde aucun champ de mot de passe
        if not accepted:
            return
        if current is not None and same_path(chosen.path, current):
            # Coffre actuel revalidé : rien à changer (la clé en double est
            # retirée de la mémoire).
            if chosen is not self.provider:
                chosen.lock()
            return
        was_volatile = self._is_volatile
        if not self.adopt_provider(chosen, owner):
            return
        if parent_dialog is None:
            return
        if was_volatile:
            parent_dialog.close()      # ses boutons visaient le mode mémoire
        else:
            parent_dialog._refresh_path_label(*self._store_paths())

    def adopt_provider(self, provider: SecretProvider, owner=None) -> bool:
        """Utilise `provider` (déjà déverrouillé) à la place du coffre
        courant. Les entrées d'un mode sans enregistrement sont AJOUTÉES au
        coffre, jamais à la place de son contenu."""
        carried = list(self.entries) if self._is_volatile else []
        try:
            entries = provider.load()
            if carried:
                entries = entries + carried
                provider.save(entries)
        except SecretProviderError as exc:
            provider.lock()
            QMessageBox.critical(owner or self, "Coffre inutilisable", str(exc))
            return False

        # Les raccourcis globaux des anciennes entrées taperaient sinon
        # encore leurs secrets.
        self.hotkeys.unregister_all(self.entries)
        old = self.provider
        if old is not None and old is not provider:
            old.lock()                  # clé de l'ancien coffre hors mémoire
        self.provider = provider
        self._acl_warned = False        # nouveau coffre : nouvel avertissement
        self.entries = entries
        self.hotkeys.register_all(self.entries)
        self._refresh_quick_table()
        self._update_storage_banner()
        path = getattr(provider, "path", None)
        if path is not None:
            remember_store(self._settings, path)
        added = f", dont {len(carried)} de la session" if carried else ""
        self._log(f"Coffre ouvert : {path} ({len(entries)} entrée(s){added}).")
        self._warn_if_acl_not_restricted()
        return True

    def _apply_send_method(self, method: str) -> None:
        self.controller.send_method = method
        self._settings.setValue("send/method", method)
        self._log(f"Méthode d'envoi : {method}.")

    def _apply_altgr_with_ctrl(self, enabled: bool) -> None:
        self._altgr_with_ctrl = enabled
        w32_module = __import__("keyboard.backend", fromlist=["backend"])
        if getattr(w32_module, "IS_REAL", False):
            from keyboard import win32_api
            win32_api.ALT_GR_WITH_CTRL = enabled
        self._settings.setValue("send/altgr_with_ctrl", enabled)
        self._log(
            f"AltGr envoyé {'avec' if enabled else 'sans'} Ctrl gauche.")

    def _refresh_options_icon(self) -> None:
        """La roue est dessinée, donc recolorée à chaque changement de thème :
        une icône figée deviendrait invisible sur le fond opposé."""
        self.options_btn.setIcon(icons.gear_icon(theme_token("on_accent")))

    def _apply_theme(self, theme: str) -> None:
        app = QApplication.instance()
        if app is not None:
            self._theme = apply_theme(app, theme)
            self._settings.setValue("ui/theme", self._theme)
            self._refresh_options_icon()

    def _reset_master_password(self, parent_dialog=None) -> None:
        owner = parent_dialog or self
        confirm = QMessageBox.warning(
            owner, "Réinitialiser le master password",
            "Toutes les entrées d'envoi rapide vont être définitivement "
            "supprimées, et un nouveau master password te sera demandé.\n\n"
            "Cette action est irréversible. Continuer ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return

        if not self.do_reset_master_password():
            return

        # Redemander tout de suite un mot de passe : sans stockage
        # deverrouille, l'onglet Envoi rapide serait inutilisable.
        from ui.master_password_dialog import MasterPasswordDialog
        dialog = MasterPasswordDialog(self.provider, first_run=True, parent=owner)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        choice = dialog.choice
        dialog.deleteLater()           # ne garde aucun champ de mot de passe
        if accepted and choice == "unlock":
            self._load_entries()
            self._log("Nouveau master password défini, stockage vide.")
            return
        # « Continuer sans enregistrer » (etait ignore : le journal annoncait
        # a tort un nouveau master password) ou dialogue ferme : on bascule
        # en memoire volatile, sinon l'Envoi rapide resterait inutilisable.
        # Options > « Activer l'enregistrement chiffré » permet d'y revenir.
        self.use_memory_storage()
        self._log("Stockage chiffré supprimé ; mode sans enregistrement "
                  "(les entrées disparaîtront à la fermeture).", "WARN")
        if parent_dialog is not None:
            parent_dialog.close()      # ses boutons visaient l'ancien coffre

    def use_memory_storage(self) -> None:
        """Bascule sur le stockage volatil (bandeau permanent)."""
        from security.memory_provider import MemoryProvider
        # Sinon un raccourci global resterait enregistre et taperait encore
        # le secret d'une entree qui n'existe plus.
        self.hotkeys.unregister_all(self.entries)
        provider = MemoryProvider()
        provider.unlock("")
        self.provider = provider
        self._acl_warned = False
        self.entries = []
        self._refresh_quick_table()
        self._update_storage_banner()

    def do_reset_master_password(self) -> bool:
        """Partie effective, sans dialogue - appelable par les tests."""
        if self.provider is None:
            return False
        try:
            self.hotkeys.unregister_all(self.entries)
            self.entries = []
            self.provider.reset()
        except SecretProviderError as exc:
            QMessageBox.critical(self, "Réinitialisation impossible", str(exc))
            return False
        self._refresh_quick_table()
        self._log("Stockage chiffré supprimé.", "WARN")
        return True

    def _update_orient_button(self) -> None:
        vertical = self.palette_window.orientation == "vertical"
        self.orient_btn.setText(
            "Palette en ligne" if vertical else "Palette en colonne")

    # ------------------------------------------------------------- actions --
    def _build_send_bar(self) -> QFrame:
        # Barre persistante ancree en bas, accessible depuis tous les onglets.
        bar = QFrame()
        bar.setObjectName("sendBar")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(3)

        # Ligne 1 : délais + boutons d'envoi
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        char_label = QLabel("Car :")
        row1.addWidget(char_label)
        self.char_delay_spin = QSpinBox()
        self.char_delay_spin.setRange(1, 1000)
        self.char_delay_spin.setValue(15)
        self.char_delay_spin.setSuffix(" ms")
        self.char_delay_spin.setMinimumWidth(86)
        self.char_delay_spin.valueChanged.connect(self._update_estimate)
        row1.addWidget(self.char_delay_spin)
        for widget in (char_label, self.char_delay_spin):
            widget.setToolTip("Délai entre deux caractères tapés")

        line_label = QLabel("Ligne :")
        row1.addWidget(line_label)
        self.line_delay_spin = QSpinBox()
        self.line_delay_spin.setRange(0, 10000)
        self.line_delay_spin.setValue(300)
        self.line_delay_spin.setSuffix(" ms")
        self.line_delay_spin.setMinimumWidth(96)
        self.line_delay_spin.valueChanged.connect(self._update_estimate)
        row1.addWidget(self.line_delay_spin)
        for widget in (line_label, self.line_delay_spin):
            widget.setToolTip("Pause après chaque ligne (après ENTRÉE)")

        countdown_label = QLabel("Délai :")
        row1.addWidget(countdown_label)
        self.countdown_spin = QSpinBox()
        self.countdown_spin.setRange(0, 60)
        self.countdown_spin.setValue(5)
        self.countdown_spin.setSuffix(" s")
        self.countdown_spin.setMinimumWidth(72)
        row1.addWidget(self.countdown_spin)
        for widget in (countdown_label, self.countdown_spin):
            widget.setToolTip(
                "Compte à rebours avant le début de l'envoi du contenu, "
                "le temps de placer le curseur dans la console")

        row1.addStretch(1)

        self.start_btn = QPushButton("\u25b6\u2002Démarrer")
        self.start_btn.setObjectName("startButton")
        self.start_btn.clicked.connect(self._start_content_send)
        row1.addWidget(self.start_btn)

        self.pause_btn = mark_secondary(QPushButton("Pause"))
        self.pause_btn.clicked.connect(self._toggle_pause)
        row1.addWidget(self.pause_btn)

        self.stop_btn = QPushButton("\u25a0\u2002Stop")
        self.stop_btn.setObjectName("stopButton")
        self.stop_btn.clicked.connect(lambda: self.controller.stop())
        row1.addWidget(self.stop_btn)
        layout.addLayout(row1)

        # Ligne 2 : indicateur cible + progression + statut + garde focus
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        self.bar_target_label = QLabel("Cible : (aucune)")
        self.bar_target_label.setObjectName("barTargetLabel")
        row2.addWidget(self.bar_target_label, stretch=2)

        self.progress = QProgressBar()
        self.progress.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Fixed)
        row2.addWidget(self.progress, stretch=1)

        self.status_label = QLabel("Prêt.")
        self.status_label.setObjectName("statusLabel")
        row2.addWidget(self.status_label, stretch=2)

        self.guard_chk = QCheckBox("Garde focus")
        self.guard_chk.setChecked(True)
        self.guard_chk.setToolTip(
            "Met l'envoi en pause si le focus quitte la fenêtre cible")
        self.guard_chk.toggled.connect(
            lambda v: setattr(self.controller, "guard_focus", v))
        row2.addWidget(self.guard_chk)
        layout.addLayout(row2)

        self._restore_delays()
        return bar

    def _update_bar_target(self) -> None:
        if self._target_hwnd:
            t = self._target_title
            t = (t[:40] + "\u2026") if len(t) > 40 else t
            admin = "  ⚠ administrateur" if self._target_elevation else ""
            self.bar_target_label.setText(
                f"Cible : {t}  (0x{self._target_hwnd:X}){admin}")
            self.bar_target_label.setProperty("active", True)
        else:
            self.bar_target_label.setText("Cible : (aucune)")
            self.bar_target_label.setProperty("active", False)
        self.bar_target_label.style().unpolish(self.bar_target_label)
        self.bar_target_label.style().polish(self.bar_target_label)

    def _build_log_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setObjectName("monoEdit")
        layout.addWidget(self.log_edit)
        return tab

    # ============================================================ fenetres --
    def _refresh_windows(self) -> None:
        needle = self.window_filter.text().strip().lower()
        windows = w32.list_windows()
        if needle:
            windows = [w for w in windows
                       if needle in w.process_name.lower() or needle in w.title.lower()]

        self.windows_table.setRowCount(0)
        for win in windows:
            row = self.windows_table.rowCount()
            self.windows_table.insertRow(row)
            self.windows_table.setItem(row, 0, QTableWidgetItem(win.process_name))
            self.windows_table.setItem(row, 1, QTableWidgetItem(str(win.pid)))
            self.windows_table.setItem(row, 2, QTableWidgetItem(f"0x{win.hwnd:X}"))
            title_item = QTableWidgetItem(win.title)
            title_item.setData(Qt.ItemDataRole.UserRole, win.hwnd)
            self.windows_table.setItem(row, 3, title_item)
        # La fenetre de travail reste selectionnee apres un rafraichissement
        # ou un filtrage (sans redeclencher _set_target).
        self._select_target_row(notify=False)

    def _find_window_row(self, hwnd: int) -> int:
        for row in range(self.windows_table.rowCount()):
            item = self.windows_table.item(row, 3)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == hwnd:
                return row
        return -1

    def _select_target_row(self, notify: bool = True) -> bool:
        """Selectionne la ligne de la fenetre de travail, comme un clic.
        Rend False si elle n'est pas dans la liste (fenetre fermee...)."""
        row = self._find_window_row(self._target_hwnd) if self._target_hwnd else -1
        if row < 0:
            return False
        was_blocked = self.windows_table.signalsBlocked()
        if not notify:
            self.windows_table.blockSignals(True)
        try:
            self.windows_table.selectRow(row)
            self.windows_table.scrollToItem(self.windows_table.item(row, 3))
        finally:
            self.windows_table.blockSignals(was_blocked)
        return True

    def _on_window_selected(self) -> None:
        rows = self.windows_table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.windows_table.item(rows[0].row(), 3)
        self._set_target(item.data(Qt.ItemDataRole.UserRole), item.text())

    def _set_target(self, hwnd: int, title: str) -> None:
        changed = hwnd != self._target_hwnd
        self._target_hwnd = hwnd
        self._target_title = title
        self.target_label.setText(f"Fenêtre de travail : {title}  (0x{hwnd:X})")
        self._update_target_layout_label()
        self._target_elevation = self._elevation_warning(hwnd)
        self.target_elevation_label.setText(self._target_elevation)
        self.target_elevation_label.setVisible(bool(self._target_elevation))
        if changed and self._target_elevation:
            self._log(self._target_elevation, "WARN")
        self._update_bar_target()
        self._sync_buttons()

    def _elevation_warning(self, hwnd: int) -> str:
        """Avertissement si `hwnd` tourne a une integrite superieure a
        l'outil (console « en administrateur ») : Windows y bloque SendInput
        SANS erreur, rien n'est tape. Chaine vide sinon. Avertir seulement :
        une detection « probable » ne doit jamais bloquer un envoi."""
        state = w32.is_elevated_above_us(hwnd)
        if state is False:
            return ""
        if state:
            return ("⚠ Cette fenêtre tourne en administrateur : Windows "
                    "bloque les frappes simulées d'un programme non "
                    "administrateur, rien n'y sera tapé. Relance "
                    "SendToConsole en administrateur (clic droit > "
                    "Exécuter en tant qu'administrateur).")
        return ("⚠ Cette fenêtre tourne peut-être en administrateur (ou sous "
                "un autre compte Windows). Si rien n'y est tapé, relance "
                "SendToConsole en administrateur (clic droit > Exécuter en "
                "tant qu'administrateur).")

    def _warn_if_elevated(self, hwnd: int) -> None:
        """Rappel au moment d'envoyer (l'envoi part quand meme)."""
        warning = self._elevation_warning(hwnd)
        if warning:
            self._log(warning, "WARN")
            self._set_status("Attention : cible administrateur, les frappes "
                             "seront probablement bloquées.")

    def _update_target_layout_label(self) -> None:
        if not self._target_hwnd:
            self.target_layout_label.setText("")
            return

        target_hkl = w32.layout_of_window(self._target_hwnd)
        own_hkl = w32.current_layout()
        target_name = w32.layout_name(target_hkl)

        if target_hkl and own_hkl and target_hkl != own_hkl:
            self.target_layout_label.setText(
                f"⚠ Clavier de cette fenêtre : {target_name} — différent du "
                f"tien ({w32.layout_name(own_hkl)}). Les frappes sont "
                f"calculées pour {target_name}.")
            self.target_layout_label.setProperty("warn", True)
        else:
            self.target_layout_label.setText(
                f"Clavier de cette fenêtre : {target_name}")
            self.target_layout_label.setProperty("warn", False)

        self.target_layout_label.style().unpolish(self.target_layout_label)
        self.target_layout_label.style().polish(self.target_layout_label)

    def _start_capture(self) -> None:
        self._pick_left = 3
        self.capture_btn.setEnabled(False)
        self._set_status(f"Clique sur la fenêtre à cibler… {self._pick_left}")
        self._pick_timer.start()

    def _pick_tick(self) -> None:
        self._pick_left -= 1
        if self._pick_left > 0:
            self._set_status(f"Clique sur la fenêtre à cibler… {self._pick_left}")
            return

        self._pick_timer.stop()
        self.capture_btn.setEnabled(True)
        self._capture_window(w32.get_foreground_window())

    def _capture_window(self, hwnd: int, from_hotkey: bool = False) -> bool:
        """Prend `hwnd` comme fenetre de travail et la selectionne dans la
        liste Cible, comme si l'operateur avait clique sa ligne. Commun au
        bouton (apres compte a rebours) et au raccourci global."""
        if not hwnd:
            self._set_status("Capture annulée : aucune fenêtre au premier plan.")
            self._log("Capture annulée (aucune fenêtre au premier plan).", "WARN")
            self._notify("Capture annulée : aucune fenêtre au premier plan.",
                         from_hotkey)
            return False
        if w32.belongs_to_process(hwnd, self._own_pid):
            self._set_status("Capture annulée : l'outil était au premier plan.")
            self._log("Capture annulée (fenêtre de l'outil au premier plan).", "WARN")
            self._notify("Capture annulée : l'outil était au premier plan.",
                         from_hotkey)
            return False

        title = w32.title_of(hwnd) or "(sans titre)"
        self._set_target(hwnd, title)
        self._refresh_windows()
        if self._find_window_row(hwnd) < 0 and self.window_filter.text():
            # Le filtre masquait la fenetre capturee : on le vide
            # (textChanged relance _refresh_windows, qui la selectionne).
            self.window_filter.clear()
        if not self._select_target_row():
            self._log("Fenêtre capturée absente de la liste (fenêtre "
                      "outil ou sans titre) : elle reste la cible.", "WARN")
        self._log(f"Capture : 0x{hwnd:X} - {title}")
        admin = ""
        if self._target_elevation:
            admin = (" (administrateur : frappes bloquées)"
                     if "tourne en administrateur" in self._target_elevation
                     else " (administrateur ? frappes peut-être bloquées)")
        self._set_status(f"Cible : {title}{admin}")
        self._notify(f"Cible : {title}{admin}", from_hotkey)
        return True

    def _notify(self, message: str, from_hotkey: bool = False) -> None:
        """Retour visible quand l'operateur ne regarde pas l'outil : bulle du
        tray, via main.py. Depuis un raccourci global, il est dans la console
        (fenetre cachee OU simplement derriere) ; depuis le bouton, seulement
        si la fenetre est cachee."""
        hidden = not self.isVisible() or self.isMinimized()
        if hidden or (from_hotkey and not self.isActiveWindow()):
            self.notify.emit(message)

    def _track_foreground(self) -> None:
        """Memorise en continu la dernière fenêtre active exterieure a l'outil."""
        hwnd = w32.get_foreground_window()
        if not hwnd:
            return
        if w32.belongs_to_process(hwnd, self._own_pid):
            return
        self._last_foreground = hwnd

    # ============================================================= contenu --
    def _load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Charger un fichier", "",
            "Fichiers texte (*.txt *.conf *.cfg *.yaml *.yml *.sh *.ini);;"
            "Tous les fichiers (*.*)")
        if not path:
            return
        try:
            content = open(path, "r", encoding="utf-8", errors="replace").read()
        except OSError as exc:
            self._log(f"Lecture impossible : {exc}", "ERR")
            return

        self.content_edit.setPlainText(content)
        self.file_label.setText(os.path.basename(path))
        self._log(f"Fichier chargé : {path}")

        non_ascii = {c for c in content if not (0x20 <= ord(c) <= 0x7E or c in "\t\r\n")}
        if non_ascii:
            self._log(
                f"{len(non_ascii)} caractère(s) non-ASCII : les consoles BMC "
                f"les transmettent souvent mal.", "WARN")

    def _update_estimate(self) -> None:
        text = self.content_edit.toPlainText()
        if not text:
            self.estimate_label.setText("Aucun contenu.")
            return
        lines = text.splitlines()
        chars = sum(len(line) for line in lines)
        ms = chars * self.char_delay_spin.value() + len(lines) * self.line_delay_spin.value()
        self.estimate_label.setText(
            f"{len(lines)} ligne(s) | {chars} caractère(s) | durée estimée ~ {ms / 1000:.1f} s")

    def _start_content_send(self) -> None:
        text = self.content_edit.toPlainText()
        if not text.strip():
            self._set_status("Aucun contenu à envoyer.")
            return
        if not self._target_hwnd:
            self._set_status("Sélectionne d'abord une fenêtre cible.")
            return

        self._update_target_layout_label()
        built = self._build(text, final_enter=self.final_enter_chk.isChecked(),
                            target_hwnd=self._target_hwnd,
                            skip_comments=self.skip_comments_chk.isChecked(),
                            skip_empty=self.skip_empty_chk.isChecked())
        if built is None:
            return

        self._log(f"Envoi du contenu vers « {self._target_title} » "
                  f"({len(built.tokens)} frappe(s)), clavier "
                  f"{w32.layout_name(self.converter.hkl_local)}.")
        self._warn_if_elevated(self._target_hwnd)
        self.controller.start(built.tokens, self.converter.hkl_local,
                              self._target_hwnd,
                              countdown=self.countdown_spin.value(),
                              force_foreground=True)

    # ======================================================= clavier cible --
    def _update_local_layout_label(self) -> None:
        self.converter.refresh_local()
        self.local_layout_label.setText(
            f"Disposition détectée sur ce poste : 0x{self.converter.hkl_local:X}"
            if w32.IS_REAL else
            "Disposition locale indisponible en mode simulation.")

    def _on_conv_mode_changed(self, index: int) -> None:
        self.converter.set_mode(MODE_LAYOUT if index == 1 else MODE_NONE)
        self.layout_combo.setEnabled(index == 1)
        if index == 1:
            self._on_target_layout_changed(self.layout_combo.currentIndex())

    def _on_target_layout_changed(self, index: int) -> None:
        if self.converter.mode != MODE_LAYOUT:
            return
        name = KNOWN_LAYOUTS[index][0]
        if self.converter.set_target_by_index(index):
            self._log(f"Disposition de la console : {name}")
        else:
            self._log(f"Disposition « {name} » indisponible sur ce poste.", "ERR")

    def _preview_conversion(self) -> None:
        """Aperçu sur un échantillon FIXE : le contenu à envoyer peut être un
        secret, il n'apparait jamais dans le journal (seul un décompte)."""
        typed, missing = self.converter.preview(PREVIEW_SAMPLE)
        self._log(f"Aperçu - voulu  : {PREVIEW_SAMPLE}")
        self._log(f"Aperçu - frappé : {typed}")
        if missing:
            self._log(f"Sans correspondance : {' '.join(sorted(missing))}", "WARN")
        _, content_missing = self.converter.preview(self.content_edit.toPlainText())
        if content_missing:
            self._log(
                f"Contenu à envoyer : {len(content_missing)} caractère(s) "
                "distinct(s) sans correspondance sur la disposition cible.",
                "WARN")

    # ======================================================== envoi rapide --
    def _load_entries(self) -> None:
        if self.provider is None or not self.provider.is_unlocked():
            self._refresh_quick_table()
            return
        try:
            self.entries = self.provider.load()
        except SecretProviderError as exc:
            QMessageBox.critical(self, "Chargement impossible", str(exc))
            self.entries = []
        self.hotkeys.register_all(self.entries)
        self._refresh_quick_table()
        self._log(f"{len(self.entries)} entrée(s) d'envoi rapide chargée(s).")
        self._warn_if_acl_not_restricted()

    def _save_entries(self) -> None:
        if self.provider is None or not self.provider.is_unlocked():
            return
        try:
            self.provider.save(self.entries)
        except SecretProviderError as exc:
            QMessageBox.critical(self, "Enregistrement impossible", str(exc))
            return
        self._warn_if_acl_not_restricted()

    def _warn_if_acl_not_restricted(self) -> None:
        """Le coffre garde ses droits hérités (clé USB FAT32, partage réseau,
        refus Windows) : un autre compte local pourrait le copier. Signalé une
        seule fois par session, sans bloquer."""
        if (getattr(self.provider, "acl_restricted", None) is False
                and not self._acl_warned):
            self._acl_warned = True
            self._log(
                "Droits du fichier de stockage non restreints au compte "
                "courant (support sans ACL NTFS ?) : d'autres comptes locaux "
                "pourraient le copier.", "WARN")

    def _refresh_quick_table(self) -> None:
        self.quick_table.setRowCount(0)
        for entry in self.entries:
            row = self.quick_table.rowCount()
            self.quick_table.insertRow(row)

            label_item = QTableWidgetItem(entry.label)
            label_item.setData(Qt.ItemDataRole.UserRole, entry)
            self.quick_table.setItem(row, 0, label_item)
            self.quick_table.setItem(
                row, 1, QTableWidgetItem(format_hotkey(entry.mods, entry.vk)))
            self.quick_table.setItem(
                row, 2, QTableWidgetItem("oui" if entry.final_enter else "non"))

            if not entry.has_hotkey:
                state = "-"
            elif entry.registered:
                state = "actif"
            else:
                state = "refusé"
            self.quick_table.setItem(row, 3, QTableWidgetItem(state))

        self.palette_window.refresh(self.entries)
        self._sync_buttons()

    def _selected_entry(self) -> Optional[QuickEntry]:
        rows = self.quick_table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.quick_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def _add_entry(self) -> None:
        dlg = QuickEntryDialog(self.entries, parent=self,
                               reserved=self._app_hotkeys)
        if not self._exec_entry_dialog(dlg):
            return
        entry = dlg.result_entry()
        self.entries.append(entry)
        self.hotkeys.register(entry)
        self._save_entries()
        self._refresh_quick_table()
        self._log(f"Entrée ajoutée : {entry.label}")

    def _edit_entry(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        dlg = QuickEntryDialog(self.entries, entry=entry, parent=self,
                               reserved=self._app_hotkeys)
        if not self._exec_entry_dialog(dlg):
            return

        updated = dlg.result_entry()
        self.hotkeys.unregister(entry)
        index = self.entries.index(entry)
        self.entries[index] = updated
        self.hotkeys.register(updated)
        self._save_entries()
        self._refresh_quick_table()
        self._log(f"Entrée modifiée : {updated.label}")

    def _delete_entry(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if QMessageBox.question(
                self, "Supprimer",
                f"Supprimer l'entrée « {entry.label} » ?") != QMessageBox.StandardButton.Yes:
            return
        self.hotkeys.unregister(entry)
        self.entries.remove(entry)
        self._save_entries()
        self._refresh_quick_table()
        self._log(f"Entrée supprimée : {entry.label}")

    def _send_selected_entry(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        self._quick_send(entry, countdown=3)

    def _on_hotkey(self, entry: QuickEntry, hwnd: int) -> None:
        if self._pause_for_hotkey_during_send():
            return
        # hwnd a ete releve dans le filtre d'evenements, a l'instant meme du
        # raccourci : c'est la fenêtre que l'operateur regardait.
        if not self._is_foreign_target(hwnd):
            # Comme pour « Coller » : pas de repli sur la derniere fenetre de
            # travail (reserve au clic sur la palette), le secret partirait
            # dans une fenetre que l'operateur ne regarde pas.
            reason = "raccourci pressé hors d'une fenêtre cible"
            self._deny(reason)
            self._notify(f"Envoi rapide refusé : {reason}", from_hotkey=True)
            return
        self._quick_send(entry, countdown=0, hwnd=hwnd)

    def _is_foreign_target(self, hwnd: int) -> bool:
        """Fenetre valide pour une frappe declenchee par raccourci : non nulle
        (transition d'activation) et exterieure a l'outil."""
        return bool(hwnd) and not w32.belongs_to_process(hwnd, self._own_pid)

    def _pause_for_hotkey_during_send(self) -> bool:
        """Un raccourci global presse PENDANT un envoi : ses modificateurs
        (Ctrl/Alt) sont physiquement tenus pendant que l'outil tape, et
        chaque caractere injecte deviendrait Ctrl+lettre ou Alt+lettre dans
        la cible. On met l'envoi en pause (Reprendre pour continuer) et on
        ignore le raccourci. Rend True si c'etait le cas."""
        if not self.controller.is_running:
            return False
        self.controller.pause()
        self._deny("raccourci pressé pendant un envoi : envoi mis en pause "
                   "(Reprendre pour continuer)")
        return True

    def _on_action_hotkey(self, action: str, hwnd: int) -> None:
        # hwnd releve a l'instant du raccourci : la fenetre que l'operateur
        # regardait (celle a cibler).
        if self._pause_for_hotkey_during_send():
            return
        # Regle 2bis : RegisterHotKey consomme la touche ; au relachement
        # d'Alt, la cible activerait sa barre de menus et l'envoi suivant
        # perdrait ses premiers caracteres. Masque Ctrl + relachement.
        w32.release_held_modifiers()
        if action == ACTION_PASTE_CLIPBOARD:
            # Hors du filtre natif (WM_HOTKEY) : lire le presse-papiers peut
            # bloquer quand son proprietaire differe le rendu (RDP, Office).
            # La fenetre visee est deja relevee, le report est sans risque.
            QTimer.singleShot(0, lambda h=hwnd: self._paste_clipboard(h))
        elif action == ACTION_CAPTURE_TARGET:
            self._capture_window(hwnd, from_hotkey=True)
        elif action == ACTION_TOGGLE_PALETTE:
            self._toggle_palette()

    def _paste_clipboard(self, hwnd: int, retried: bool = False) -> None:
        """« Coller » la ou le vrai collage est bloque : le texte copie
        (Ctrl+C) est TAPE dans la fenetre ou le raccourci a ete presse, par
        le meme chemin qu'une entree d'envoi rapide (conversion clavier,
        garde-fou de focus, Pause/Stop). Lecture seule du presse-papiers ;
        le texte est traite comme un secret (jamais affiche ni journalise).

        Garde-fous (une frappe n'a pas les protections d'un vrai collage) :
        jamais de repli sur une autre fenetre, caracteres de controle
        refuses, pas d'ENTREE sur la derniere ligne, et un texte de
        plusieurs lignes (chacune validee par ENTREE) demande un second
        appui du raccourci."""
        if not self._is_foreign_target(hwnd):
            # Contrairement a la palette, aucun repli sur la derniere fenetre
            # de travail : le contenu (souvent un secret) partirait dans une
            # fenetre que l'operateur ne regarde pas.
            self._refuse_paste("raccourci pressé hors d'une fenêtre cible")
            return
        from ui.clipboard import read_clipboard_text
        raw = read_clipboard_text()
        if not raw and not retried:
            # Presse-papiers peut-etre verrouille un instant par une autre
            # application : une seconde lecture avant de conclure « vide ».
            QTimer.singleShot(
                120, lambda: self._paste_clipboard(hwnd, retried=True))
            return
        plan, reason = prepare_paste(raw)
        del raw
        if plan is None:
            self._refuse_paste(reason or EMPTY_REASON)
            return
        if plan.lines > 1 and not self._confirm_multiline_paste(plan, hwnd):
            return
        entry = QuickEntry(label="Presse-papiers", text=plan.text, secret=True,
                           final_enter=False)
        self._quick_send(entry, countdown=0, hwnd=hwnd)

    def _refuse_paste(self, reason: str) -> None:
        """Refus visible meme fenetre cachee (bulle du tray) : l'operateur
        regarde sa console, pas l'outil."""
        self._deny(reason)
        self._notify(f"Collage refusé : {reason}", from_hotkey=True)

    def _confirm_multiline_paste(self, plan: PastePlan, hwnd: int) -> bool:
        """Plusieurs lignes = plusieurs commandes executees (ENTREE apres
        chacune) : premier appui = avertissement, second appui dans le delai,
        meme fenetre et meme texte = frappe. Seule une empreinte HMAC (cle
        aleatoire de la session) du texte est gardee, jamais le texte."""
        digest = hmac.new(self._paste_key, plan.text.encode("utf-8"),
                          hashlib.sha256).digest()
        now = time.monotonic()
        pending = self._paste_pending
        self._paste_pending = None
        if (pending is not None and hmac.compare_digest(pending[0], digest)
                and pending[1] == hwnd and now <= pending[2]):
            return True
        self._paste_pending = (digest, hwnd, now + PASTE_CONFIRM_S)
        message = (f"{plan.lines} lignes à coller : chaque ligne sera validée "
                   "par ENTRÉE. Presse de nouveau le raccourci dans les "
                   f"{PASTE_CONFIRM_S} s pour confirmer.")
        self._log(f"Collage de {plan.lines} lignes en attente de confirmation.",
                  "WARN")
        self.status_label.setText(message)
        self._notify(message, from_hotkey=True)
        return False

    def register_app_hotkeys(self) -> None:
        """A appeler une fois le HWND attache (main.py)."""
        for action, (mods, vk) in self._app_hotkeys.items():
            if not (mods and vk):
                continue
            owner = hotkey_owner(self.entries, {}, mods, vk)
            if owner is not None:
                self._log(
                    f"Raccourci {format_hotkey(mods, vk)} "
                    f"(« {ACTION_LABELS[action]} ») inactif : déjà attribué à "
                    f"{owner}.", "WARN")
            elif not self.hotkeys.register_action(action, mods, vk):
                self._log(
                    f"Raccourci {format_hotkey(mods, vk)} "
                    f"(« {ACTION_LABELS[action]} ») refusé : déjà pris par "
                    "une autre application.", "WARN")

    def _refused_actions(self) -> set[str]:
        """Actions reglees mais non actives (refus au demarrage)."""
        if not self.hotkeys.is_attached:
            return set()
        return {action for action, (mods, vk) in self._app_hotkeys.items()
                if mods and vk and not self.hotkeys.is_action_registered(action)}

    def _exec_entry_dialog(self, dlg: QuickEntryDialog) -> bool:
        """Ouvre le dialogue d'entree, raccourcis globaux suspendus pendant
        la saisie de la combinaison. Rend True si valide."""
        self._suspend_during_capture(dlg.hotkey_edit)
        try:
            return dlg.exec() == QuickEntryDialog.DialogCode.Accepted
        finally:
            self.hotkeys.resume()

    def _suspend_during_capture(self, *edits) -> None:
        """Raccourcis globaux suspendus tant qu'un champ de saisie de
        combinaison a le focus (voir HotkeyManager.suspend)."""
        for edit in edits:
            edit.capture_started.connect(self.hotkeys.suspend)
            edit.capture_ended.connect(self.hotkeys.resume)

    def set_app_hotkey(self, action: str, mods: int, vk: int) -> Optional[str]:
        """Change le raccourci d'une action. Rend None si c'est fait, sinon
        la raison du refus (l'ancien raccourci est alors conserve)."""
        old = self._app_hotkeys.get(action, (0, 0))
        unchanged = (mods, vk) == tuple(old)
        if unchanged and (not (mods and vk) or not self.hotkeys.is_attached
                          or self.hotkeys.is_action_registered(action)):
            return None
        unsafe = unsafe_hotkey_reason(mods, vk, w32.current_layout())
        if unsafe is not None:
            return unsafe
        owner = hotkey_owner(self.entries, self._app_hotkeys, mods, vk,
                             exclude_action=action)
        if owner is not None:
            return f"{format_hotkey(mods, vk)} est déjà attribué à {owner}."
        # Enregistre seulement si l'outil est attache a un HWND (sinon, ce
        # sera fait par register_app_hotkeys au demarrage). En cas de refus,
        # register_action a laisse l'ancien raccourci actif.
        if self.hotkeys.is_attached and not self.hotkeys.register_action(
                action, mods, vk):
            return (f"{format_hotkey(mods, vk)} est déjà pris par une autre "
                    "application.")
        self._app_hotkeys[action] = (mods, vk)
        self._settings.setValue(f"hotkeys/{action}_mods", mods)
        self._settings.setValue(f"hotkeys/{action}_vk", vk)
        self._log(f"Raccourci « {ACTION_LABELS[action]} » : "
                  f"{format_hotkey(mods, vk)}.")
        return None

    def _on_options_hotkey(self, dialog: OptionsDialog, action: str,
                           mods: int, vk: int) -> None:
        error = self.set_app_hotkey(action, mods, vk)
        if error is not None:
            QMessageBox.warning(dialog, "Raccourci refusé",
                                f"{error}\n\nChoisis une autre combinaison.")
            dialog.set_app_hotkey(action, *self._app_hotkeys[action])
        dialog.set_refused_actions(self._refused_actions())

    def _on_palette_click(self, entry: QuickEntry) -> None:
        self._quick_send(entry, countdown=0)

    def _on_hotkey_refused(self, entry: QuickEntry) -> None:
        self._log(
            f"Raccourci {format_hotkey(entry.mods, entry.vk)} refusé : "
            f"déjà pris par une autre application.", "WARN")

    def _quick_send(self, entry: QuickEntry, countdown: int,
                    hwnd: int = 0) -> None:
        """Envoi rapide : on ne force pas le premier plan. Le curseur est déjà
        dans le champ voulu au moment ou le raccourci tombe.

        'hwnd' : fenêtre visee relevee par l'appelant a l'instant du
        declenchement. A defaut, on lit le premier plan maintenant."""
        if self.controller.is_running:
            self._deny("un envoi est déjà en cours")
            return
        if not entry.text:
            self._deny(f"l'entrée « {entry.label} » est vide")
            return

        if not hwnd:
            hwnd = w32.get_foreground_window()
        restore = False
        if not hwnd or w32.belongs_to_process(hwnd, self._own_pid):
            # Le focus est sur l'outil (clic sur la palette) : on retombe sur
            # la derniere fenetre de travail connue et on lui rend le focus.
            hwnd = self._last_foreground
            restore = True

        if not hwnd:
            self._deny("aucune fenêtre de travail identifiée")
            return

        built = self._build(entry.text, final_enter=entry.final_enter,
                            target_hwnd=hwnd)
        if built is None:
            return

        target_title = w32.title_of(hwnd) or f"0x{hwnd:X}"
        self._log(f"Envoi rapide « {entry.label} » vers {target_title}, "
                  f"clavier {w32.layout_name(self.converter.hkl_local)}.")
        self._warn_if_elevated(hwnd)
        self.controller.start(built.tokens, self.converter.hkl_local, hwnd,
                              countdown=countdown, force_foreground=False,
                              restore_focus=restore)

    def _toggle_palette(self) -> None:
        if self.palette_window.isVisible():
            self.palette_window.hide()
        else:
            self.palette_window.refresh(self.entries)
            self.palette_window.show()
        # Le libellé n'est plus pose ici : il suit la visibilité reelle de la
        # palette (signal visibility_changed), sans quoi une fermeture par la
        # croix de la palette laissait le bouton bloque sur « Masquer ».

    def _sync_palette_button(self, visible: bool) -> None:
        self.palette_btn.setText(
            "Masquer la palette" if visible else "Afficher la palette")

    # ============================================================== commun --
    def _build(self, text: str, final_enter: bool, target_hwnd: int,
               skip_comments: bool = False, skip_empty: bool = False):
        # Reference de calcul des scancodes = disposition de la FENETRE VISEE.
        # Se fier a la notre produisait des caracteres faux des que Windows
        # avait memorise une langue de saisie differente pour cette
        # application.
        hkl_target = w32.layout_of_window(target_hwnd) or w32.current_layout()
        self.converter.hkl_local = hkl_target

        built = build_tokens(
            text,
            char_delay_ms=self.char_delay_spin.value(),
            line_delay_ms=self.line_delay_spin.value(),
            skip_comments=skip_comments,
            skip_empty=skip_empty,
            final_enter=final_enter,
            convert=self.converter.active,
            convert_fn=self.converter.convert,
            hkl_local=hkl_target,
        )
        if built is None:
            self._set_status("Rien à envoyer.")
            return None

        if built.unmapped:
            self._log(
                f"{len(built.unmapped)} caractère(s) distinct(s) sans "
                "correspondance sur la disposition cible, envoyés tels quels.",
                "WARN")

        self.progress.setMaximum(len(built.tokens))
        self.progress.setValue(0)
        return built

    def _toggle_pause(self) -> None:
        if self.controller.state == "paused":
            self.controller.resume()
        else:
            self.controller.pause()

    def _deny(self, reason: str) -> None:
        # Un refus doit se voir : le journal seul passe inapercu quand
        # l'opérateur regarde sa console et non l'outil.
        self.status_label.setProperty("denied", True)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.setText(f"Envoi rapide refusé - {reason}")
        self._log(f"Envoi rapide refusé : {reason}", "WARN")
        QTimer.singleShot(4000, self._clear_denied)

    def _clear_denied(self) -> None:
        self.status_label.setProperty("denied", False)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(done)

    def _on_diagnostics(self, fallback: set, failed: set) -> None:
        """Aucun caractère ne doit disparaitre en silence : c'est ainsi qu'un
        mot de passe arrive tronque sans que personne ne comprenne pourquoi.
        Seul le NOMBRE est affiché : le texte envoyé peut être un secret."""
        if fallback:
            self._log(
                f"{len(fallback)} caractère(s) distinct(s) sans touche sur la "
                "disposition de la cible, injectés en Unicode (une console "
                "distante peut les ignorer).", "WARN")
        if failed:
            self._log(
                f"NON TAPÉS : {len(failed)} caractère(s) distinct(s) — le "
                "texte reçu est incomplet.", "ERR")
            self._set_status(
                f"Attention : {len(failed)} caractère(s) n'ont pas pu être tapés.")

    def _on_finished(self) -> None:
        self._log("Envoi terminé.")
        self._sync_buttons()

    def _on_failed(self, message: str) -> None:
        self._set_status(f"Erreur : {message}")
        self._log(message, "ERR")
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        running = self.controller.is_running
        paused = self.controller.state == "paused"

        self.start_btn.setEnabled(not running and bool(self._target_hwnd))
        self.pause_btn.setEnabled(running)
        self.pause_btn.setText("Reprendre" if paused else "Pause")
        self.stop_btn.setEnabled(running)
        self.capture_btn.setEnabled(not running)
        has_entry = self._selected_entry() is not None
        self.send_entry_btn.setEnabled(not running and has_entry)
        # Sans sélection, Modifier/Supprimer ne faisaient rien, en silence.
        self.edit_entry_btn.setEnabled(has_entry)
        self.del_entry_btn.setEnabled(has_entry)

    def _log(self, message: str, level: str = "INFO") -> None:
        from datetime import datetime
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_edit.appendPlainText(f"[{stamp}] {level:<4} {message}")

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
