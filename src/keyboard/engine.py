"""
engine.py - Construction et envoi des jetons de frappe.

Porte de Build-Tokens / Send-Token (script PowerShell d'origine, historique git 68f1fc4). Cette version propose un
envoi bloquant (send_all_blocking), reserve au test CLI. L'UI PySide6 utilise
InjectController (src/core/inject_controller.py), pilote par QTimer, sur le
meme principe que le Timer WinForms d'origine ($timer.Add_Tick) : l'interface
reste reactive (Pause/Stop) pendant que le focus est sur la console distante.

Ce module passe par backend.py (pas win32_api.py directement) pour rester
importable et testable hors Windows - voir backend.py.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from . import backend as w32


@dataclass
class Token:
    kind: str          # 'char' | 'enter' | 'tab'
    ch: Optional[str]
    alt_gr: bool
    delay_ms: int
    line: int


@dataclass
class BuildResult:
    tokens: list[Token]
    line_count: int
    unmapped: set[str]
    alt_gr_chars: set[str]


def build_tokens(
    text: str,
    char_delay_ms: int,
    line_delay_ms: int,
    skip_comments: bool = False,
    skip_empty: bool = False,
    final_enter: bool = True,
    convert: bool = False,
    convert_fn: Optional[Callable[[str], Optional[str]]] = None,
    hkl_local: int = 0,
) -> Optional[BuildResult]:
    lines = text.splitlines()
    if skip_comments:
        lines = [l for l in lines if not l.strip().startswith(("#", ";"))]
    if skip_empty:
        lines = [l for l in lines if l.strip() != ""]
    if not lines:
        return None

    tokens: list[Token] = []
    unmapped: set[str] = set()
    alt_gr_chars: set[str] = set()

    for i, line in enumerate(lines):
        for ch in line:
            if ch == "\t":
                tokens.append(Token("tab", None, False, char_delay_ms, i + 1))
                continue

            send = ch
            if convert and convert_fn is not None:
                conv = convert_fn(ch)
                if not conv:
                    unmapped.add(ch)
                else:
                    send = conv

            c = send[0]
            alt_gr = w32.needs_altgr(c, hkl_local) if hkl_local else False
            # Un caractere AltGr demande plus de temps a l'application pour
            # revenir a un etat clavier neutre : enchainer trop vite fait
            # perdre les caracteres AltGr suivants.
            delay = max(char_delay_ms, w32.ALT_GR_MIN_DELAY_MS) if alt_gr \
                else char_delay_ms
            if alt_gr:
                alt_gr_chars.add(c)

            tokens.append(Token("char", c, alt_gr, delay, i + 1))

        is_last = i == len(lines) - 1
        if not (is_last and not final_enter):
            tokens.append(Token("enter", None, False, line_delay_ms, i + 1))

    return BuildResult(tokens, len(lines), unmapped, alt_gr_chars)


#: Caracteres ayant du emprunter le repli Unicode faute de touche
#: correspondante, et ceux qui n'ont pas pu etre tapes du tout. L'appelant
#: les vide avant un envoi et les relit apres, pour en informer l'operateur.
fallback_chars: set[str] = set()
failed_chars: set[str] = set()


def reset_diagnostics() -> None:
    fallback_chars.clear()
    failed_chars.clear()


#: Methodes d'envoi. Le scancode est indispensable pour les consoles
#: distantes (KVM, iDRAC) qui ne transmettent que les touches physiques ;
#: l'Unicode est bien plus fiable dans les applications Windows ordinaires,
#: ou il ignore completement dispositions et modificateurs.
METHOD_SCANCODE = "scancode"
METHOD_UNICODE = "unicode"


def send_token(token: Token, hkl_target: int, allow_unicode: bool = True,
               method: str = METHOD_SCANCODE) -> bool:
    """Emet un jeton. 'hkl_target' est la disposition de la FENETRE VISEE
    (layout_of_window), pas celle de notre propre thread : c'est la fenetre
    receptrice qui interprete les scancodes."""
    if token.kind == "enter":
        return w32.send_scan(w32.SC_ENTER, False)
    if token.kind == "tab":
        return w32.send_scan(w32.SC_TAB, False)

    assert token.ch is not None

    if method == METHOD_UNICODE:
        if w32.send_char_unicode(token.ch):
            return True
        failed_chars.add(token.ch)
        return False
    # Python n'a pas d'equivalent direct a SendKeys (WScript.Shell) : tout
    # caractere passe par SendInput/scancode, pas seulement l'AltGr comme
    # dans le mode "auto" du script PowerShell d'origine. C'est l'equivalent du mode
    # "tout en scancode" - plus fidele, seule contrainte heritee : refuse
    # vers un processus d'integrite superieure (executer en admin si la
    # console cible tourne dans un navigateur admin).
    if w32.send_char_scan(token.ch, hkl_target):
        return True

    # Aucune touche de la disposition ne produit ce caractere. Plutot que de
    # l'abandonner en silence (comportement precedent, et source de textes
    # tronques sans explication), on tente l'injection Unicode directe.
    if allow_unicode and w32.send_char_unicode(token.ch):
        fallback_chars.add(token.ch)
        return True

    failed_chars.add(token.ch)
    return False


def send_all_blocking(
    tokens: list[Token],
    hkl_target: int,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Envoi sequentiel bloquant - reserve au test CLI (cli_test.py)."""
    reset_diagnostics()
    total = len(tokens)
    for i, tk in enumerate(tokens):
        send_token(tk, hkl_target)
        if on_progress:
            on_progress(i + 1, total)
        time.sleep(max(1, tk.delay_ms) / 1000.0)
