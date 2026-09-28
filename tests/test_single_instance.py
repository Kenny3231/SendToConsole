"""Instance unique (QLocalServer) : une relance ramene l'instance lancee."""

import sys
import uuid

import pytest

pytest.importorskip("PySide6.QtNetwork")

from core.single_instance import SingleInstance, default_server_name  # noqa: E402


@pytest.fixture
def name():
    # Nom unique : ne jamais toucher une vraie instance lancee sur le poste.
    return f"SendToConsole-test-{uuid.uuid4().hex}"


def _wait_for(qapp, predicate, timeout_ms=2000):
    from PySide6.QtCore import QElapsedTimer
    timer = QElapsedTimer()
    timer.start()
    while not predicate() and timer.elapsed() < timeout_ms:
        qapp.processEvents()
    return predicate()


def test_no_instance_running_means_no_notification(qapp, name):
    assert SingleInstance(name).notify_existing(timeout_ms=200) is False


def test_second_launch_asks_first_to_show(qapp, name):
    """La relance est un AUTRE processus (dans le meme processus, le client
    bloquant empeche le serveur d'accepter la connexion)."""
    import subprocess
    from pathlib import Path
    first = SingleInstance(name)
    assert first.listen()
    shown = []
    first.activation_requested.connect(lambda: shown.append(1))
    src = str(Path(__file__).resolve().parents[1] / "src")
    code = (f"import sys; sys.path.insert(0, {src!r});"
            "from PySide6.QtCore import QCoreApplication;"
            "app = QCoreApplication([]);"
            "from core.single_instance import SingleInstance;"
            f"sys.exit(0 if SingleInstance({name!r}).notify_existing(2000) else 3)")
    proc = subprocess.Popen([sys.executable, "-c", code])
    try:
        # Delai large : sur un runner de CI froid, le second processus met
        # plusieurs secondes a importer PySide6 avant de se connecter.
        activated = _wait_for(qapp, lambda: shown == [1], timeout_ms=45000)
        assert activated, f"aucune activation (relance : code {proc.poll()})"
        assert proc.wait(timeout=30) == 0
    finally:
        first.close()
        if proc.poll() is None:
            proc.kill()


def test_simultaneous_launch_cannot_listen_twice(qapp, name):
    first = SingleInstance(name)
    assert first.listen()
    try:
        assert SingleInstance(name).listen() is False
    finally:
        first.close()


def test_server_name_is_per_user_and_hides_account_name():
    import getpass
    import os
    name = default_server_name()
    user = os.environ.get("USERNAME") or getpass.getuser()
    assert name == default_server_name()
    assert name.startswith("SendToConsole-")
    assert user.lower() not in name.lower()
