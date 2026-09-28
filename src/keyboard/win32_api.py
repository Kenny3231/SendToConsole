"""
win32_api.py - Moteur d'injection clavier bas niveau (SendInput/scancode).

Porte depuis le script PowerShell d'origine (historique git 68f1fc4 : classe C# embarquee
ConsoleInjectWin32). Toute la logique AltGr, conversion de disposition
clavier et ciblage de fenetre par HWND est reprise du script PowerShell
d'origine - s'y referer avant de modifier cette logique.

Fonctionne uniquement sous Windows (ctypes.WinDLL). Ne s'importe pas sur
Linux/macOS : les appels a WinDLL levent une AttributeError des le chargement
du module, volontairement (pas de mode degrade silencieux).
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

HKL = wintypes.HANDLE
WCHAR = ctypes.c_wchar
ULONG_PTR = ctypes.c_size_t

# ---------------------------------------------------------------------------
# Structures SendInput
# ---------------------------------------------------------------------------

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]


INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14
SW_RESTORE = 9

MAPVK_VK_TO_VSC = 0
MAPVK_VSC_TO_VK = 1

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SPACE = 0x20

# Scancodes des modificateurs et touches usuelles (identiques au script PowerShell d'origine)
SC_LSHIFT = 0x2A
SC_LCTRL = 0x1D
SC_LALT = 0x38
SC_RALT = 0x38   # + etendue = AltGr
SC_ENTER = 0x1C
SC_TAB = 0x0F

# Une pression physique sur AltGr genere LCtrl PUIS RAlt etendue. Injecter
# RAlt seule ne bascule pas toujours la disposition en etat AltGr, d'ou ce
# comportement par defaut - commutable si une console reagit mal au Ctrl.
ALT_GR_WITH_CTRL = True

#: Respiration entre les trois temps d'un caractere AltGr (secondes).
#: Quelques millisecondes suffisent et restent imperceptibles, mais sans
#: elles une suite de caracteres AltGr se perd (voir send_char_scan).
ALT_GR_SETTLE = 0.012

#: Delai minimal APRES un caractere AltGr, quel que soit le reglage de
#: l'operateur : c'est le temps qu'il faut a l'application pour revenir a un
#: etat clavier neutre avant la frappe suivante.
ALT_GR_MIN_DELAY_MS = 60

# ---------------------------------------------------------------------------
# Prototypes Win32
# ---------------------------------------------------------------------------

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = (EnumWindowsProc, wintypes.LPARAM)
user32.EnumWindows.restype = wintypes.BOOL

user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindowVisible.restype = wintypes.BOOL

user32.IsWindow.argtypes = (wintypes.HWND,)
user32.IsWindow.restype = wintypes.BOOL

user32.IsIconic.argtypes = (wintypes.HWND,)
user32.IsIconic.restype = wintypes.BOOL

user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextLengthW.restype = ctypes.c_int

user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.restype = ctypes.c_int

user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.GetForegroundWindow.restype = wintypes.HWND

user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.restype = wintypes.BOOL

user32.BringWindowToTop.argtypes = (wintypes.HWND,)
user32.BringWindowToTop.restype = wintypes.BOOL

user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.ShowWindow.restype = wintypes.BOOL

user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
user32.AttachThreadInput.restype = wintypes.BOOL

user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetWindowLongW.restype = ctypes.c_long

kernel32.GetCurrentThreadId.restype = wintypes.DWORD

dwmapi.DwmGetWindowAttribute.argtypes = (
    wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

user32.LoadKeyboardLayoutW.argtypes = (wintypes.LPCWSTR, wintypes.UINT)
user32.LoadKeyboardLayoutW.restype = HKL

user32.GetKeyboardLayout.argtypes = (wintypes.DWORD,)
user32.GetKeyboardLayout.restype = HKL

user32.VkKeyScanExW.argtypes = (WCHAR, HKL)
user32.VkKeyScanExW.restype = ctypes.c_short

user32.MapVirtualKeyExW.argtypes = (wintypes.UINT, wintypes.UINT, HKL)
user32.MapVirtualKeyExW.restype = wintypes.UINT

user32.ToUnicodeEx.argtypes = (
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_ubyte),
    wintypes.LPWSTR, ctypes.c_int, wintypes.UINT, HKL)
user32.ToUnicodeEx.restype = ctypes.c_int

user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
user32.RegisterHotKey.restype = wintypes.BOOL

user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short

user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
user32.UnregisterHotKey.restype = wintypes.BOOL


# ---------------------------------------------------------------------------
# Fenetres
# ---------------------------------------------------------------------------

@dataclass
class WinInfo:
    hwnd: int
    pid: int
    process_name: str
    title: str


def _process_names() -> dict[int, str]:
    """Table pid -> nom de processus (equiv. Get-Process). psutil optionnel :
    en son absence, les processus s'affichent avec le nom '?'."""
    try:
        import psutil
    except ImportError:
        return {}
    names: dict[int, str] = {}
    for p in psutil.process_iter(["pid", "name"]):
        try:
            names[p.info["pid"]] = p.info["name"]
        except Exception:
            continue
    return names


