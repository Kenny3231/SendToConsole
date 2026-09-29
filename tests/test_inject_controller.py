"""Machine a etats de l'envoi (QTimer), pilotee a la main via _tick()."""

import pytest

pytest.importorskip("PySide6")

from keyboard import backend, engine              # noqa: E402
from keyboard.engine import build_tokens          # noqa: E402

HKL = 0x040C040C
TARGET = 1001                                       # fenetre factice, au 1er plan


@pytest.fixture
def ctl(qcore_app):
    from core.inject_controller import InjectController
    c = InjectController()
    yield c
    c.stop()


def tokens(text="abc"):
    return build_tokens(text, 1, 1, final_enter=False, hkl_local=HKL).tokens


def run_to_end(ctl, limit=200):
    for _ in range(limit):
        if ctl.state == "idle":
            return
        ctl._tick()
    raise AssertionError(f"pas termine, etat={ctl.state}")


def test_rejects_empty_and_missing_target(ctl):
    errors = []
    ctl.failed.connect(errors.append)
    ctl.start([], HKL, TARGET)
    ctl.start(tokens(), HKL, 0)
    assert len(errors) == 2 and ctl.state == "idle"


def test_full_send_types_everything(ctl):
    backend.force_foreground(TARGET)
    ctl.start(tokens("abc"), HKL, TARGET, force_foreground=False)
    run_to_end(ctl)
    assert backend.sent_scan == ["a", "b", "c"]


def test_focus_loss_pauses_and_never_types_elsewhere(ctl):
    backend.force_foreground(1002)                  # autre fenetre
    paused = []
    ctl.paused_on_focus_loss.connect(lambda: paused.append(True))
    ctl.start(tokens("abc"), HKL, TARGET, force_foreground=False)
    ctl._tick()
    assert paused and ctl.state == "paused"
    assert backend.sent_scan == []                  # rien tape dans la mauvaise fenetre


def test_held_modifiers_are_released_before_typing(ctl):
    backend.force_foreground(TARGET)
    backend.held_modifiers.append("Alt")
    released = []
    ctl.modifiers_released.connect(released.append)
    ctl.start(tokens("abc"), HKL, TARGET, force_foreground=False)
    assert ctl.state == "settling" and released == [["Alt"]]
    run_to_end(ctl)
    assert backend.sent_scan == ["a", "b", "c"]


def test_pause_resume_stop(ctl):
    backend.force_foreground(TARGET)
    ctl.start(tokens("abcdef"), HKL, TARGET, force_foreground=False)
    ctl._tick(); ctl._tick()
    ctl.pause()
    assert ctl.state == "paused"
    ctl.resume()
    assert ctl.state == "sending"
    ctl.stop()
    assert ctl.state == "idle" and not ctl.is_running


def test_regression_final_enter_changing_focus_still_finishes(ctl, monkeypatch):
    """ENTREE finale qui fait quitter la cible (connexion validee, boite
    fermee) : l'envoi restait en pause auto sans plus rien a taper, et tout
    raccourci suivant etait refuse (« un envoi est déjà en cours »)."""
    backend.force_foreground(TARGET)
    real_send = engine.send_token

    def send_and_lose_focus(tk, *args, **kwargs):
        ok = real_send(tk, *args, **kwargs)
        if tk.kind == "enter":
            backend.force_foreground(1002)
        return ok

    monkeypatch.setattr("core.inject_controller.send_token", send_and_lose_focus)
    done = []
    ctl.finished.connect(lambda: done.append(True))
    toks = build_tokens("abc", 1, 1, final_enter=True, hkl_local=HKL).tokens
    ctl.start(toks, HKL, TARGET, force_foreground=False)
    run_to_end(ctl)
    assert done and ctl.state == "idle" and not ctl.is_running


def test_second_start_while_running_is_refused(ctl):
    backend.force_foreground(TARGET)
    errors = []
    ctl.failed.connect(errors.append)
    ctl.start(tokens(), HKL, TARGET, force_foreground=False)
    ctl.start(tokens(), HKL, TARGET, force_foreground=False)
    assert errors and "déjà" in errors[0]
