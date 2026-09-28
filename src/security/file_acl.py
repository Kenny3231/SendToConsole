"""
file_acl.py - Restreint l'ACL NTFS du coffre chiffre.

Le coffre vit a cote de l'executable. Sur un disque de donnees (D:\\...), la
racine accorde souvent « Modifier » a tous les Utilisateurs authentifies : un
autre compte local pourrait copier le fichier et attaquer le master password
hors ligne. On pose donc une DACL protegee (sans heritage) :

    utilisateur courant            : controle total
    proprietaire du coffre existant: controle total (s'il differe : l'outil
                                     lance depuis un compte admin distinct ne
                                     doit pas evincer le compte habituel)
    SYSTEM                         : controle total (sauvegardes, antivirus)
    Administrateurs                : controle total (recuperation du coffre)

Si le coffre existant porte deja une DACL protegee, elle est recopiee telle
quelle : on ne retire jamais un acces accorde auparavant.

Defense en profondeur seulement : le contenu reste protege par Fernet/PBKDF2.
Un echec rend False (le fichier garde ses droits herites) ; a l'appelant de
le signaler, sans bloquer l'enregistrement.
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _TOKEN_QUERY = 0x0008
    _TOKEN_USER_CLASS = 1
    _SDDL_REVISION_1 = 1
    _SE_FILE_OBJECT = 1
    _OWNER_SECURITY_INFORMATION = 0x00000001
    _DACL_SECURITY_INFORMATION = 0x00000004
    _PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    _SE_DACL_PROTECTED = 0x1000
    _SID_ADMINISTRATORS = "S-1-5-32-544"
    _SID_SYSTEM = "S-1-5-18"

    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD)]
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = \
        wintypes.BOOL
    _advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    _advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    _advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD)]
    _advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    _advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p)]
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    _advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD

    def _sid_to_str(psid: int | None) -> str | None:
        if not psid:
            return None
        text = wintypes.LPWSTR()
        if not _advapi32.ConvertSidToStringSidW(psid, ctypes.byref(text)):
            return None
        try:
            return text.value
        finally:
            _kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))

    def _current_user_sid() -> str | None:
        token = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(
                _kernel32.GetCurrentProcess(), _TOKEN_QUERY,
                ctypes.byref(token)):
            return None
        try:
            size = wintypes.DWORD(0)
            _advapi32.GetTokenInformation(
                token, _TOKEN_USER_CLASS, None, 0, ctypes.byref(size))
            if not size.value:
                return None
            buf = ctypes.create_string_buffer(size.value)
            if not _advapi32.GetTokenInformation(
                    token, _TOKEN_USER_CLASS, buf, size, ctypes.byref(size)):
                return None
            # TOKEN_USER commence par SID_AND_ATTRIBUTES { PSID Sid; ... }.
            return _sid_to_str(ctypes.c_void_p.from_buffer(buf).value)
        finally:
            _kernel32.CloseHandle(token)

    def _set_protected_dacl(path: Path, pdacl: ctypes.c_void_p) -> bool:
        err = _advapi32.SetNamedSecurityInfoW(
            str(path), _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, pdacl, None)
        return err == 0

    def _copy_protected_dacl(path: Path, like: Path) -> bool | None:
        """Recopie la DACL de `like` si elle est deja protegee. Rend None si
        `like` n'a pas de DACL protegee (ou est illisible) : rien n'est fait."""
        powner = ctypes.c_void_p()
        pdacl = ctypes.c_void_p()
        psd = ctypes.c_void_p()
        if _advapi32.GetNamedSecurityInfoW(
                str(like), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION,
                None, None, ctypes.byref(pdacl), None,
                ctypes.byref(psd)) != 0:
            return None
        try:
            control = wintypes.WORD()
            revision = wintypes.DWORD()
            if (not _advapi32.GetSecurityDescriptorControl(
                    psd, ctypes.byref(control), ctypes.byref(revision))
                    or not control.value & _SE_DACL_PROTECTED
                    or not pdacl.value):
                return None
            return _set_protected_dacl(path, pdacl)
        finally:
            _kernel32.LocalFree(psd)

    def _owner_sid(path: Path) -> str | None:
        powner = ctypes.c_void_p()
        psd = ctypes.c_void_p()
        if _advapi32.GetNamedSecurityInfoW(
                str(path), _SE_FILE_OBJECT, _OWNER_SECURITY_INFORMATION,
                ctypes.byref(powner), None, None, None,
                ctypes.byref(psd)) != 0:
            return None
        try:
            return _sid_to_str(powner.value)
        finally:
            _kernel32.LocalFree(psd)

    def restrict_to_current_user(path: Path, like: Path | None = None) -> bool:
        """Pose sur `path` la DACL protegee decrite en tete de module. `like`
        = coffre existant dont on preserve les acces. Rend True si une DACL
        protegee est en place ; False sinon (droits herites inchanges)."""
        if like is not None and like.exists():
            copied = _copy_protected_dacl(path, like)
            if copied is not None:
                return copied

        sids: list[str] = []
        for sid in (_current_user_sid(),
                    _owner_sid(like) if like is not None and like.exists()
                    else None):
            if sid and sid not in sids and sid not in (
                    _SID_ADMINISTRATORS, _SID_SYSTEM):
                sids.append(sid)
        if not sids:
            return False
        aces = "".join(f"(A;;FA;;;{sid})" for sid in sids)
        sddl = f"D:P{aces}(A;;FA;;;SY)(A;;FA;;;BA)"

        psd = ctypes.c_void_p()
        if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, _SDDL_REVISION_1, ctypes.byref(psd), None):
            return False
        try:
            present = wintypes.BOOL()
            defaulted = wintypes.BOOL()
            pdacl = ctypes.c_void_p()
            if not _advapi32.GetSecurityDescriptorDacl(
                    psd, ctypes.byref(present), ctypes.byref(pdacl),
                    ctypes.byref(defaulted)) or not present:
                return False
            return _set_protected_dacl(path, pdacl)
        finally:
            _kernel32.LocalFree(psd)

else:
    def restrict_to_current_user(path: Path, like: Path | None = None) -> bool:
        """Hors Windows : droits POSIX 0600 (proprietaire seul)."""
        try:
            path.chmod(0o600)
            return True
        except OSError:
            return False
