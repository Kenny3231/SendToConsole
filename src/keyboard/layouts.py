"""Dispositions clavier connues (KLID Windows), portees de $script:Layouts
dans le script PowerShell d'origine (historique git 68f1fc4)."""

from __future__ import annotations

KNOWN_LAYOUTS: list[tuple[str, str]] = [
    ("Anglais (US) - QWERTY", "00000409"),
    ("Anglais (RU) - QWERTY", "00000809"),
    ("Francais (FR) - AZERTY", "0000040C"),
    ("Francais (BE) - AZERTY", "0000080C"),
    ("Francais (CH)", "0000100C"),
    ("Allemand (DE) - QWERTZ", "00000407"),
    ("Suisse allemand - QWERTZ", "00000807"),
    ("Espagnol (ES)", "0000040A"),
    ("Italien (IT)", "00000410"),
    ("Portugais (PT)", "00000816"),
    ("Neerlandais (NL)", "00000413"),
    ("Canadien francais", "00000C0C"),
    ("US International", "00020409"),
]
