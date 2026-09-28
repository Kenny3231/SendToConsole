from core.models import QuickEntry


def test_roundtrip():
    e = QuickEntry("iDRAC", "p@ss|\\~#{[€", mods=3, vk=0x70,
                   final_enter=True, secret=False)
    assert QuickEntry.from_dict(e.to_dict()) == e


def test_defaults_and_missing_keys():
    e = QuickEntry.from_dict({})
    assert e.label == "(sans nom)" and e.text == "" and e.secret is True
    assert not e.has_hotkey


def test_runtime_state_is_not_serialized():
    e = QuickEntry("x", registered=True, hotkey_id=7)
    d = e.to_dict()
    assert "registered" not in d and "hotkey_id" not in d


def test_has_hotkey_needs_both():
    assert not QuickEntry("x", mods=2, vk=0).has_hotkey
    assert not QuickEntry("x", mods=0, vk=0x70).has_hotkey
    assert QuickEntry("x", mods=2, vk=0x70).has_hotkey


def test_matches_filter():
    e = QuickEntry("Serveur iDRAC prod")
    assert e.matches("") and e.matches("  idrac ") and not e.matches("ilo")
