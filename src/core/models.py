"""Modeles de donnees.

Une QuickEntry = une entree d'envoi rapide : un libelle, le texte a taper
(souvent un mot de passe), un raccourci clavier global optionnel, et le
choix d'envoyer ENTREE apres. C'est la structure de l'onglet 5 du script
PowerShell d'origine - pas une notion de "profil".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QuickEntry:
    label: str
    # Hors du repr : un `{entry!r}` de debogage n'afficherait jamais le secret.
    text: str = field(default="", repr=False)
    # Raccourci global : modificateurs Win32 (MOD_ALT=1, MOD_CONTROL=2,
    # MOD_SHIFT=4, MOD_WIN=8) et code de touche virtuelle. 0/0 = aucun.
    mods: int = 0
    vk: int = 0
    final_enter: bool = False
    # Masque le texte dans l'interface (mot de passe vs texte ordinaire).
    secret: bool = True
    # Renseigne a l'execution : le raccourci a-t-il ete accepte par Windows.
    registered: bool = field(default=False, compare=False)
    hotkey_id: int = field(default=0, compare=False)

    @property
    def has_hotkey(self) -> bool:
        return self.mods != 0 and self.vk != 0

    def matches(self, needle: str) -> bool:
        needle = needle.strip().lower()
        if not needle:
            return True
        return needle in self.label.lower()

    # -- serialisation (stockage chiffre) ---------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "text": self.text,
            "mods": self.mods,
            "vk": self.vk,
            "final_enter": self.final_enter,
            "secret": self.secret,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "QuickEntry":
        return QuickEntry(
            label=data.get("label", "(sans nom)"),
            text=data.get("text", ""),
            mods=int(data.get("mods", 0)),
            vk=int(data.get("vk", 0)),
            final_enter=bool(data.get("final_enter", False)),
            secret=bool(data.get("secret", True)),
        )
