"""Garde-fous statiques sur les regles NON NEGOCIABLES de SECURITY.md."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
FILES = [p for p in SRC.rglob("*.py")]


def _hits(pattern, files=FILES):
    rx = re.compile(pattern)
    out = []
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if rx.search(line) and not line.lstrip().startswith("#"):
                out.append(f"{f.relative_to(SRC)}:{n}: {line.strip()}")
    return out


def test_no_clipboard_usage():
    """Aucune donnee sensible ne transite par le presse-papiers."""
    assert _hits(r"(?i)qclipboard|clipboard\(\)|pyperclip|OpenClipboard|"
                 r"SetClipboardData") == []


def test_no_print_in_shipped_code():
    """Jamais de print de debug (risque de fuite de mot de passe)."""
    assert _hits(r"\bprint\(") == []


def test_no_hardcoded_personal_paths():
    assert _hits(r"(?i)C:\\\\?Users\\\\?[A-Za-z]|D:\\\\?Projets") == []


def test_no_dead_profile_dialog():
    assert not (SRC / "ui" / "profile_dialog.py").exists()
