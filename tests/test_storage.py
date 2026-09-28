"""Stockage chiffre : les garanties non negociables de SECURITY.md."""

import base64
import json
import os
import subprocess
import sys

import pytest

from core.models import QuickEntry
from security import local_encrypted as le
from security.local_encrypted import LocalEncryptedProvider
from security.provider import SecretProvider, SecretProviderError

SECRET = "Sup3r-S3cr3t!@#€|\\"


@pytest.fixture
def store(tmp_path, fast_kdf):
    return tmp_path / "entries.enc"


def _fresh(path, password="master"):
    p = LocalEncryptedProvider(path)
    p.unlock(password)
    return p


def test_provider_is_abstract_interface():
    assert issubclass(LocalEncryptedProvider, SecretProvider)


def test_roundtrip(store):
    p = _fresh(store)
    p.save([QuickEntry("a", SECRET, mods=2, vk=0x70, final_enter=True)])
    q = LocalEncryptedProvider(store)
    q.unlock("master")
    got = q.load()
    assert got[0].text == SECRET and got[0].vk == 0x70 and got[0].final_enter


def test_no_plaintext_on_disk(store):
    p = _fresh(store)
    p.save([QuickEntry("LABEL-EN-CLAIR?", SECRET)])
    raw = store.read_bytes()
    assert b"Sup3r" not in raw and b"LABEL-EN-CLAIR" not in raw
    assert b"master" not in raw


def test_wrong_password_rejected(store):
    _fresh(store)
    with pytest.raises(SecretProviderError):
        LocalEncryptedProvider(store).unlock("mauvais")


def test_empty_password_rejected(store):
    with pytest.raises(SecretProviderError):
        LocalEncryptedProvider(store).unlock("")


def test_tampered_payload_rejected(store):
    p = _fresh(store)
    p.save([QuickEntry("a", "x")])
    doc = json.loads(store.read_text(encoding="utf-8"))
    raw = bytearray(base64.urlsafe_b64decode(doc["payload"]))
    raw[-1] ^= 0x01
    doc["payload"] = base64.urlsafe_b64encode(bytes(raw)).decode("ascii")
    store.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SecretProviderError):
        LocalEncryptedProvider(store).unlock("master")


def test_corrupted_file_rejected(store):
    _fresh(store)
    store.write_text("pas du json", encoding="utf-8")
    with pytest.raises(SecretProviderError):
        LocalEncryptedProvider(store).unlock("master")


def test_locked_provider_refuses_load_and_save(store):
    p = _fresh(store)
    p.lock()
    assert not p.is_unlocked()
    with pytest.raises(SecretProviderError):
        p.load()
    with pytest.raises(SecretProviderError):
        p.save([])


def test_salt_is_random_per_store(tmp_path, fast_kdf):
    a, b = tmp_path / "a.enc", tmp_path / "b.enc"
    _fresh(a)
    _fresh(b)
    salt = lambda f: json.loads(f.read_text(encoding="utf-8"))["salt"]
    assert salt(a) != salt(b)


def test_atomic_write_leaves_no_tmp(store):
    p = _fresh(store)
    p.save([QuickEntry("a")])
    assert not store.with_suffix(".tmp").exists()
    assert not list(store.parent.glob("*.tmp"))


def test_reset_deletes_everything(store):
    p = _fresh(store)
    p.reset()
    assert not store.exists() and not p.is_unlocked()


def test_kdf_cost_meets_security_requirement():
    """SECURITY.md : PBKDF2-SHA256, 600k iterations minimum (valeur reelle,
    donc sans la fixture fast_kdf)."""
    assert le.PBKDF2_ITERATIONS >= 600_000


def test_header_records_kdf_parameters(store):
    _fresh(store)
    doc = json.loads(store.read_text(encoding="utf-8"))
    assert doc["kdf"] == "pbkdf2-sha256"
    assert doc["iterations"] == le.PBKDF2_ITERATIONS
    assert len(base64.b64decode(doc["salt"])) == le.SALT_BYTES


def test_unlock_honors_iterations_from_header(store, monkeypatch):
    _fresh(store)                                   # cree avec 1000
    monkeypatch.setattr(le, "PBKDF2_ITERATIONS", 2_000)
    LocalEncryptedProvider(store).unlock("master")  # doit lire 1000


