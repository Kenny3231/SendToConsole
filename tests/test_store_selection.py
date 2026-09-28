"""Choix du coffre : historique des coffres récents et dialogue de
déverrouillage avec sélecteur (coffre existant / nouveau, jamais écrasé)."""

from pathlib import Path

import pytest

from core.models import QuickEntry
from ui.store_history import (
    LAST_KEY, MAX_RECENT, RECENT_KEY, recent_stores, remember_store,
    same_path, with_store_suffix)


class _Settings:
    """QSettings minimal (value/setValue) : aucun accès au registre."""

    def __init__(self, **values):
        self.values = dict(values)

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


# ------------------------------------------------------------- historique --
def test_recent_stores_accepts_none_str_and_list(tmp_path):
    assert recent_stores(_Settings()) == []
    one = str(tmp_path / "a.enc")
    # QSettings relit une liste d'un seul élément comme un simple str.
    assert recent_stores(_Settings(**{RECENT_KEY: one})) == [Path(one)]
    assert recent_stores(_Settings(**{RECENT_KEY: [one, "", one.upper()]})) \
        == [Path(one)]


def test_remember_store_puts_last_first_without_duplicates(tmp_path):
    settings = _Settings()
    a, b = tmp_path / "a.enc", tmp_path / "b.enc"
    remember_store(settings, a)
    remember_store(settings, b)
    remember_store(settings, Path(str(a).upper()))   # même fichier (Windows)
    assert settings.values[LAST_KEY] == str(a).upper()
    recent = recent_stores(settings)
    assert len(recent) == 2 and same_path(recent[0], a) and recent[1] == b


def test_remember_store_is_bounded(tmp_path):
    settings = _Settings()
    for i in range(MAX_RECENT + 5):
        remember_store(settings, tmp_path / f"{i}.enc")
    assert len(recent_stores(settings)) == MAX_RECENT


def test_with_store_suffix(tmp_path):
    assert with_store_suffix(tmp_path / "coffre") == tmp_path / "coffre.enc"
    assert with_store_suffix(tmp_path / "x.dat") == tmp_path / "x.dat"


# --------------------------------------------------------------- dialogue --
@pytest.fixture
def mpd(qapp):
    try:
        import ui.master_password_dialog as module
    except ImportError as exc:
        pytest.skip(f"Interface Qt indisponible : {exc}")
    return module


def _make_store(path: Path, password: str, entries=()) -> Path:
    from security.local_encrypted import LocalEncryptedProvider
    store = LocalEncryptedProvider(path)
    store.unlock(password)
    if entries:
        store.save(list(entries))
    store.lock()
    return path


def _dialog(mpd, path: Path, **kwargs):
    from security.local_encrypted import LocalEncryptedProvider
    provider = LocalEncryptedProvider(path)
    return mpd.MasterPasswordDialog(provider, first_run=not provider.exists,
                                    choose_store=True, **kwargs)


def test_first_launch_can_open_existing_store(mpd, tmp_path, fast_kdf):
    """Premier lancement (coffre par défaut absent) : un coffre existant
    ailleurs peut être choisi et ouvert avec SON master password."""
    existing = _make_store(tmp_path / "ailleurs" / "perso.enc", "secret",
                           [QuickEntry(label="root", text="x")])
    dialog = _dialog(mpd, tmp_path / "defaut" / "entries.enc")
    assert dialog.first_run and not dialog.confirm_edit.isHidden()

    dialog.select_store(existing)
    assert not dialog.first_run
    assert dialog.confirm_edit.isHidden()
    assert dialog.ok_btn.text() == "Déverrouiller"

    dialog.password_edit.setText("secret")
    dialog._try_unlock()
    assert dialog.choice == "unlock"
    assert same_path(dialog.provider.path, existing)
    assert [e.label for e in dialog.provider.load()] == ["root"]
    assert not (tmp_path / "defaut" / "entries.enc").exists()


def test_recent_stores_listed_only_if_present(mpd, tmp_path, fast_kdf):
    present = _make_store(tmp_path / "a.enc", "pw")
    gone = tmp_path / "gone.enc"
    dialog = _dialog(mpd, tmp_path / "entries.enc",
                     recent_stores=[present, gone])
    listed = [dialog.store_combo.itemData(i)
              for i in range(dialog.store_combo.count())]
    assert any(same_path(p, present) for p in listed)
    assert not any(same_path(p, gone) for p in listed)

    dialog.store_combo.setCurrentIndex(1)      # choix dans la liste
    assert same_path(dialog.provider.path, present) and not dialog.first_run


def test_missing_last_store_is_flagged(mpd, tmp_path):
    last = tmp_path / "usb" / "entries.enc"
    dialog = _dialog(mpd, last, missing_last=last)
    assert not dialog.missing_label.isHidden()
    assert "introuvable" in dialog.store_combo.itemText(0)


