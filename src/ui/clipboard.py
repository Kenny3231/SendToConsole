"""Lecture du presse-papiers pour l'action « Coller le presse-papiers ».

SEUL module autorise a acceder au presse-papiers (tests/test_hygiene.py), et
en LECTURE uniquement : l'outil n'y ecrit jamais, ni ne le vide. Le texte lu
est valide (core/paste.py) puis tape par simulation clavier (jamais recolle
par Ctrl+V). Il n'est jamais journalise ; la sequence de frappe est liberee
en fin d'envoi (sans garantie d'effacement de la memoire Python).
"""

from __future__ import annotations

from PySide6.QtGui import QClipboard, QGuiApplication


def read_clipboard_text() -> str:
    """Texte du presse-papiers, ou "" s'il est vide, non textuel (image,
    fichiers) ou momentanement verrouille par une autre application."""
    clipboard = QGuiApplication.clipboard()
    if clipboard is None:
        return ""
    return clipboard.text(QClipboard.Mode.Clipboard) or ""
