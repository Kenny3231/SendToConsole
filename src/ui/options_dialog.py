"""Boite de dialogue Options : thème et gestion du stockage chiffré."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QGroupBox,
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from core.hotkeys import ACTION_LABELS, APP_ACTIONS
from keyboard.engine import METHOD_SCANCODE, METHOD_UNICODE
from ui.hotkey_edit import HotkeyEdit
from ui.theme import DARK, LIGHT, mark_secondary


class OptionsDialog(QDialog):
    """Les reglages s'appliquent en direct (l'utilisateur voit ce qu'il
    choisit) ; Fermer ne les annule pas."""

    theme_previewed = Signal(str)
    method_changed = Signal(str)
    altgr_with_ctrl_changed = Signal(bool)
    reset_requested = Signal()
    open_folder_requested = Signal()
    open_store_requested = Signal()      # utiliser un autre coffre (ou en créer un)
    save_as_requested = Signal()         # copier le coffre vers un NOUVEAU fichier
    enable_storage_requested = Signal()
    app_hotkey_changed = Signal(str, int, int)   # (ACTION_*, mods, vk)

    def __init__(self, current_theme: str, store_path: str,
                 current_method: str = METHOD_SCANCODE,
                 altgr_with_ctrl: bool = True, volatile: bool = False,
                 real_path: str = "",
                 app_hotkeys: dict[str, tuple[int, int]] | None = None,
                 refused_actions: set[str] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.setMinimumWidth(500)

        # Les groupes vivent dans une zone defilante : sur un petit ecran
        # (portable 1080p en 150 %), la boite ne tasse plus ses textes (ils se
        # chevauchaient) et « Fermer » reste toujours visible en bas.
        self._content = QWidget()
        self._content.setObjectName("optionsContent")
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 4, 0)

        # --- Apparence ---
        appearance = QGroupBox("Apparence")
        appearance_layout = QHBoxLayout(appearance)
        appearance_layout.addWidget(QLabel("Thème :"))

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Clair", LIGHT)
        self.theme_combo.addItem("Sombre", DARK)
        index = self.theme_combo.findData(current_theme)
        self.theme_combo.setCurrentIndex(max(0, index))
        self.theme_combo.currentIndexChanged.connect(
            lambda _i: self.theme_previewed.emit(self.theme_combo.currentData()))
        appearance_layout.addWidget(self.theme_combo, stretch=1)
        layout.addWidget(appearance)

        # --- Methode d'envoi ---
        sending = QGroupBox("Méthode d'envoi")
        sending_layout = QVBoxLayout(sending)

        method_row = QHBoxLayout()
        method_row.addWidget(QLabel("Frappe :"))
        self.method_combo = QComboBox()
        self.method_combo.addItem("Scancodes (consoles distantes)", METHOD_SCANCODE)
        self.method_combo.addItem("Unicode (applications Windows)", METHOD_UNICODE)
        index = self.method_combo.findData(current_method)
        self.method_combo.setCurrentIndex(max(0, index))
        self.method_combo.currentIndexChanged.connect(
            lambda _i: self.method_changed.emit(self.method_combo.currentData()))
        method_row.addWidget(self.method_combo, stretch=1)
        sending_layout.addLayout(method_row)

        method_help = QLabel(
            "Les scancodes imitent un vrai clavier : c'est le seul mode que "
            "comprennent les consoles iDRAC/iLO/IPMI et les KVM. L'Unicode "
            "ignore dispositions et modificateurs, donc plus fiable dans les "
            "applications Windows ordinaires, mais souvent ignoré par une "
            "console distante.")
        method_help.setWordWrap(True)
        method_help.setObjectName("hintLabel")
        sending_layout.addWidget(method_help)

        self.altgr_ctrl_chk = QCheckBox(
            "AltGr = Ctrl gauche + Alt droite (fidèle au matériel)")
        self.altgr_ctrl_chk.setChecked(altgr_with_ctrl)
        self.altgr_ctrl_chk.toggled.connect(self.altgr_with_ctrl_changed.emit)
        sending_layout.addWidget(self.altgr_ctrl_chk)

        altgr_help = QLabel(
            "À décocher si une cible réagit mal au Ctrl : AltGr sera alors "
            "envoyé comme Alt droite seule.")
        altgr_help.setWordWrap(True)
        altgr_help.setObjectName("hintLabel")
        sending_layout.addWidget(altgr_help)
        layout.addWidget(sending)

        # --- Raccourcis globaux des actions de l'outil ---
        shortcuts = QGroupBox("Raccourcis globaux")
        shortcuts_layout = QGridLayout(shortcuts)
        app_hotkeys = app_hotkeys or {}
        self.hotkey_edits: dict[str, HotkeyEdit] = {}
        for row, action in enumerate(APP_ACTIONS):
            mods, vk = app_hotkeys.get(action, (0, 0))
            edit = HotkeyEdit(mods, vk)
            edit.hotkey_changed.connect(
                lambda m, v, a=action: self.app_hotkey_changed.emit(a, m, v))
            clear_btn = mark_secondary(QPushButton("Effacer"))
            clear_btn.clicked.connect(edit.clear_hotkey)
            shortcuts_layout.addWidget(QLabel(f"{ACTION_LABELS[action]} :"), row, 0)
            shortcuts_layout.addWidget(edit, row, 1)
            shortcuts_layout.addWidget(clear_btn, row, 2)
            self.hotkey_edits[action] = edit
        shortcuts_layout.setColumnStretch(1, 1)

        help_row = len(APP_ACTIONS)
        shortcuts_help = QLabel(
            "Actifs même dans la console distante. Clique dans un champ puis "
            "presse la combinaison (avec au moins Ctrl, Alt, Maj ou Win).\n"
            "Coller le presse-papiers : copie le texte (Ctrl+C), clique dans "
            "la fenêtre cible puis presse ce raccourci : le texte y est tapé "
            "au clavier, même là où le collage est bloqué. Plusieurs lignes : "
            "presse deux fois (chaque ligne est validée par ENTRÉE). Pour un "
            "mot de passe récurrent, préfère une entrée d'envoi rapide : ce "
            "que tu copies reste dans l'historique Windows (Win+V).")
        shortcuts_help.setWordWrap(True)
        shortcuts_help.setObjectName("hintLabel")
        shortcuts_layout.addWidget(shortcuts_help, help_row, 0, 1, 3)

        # Combinaison enregistree dans les reglages mais refusee par Windows
        # (prise par une autre application) : elle n'est PAS active.
        self.refused_label = QLabel()
        self.refused_label.setWordWrap(True)
        self.refused_label.setObjectName("hintLabel")
        self.refused_label.setProperty("warn", True)
        shortcuts_layout.addWidget(self.refused_label, help_row + 1, 0, 1, 3)
        self.set_refused_actions(refused_actions or set())
        layout.addWidget(shortcuts)

        # --- Stockage ---
        storage = QGroupBox("Coffre des entrées d'envoi rapide")
        storage_layout = QVBoxLayout(storage)

        if volatile:
            volatile_label = QLabel(
                "Mode « sans enregistrement » : les entrées vivent en mémoire "
                "et disparaissent à la fermeture. Rien n'est écrit sur disque.")
            volatile_label.setWordWrap(True)
            volatile_label.setObjectName("hintLabel")
            volatile_label.setProperty("warn", True)
            storage_layout.addWidget(volatile_label)

            self.enable_storage_btn = QPushButton(
                "Activer l'enregistrement chiffré (nouveau coffre ou coffre existant)")
            self.enable_storage_btn.clicked.connect(
                self.enable_storage_requested.emit)
            storage_layout.addWidget(self.enable_storage_btn)
            layout.addWidget(storage)
        else:
            self.path_label = QLabel()
            self.path_label.setObjectName("hintLabel")
            self.path_label.setWordWrap(True)
            self.path_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            storage_layout.addWidget(self.path_label)
            self._refresh_path_label(store_path, real_path)

            path_row = QHBoxLayout()
            self.open_folder_btn = mark_secondary(QPushButton("Ouvrir le dossier"))
            self.open_folder_btn.clicked.connect(self.open_folder_requested.emit)
            self.open_store_btn = mark_secondary(
                QPushButton("Ouvrir un autre coffre…"))
            self.open_store_btn.setToolTip(
                "Utiliser un coffre existant ou en créer un nouveau (vide). "
                "Aucun coffre existant n'est modifié.")
            self.open_store_btn.clicked.connect(self.open_store_requested.emit)
            self.save_as_btn = mark_secondary(QPushButton("Enregistrer sous…"))
            self.save_as_btn.setToolTip(
                "Copie le coffre dans un nouveau fichier et l'utilise "
                "désormais ; l'ancien fichier reste en place. Un fichier "
                "existant n'est jamais écrasé.")
            self.save_as_btn.clicked.connect(self.save_as_requested.emit)
            path_row.addWidget(self.open_folder_btn)
            path_row.addWidget(self.open_store_btn)
            path_row.addWidget(self.save_as_btn)
            path_row.addStretch(1)
            storage_layout.addLayout(path_row)

            warning = QLabel(
                "Le master password n'est stocké nulle part : s'il est oublié, "
                "les entrées sont définitivement irrécupérables. Réinitialiser "
                "supprime le fichier et repart d'un stockage vide.")
            warning.setWordWrap(True)
            warning.setObjectName("hintLabel")
            storage_layout.addWidget(warning)

            self.reset_btn = QPushButton(
                "Réinitialiser le master password (supprime toutes les entrées)")
            self.reset_btn.setObjectName("stopButton")
            self.reset_btn.clicked.connect(self.reset_requested.emit)
            storage_layout.addWidget(self.reset_btn)
            layout.addWidget(storage)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_btn.setText("Fermer")
        from PySide6.QtGui import QIcon
        close_btn.setIcon(QIcon())
        buttons.rejected.connect(self.accept)
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._content)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        outer = QVBoxLayout(self)
        outer.addWidget(self._scroll, stretch=1)
        outer.addWidget(buttons)
        self._fit_height()

    def set_refused_actions(self, refused: set[str]) -> None:
        names = [ACTION_LABELS[a] for a in APP_ACTIONS if a in refused]
        if names:
            self.refused_label.setText(
                "⚠ Inactif, déjà pris par une autre application : "
                + ", ".join(f"« {n} »" for n in names)
                + ". Choisis une autre combinaison.")
        else:
            self.refused_label.setText("")
        self.refused_label.setVisible(bool(names))

    def set_app_hotkey(self, action: str, mods: int, vk: int) -> None:
        """Affiche une combinaison SANS reemettre app_hotkey_changed (retour
        a l'ancienne valeur apres un refus)."""
        edit = self.hotkey_edits[action]
        edit.blockSignals(True)
        try:
            edit.set_hotkey(mods, vk)
        finally:
            edit.blockSignals(False)

    def _fit_height(self) -> None:
        """Hauteur réellement nécessaire aux textes repliés.

        exec() dimensionne par adjustSize(), plafonné aux 2/3 de l'écran :
        sur un portable 1080p en 150 % (720 px logiques), la boîte était
        trop basse et ses paragraphes se chevauchaient."""
        self.ensurePolished()
        self._content.ensurePolished()
        width = max(self.width() if self.isVisible() else 0,
                    self.minimumWidth(), self._content.sizeHint().width() + 40)
        content_height = self._content.heightForWidth(width - 40)
        if content_height <= 0:
            content_height = self._content.sizeHint().height()
        margins = self.layout().contentsMargins()
        height = (content_height + self.layout().spacing()
                  + self.layout().itemAt(1).widget().sizeHint().height()
                  + margins.top() + margins.bottom())
        # Jamais plus haut que l'écran : au-delà, la zone défile (le contenu
        # garde sa taille, « Fermer » reste atteignable).
        screen = self.screen()
        if screen is not None:
            height = min(height, screen.availableGeometry().height() - 60)
        self.resize(width, height)

    def _refresh_path_label(self, store_path: str, real_path: str) -> None:
        from pathlib import Path

        exists = Path(real_path or store_path).exists()
        etat = "présent" if exists else "pas encore créé"
        text = f"Coffre utilisé : {store_path}\n(actuellement {etat})"

        if real_path and real_path != store_path:
            # Python du Microsoft Store : Windows redirige les ecritures dans
            # AppData vers un dossier virtualise propre au paquet. Sans cette
            # precision, le chemin affiche ne mene nulle part dans
            # l'Explorateur - exactement ce qui a derouté l'operateur.
            text += ("\n\nWindows redirige ce chemin (Python du Microsoft "
                     f"Store). Emplacement réel sur le disque :\n{real_path}")

        self.path_label.setText(text)
        if self.isVisible():
            self._fit_height()

    @property
    def selected_theme(self) -> str:
        return self.theme_combo.currentData()