def list_windows() -> list[WinInfo]:
    """Equivalent de Get-AllWindow : fenetres de premier niveau visibles,
    hors fenetres-outils et fenetres masquees (cloaked) par DWM."""
    results: list[WinInfo] = []
    names = _process_names()

    def callback(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True

        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True

        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if ex_style & WS_EX_TOOLWINDOW:
            return True

        cloaked = ctypes.c_int(0)
        hr = dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if hr == 0 and cloaked.value != 0:
            return True

        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)

        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        results.append(WinInfo(
            hwnd=int(hwnd),
            pid=int(pid.value),
            process_name=names.get(int(pid.value), "?"),
            title=buf.value,
        ))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return results


def force_foreground(hwnd: int) -> bool:
    """Porte ForceForeground : restaure la fenetre si reduite, puis effectue
    la danse AttachThreadInput necessaire pour voler le focus depuis un
    thread different (indispensable pour SetForegroundWindow hors contexte)."""
    hwnd = wintypes.HWND(hwnd)
    if not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    fg = user32.GetForegroundWindow()
    fg_pid = wintypes.DWORD(0)
    fg_thread = user32.GetWindowThreadProcessId(fg, ctypes.byref(fg_pid))
    cur_thread = kernel32.GetCurrentThreadId()

    attached = False
    if fg_thread != cur_thread:
        attached = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))

    user32.BringWindowToTop(hwnd)
    ok = bool(user32.SetForegroundWindow(hwnd))

    if attached:
        user32.AttachThreadInput(cur_thread, fg_thread, False)

    return ok or user32.GetForegroundWindow() == hwnd


def _handle(value) -> int:
    """Convertit un handle Win32 renvoye par ctypes en entier.

    HWND et HKL sont declares c_void_p : ctypes renvoie None - et non 0 -
    quand le pointeur est nul, et int(None) leve une TypeError. Or un
    handle nul est un etat NORMAL : GetForegroundWindow() ne renvoie rien
    pendant un verrouillage de session, une invite UAC, un changement de
    bureau ou une simple transition entre fenetres. Sans cette conversion,
    le suivi de la fenetre de travail levait une exception toutes les
    150 ms et inondait la console.
    """
    return int(value) if value else 0


def get_foreground_window() -> int:
    return _handle(user32.GetForegroundWindow())


def title_of(hwnd: int) -> str:
    hwnd = wintypes.HWND(hwnd)
    if not hwnd or not user32.IsWindow(hwnd):
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def belongs_to_process(hwnd: int, pid: int) -> bool:
    if not hwnd:
        return False
    owner = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(owner))
    return owner.value == pid


# ---------------------------------------------------------------------------
# Niveau d'integrite (elevation) de la fenetre visee
# ---------------------------------------------------------------------------
# SendInput est BLOQUE en silence (UIPI) vers un processus d'integrite
# superieure : la console lancee « en administrateur » ne recoit rien, sans
# aucune erreur. On le detecte pour prevenir l'operateur.

advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_INTEGRITY_LEVEL_CLASS = 25
ERROR_ACCESS_DENIED = 5

kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetCurrentProcess.argtypes = ()
kernel32.GetCurrentProcess.restype = wintypes.HANDLE

advapi32.OpenProcessToken.argtypes = (
    wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
advapi32.OpenProcessToken.restype = wintypes.BOOL
advapi32.GetTokenInformation.argtypes = (
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD))
advapi32.GetTokenInformation.restype = wintypes.BOOL
advapi32.GetSidSubAuthorityCount.argtypes = (ctypes.c_void_p,)
advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
advapi32.GetSidSubAuthority.argtypes = (ctypes.c_void_p, wintypes.DWORD)
advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

