"""Boite de dialogue d'ajout/modification d'une entrée d'envoi rapide."""

from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QWidget,
)

from core.hotkeys import format_hotkey, hotkey_owner, unsafe_hotkey_reason
from keyboard import backend as w32
from core.models import QuickEntry
from ui.hotkey_edit import HotkeyEdit
from ui.theme import mark_secondary


class QuickEntryDialog(QDialog):
    def __init__(self, existing: list[QuickEntry],
                 entry: Optional[QuickEntry] = None, parent=None,
                 reserved: Optional[dict[str, tuple[int, int]]] = None) -> None:
        super().__init__(parent)
        self._existing = existing
        self._entry = entry
        # Raccourcis des actions de l'outil (Options) : interdits aux entrees.
        self._reserved = dict(reserved or {})
        self.setWindowTitle("Nouvelle entrée" if entry is None else "Modifier l'entrée")
        self.setMinimumWidth(440)

        self.label_edit = QLineEdit(entry.label if entry else "")
        self.label_edit.setPlaceholderText("ex. Mot de passe root iDRAC")

        self.text_edit = QLineEdit(entry.text if entry else "")
        self.text_edit.setPlaceholderText("Texte réellement tapé dans la console")

        self.secret_chk = QCheckBox("Masquer (mot de passe)")
        self.secret_chk.setChecked(entry.secret if entry else True)
        self.secret_chk.toggled.connect(self._apply_echo_mode)

        show_btn = QCheckBox("Afficher")
        show_btn.toggled.connect(self._toggle_visibility)
        self._show_chk = show_btn

        text_row = QWidget()
        text_layout = QHBoxLayout(text_row)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.addWidget(self.text_edit)
        text_layout.addWidget(show_btn)

        self.hotkey_edit = HotkeyEdit(
            entry.mods if entry else 0, entry.vk if entry else 0)
        clear_btn = mark_secondary(QPushButton("Effacer"))
        clear_btn.clicked.connect(self.hotkey_edit.clear_hotkey)

        hotkey_row = QWidget()
        hotkey_layout = QHBoxLayout(hotkey_row)
        hotkey_layout.setContentsMargins(0, 0, 0, 0)
        hotkey_layout.addWidget(self.hotkey_edit)
        hotkey_layout.addWidget(clear_btn)

        self.enter_chk = QCheckBox("Envoyer ENTRÉE après le texte")
        self.enter_chk.setChecked(entry.final_enter if entry else False)

        hint = QLabel(
            "Clique le champ du raccourci puis presse la combinaison "
            "(ex. Ctrl+Alt+F1). Elle reste active même quand le focus est "
            "dans la console distante.")
        hint.setWordWrap(True)
        hint.setObjectName("hintLabel")

        form = QFormLayout(self)
        form.addRow("Libellé :", self.label_edit)
        form.addRow("Contenu :", text_row)
        form.addRow("", self.secret_chk)
        form.addRow("Raccourci :", hotkey_row)
        form.addRow("", self.enter_chk)
        form.addRow(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Enregistrer")
        ok_btn.setIcon(QIcon())          # sans l'icone systeme, incoherente ici
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_btn.setText("Annuler")
        cancel_btn.setIcon(QIcon())
        mark_secondary(cancel_btn)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._apply_echo_mode(self.secret_chk.isChecked())

    def _apply_echo_mode(self, secret: bool) -> None:
        self._show_chk.setEnabled(secret)
        if not secret:
            self.text_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            self._toggle_visibility(self._show_chk.isChecked())

    def _toggle_visibility(self, visible: bool) -> None:
        if not self.secret_chk.isChecked():
            return
        self.text_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)

    def _on_accept(self) -> None:
        if not self.label_edit.text().strip():
            QMessageBox.warning(self, "Libellé manquant",
                                "Donne un libellé à cette entrée.")
            return

        mods, vk = self.hotkey_edit.mods, self.hotkey_edit.vk
        unsafe = unsafe_hotkey_reason(mods, vk, w32.current_layout())
        if unsafe is not None:
            QMessageBox.warning(self, "Raccourci déconseillé",
                                f"{unsafe}\n\nChoisis une autre combinaison.")
            return
        owner = hotkey_owner(self._existing, self._reserved, mods, vk,
                             exclude_entry=self._entry)
        if owner is not None:
            QMessageBox.warning(
                self, "Raccourci déjà utilisé",
                f"{format_hotkey(mods, vk)} est déjà attribué à "
                f"{owner}.\n\nChoisis une autre combinaison.")
            return

        self.accept()

    def result_entry(self) -> QuickEntry:
        return QuickEntry(
            label=self.label_edit.text().strip(),
            text=self.text_edit.text(),
            mods=self.hotkey_edit.mods,
            vk=self.hotkey_edit.vk,
            final_enter=self.enter_chk.isChecked(),
            secret=self.secret_chk.isChecked(),
        )
