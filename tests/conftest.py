"""Configuration commune des tests.

SECURITE : les tests ne doivent JAMAIS injecter de vraies frappes dans la
session de l'operateur. On force donc le backend clavier factice
(STC_SIMULATE=1, voir src/keyboard/backend.py) et Qt en mode offscreen AVANT
tout import du projet.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["STC_SIMULATE"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _simulation_only():
    from keyboard import backend, engine
    if backend.IS_REAL:
        pytest.exit("Backend clavier REEL actif : abandon pour ne pas "
                    "injecter de frappes reelles.", returncode=2)
    backend.sent_scan.clear()
    backend.sent_unicode.clear()
    backend.held_modifiers.clear()
    engine.reset_diagnostics()
    yield


@pytest.fixture
def fast_kdf(monkeypatch):
    """PBKDF2 a bas cout pour ne pas rendre la suite lente (les tests qui
    verifient le cout reel n'utilisent pas cette fixture)."""
    from security import local_encrypted
    monkeypatch.setattr(local_encrypted, "PBKDF2_ITERATIONS", 1_000)
    # Le plancher de validite de l'entete doit rester sous ce cout de test.
    monkeypatch.setattr(local_encrypted, "MIN_PBKDF2_ITERATIONS", 100)
    monkeypatch.setattr(local_encrypted, "LEGACY_PBKDF2_ITERATIONS", 1_000)
    return 1_000


@pytest.fixture(scope="session")
def qcore_app():
    """Boucle Qt pour QTimer/signaux. Cree une QApplication quand les widgets
    sont disponibles : une QCoreApplication deja presente empecherait ensuite
    toute QApplication dans la meme session (crash de tests/test_ui_smoke.py).
    Repli sur QCoreApplication si les libs graphiques manquent."""
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        yield QCoreApplication.instance() or QCoreApplication([])
        return
    yield QApplication.instance() or QApplication([])


@pytest.fixture(scope="session")
def qapp():
    """QApplication complete (widgets), ignoree si les libs graphiques
    manquent (VM/CI sans libEGL)."""
    pytest.importorskip("PySide6")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        pytest.skip(f"PySide6.QtWidgets indisponible : {exc}")
    app = QApplication.instance()
    if app is not None and not isinstance(app, QApplication):
        pytest.skip("Une QCoreApplication existe deja dans cette session.")
    yield app or QApplication([])


@pytest.fixture(autouse=True)
def _clipboard_is_never_written(monkeypatch):
    """Garde-fou d'execution : l'outil ne doit JAMAIS ecrire dans le
    presse-papiers (lecture seule, action « Coller »). Toute ecriture pendant
    un test fait echouer ce test."""
    try:
        from PySide6.QtGui import QClipboard
    except ImportError:
        return

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("ecriture dans le presse-papiers interdite")
    for name in ("setText", "setMimeData", "setImage", "setPixmap", "clear"):
        monkeypatch.setattr(QClipboard, name, _forbidden)
