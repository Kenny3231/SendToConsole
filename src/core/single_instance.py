"""
single_instance.py - Une seule instance de l'outil par utilisateur.

Deux instances se disputeraient les raccourcis globaux (le second
RegisterHotKey est refuse) et les fichiers temporaires de l'interface. Au
lancement, on tente de joindre une instance deja lancee : si elle repond, on
lui demande de s'afficher et on s'arrete ; sinon on devient l'instance
d'ecoute.

Canal : QLocalServer (pipe nomme sous Windows), restreint a l'utilisateur
courant (UserAccessOption). Le seul message reconnu est « show » : aucune
donnee ne transite, et un tiers ne peut au pire que faire apparaitre la
fenetre.
"""

from __future__ import annotations

import getpass
import hashlib
import os
import sys
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SHOW_MESSAGE = b"show\n"


def default_server_name() -> str:
    """Nom propre a l'utilisateur (deux comptes sur le meme poste, ex.
    serveur RDS, ont chacun leur instance). Haché : pas de caractere
    interdit dans un nom de pipe, pas de nom de compte expose."""
    try:
        user = os.environ.get("USERNAME") or getpass.getuser()
    except Exception:      # getuser() peut lever sans variable d'environnement
        user = "inconnu"
    digest = hashlib.sha256(user.lower().encode("utf-8")).hexdigest()[:16]
    return f"SendToConsole-{digest}"


class SingleInstance(QObject):
    """activation_requested : une autre instance vient d'etre lancee."""

    activation_requested = Signal()

    def __init__(self, name: Optional[str] = None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.name = name or default_server_name()
        self._server: Optional[QLocalServer] = None
        self._owns_mutex = False

    def notify_existing(self, timeout_ms: int = 500) -> bool:
        """Demande a l'instance deja lancee de s'afficher. Rend True si elle
        a repondu (l'appelant doit alors s'arreter)."""
        # Tout est pret AVANT la connexion : l'instance lancee n'attend le
        # message que brievement, un import entre connexion et ecriture
        # (disque froid) le lui faisait manquer.
        from keyboard import backend as w32
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if not socket.waitForConnected(timeout_ms):
            return False
        # Autorise l'instance existante a passer au premier plan (Windows
        # refuse sinon qu'un processus d'arriere-plan s'active lui-meme).
        w32.allow_foreground_switch()
        socket.write(SHOW_MESSAGE)
        socket.flush()
        socket.waitForBytesWritten(timeout_ms)
        socket.disconnectFromServer()
        return True

    def listen(self) -> bool:
        """Devient l'instance d'ecoute. False si une autre instance ecoute
        deja (lancement simultane) : l'appelant doit alors s'arreter."""
        from keyboard import backend as w32
        # Sous Windows, deux QLocalServer peuvent ecouter sur le meme nom :
        # seul le mutex nomme tranche un lancement simultane.
        if not w32.acquire_instance_mutex(self.name):
            return False
        self._owns_mutex = True
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self.name):
            if sys.platform == "win32":
                return False
            # Unix : socket orpheline d'une instance plantee.
            QLocalServer.removeServer(self.name)
            if not server.listen(self.name):
                return False
        server.newConnection.connect(self._on_new_connection)
        self._server = server
        return True

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._owns_mutex:
            from keyboard import backend as w32
            w32.release_instance_mutex(self.name)
            self._owns_mutex = False

    def _on_new_connection(self) -> None:
        """Lecture immediate, sans rappel sur la connexion : un lambda
        conserve sur un QLocalSocket detruit par deleteLater faisait planter
        la boucle Qt plus tard (violation d'acces). L'attente est plafonnee :
        la relance ecrit son message aussitot connectee."""
        while self._server is not None and self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            if conn is None:
                break
            if not conn.bytesAvailable():
                conn.waitForReadyRead(200)
            data = bytes(conn.readAll())
            conn.disconnectFromServer()
            conn.deleteLater()
            if SHOW_MESSAGE.strip() in data:
                self.activation_requested.emit()
