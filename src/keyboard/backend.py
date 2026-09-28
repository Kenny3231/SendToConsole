"""
backend.py - Aiguille vers l'implementation reelle (Windows, win32_api) ou
vers une implementation factice (autres OS, pour developper/tester
l'interface graphique). La logique d'injection reelle reste entierement
dans win32_api.py - ce module ne fait que choisir laquelle exposer, sous les
memes noms, pour que le reste du code (engine.py, l'UI) n'ait jamais besoin
de savoir sur quel OS il tourne.
"""

from __future__ import annotations

import os
import sys

# STC_SIMULATE=1 force l'implementation factice meme sous Windows : c'est ce
# qui permet aux tests (tests/conftest.py) de tourner SANS injecter de vraies
# frappes dans la session de l'operateur. Jamais defini en usage normal.
_USE_REAL = sys.platform == "win32" and not os.environ.get("STC_SIMULATE")

if _USE_REAL:
    from .win32_api import (  # noqa: F401
        WinInfo,
        list_windows,
        force_foreground,
        get_foreground_window,
        title_of,
        belongs_to_process,
        is_elevated_above_us,
        allow_foreground_switch,
        acquire_instance_mutex,
        release_instance_mutex,
        current_layout,
        layout_of_window,
        layout_name,
        load_keyboard_layout,
        translate_char,
        needs_altgr,
        altgr_char_of_vk,
        send_scan,
        send_char_scan,
        send_char_unicode,
        release_held_modifiers,
        modifiers_down,
        ALT_GR_MIN_DELAY_MS,
        SC_ENTER,
        SC_TAB,
    )

    IS_REAL = True

else:
    from dataclasses import dataclass
    from typing import Optional

    @dataclass
    class WinInfo:
        hwnd: int
        pid: int
        process_name: str
        title: str

    IS_REAL = False
    SC_ENTER = 0x1C
    SC_TAB = 0x0F
    ALT_GR_MIN_DELAY_MS = 60

    # Fenetres factices : permettent de developper et visualiser l'UI hors
    # Windows. Aucune frappe n'est reellement envoyee dans cette branche.
    _FAKE_WINDOWS = [
        WinInfo(1001, 111, "chrome.exe", "iDRAC - Console distante (exemple)"),
        WinInfo(1002, 222, "mstsc.exe", "Connexion Bureau a distance (exemple)"),
        WinInfo(1003, 333, "notepad.exe", "Sans titre - Bloc-notes (exemple)"),
    ]
    _fg_hwnd = _FAKE_WINDOWS[0].hwnd

    # Jeu de caracteres que la "disposition" simulee sait produire au
    # scancode : tout le reste doit emprunter le repli Unicode, ce qui rend
    # ce chemin testable hors Windows.
    _TYPEABLE = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789 &~\"#'{([-|`_\\^@)]=+}/*.,;:!?<>%$£µ§°"
    )

    sent_unicode: list[str] = []
    sent_scan: list[str] = []
    #: Modificateurs que la simulation considere comme enfonces, pour
    #: pouvoir tester le scenario du raccourci global encore tenu.
    held_modifiers: list[str] = []

    def list_windows() -> list[WinInfo]:
        return list(_FAKE_WINDOWS)

    def force_foreground(hwnd: int) -> bool:
        global _fg_hwnd
        _fg_hwnd = hwnd
        return True

    def get_foreground_window() -> int:
        return _fg_hwnd

    def title_of(hwnd: int) -> str:
        for w in _FAKE_WINDOWS:
            if w.hwnd == hwnd:
                return w.title
        return ""

    def belongs_to_process(hwnd: int, pid: int) -> bool:
        return False

    #: Fenetres factices « lancees en administrateur » (tests) : hwnd -> etat
    #: rendu par is_elevated_above_us (True certain, None probable).
    _FAKE_ELEVATED: dict[int, Optional[bool]] = {}

    def is_elevated_above_us(hwnd: int) -> Optional[bool]:
        return _FAKE_ELEVATED.get(hwnd, False)

    def allow_foreground_switch() -> None:
        return None

    #: Mutex simules (dans ce processus seulement).
    _FAKE_MUTEXES: set[str] = set()

    def acquire_instance_mutex(name: str) -> bool:
        if name in _FAKE_MUTEXES:
            return False
        _FAKE_MUTEXES.add(name)
        return True

    def release_instance_mutex(name: str) -> None:
        _FAKE_MUTEXES.discard(name)

    def current_layout() -> int:
        return 0x040C040C          # francais, pour la simulation

    def layout_of_window(hwnd: int) -> int:
        # La fenetre 1003 simule une application dont la langue de saisie
        # differe de la notre - exactement le cas qui produisait des
        # caracteres faux.
        return 0x04090409 if hwnd == 1003 else 0x040C040C

    def layout_name(hkl: int) -> str:
        return {0x040C040C: "fr_FR (0x040C)",
                0x04090409: "en_US (0x0409)"}.get(hkl, f"0x{hkl:08X}")

    def load_keyboard_layout(klid: str) -> Optional[int]:
        return int(klid, 16) or None

    def translate_char(desired: str, hkl_target: int, hkl_local: int) -> Optional[str]:
        return desired

    def needs_altgr(ch: str, hkl_local: int) -> bool:
        return False

    #: Simulation fr_FR : AltGr+0 = @, AltGr+E = EUR (pour tester le refus
    #: des raccourcis globaux qui confisqueraient un caractere AltGr).
    _FAKE_ALTGR = {0x30: "@", 0x45: "€"}

    def altgr_char_of_vk(vk: int, hkl: int) -> str:
        return _FAKE_ALTGR.get(vk, "")

    def send_scan(scan: int, extended: bool) -> bool:
        return True

    def modifiers_down() -> list[str]:
        return list(held_modifiers)

    def release_held_modifiers() -> list[str]:
        released = list(held_modifiers)
        held_modifiers.clear()
        return released

    def send_char_scan(ch: str, hkl_local: int) -> bool:
        if ch not in _TYPEABLE:
            return False
        # Reproduit le bug : un modificateur encore enfonce transforme la
        # frappe en raccourci, et rien ne s'ecrit.
        if held_modifiers and ch.isalnum():
            return True
        sent_scan.append(ch)
        return True

    def send_char_unicode(ch: str) -> bool:
        sent_unicode.append(ch)
        return True
