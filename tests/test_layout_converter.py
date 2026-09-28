import re

from keyboard import backend
from keyboard.layout_converter import (LayoutConverter, MODE_LAYOUT,
                                       MODE_NONE)
from keyboard.layouts import KNOWN_LAYOUTS


def test_known_layouts_are_wellformed_and_unique():
    klids = [k for _, k in KNOWN_LAYOUTS]
    assert all(re.fullmatch(r"[0-9A-F]{8}", k) for k in klids)
    assert len(set(klids)) == len(klids)


def test_mode_none_passes_through():
    c = LayoutConverter()
    assert c.mode == MODE_NONE and not c.active
    assert c.convert("@") == "@"


def test_layout_mode_without_layouts_passes_through():
    c = LayoutConverter()
    c.set_mode(MODE_LAYOUT)
    assert c.convert("a") == "a"


def test_layout_mode_translates_and_reports_missing(monkeypatch):
    monkeypatch.setattr(backend, "translate_char",
                        lambda ch, tgt, loc: None if ch == "€" else ch.upper())
    c = LayoutConverter()
    c.set_mode(MODE_LAYOUT)
    c.hkl_local, c.hkl_target = 1, 2
    assert c.active
    typed, missing = c.preview("a€")
    assert typed == "A?" and missing == {"€"}


def test_set_target_by_index_bounds():
    c = LayoutConverter()
    assert c.set_target_by_index(0)
    assert not c.set_target_by_index(-1)
    assert not c.set_target_by_index(len(KNOWN_LAYOUTS))
