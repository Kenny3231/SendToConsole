"""Parite win32_api (reel) / backend factice (CONTRIBUTING.md, discipline de code).

Le backend factice est force par conftest.py (STC_SIMULATE=1) : on compare
donc ses fonctions publiques a celles de win32_api, importe directement
(Windows seulement, lecture seule, aucune frappe envoyee)."""

import inspect
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="win32_api n'existe que sous Windows")


def _real():
    from keyboard import win32_api
    return win32_api


def test_every_function_exported_by_backend_exists_in_both_branches():
    """Chaque nom que backend.py importe de win32_api existe aussi dans la
    branche factice, avec les memes parametres."""
    import ast
    from pathlib import Path
    from keyboard import backend

    source = Path(backend.__file__).read_text(encoding="utf-8")
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == "win32_api":
            imported += [alias.name for alias in node.names]
    assert imported, "import de win32_api introuvable dans backend.py"

    real = _real()
    for name in imported:
        fake_obj = getattr(backend, name, None)
        assert fake_obj is not None, f"{name} absent de la branche factice"
        real_obj = getattr(real, name)
        if inspect.isfunction(real_obj):
            assert list(inspect.signature(fake_obj).parameters) == \
                list(inspect.signature(real_obj).parameters), name


def test_real_integrity_read_only():
    real = _real()
    own = real._integrity_of_process(real.kernel32.GetCurrentProcess())
    assert own is not None and own >= 0x2000
    assert real.is_elevated_above_us(0) is False
    real.user32.GetShellWindow.argtypes = ()
    real.user32.GetShellWindow.restype = real.wintypes.HWND
    shell = real.user32.GetShellWindow()
    if shell:
        assert real.is_elevated_above_us(int(shell)) is False


def test_real_mutex_detects_second_holder():
    import uuid
    real = _real()
    name = f"SendToConsole-test-{uuid.uuid4().hex}"
    assert real.acquire_instance_mutex(name) is True
    held = real._instance_mutexes.pop(name)       # simule un autre detenteur
    try:
        assert real.acquire_instance_mutex(name) is False
    finally:
        real._instance_mutexes[name] = held
        real.release_instance_mutex(name)