def test_new_store_never_overwrites_existing_file(mpd, tmp_path, fast_kdf,
                                                  monkeypatch):
    existing = _make_store(tmp_path / "pris.enc", "ancien")
    before = existing.read_bytes()
    dialog = _dialog(mpd, tmp_path / "entries.enc")
    monkeypatch.setattr(mpd.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(existing), "")))

    # Refus : rien ne change.
    monkeypatch.setattr(mpd.QMessageBox, "question",
                        staticmethod(lambda *a, **k: mpd.QMessageBox.StandardButton.No))
    dialog._browse_new()
    assert same_path(dialog.provider.path, tmp_path / "entries.enc")

    # Acceptation : le fichier est OUVERT (mot de passe demandé), pas recréé.
    monkeypatch.setattr(mpd.QMessageBox, "question",
                        staticmethod(lambda *a, **k: mpd.QMessageBox.StandardButton.Yes))
    dialog._browse_new()
    assert same_path(dialog.provider.path, existing) and not dialog.first_run

    monkeypatch.setattr(mpd.QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    dialog.password_edit.setText("nouveau")
    dialog._try_unlock()                        # mauvais mot de passe
    assert not dialog.provider.is_unlocked()
    assert existing.read_bytes() == before


def test_new_store_adds_enc_suffix_and_creates_it(mpd, tmp_path, fast_kdf,
                                                  monkeypatch):
    dialog = _dialog(mpd, tmp_path / "entries.enc")
    monkeypatch.setattr(mpd.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(tmp_path / "client"), "")))
    dialog._browse_new()
    assert dialog.provider.path == tmp_path / "client.enc" and dialog.first_run
    dialog.password_edit.setText("pw")
    dialog.confirm_edit.setText("pw")
    dialog._try_unlock()
    assert dialog.choice == "unlock" and (tmp_path / "client.enc").exists()


def test_dialog_without_chooser_keeps_given_provider(mpd, tmp_path, fast_kdf):
    from security.local_encrypted import LocalEncryptedProvider
    provider = LocalEncryptedProvider(tmp_path / "entries.enc")
    dialog = mpd.MasterPasswordDialog(provider, first_run=True)
    assert dialog.store_combo is None
    dialog.password_edit.setText("pw")
    dialog.confirm_edit.setText("pw")
    dialog._try_unlock()
    assert dialog.provider is provider and provider.is_unlocked()


def test_regression_focus_starts_in_password_field(mpd, tmp_path, qapp):
    """Le focus arrivait sur la liste des coffres : un mot de passe tapé
    sans cliquer changeait de coffre par recherche clavier."""
    dialog = _dialog(mpd, tmp_path / "entries.enc")
    dialog.show()
    qapp.processEvents()
    assert dialog.focusWidget() is dialog.password_edit
    dialog.reject()


def test_passwords_cleared_on_store_change_and_after_unlock(mpd, tmp_path,
                                                            fast_kdf):
    other = _make_store(tmp_path / "b.enc", "pw")
    dialog = _dialog(mpd, tmp_path / "a.enc")
    dialog.password_edit.setText("tape pour a")
    dialog.select_store(other)
    assert dialog.password_edit.text() == ""
    dialog.password_edit.setText("pw")
    dialog._try_unlock()
    assert dialog.choice == "unlock"
    assert dialog.password_edit.text() == "" and dialog.confirm_edit.text() == ""


def test_vanished_store_is_not_recreated_silently(mpd, tmp_path, fast_kdf,
                                                  monkeypatch):
    """Coffre choisi puis retiré (clé USB) : pas de création avec un mot de
    passe saisi une seule fois."""
    store = _make_store(tmp_path / "usb.enc", "pw")
    dialog = _dialog(mpd, store)
    assert not dialog.first_run
    store.unlink()
    monkeypatch.setattr(mpd.QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    dialog.password_edit.setText("pw")
    dialog._try_unlock()
    assert not store.exists() and not dialog.provider.is_unlocked()
    assert dialog.first_run and not dialog.confirm_edit.isHidden()


# ------------------------------------------------- stockage : exclusivité --
def test_regression_create_never_replaces_store_appearing_meanwhile(
        tmp_path, fast_kdf, monkeypatch):
    """Coffre créé par une autre session pendant la dérivation PBKDF2 :
    unlock() en mode création le remplaçait par un coffre vide."""
    from security import local_encrypted
    from security.local_encrypted import LocalEncryptedProvider
    from security.provider import SecretProviderError
    path = tmp_path / "entries.enc"
    provider = LocalEncryptedProvider(path)
    real_derive = LocalEncryptedProvider._derive

    def derive_then_other_session_writes(password, salt, iterations):
        path.write_text("coffre d'une autre session", encoding="utf-8")
        return real_derive(password, salt, iterations)
    monkeypatch.setattr(local_encrypted.LocalEncryptedProvider, "_derive",
                        staticmethod(derive_then_other_session_writes))
    with pytest.raises(SecretProviderError, match="jamais écrasé"):
        provider.unlock("pw")
    assert path.read_text(encoding="utf-8") == "coffre d'une autre session"
    assert not provider.is_unlocked()
    assert not list(tmp_path.glob("*.tmp"))


def test_save_as_refuses_existing_file_and_keeps_path(tmp_path, fast_kdf):
    from security.local_encrypted import LocalEncryptedProvider
    from security.provider import SecretProviderError
    provider = LocalEncryptedProvider(tmp_path / "a.enc")
    provider.unlock("pw")
    taken = tmp_path / "b.enc"
    taken.write_text("autre", encoding="utf-8")
    with pytest.raises(SecretProviderError):
        provider.save_as(taken, [QuickEntry(label="x", text="y")])
    assert taken.read_text(encoding="utf-8") == "autre"
    assert provider.path == tmp_path / "a.enc"

    provider.save_as(tmp_path / "c.enc", [QuickEntry(label="x", text="y")])
    assert provider.path == tmp_path / "c.enc"
    assert [e.label for e in provider.load()] == ["x"]
    assert (tmp_path / "a.enc").exists()


def test_add_store_item_without_chooser_raises(mpd, tmp_path):
    """Garde explicite (ex-assert) : sans selecteur, erreur claire."""
    from security.local_encrypted import LocalEncryptedProvider
    dialog = mpd.MasterPasswordDialog(
        LocalEncryptedProvider(tmp_path / "entries.enc"), first_run=True)
    with pytest.raises(RuntimeError):
        dialog._add_store_item(tmp_path / "autre.enc")
