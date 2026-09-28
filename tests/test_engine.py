"""Moteur d'injection, sur le backend FACTICE (aucune vraie frappe)."""

from keyboard import backend, engine
from keyboard.engine import Token, build_tokens, send_token


def kinds(res):
    return [t.kind for t in res.tokens]


def test_empty_text_returns_none():
    assert build_tokens("", 15, 300) is None
    assert build_tokens("# c\n\n", 15, 300, skip_comments=True,
                        skip_empty=True) is None


def test_final_enter_option():
    with_enter = build_tokens("ab", 15, 300, final_enter=True)
    without = build_tokens("ab", 15, 300, final_enter=False)
    assert kinds(with_enter) == ["char", "char", "enter"]
    assert kinds(without) == ["char", "char"]


def test_enter_between_lines_even_without_final_enter():
    res = build_tokens("a\nb", 15, 300, final_enter=False)
    assert kinds(res) == ["char", "enter", "char"]
    assert res.line_count == 2


def test_delays_are_applied():
    res = build_tokens("a\nb", 15, 300, final_enter=True)
    assert res.tokens[0].delay_ms == 15
    assert res.tokens[1].delay_ms == 300      # apres ENTREE


def test_skip_comments_and_empty():
    text = "# note\n; autre\n\nreel\n"
    res = build_tokens(text, 15, 300, skip_comments=True, skip_empty=True,
                       final_enter=False)
    assert "".join(t.ch for t in res.tokens if t.kind == "char") == "reel"


def test_tab_becomes_tab_token():
    assert kinds(build_tokens("a\tb", 15, 300, final_enter=False)) == \
        ["char", "tab", "char"]


def test_convert_fn_and_unmapped():
    res = build_tokens("ab", 15, 300, final_enter=False, convert=True,
                       convert_fn=lambda c: "X" if c == "a" else None)
    assert res.tokens[0].ch == "X"
    assert res.unmapped == {"b"}


def test_altgr_char_gets_minimum_delay(monkeypatch):
    monkeypatch.setattr(backend, "needs_altgr", lambda ch, hkl: ch == "@")
    res = build_tokens("a@", 15, 300, final_enter=False, hkl_local=0x040C040C)
    a, at = res.tokens
    assert not a.alt_gr and a.delay_ms == 15
    assert at.alt_gr and at.delay_ms >= backend.ALT_GR_MIN_DELAY_MS
    assert res.alt_gr_chars == {"@"}


def test_scancode_send_uses_scan_path():
    assert send_token(Token("char", "a", False, 15, 1), 0x040C040C)
    assert backend.sent_scan == ["a"] and backend.sent_unicode == []


def test_untypable_char_falls_back_to_unicode_and_is_reported():
    assert send_token(Token("char", "é", False, 15, 1), 0x040C040C)
    assert backend.sent_unicode == ["é"]
    assert "é" in engine.fallback_chars and not engine.failed_chars


def test_untypable_char_without_fallback_is_flagged_not_silent():
    assert not send_token(Token("char", "é", False, 15, 1), 0x040C040C,
                          allow_unicode=False)
    assert "é" in engine.failed_chars


def test_unicode_method_never_uses_scancodes():
    send_token(Token("char", "a", False, 15, 1), 0, method=engine.METHOD_UNICODE)
    assert backend.sent_unicode == ["a"] and backend.sent_scan == []


def test_enter_and_tab_tokens_succeed():
    assert send_token(Token("enter", None, False, 300, 1), 0)
    assert send_token(Token("tab", None, False, 15, 1), 0)


def test_regression_held_modifier_swallows_letters():
    """Regle 2 (CONTRIBUTING.md, moteur clavier) : un modificateur encore
    enfonce transforme les lettres en raccourcis. Il doit etre relache avant
    la premiere frappe."""
    backend.held_modifiers.append("Alt")
    send_token(Token("char", "a", False, 15, 1), 0x040C040C)
    assert backend.sent_scan == []            # avalee : le bug
    assert backend.release_held_modifiers() == ["Alt"]
    send_token(Token("char", "a", False, 15, 1), 0x040C040C)
    assert backend.sent_scan == ["a"]         # corrige apres relachement


def test_regression_char_token_without_char_is_not_sent():
    """Un `assert` gardait ce cas : il disparait en mode optimise (-O).
    Le jeton est desormais compte comme non tape, sans rien envoyer."""
    backend.sent_scan.clear()
    backend.sent_unicode.clear()
    token = Token(kind="char", ch=None, alt_gr=False, delay_ms=0, line=1)
    assert send_token(token, hkl_target=0) is False
    assert backend.sent_scan == [] and backend.sent_unicode == []
