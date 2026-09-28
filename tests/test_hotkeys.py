import pytest

pytest.importorskip("PySide6")

from core import hotkeys as hk                     # noqa: E402
from core.models import QuickEntry                 # noqa: E402


def test_format_hotkey():
    assert hk.format_hotkey(0, 0) == "(aucun)"
    assert hk.format_hotkey(hk.MOD_CONTROL | hk.MOD_ALT, 0x70) == "Ctrl+Alt+F1"
    assert hk.format_hotkey(hk.MOD_CONTROL | hk.MOD_SHIFT, ord("A")) == \
        "Ctrl+Maj+A"


def test_vk_name():
    assert hk.vk_name(0x70) == "F1" and hk.vk_name(0x87) == "F24"
    assert hk.vk_name(0x20) == "Espace" and hk.vk_name(0xFF) == "0xFF"


def test_same_hotkey_cannot_be_assigned_twice():
    a = QuickEntry("a", mods=hk.MOD_CONTROL, vk=0x70)
    b = QuickEntry("b", mods=hk.MOD_CONTROL, vk=0x71)
    assert hk.HotkeyManager.is_taken([a, b], hk.MOD_CONTROL, 0x70) is a
    assert hk.HotkeyManager.is_taken([a, b], hk.MOD_CONTROL, 0x72) is None


def test_is_taken_ignores_itself_and_empty_combo():
    a = QuickEntry("a", mods=hk.MOD_CONTROL, vk=0x70)
    assert hk.HotkeyManager.is_taken([a], hk.MOD_CONTROL, 0x70, exclude=a) is None
    assert hk.HotkeyManager.is_taken([a], 0, 0) is None


def test_debounce_rejects_second_trigger():
    m = hk.HotkeyManager()
    assert m._accept(1) is True
    assert m._accept(1) is False      # < DEBOUNCE_MS
    assert m._accept(2) is True       # autre raccourci : independant


def test_register_without_hotkey_is_noop_ok():
    m = hk.HotkeyManager()
    e = QuickEntry("a")
    assert m.register(e) is True and not e.registered


# --- Actions de l'outil (cibler la fenetre active, palette) -----------------

def test_action_without_combo_is_noop_ok():
    m = hk.HotkeyManager()
    assert m.register_action(hk.ACTION_CAPTURE_TARGET, 0, 0) is True
    assert not m.is_action_registered(hk.ACTION_CAPTURE_TARGET)


def test_action_not_registered_before_attach():
    """Sans HWND (avant main.py:attach), rien n'est enregistre."""
    m = hk.HotkeyManager()
    assert not m.is_attached
    assert m.register_action(hk.ACTION_TOGGLE_PALETTE,
                             hk.MOD_CONTROL | hk.MOD_ALT, ord("P")) is False


def test_hotkey_owner_covers_entries_and_actions():
    a = QuickEntry("root", mods=hk.MOD_CONTROL, vk=0x70)
    actions = {hk.ACTION_CAPTURE_TARGET: (hk.MOD_CONTROL, 0x71),
               hk.ACTION_TOGGLE_PALETTE: (0, 0)}
    assert "root" in hk.hotkey_owner([a], actions, hk.MOD_CONTROL, 0x70)
    owner = hk.hotkey_owner([a], actions, hk.MOD_CONTROL, 0x71)
    assert "Cibler la fenêtre active" in owner
    # Libre, ou deja a soi-meme : pas de conflit.
    assert hk.hotkey_owner([a], actions, hk.MOD_CONTROL, 0x72) is None
    assert hk.hotkey_owner([a], actions, hk.MOD_CONTROL, 0x71,
                           exclude_action=hk.ACTION_CAPTURE_TARGET) is None
    assert hk.hotkey_owner([a], actions, hk.MOD_CONTROL, 0x70,
                           exclude_entry=a) is None
    assert hk.hotkey_owner([a], actions, 0, 0) is None


def test_dispatch_emits_action_with_foreground_window(monkeypatch):
    from keyboard import backend as w32
    m = hk.HotkeyManager()
    m._actions[7] = hk.ACTION_CAPTURE_TARGET
    got = []
    m.action_triggered.connect(lambda action, hwnd: got.append((action, hwnd)))
    monkeypatch.setattr(w32, "get_foreground_window", lambda: 0x1234)
    m._dispatch(7)
    m._dispatch(7)                    # rebond : ignore
    m._dispatch(99)                   # id inconnu : ignore
    assert got == [(hk.ACTION_CAPTURE_TARGET, 0x1234)]



# --- Suspension, refus, combinaisons dangereuses (relecture v0.6.3) ---------

class _FakeUser32:
    """Remplace user32 : AUCUN vrai raccourci global n'est enregistre."""

    def __init__(self, refuse=()):
        self.refuse = set(refuse)          # (mods, vk) refuses par « Windows »
        self.live: dict[int, tuple[int, int]] = {}
        self.calls: list[tuple] = []

    def RegisterHotKey(self, hwnd, hotkey_id, mods, vk):  # noqa: N802
        combo = (mods & ~hk.MOD_NOREPEAT, vk)
        self.calls.append(("reg", hotkey_id, combo))
        if combo in self.refuse or hotkey_id in self.live:
            return False
        self.live[hotkey_id] = combo
        return True

    def UnregisterHotKey(self, hwnd, hotkey_id):  # noqa: N802
        self.calls.append(("unreg", hotkey_id))
        return self.live.pop(hotkey_id, None) is not None