#: Marqueur interne : acces refuse (ne vaut pas « niveau inconnu »).
_DENIED = -1


def _integrity_of_process(hproc: int) -> Optional[int]:
    """RID du niveau d'integrite (0x2000 moyen, 0x3000 eleve...), _DENIED
    si Windows refuse la lecture du jeton, None si illisible autrement."""
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(hproc, TOKEN_QUERY, ctypes.byref(token)):
        return _DENIED if ctypes.get_last_error() == ERROR_ACCESS_DENIED else None
    try:
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation(
            token, TOKEN_INTEGRITY_LEVEL_CLASS, None, 0, ctypes.byref(size))
        if not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
                token, TOKEN_INTEGRITY_LEVEL_CLASS, buf, size,
                ctypes.byref(size)):
            return None
        # TOKEN_MANDATORY_LABEL commence par SID_AND_ATTRIBUTES { PSID; ... }.
        psid = ctypes.c_void_p.from_buffer(buf).value
        if not psid:
            return None
        count = advapi32.GetSidSubAuthorityCount(psid)[0]
        if count == 0:
            return None
        return int(advapi32.GetSidSubAuthority(psid, count - 1)[0])
    finally:
        kernel32.CloseHandle(token)


user32.AllowSetForegroundWindow.argtypes = (wintypes.DWORD,)
user32.AllowSetForegroundWindow.restype = wintypes.BOOL
ASFW_ANY = 0xFFFFFFFF


def allow_foreground_switch() -> None:
    """Autorise un autre processus (l'instance deja lancee) a passer au
    premier plan : Windows l'interdit a un processus d'arriere-plan, sauf si
    le processus au premier plan (celui qu'on vient de lancer) le permet."""
    user32.AllowSetForegroundWindow(ASFW_ANY)


kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL,
                                  wintypes.LPCWSTR)
kernel32.CreateMutexW.restype = wintypes.HANDLE
ERROR_ALREADY_EXISTS = 183
_instance_mutexes: dict[str, int] = {}


def acquire_instance_mutex(name: str) -> bool:
    """Mutex nomme (espace de la session) : False si une autre instance le
    detient deja. Garde contre deux lancements simultanes, que le pipe
    nomme de QLocalServer ne detecte pas sous Windows. En cas d'erreur
    inattendue, True : ne jamais empecher le demarrage sur un doute."""
    if name in _instance_mutexes:
        return True
    handle = kernel32.CreateMutexW(None, False, f"Local\\{name}")
    if not handle:
        # Le mutex EXISTE mais son ACL nous le refuse (premiere instance
        # lancee en administrateur) : il est donc deja pris.
        return ctypes.get_last_error() != ERROR_ACCESS_DENIED
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _instance_mutexes[name] = handle        # garde jusqu'a la fin du processus
    return True


def release_instance_mutex(name: str) -> None:
    handle = _instance_mutexes.pop(name, None)
    if handle:
        kernel32.CloseHandle(handle)


#: Niveau « eleve » (administrateur) : au-dela, un acces refuse vient d'un
#: autre compte ou d'un service, pas d'une elevation que l'on pourrait
#: rattraper en relancant l'outil en administrateur.
INTEGRITY_HIGH = 0x3000


def is_elevated_above_us(hwnd: int) -> Optional[bool]:
    """La fenetre `hwnd` tourne-t-elle a une integrite superieure a l'outil
    (donc SendInput bloque en silence) ?

    True  : certain (niveau lu, superieur au notre) ;
    None  : probable (Windows refuse l'acces au processus ou a son jeton :
            cible en administrateur, ou lancee sous un autre compte) ;
    False : non, ou fenetre invalide.

    Ne leve jamais : sert seulement a avertir, et peut etre appele depuis le
    filtre d'evenements natif (une exception y annulerait l'envoi).
    """
    try:
        return _is_elevated_above_us(hwnd)
    except Exception:
        return False