def test_header_iterations_different_from_constant_opens_and_is_kept(
        tmp_path, monkeypatch):
    """Fichier a 600 000 iterations (bornes reelles) : il s'ouvre meme si la
    constante courante differe, et save() ne change pas le cout."""
    path = tmp_path / "entries.enc"
    monkeypatch.setattr(le, "PBKDF2_ITERATIONS", 600_000)
    p = _fresh(path)
    p.save([QuickEntry(label="a", text=SECRET)])

    monkeypatch.setattr(le, "PBKDF2_ITERATIONS", 700_000)
    q = LocalEncryptedProvider(path)
    q.unlock("master")
    assert q.load()[0].text == SECRET
    q.save(q.load())
    assert json.loads(path.read_text(encoding="utf-8"))["iterations"] == 600_000


def test_iteration_bounds_meet_security_requirement():
    """Valeurs reelles (sans fast_kdf) : le plancher accepte a la lecture est
    celui de SECURITY.md, et le cout des fichiers sans en-tete reste fige."""
    assert le.MIN_PBKDF2_ITERATIONS >= 600_000
    assert le.MIN_PBKDF2_ITERATIONS <= le.PBKDF2_ITERATIONS
    assert le.LEGACY_PBKDF2_ITERATIONS == 600_000


def test_first_run_save_failure_leaves_store_locked(store, monkeypatch):
    def _fail(self, entries, target, exclusive):
        raise le.SecretProviderError("disque plein")
    # La création passe par l'écriture exclusive, pas par save().
    monkeypatch.setattr(LocalEncryptedProvider, "_write", _fail)
    p = LocalEncryptedProvider(store)
    with pytest.raises(SecretProviderError):
        p.unlock("master")
    assert not p.is_unlocked()


@pytest.mark.parametrize("payload", [123, None, ["x"]])
def test_load_non_string_payload_raises_provider_error(store, payload):
    """TypeError/AttributeError sur un en-tete altere -> SecretProviderError."""
    p = _fresh(store)
    doc = json.loads(store.read_text(encoding="utf-8"))
    doc["payload"] = payload
    store.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SecretProviderError):
        p.load()


@pytest.mark.parametrize("clear", [b"not json " + SECRET.encode(),
                                   json.dumps({"k": SECRET}).encode(),
                                   json.dumps([SECRET]).encode(),
                                   b"\xff\xfe"])
def test_load_invalid_decrypted_content_does_not_leak(store, clear):
    """Contenu dechiffre mais mal forme : erreur propre, sans fragment du
    clair dans le message."""
    p = _fresh(store)
    doc = json.loads(store.read_text(encoding="utf-8"))
    doc["payload"] = p._fernet.encrypt(clear).decode("ascii")
    store.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SecretProviderError) as exc:
        p.load()
    assert SECRET not in str(exc.value) and "Sup3r" not in str(exc.value)


def test_save_fsyncs_and_restricts_acl_before_replace(store, monkeypatch):
    calls = []
    real_fsync = le.os.fsync
    monkeypatch.setattr(le.os, "fsync",
                        lambda fd: (calls.append("fsync"), real_fsync(fd)))
    monkeypatch.setattr(le, "restrict_to_current_user",
                        lambda path, like=None: calls.append(("acl", path.suffix)))
    _fresh(store)
    assert calls == [("acl", ".tmp"), "fsync"]     # ACL avant le contenu



@pytest.mark.skipif(sys.platform != "win32", reason="ACL NTFS")
def test_saved_store_has_protected_acl_and_stays_usable(store):
    _fresh(store).save([QuickEntry(label="a", text=SECRET)])
    out = subprocess.run(["icacls", str(store)], capture_output=True,
                         text=True, errors="replace").stdout
    assert "(I)" not in out            # plus aucun droit herite
    q = LocalEncryptedProvider(store)  # toujours lisible par nous
    q.unlock("master")
    assert q.load()[0].text == SECRET


@pytest.mark.parametrize("bad", [599_999, 0, -1, 10_000_001,
                                 "600000", None, 1.5, True])