@pytest.fixture
def fake_win(monkeypatch):
    fake = _FakeUser32()
    monkeypatch.setattr(hk, "user32", fake)
    monkeypatch.setattr(hk, "IS_WINDOWS", True)
    m = hk.HotkeyManager()
    m.attach(0xABC)
    return m, fake


def test_suspend_releases_everything_and_resume_restores(fake_win):
    m, fake = fake_win
    entry = QuickEntry("root", mods=hk.MOD_CONTROL, vk=0x70)
    assert m.register(entry)
    assert m.register_action(hk.ACTION_TOGGLE_PALETTE, hk.MOD_ALT, ord("P"))
    assert len(fake.live) == 2

    m.suspend()
    assert fake.live == {} and m.is_suspended
    m.resume()
    assert set(fake.live.values()) == {(hk.MOD_CONTROL, 0x70),
                                       (hk.MOD_ALT, ord("P"))}
    assert entry.registered and m.is_action_registered(hk.ACTION_TOGGLE_PALETTE)


def test_regression_suspended_manager_ignores_wm_hotkey(fake_win):
    """Saisie d'une combinaison deja prise dans un HotkeyEdit : l'entree
    partait vers la derniere fenetre de travail."""
    m, _fake = fake_win
    entry = QuickEntry("root", mods=hk.MOD_CONTROL, vk=0x70)
    m.register(entry)
    fired = []
    m.triggered.connect(lambda e, h: fired.append(e))
    m.suspend()
    m._dispatch(entry.hotkey_id)
    assert fired == []


def test_register_while_suspended_only_checks_then_resume_registers(fake_win):
    m, fake = fake_win
    m.suspend()
    assert m.register_action(hk.ACTION_CAPTURE_TARGET, hk.MOD_ALT, ord("C"))
    assert fake.live == {}                 # verifie puis libere aussitot
    m.resume()
    assert list(fake.live.values()) == [(hk.MOD_ALT, ord("C"))]


def test_resume_reports_combo_taken_meanwhile(fake_win):
    m, fake = fake_win
    entry = QuickEntry("root", mods=hk.MOD_CONTROL, vk=0x70)
    m.register(entry)
    m.register_action(hk.ACTION_TOGGLE_PALETTE, hk.MOD_ALT, ord("P"))
    lost_entries, lost_actions = [], []
    m.registration_failed.connect(lost_entries.append)
    m.action_registration_failed.connect(lost_actions.append)
    m.suspend()
    fake.refuse = {(hk.MOD_CONTROL, 0x70), (hk.MOD_ALT, ord("P"))}
    m.resume()
    assert lost_entries == [entry] and not entry.registered
    assert lost_actions == [hk.ACTION_TOGGLE_PALETTE]
    assert not m.is_action_registered(hk.ACTION_TOGGLE_PALETTE)


def test_refused_new_action_combo_keeps_old_one_active(fake_win):
    m, fake = fake_win
    assert m.register_action(hk.ACTION_CAPTURE_TARGET, hk.MOD_ALT, ord("C"))
    fake.refuse = {(hk.MOD_ALT, ord("X"))}
    assert m.register_action(hk.ACTION_CAPTURE_TARGET, hk.MOD_ALT, ord("X")) is False
    assert list(fake.live.values()) == [(hk.MOD_ALT, ord("C"))]
    assert m.is_action_registered(hk.ACTION_CAPTURE_TARGET)


def test_same_action_combo_is_not_registered_twice(fake_win):
    m, fake = fake_win
    m.register_action(hk.ACTION_CAPTURE_TARGET, hk.MOD_ALT, ord("C"))
    m.register_action(hk.ACTION_CAPTURE_TARGET, hk.MOD_ALT, ord("C"))
    assert len(fake.live) == 1


def test_unregister_all_actions_frees_windows_hotkeys(fake_win):
    m, fake = fake_win
    m.register_action(hk.ACTION_CAPTURE_TARGET, hk.MOD_ALT, ord("C"))
    m.register_action(hk.ACTION_TOGGLE_PALETTE, hk.MOD_ALT, ord("P"))
    m.unregister_all_actions()
    assert fake.live == {}


def test_action_hotkey_is_debounced(monkeypatch):
    from keyboard import backend as w32
    m = hk.HotkeyManager()
    m._actions[5] = hk.ACTION_TOGGLE_PALETTE
    got = []
    m.action_triggered.connect(lambda a, h: got.append(a))
    monkeypatch.setattr(w32, "get_foreground_window", lambda: 1)
    m._dispatch(5)
    m._dispatch(5)
    assert got == [hk.ACTION_TOGGLE_PALETTE]


@pytest.mark.parametrize("mods, vk, unsafe", [
    (hk.MOD_SHIFT, ord("A"), True),                      # majuscule volee
    (hk.MOD_SHIFT, 0x70, False),                         # Maj+F1 : inoffensif
    (hk.MOD_CONTROL | hk.MOD_ALT, ord("0"), True),       # AltGr+0 = @ (simule)
    (hk.MOD_CONTROL | hk.MOD_ALT, ord("E"), True),       # AltGr+E = EUR
    (hk.MOD_CONTROL | hk.MOD_ALT, ord("C"), False),      # rien en AltGr
    (hk.MOD_CONTROL | hk.MOD_ALT | hk.MOD_SHIFT, ord("0"), False),
    (hk.MOD_CONTROL, ord("0"), False),
])
def test_unsafe_hotkey_reason(mods, vk, unsafe):
    reason = hk.unsafe_hotkey_reason(mods, vk, 0x040C040C)
    assert (reason is not None) is unsafe
