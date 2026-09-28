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


CLIPBOARD_MODULE = SRC / "ui" / "clipboard.py"


def test_clipboard_read_only_in_dedicated_module():
    """Seul ui/clipboard.py (action « Coller le presse-papiers ») accede au
    presse-papiers ; partout ailleurs, aucun acces."""
    others = [f for f in FILES if f != CLIPBOARD_MODULE]
    assert _hits(r"(?i)qclipboard|clipboard\(\)|pyperclip|OpenClipboard|"
                 r"GetClipboardData|SetClipboardData", others) == []


def test_clipboard_never_written():
    """L'outil n'ecrit jamais dans le presse-papiers (ni ne le vide) : aucun
    secret n'y est depose, meme dans le module de lecture."""
    assert _hits(r"(?i)SetClipboardData|EmptyClipboard|pyperclip|"
                 r"\.set(Text|MimeData|Image|Pixmap)\(|\.clear\(\s*(QClipboard|mode)",
                 [CLIPBOARD_MODULE]) == []
    text = CLIPBOARD_MODULE.read_text(encoding="utf-8")
    assert "clipboard.text(" in text and "setText" not in text


def test_no_print_in_shipped_code():
    """Jamais de print de debug (risque de fuite de mot de passe)."""
    assert _hits(r"\bprint\(") == []


def test_no_hardcoded_personal_paths():
    assert _hits(r"(?i)C:\\\\?Users\\\\?[A-Za-z]|D:\\\\?Projets") == []


def test_no_dead_profile_dialog():
    assert not (SRC / "ui" / "profile_dialog.py").exists()


def test_clipboard_attribute_only_in_dedicated_module():
    """Analyse AST (resiste aux alias et aux appels sur plusieurs lignes) :
    l'attribut `clipboard` n'est reference que dans ui/clipboard.py, et les
    modules d'acces direct au presse-papiers ne sont jamais importes."""
    import ast
    forbidden_modules = {"win32clipboard", "pyperclip", "tkinter", "clipboard"}
    offenders = []
    for f in FILES:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr == "clipboard"
                    and f != CLIPBOARD_MODULE):
                offenders.append(f"{f.relative_to(SRC)}:{node.lineno} .clipboard")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""])
                for name in names:
                    if name.split(".")[0] in forbidden_modules:
                        offenders.append(f"{f.relative_to(SRC)}:{node.lineno} import {name}")
    assert offenders == []
