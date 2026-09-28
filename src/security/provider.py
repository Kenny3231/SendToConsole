"""
provider.py - Interface abstraite des sources d'entrees d'envoi rapide.

C'est le point d'extension prevu des la V1 (voir CONTRIBUTING.md) : la V1 fournit
LocalEncryptedProvider (fichier local chiffre), et les futurs
KeePassProvider / VaultwardenProvider viendront se brancher ici sans
toucher au reste de l'application.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.models import QuickEntry


class SecretProviderError(Exception):
    """Erreur d'accès au stockage (mauvais mot de passe, fichier corrompu...)."""


class SecretProvider(ABC):
    """Source d'entrees d'envoi rapide."""

    #: Nom affiche a l'utilisateur.
    name: str = "source"

    #: Un provider en lecture seule (KeePass, Vaultwarden) refusera save().
    read_only: bool = False

    @abstractmethod
    def unlock(self, secret: str) -> None:
        """Ouvre la source. Leve SecretProviderError si le secret est faux."""

    @abstractmethod
    def is_unlocked(self) -> bool:
        ...

    @abstractmethod
    def load(self) -> list[QuickEntry]:
        ...

    @abstractmethod
    def save(self, entries: list[QuickEntry]) -> None:
        ...

    @abstractmethod
    def lock(self) -> None:
        """Referme la source et oublie la cle en memoire."""

    @abstractmethod
    def reset(self) -> None:
        """Efface definitivement le stockage (et donc toutes les entrees).

        Sert quand le master password est oublie : il n'est stocke nulle
        part, il n'y a donc rien a "retrouver" - la seule issue est de
        repartir d'un stockage vide.
        """
