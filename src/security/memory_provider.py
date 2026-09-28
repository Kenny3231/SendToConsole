"""
memory_provider.py - Entrees d'envoi rapide en memoire uniquement.

C'est le comportement du script PowerShell d'origine (historique git 68f1fc4) : les entrees vivent le temps
de la session, rien n'est ecrit sur disque, tout disparait a la fermeture.
Permet d'utiliser l'outil sans saisir de master password - pratique sur un
poste qui n'est pas le sien, ou pour un usage ponctuel.
"""

from __future__ import annotations

from core.models import QuickEntry
from security.provider import SecretProvider


class MemoryProvider(SecretProvider):
    name = "Memoire uniquement (rien n'est enregistre)"
    read_only = False

    #: Repere pour l'interface : elle previent l'operateur que tout sera perdu.
    volatile = True

    def __init__(self) -> None:
        self._entries: list[QuickEntry] = []

    def unlock(self, secret: str) -> None:
        return          # rien a deverrouiller

    def is_unlocked(self) -> bool:
        return True     # toujours disponible

    def load(self) -> list[QuickEntry]:
        return list(self._entries)

    def save(self, entries: list[QuickEntry]) -> None:
        # "Enregistrer" se limite ici a garder la liste en memoire : aucune
        # ecriture disque, c'est tout l'interet du mode.
        self._entries = list(entries)

    def lock(self) -> None:
        self._entries = []

    def reset(self) -> None:
        self._entries = []