def _is_elevated_above_us(hwnd: int) -> Optional[bool]:
    if not hwnd:
        return False
    own = _integrity_of_process(kernel32.GetCurrentProcess())
    if own is None or own == _DENIED:
        return False
    # Deja administrateur : un refus d'acces ne vient plus d'une elevation.
    probable = None if own < INTEGRITY_HIGH else False
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    if not pid.value:
        return False
    hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False,
                                 pid.value)
    if not hproc:
        return probable if ctypes.get_last_error() == ERROR_ACCESS_DENIED \
            else False
    try:
        level = _integrity_of_process(hproc)
    finally:
        kernel32.CloseHandle(hproc)
    if level == _DENIED:
        return probable
    return level is not None and level > own


# ---------------------------------------------------------------------------
# Dispositions clavier
# ---------------------------------------------------------------------------

def current_layout() -> int:
    """Disposition du thread de CET outil. Ne sert que d'information : ce qui
    compte pour taper, c'est la disposition de la fenetre visee - voir
    layout_of_window()."""
    return _handle(user32.GetKeyboardLayout(0))


def layout_of_window(hwnd: int) -> int:
    """Disposition active dans le thread proprietaire de la fenetre visee.

    C'est LA reference correcte pour calculer les scancodes : la fenetre qui
    recoit nos scancodes les interprete avec SA disposition, pas avec la
    notre. Windows memorise la langue de saisie par application - notre
    fenetre peut donc etre en francais pendant que Notepad++ est en anglais,
    et tout ressort faux si l'on se fie a notre propre disposition.
    """
    if not hwnd:
        return 0
    tid = user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), None)
    if not tid:
        return 0
    return _handle(user32.GetKeyboardLayout(tid))


def layout_name(hkl: int) -> str:
    """Nom lisible d'une disposition, ex. 'fr_FR (0x040C)'."""
    if not hkl:
        return "inconnue"
    langid = hkl & 0xFFFF
    try:
        import locale as _locale
        name = _locale.windows_locale.get(langid)
    except Exception:
        name = None
    return f"{name} (0x{langid:04X})" if name else f"0x{langid:04X}"


def load_keyboard_layout(klid: str) -> Optional[int]:
    """klid : chaine hexadecimale a 8 chiffres, ex. '0000040C' (francais)."""
    hkl = user32.LoadKeyboardLayoutW(klid, 0x0080)  # KLF_NOTELLSHELL
    return hkl if hkl else None


def _flush_dead_key(hkl: int) -> None:
    ks = (ctypes.c_ubyte * 256)()
    buf = ctypes.create_unicode_buffer(8)
    sc = user32.MapVirtualKeyExW(VK_SPACE, MAPVK_VK_TO_VSC, hkl)
    user32.ToUnicodeEx(VK_SPACE, sc, ks, buf, 8, 0, hkl)
    user32.ToUnicodeEx(VK_SPACE, sc, ks, buf, 8, 0, hkl)


def translate_char(desired: str, hkl_target: int, hkl_local: int) -> Optional[str]:
    """Quel caractere taper localement pour obtenir 'desired' sur la
    disposition cible. Porte TranslateChar du script PowerShell d'origine."""
    if not hkl_target or not hkl_local:
        return None

    vks = user32.VkKeyScanExW(desired, hkl_target)
    if vks == -1:
        return None

    vk_target = vks & 0xFF
    shift = (vks >> 8) & 0xFF

    sc = user32.MapVirtualKeyExW(vk_target, MAPVK_VK_TO_VSC, hkl_target)
    if sc == 0:
        return None

    vk_local = user32.MapVirtualKeyExW(sc, MAPVK_VSC_TO_VK, hkl_local)
    if vk_local == 0:
        return None

    ks = (ctypes.c_ubyte * 256)()
    if shift & 1:
        ks[VK_SHIFT] = 0x80
    if shift & 2:
        ks[VK_CONTROL] = 0x80
    if shift & 4:
        ks[VK_MENU] = 0x80

    buf = ctypes.create_unicode_buffer(8)
    r = user32.ToUnicodeEx(vk_local, sc, ks, buf, 8, 0, hkl_local)
    _flush_dead_key(hkl_local)

    if r <= 0 or len(buf.value) == 0:
        return None
    return buf.value[0]


def shift_state_for(ch: str, hkl_local: int) -> int:
    vks = user32.VkKeyScanExW(ch, hkl_local)
    if vks == -1:
        return -1
    return (vks >> 8) & 0xFF


