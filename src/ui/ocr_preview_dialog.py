"""Apercu editable du texte reconnu par OCR, avant sa frappe.

L'OCR confond O/0, l/1/I, S/5... : sur un mot de passe, une erreur ferait
echouer la connexion. Le texte n'est donc jamais tape sans validation.
Compromis assume : le texte est affiche en clair le temps de la verification.
"""

from __future__ import annotations

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout,
)


class OcrPreviewDialog(QDialog):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Texte reconnu : à vérifier")
        self.resize(560, 320)
        hint = QLabel(
            "Vérifie le texte avant de le taper (O/0, l/1/I et S/5 sont "
            "souvent confondus) ; corrige-le ici si besoin. Chaque saut de "
            "ligne sera validé par ENTRÉE.")
        hint.setWordWrap(True)
        self.edit = QPlainTextEdit(text)
        self.edit.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Taper")
        buttons.button(QDialogButtonBox.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(self.edit)
        layout.addWidget(buttons)

    def text(self) -> str:
        return self.edit.toPlainText()