def test_header_iterations_out_of_bounds_rejected(tmp_path, monkeypatch, bad):
    """Borne basse (downgrade), borne haute (DoS) et types invalides : refus
    explicite, sans lancer la derivation de cle."""
    path = tmp_path / "entries.enc"
    monkeypatch.setattr(le, "PBKDF2_ITERATIONS", 100_000)
    _fresh(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["iterations"] = bad
    path.write_text(json.dumps(doc), encoding="utf-8")

    def _boom(*args, **kwargs):
        raise AssertionError("derivation lancee avec une valeur invalide")
    monkeypatch.setattr(LocalEncryptedProvider, "_derive", staticmethod(_boom))

    p = LocalEncryptedProvider(path)
    with pytest.raises(SecretProviderError):
        p.unlock("master")
    assert not p.is_unlocked()


def test_legacy_file_without_iterations_field_opens(store):
    """Ancien fichier sans champ 'iterations' : cout historique par defaut."""
    _fresh(store).save([QuickEntry(label="a", text="x")])
    doc = json.loads(store.read_text(encoding="utf-8"))
    del doc["iterations"]
    store.write_text(json.dumps(doc), encoding="utf-8")

    p = LocalEncryptedProvider(store)
    p.unlock("master")
    assert p.load()[0].text == "x"


# --- Durcissement v0.6.2 (audit : ACL, erreurs d'E/S, fuite par __context__) --

def test_unlock_permission_error_becomes_provider_error(store, monkeypatch):
    """Coffre cree depuis un autre compte / session admin : message clair au
    lieu d'une PermissionError qui sortait du slot Qt sans rien afficher."""
    _fresh(store)

    def _denied(self, *a, **k):
        raise PermissionError(13, "Accès refusé")
    monkeypatch.setattr(le.Path, "read_text", _denied)
    p = LocalEncryptedProvider(store)
    with pytest.raises(SecretProviderError, match="Accès refusé"):
        p.unlock("master")
    assert not p.is_unlocked()


def test_save_io_error_keeps_previous_store_and_no_tmp(store, monkeypatch):
    p = _fresh(store)
    p.save([QuickEntry(label="a", text=SECRET)])
    before = store.read_bytes()

    def _fail(self, target):
        raise PermissionError(13, "Accès refusé")
    monkeypatch.setattr(le.Path, "replace", _fail)
    with pytest.raises(SecretProviderError, match="intact"):
        p.save([])
    assert store.read_bytes() == before
    assert not list(store.parent.glob("*.tmp"))


def test_save_aborts_if_tmp_readback_differs(store, monkeypatch):
    p = _fresh(store)
    before = store.read_bytes()
    real = le.Path.read_text
    monkeypatch.setattr(
        le.Path, "read_text",
        lambda self, *a, **k: "" if self.suffix == ".tmp" else real(self, *a, **k))
    with pytest.raises(SecretProviderError):
        p.save([QuickEntry(label="a", text="x")])
    assert store.read_bytes() == before


def test_regression_invalid_content_error_has_no_chained_plaintext(store):
    """L'exception d'origine (JSONDecodeError.doc = tout le clair) ne doit
    pas rester accessible par __context__/__cause__."""
    p = _fresh(store)
    doc = json.loads(store.read_text(encoding="utf-8"))
    doc["payload"] = p._fernet.encrypt(b"oops " + SECRET.encode()).decode("ascii")
    store.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SecretProviderError) as exc:
        p.load()
    assert exc.value.__context__ is None and exc.value.__cause__ is None


def test_reset_removes_stale_random_tmp_files(store):
    p = _fresh(store)
    stale = store.parent / f"{store.stem}.abc123.tmp"
    stale.write_text("x", encoding="utf-8")
    p.reset()
    assert not stale.exists() and not store.exists()


def _icacls(path):
    return subprocess.run(["icacls", str(path)], capture_output=True,
                          text=True, errors="replace").stdout


@pytest.mark.skipif(sys.platform != "win32", reason="ACL NTFS")
def test_unlock_hardens_store_that_kept_inherited_acl(store):
    """Coffre existant jamais reecrit (ou migre par copie) : l'ouverture
    suffit a retirer les droits herites."""
    _fresh(store)
    subprocess.run(["icacls", str(store), "/reset"], capture_output=True)
    assert "(I)" in _icacls(store)
    p = LocalEncryptedProvider(store)
    p.unlock("master")
    assert p.acl_restricted is True
    assert "(I)" not in _icacls(store)


def _acl_sids(path):
    """SID bruts des ACE explicites (insensible a la langue de Windows)."""
    cmd = ("(Get-Acl -LiteralPath $env:STC_P).GetAccessRules($true, $false, "
           "[System.Security.Principal.SecurityIdentifier]) | "
           "ForEach-Object { $_.IdentityReference.Value }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                         capture_output=True, text=True,
                         env={**os.environ, "STC_P": str(path)}).stdout
    return set(out.split())


@pytest.mark.skipif(sys.platform != "win32", reason="ACL NTFS")
def test_save_preserves_existing_protected_dacl(store):
    """Un acces accorde auparavant (ex. le compte habituel quand l'outil
    tourne sous un compte admin distinct) n'est jamais retire."""
    users = "S-1-5-32-545"                         # BUILTIN Utilisateurs
    p = _fresh(store)
    subprocess.run(["icacls", str(store), "/grant", f"*{users}:(R)"],
                   capture_output=True, check=True)
    assert users in _acl_sids(store)
    p.save([QuickEntry(label="a", text="x")])
    assert users in _acl_sids(store)
    assert "(I)" not in _icacls(store)