def altgr_char_of_vk(vk: int, hkl: int) -> str:
    """Caractere que produit AltGr (Ctrl+Alt) + `vk` sur la disposition
    `hkl`, ou "" s'il n'en produit aucun. Sert a refuser un raccourci global
    qui confisquerait ce caractere (et que l'outil declencherait lui-meme en
    injectant AltGr). Flag 0x4 : ne pas toucher a l'etat clavier du noyau
    (touches mortes en cours)."""
    state = (ctypes.c_ubyte * 256)()
    for key in (0x11, 0x12, 0xA2, 0xA5):     # Ctrl, Alt, LCtrl, RAlt
        state[key] = 0x80
    scan = user32.MapVirtualKeyExW(vk, MAPVK_VK_TO_VSC, hkl)
    buf = ctypes.create_unicode_buffer(8)
    count = user32.ToUnicodeEx(vk, scan, state, buf, len(buf), 0x4, hkl)
    if count == 0:
        return ""
    # count < 0 : touche morte (le caractere mort est dans buf[0]).
    text = buf.value[:abs(count)] if count > 0 else buf.value[:1]
    return text if text and text.isprintable() else ""


def needs_altgr(ch: str, hkl_local: int) -> bool:
    st = shift_state_for(ch, hkl_local)
    if st < 0:
        return False
    return bool(st & 2) and bool(st & 4)


# ---------------------------------------------------------------------------
# Injection par scancode
# ---------------------------------------------------------------------------

def _make_key(scan: int, extended: bool, key_up: bool) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki.wVk = 0   # scancode seul : wVk doit etre nul
    inp.u.ki.wScan = scan
    inp.u.ki.dwFlags = (
        KEYEVENTF_SCANCODE
        | (KEYEVENTF_EXTENDEDKEY if extended else 0)
        | (KEYEVENTF_KEYUP if key_up else 0)
    )
    inp.u.ki.time = 0
    inp.u.ki.dwExtraInfo = 0
    return inp


def _dispatch(seq: list[INPUT]) -> bool:
    if not seq:
        return False
    arr = (INPUT * len(seq))(*seq)
    sent = user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))
    return sent == len(seq)


def send_scan(scan: int, extended: bool) -> bool:
    """Frappe d'une touche nue designee par son scancode (ENTREE, TAB...)."""
    seq = [_make_key(scan, extended, False), _make_key(scan, extended, True)]
    return _dispatch(seq)


def _make_unicode(unit: int, key_up: bool) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki.wVk = 0
    inp.u.ki.wScan = unit
    inp.u.ki.dwFlags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if key_up else 0)
    inp.u.ki.time = 0
    inp.u.ki.dwExtraInfo = 0
    return inp


# (nom lisible, code de touche virtuelle, scancode, touche etendue)
_MODIFIER_KEYS = (
    ("Maj gauche", 0xA0, 0x2A, False),
    ("Maj droite", 0xA1, 0x36, False),
    ("Ctrl gauche", 0xA2, 0x1D, False),
    ("Ctrl droite", 0xA3, 0x1D, True),
    ("Alt gauche", 0xA4, 0x38, False),
    ("Alt droite", 0xA5, 0x38, True),
    ("Win gauche", 0x5B, 0x5B, True),
    ("Win droite", 0x5C, 0x5C, True),
)


def modifiers_down() -> list[str]:
    """Modificateurs physiquement enfonces a cet instant."""
    return [name for name, vk, _scan, _ext in _MODIFIER_KEYS
            if user32.GetAsyncKeyState(vk) & 0x8000]


def release_held_modifiers() -> list[str]:
    """Relache les modificateurs encore physiquement enfonces.

    Indispensable avant un envoi declenche par un raccourci global : au
    moment ou WM_HOTKEY arrive, l'operateur tient encore Alt (ou Ctrl). Tout
    ce qu'on tape ensuite est alors interprete comme un raccourci - "a"
    devient Alt+a, un accelerateur de menu qui n'ecrit rien.

    Un relachement d'Alt ne suffit pas : RegisterHotKey CONSOMME la touche du
    raccourci, l'application ne voit donc jamais qu'une touche a ete pressee
    pendant qu'Alt etait enfonce. Elle ne recoit que le relachement, le prend
    pour un appui isole sur Alt, et ACTIVE SA BARRE DE MENUS - le premier
    caractere tape ensuite part dans le menu au lieu du texte. On envoie donc
    d'abord une frappe anodine (Ctrl) pendant qu'Alt est encore enfonce :
    c'est le "masque" qui empeche cette interpretation.

    On relache sans re-enfoncer : la touche physique produira de toute facon
    son propre KEYUP quand l'operateur la lachera.
    """
    held = modifiers_down()
    if not held:
        return []

    seq: list[INPUT] = []
    if any("Alt" in name for name in held):
        seq.append(_make_key(SC_LCTRL, False, False))
        seq.append(_make_key(SC_LCTRL, False, True))

    for name, _vk, scan, extended in _MODIFIER_KEYS:
        if name in held:
            seq.append(_make_key(scan, extended, True))

    _dispatch(seq)
    return held


def send_char_unicode(ch: str) -> bool:
    """Injection directe du caractere Unicode, sans passer par une touche.

    Filet de securite pour les caracteres qu'aucune touche de la disposition
    ne produit : ils etaient auparavant abandonnes en silence. Fonctionne
    dans les applications Windows ordinaires ; une console distante qui ne
    lit que les scancodes physiques peut l'ignorer - d'ou la trace dans le
    journal a chaque recours.
    """
    data = ch.encode("utf-16-le")
    seq: list[INPUT] = []
    for i in range(0, len(data), 2):
        unit = data[i] | (data[i + 1] << 8)
        seq.append(_make_unicode(unit, False))
        seq.append(_make_unicode(unit, True))
    return _dispatch(seq)


def send_char_scan(ch: str, hkl_local: int) -> bool:
    """Frappe du caractere 'ch' via le scancode qui le produit sous la
    disposition 'hkl_local', modificateurs compris (dont AltGr = Alt droite
    etendue).

    ATTENTION : 'hkl_local' doit etre la disposition de la FENETRE VISEE
    (layout_of_window), pas celle de notre propre thread. C'est la fenetre
    receptrice qui interprete les scancodes ; se fier a notre disposition
    donne des caracteres faux des que les deux different.
    """
    vks = user32.VkKeyScanExW(ch, hkl_local)
    if vks == -1:
        return False

    vk = vks & 0xFF
    shift = (vks >> 8) & 0xFF

    sc = user32.MapVirtualKeyExW(vk, MAPVK_VK_TO_VSC, hkl_local)
    if sc == 0:
        return False

    need_shift = bool(shift & 1)
    need_ctrl = bool(shift & 2)
    need_alt = bool(shift & 4)
    alt_gr = need_ctrl and need_alt

    press: list[INPUT] = []
    if need_shift:
        press.append(_make_key(SC_LSHIFT, False, False))

    if alt_gr:
        if ALT_GR_WITH_CTRL:
            press.append(_make_key(SC_LCTRL, False, False))
        press.append(_make_key(SC_RALT, True, False))   # Alt droite etendue
    else:
        if need_ctrl:
            press.append(_make_key(SC_LCTRL, False, False))
        if need_alt:
            press.append(_make_key(SC_LALT, False, False))

    stroke = [_make_key(sc, False, False), _make_key(sc, False, True)]

    release: list[INPUT] = []
    if alt_gr:
        release.append(_make_key(SC_RALT, True, True))
        if ALT_GR_WITH_CTRL:
            release.append(_make_key(SC_LCTRL, False, True))
    else:
        if need_alt:
            release.append(_make_key(SC_LALT, False, True))
        if need_ctrl:
            release.append(_make_key(SC_LCTRL, False, True))

    if need_shift:
        release.append(_make_key(SC_LSHIFT, False, True))

    if not alt_gr:
        return _dispatch(press + stroke + release)

    # AltGr en trois temps, avec une respiration entre chaque.
    # Envoyer Ctrl+AltGr, la touche et le relachement dans un seul lot, puis
    # recommencer 15 ms plus tard pour le caractere suivant, va trop vite
    # pour la machine a etats du clavier : sur une suite de caracteres AltGr
    # (@ # € en AZERTY), le premier passait et les suivants etaient perdus.
    ok = _dispatch(press)
    time.sleep(ALT_GR_SETTLE)
    ok = _dispatch(stroke) and ok
    time.sleep(ALT_GR_SETTLE)
    return _dispatch(release) and ok
