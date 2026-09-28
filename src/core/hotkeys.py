"""
hotkeys.py - Raccourcis clavier GLOBAUX (RegisterHotKey).

Globaux = ils repondent meme quand le focus est dans la console distante,
ce qui est tout l'interet ici. Porte de l'onglet 5 du script PowerShell d'origine (git 68f1fc4), ou un
HotkeySink (NativeWindow) empilait les WM_HOTKEY.

En PySide6 on n'a pas besoin d'une fenetre cachee dedidee : les raccourcis
sont enregistres sur le HWND de la fenetre principale, et un
QAbstractNativeEventFilter intercepte WM_HOTKEY dans la boucle Qt.

Deux entrees ne peuvent pas partager la meme combinaison : is_taken() le
verifie a la saisie, avant meme que Windows ne refuse silencieusement le
second RegisterHotKey.

En plus des entrees, deux ACTIONS de l'outil ont leur raccourci global
(regle dans Options) : cibler la fenetre active, afficher/masquer la
palette. hotkey_owner() couvre les deux familles pour les conflits.
"""

from __future__ import annotations

import sys
from typing import Callable, Optional

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

from core.models import QuickEntry

WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000   # un appui maintenu ne declenche qu'une fois

IS_WINDOWS = sys.platform == "win32"

#: Actions de l'outil declenchables par raccourci global.
ACTION_CAPTURE_TARGET = "capture_target"
ACTION_TOGGLE_PALETTE = "toggle_palette"
ACTION_LABELS = {
    ACTION_CAPTURE_TARGET: "Cibler la fenêtre active",
    ACTION_TOGGLE_PALETTE: "Afficher / masquer la palette",
}

if IS_WINDOWS:
    from keyboard.win32_api import user32
else:
    user32 = None


def format_hotkey(mods: int, vk: int) -> str:
    """Rend une combinaison lisible : 'Ctrl+Maj+F1'."""
    if mods == 0 or vk == 0:
        return "(aucun)"

    parts: list[str] = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_SHIFT:
        parts.append("Maj")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_WIN:
        parts.append("Win")
    parts.append(vk_name(vk))
    return "+".join(parts)


def vk_name(vk: int) -> str:
    if 0x70 <= vk <= 0x87:                      # F1..F24
        return f"F{vk - 0x6F}"
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:  # 0-9, A-Z
        return chr(vk)
    special = {
        0x20: "Espace", 0x0D: "Entree", 0x09: "Tab", 0x08: "Retour",
        0x2D: "Inser", 0x2E: "Suppr", 0x24: "Origine", 0x23: "Fin",
        0x21: "PgPrec", 0x22: "PgSuiv",
        0x25: "Gauche", 0x26: "Haut", 0x27: "Droite", 0x28: "Bas",
    }
    return special.get(vk, f"0x{vk:02X}")


#: Deux WM_HOTKEY separes de moins de ce delai designent le meme appui :
#: un appui un peu long, ou un rebond, ne doit pas declencher deux envois.
DEBOUNCE_MS = 400


class HotkeyManager(QObject, QAbstractNativeEventFilter):
    """Enregistre les raccourcis et emet triggered(entry, hwnd) quand l'un
    tombe. 'hwnd' est la fenetre au premier plan a l'instant precis du
    declenchement : c'est celle que l'operateur regarde, et donc la seule
    cible correcte."""

    triggered = Signal(object, int)        # (QuickEntry, hwnd au premier plan)
    registration_failed = Signal(object)   # QuickEntry refusee par Windows
    action_triggered = Signal(str, int)    # (ACTION_*, hwnd au premier plan)
    action_registration_failed = Signal(str)   # ACTION_* perdue a la reprise

    def __init__(self, parent: QObject | None = None) -> None:
        QObject.__init__(self, parent)
        QAbstractNativeEventFilter.__init__(self)
        self._hwnd: int = 0
        self._by_id: dict[int, QuickEntry] = {}
        self._next_id: int = 1
        self._last_fire: dict[int, int] = {}
        self._actions: dict[int, str] = {}        # id -> ACTION_*
        self._action_ids: dict[str, int] = {}     # ACTION_* -> id
        self._action_combos: dict[str, tuple[int, int]] = {}
        # Suspendu pendant la saisie d'une combinaison (HotkeyEdit) : sinon
        # Windows consomme une combinaison deja enregistree AVANT le champ, et
        # l'entree correspondante part vers la derniere fenetre de travail.
        self._suspended = False

    def attach(self, hwnd: int) -> None:
        self._hwnd = hwnd

    @property
    def is_attached(self) -> bool:
        return bool(self._hwnd)

    # ------------------------------------------------- appels Windows bruts --
    def _win_register(self, hotkey_id: int, mods: int, vk: int) -> bool:
        """RegisterHotKey. Pendant une suspension, on verifie seulement que
        Windows accepterait (enregistre puis libere aussitot) : resume()
        l'enregistrera pour de bon."""
        ok = bool(user32.RegisterHotKey(
            self._hwnd, hotkey_id, mods | MOD_NOREPEAT, vk))
        if ok and self._suspended:
            user32.UnregisterHotKey(self._hwnd, hotkey_id)
        return ok

    def _win_unregister(self, hotkey_id: int) -> None:
        if IS_WINDOWS and self._hwnd and not self._suspended:
            user32.UnregisterHotKey(self._hwnd, hotkey_id)

    # ------------------------------------------------------ enregistrement --
    def register(self, entry: QuickEntry) -> bool:
        if not entry.has_hotkey:
            entry.registered = False
            return True
        if not IS_WINDOWS or not self._hwnd:
            entry.registered = False
            return False

        hotkey_id = self._next_id
        self._next_id += 1

        ok = self._win_register(hotkey_id, entry.mods, entry.vk)

        entry.registered = ok
        if ok:
            entry.hotkey_id = hotkey_id
            self._by_id[hotkey_id] = entry
        else:
            entry.hotkey_id = 0
            self.registration_failed.emit(entry)
        return ok

    def unregister(self, entry: QuickEntry) -> None:
        if not entry.registered or not entry.hotkey_id:
            return
        self._win_unregister(entry.hotkey_id)
        self._by_id.pop(entry.hotkey_id, None)
        entry.registered = False
        entry.hotkey_id = 0

    def register_all(self, entries: list[QuickEntry]) -> None:
        for entry in entries:
            self.register(entry)

    def unregister_all(self, entries: list[QuickEntry]) -> None:
        # Les raccourcis globaux sont enregistres aupres du systeme : ne pas
        # les liberer les laisserait confisques jusqu'a la fin de la session
        # Windows (remarque heritee du script PowerShell d'origine).
        for entry in entries:
            self.unregister(entry)

    # ------------------------------------------------------------ actions --
    def register_action(self, action: str, mods: int, vk: int) -> bool:
        """(Re)lie une action a une combinaison. (0, 0) = aucun raccourci :
        l'ancien est libere et True est rendu. False si Windows refuse
        (combinaison deja prise par une autre application) : l'ANCIEN
        raccourci reste alors actif (le nouveau est tente avant de liberer
        l'ancien, qui ne peut donc pas etre vole entre-temps)."""
        if mods == 0 or vk == 0:
            self.unregister_action(action)
            return True
        if not IS_WINDOWS or not self._hwnd:
            return False
        if self._action_combos.get(action) == (mods, vk) \
                and action in self._action_ids:
            return True                     # deja en place

        hotkey_id = self._next_id
        self._next_id += 1
        if not self._win_register(hotkey_id, mods, vk):
            return False
        self.unregister_action(action)
        self._actions[hotkey_id] = action
        self._action_ids[action] = hotkey_id
        self._action_combos[action] = (mods, vk)
        return True

    def unregister_action(self, action: str) -> None:
        hotkey_id = self._action_ids.pop(action, 0)
        self._action_combos.pop(action, None)
        if not hotkey_id:
            return
        self._actions.pop(hotkey_id, None)
        self._win_unregister(hotkey_id)

    def unregister_all_actions(self) -> None:
        for action in list(self._action_ids):
            self.unregister_action(action)

    def is_action_registered(self, action: str) -> bool:
        return action in self._action_ids

    # --------------------------------------------------------- suspension --
    @property
    def is_suspended(self) -> bool:
        return self._suspended

    def suspend(self) -> None:
        """Libere TEMPORAIREMENT toutes les combinaisons aupres de Windows
        (sans oublier lesquelles) : la saisie d'un raccourci dans un
        HotkeyEdit recoit alors vraiment les touches, et aucune entree ne
        part vers la derniere fenetre de travail."""
        if self._suspended:
            return
        if IS_WINDOWS and self._hwnd:
            for hotkey_id in list(self._by_id) + list(self._actions):
                user32.UnregisterHotKey(self._hwnd, hotkey_id)
        self._suspended = True

    def resume(self) -> None:
        """Reenregistre tout apres suspend(). Une combinaison prise entre-
        temps par une autre application est signalee (registration_failed /
        action_registration_failed) et oubliee."""
        if not self._suspended:
            return
        self._suspended = False
        if not (IS_WINDOWS and self._hwnd):
            return
        for hotkey_id, entry in list(self._by_id.items()):
            if not self._win_register(hotkey_id, entry.mods, entry.vk):
                self._by_id.pop(hotkey_id, None)
                entry.registered = False
                entry.hotkey_id = 0
                self.registration_failed.emit(entry)
        for hotkey_id, action in list(self._actions.items()):
            mods, vk = self._action_combos.get(action, (0, 0))
            if not self._win_register(hotkey_id, mods, vk):
                self._actions.pop(hotkey_id, None)
                self._action_ids.pop(action, None)
                self._action_combos.pop(action, None)
                self.action_registration_failed.emit(action)

    @staticmethod
    def is_taken(entries: list[QuickEntry], mods: int, vk: int,
                 exclude: Optional[QuickEntry] = None) -> Optional[QuickEntry]:
        if mods == 0 or vk == 0:
            return None
        for entry in entries:
            if entry is exclude:
                continue
            if entry.mods == mods and entry.vk == vk:
                return entry
        return None

    def _accept(self, hotkey_id: int) -> bool:
        """Anti-rebond : MOD_NOREPEAT ne suffit pas toujours, et un second
        WM_HOTKEY dans la foulee lancait un envoi refuse aussitot avec
        « un envoi est deja en cours »."""
        from PySide6.QtCore import QDateTime
        now = QDateTime.currentMSecsSinceEpoch()
        previous = self._last_fire.get(hotkey_id, 0)
        if now - previous < DEBOUNCE_MS:
            return False
        self._last_fire[hotkey_id] = now
        return True

    # ---------------------------------------------------- boucle d'evenements --
    def nativeEventFilter(self, event_type, message):  # noqa: N802 (API Qt)
        if not IS_WINDOWS:
            return False, 0
        if event_type != b"windows_generic_MSG":
            return False, 0

        try:
            import ctypes
            from ctypes import wintypes

            class MSG(ctypes.Structure):
                _fields_ = [
                    ("hwnd", wintypes.HWND),
                    ("message", wintypes.UINT),
                    ("wParam", ctypes.c_size_t),
                    ("lParam", ctypes.c_ssize_t),
                    ("time", wintypes.DWORD),
                    ("pt_x", ctypes.c_long),
                    ("pt_y", ctypes.c_long),
                ]

            msg = ctypes.cast(int(message), ctypes.POINTER(MSG)).contents
            if msg.message == WM_HOTKEY:
                self._dispatch(int(msg.wParam))
        except Exception:
            # Un filtre d'evenements ne doit jamais faire tomber la boucle Qt.
            return False, 0

        return False, 0

    def _dispatch(self, hotkey_id: int) -> None:
        """WM_HOTKEY recu : entree d'envoi rapide ou action de l'outil."""
        if self._suspended:
            return
        entry = self._by_id.get(hotkey_id)
        action = self._actions.get(hotkey_id)
        if (entry is None and action is None) or not self._accept(hotkey_id):
            return
        # La fenetre visee est relevee ICI, avant que Qt n'ait pu traiter
        # quoi que ce soit qui deplacerait le focus.
        from keyboard import backend as w32
        hwnd = w32.get_foreground_window()
        if entry is not None:
            self.triggered.emit(entry, hwnd)
        else:
            self.action_triggered.emit(action, hwnd)


def hotkey_owner(entries: list[QuickEntry],
                 actions: dict[str, tuple[int, int]], mods: int, vk: int, *,
                 exclude_entry: Optional[QuickEntry] = None,
                 exclude_action: Optional[str] = None) -> Optional[str]:
    """Ce qui utilise deja (mods, vk) - entree ou action -, en clair pour un
    message (« l'entree « X » »), ou None si la combinaison est libre."""
    if mods == 0 or vk == 0:
        return None
    clash = HotkeyManager.is_taken(entries, mods, vk, exclude=exclude_entry)
    if clash is not None:
        return f"l'entrée « {clash.label} »"
    for action, combo in actions.items():
        if action != exclude_action and tuple(combo) == (mods, vk):
            return f"l'action « {ACTION_LABELS.get(action, action)} »"
    return None


#: Touches qui produisent un caractere : avec Maj seul, le raccourci global
#: volerait la majuscule (ou le symbole) dans toutes les applications.
_CHAR_VKS = set(range(0x30, 0x3A)) | set(range(0x41, 0x5B)) | {0x20}


def unsafe_hotkey_reason(mods: int, vk: int, hkl: int) -> Optional[str]:
    """Pourquoi (mods, vk) ne doit pas devenir un raccourci global, ou None.

    - Maj + touche de caractere : la majuscule serait confisquee partout.
    - Ctrl+Alt (= AltGr) + touche qui produit un caractere AltGr sur la
      disposition `hkl` (AltGr+0 = @, AltGr+E = EUR...) : le caractere serait
      confisque partout, y compris dans les envois de l'outil (il injecte
      AltGr = LCtrl + RAlt) - le raccourci s'y declencherait en plein envoi.
    """
    if mods == 0 or vk == 0:
        return None
    if mods == MOD_SHIFT and vk in _CHAR_VKS:
        return (f"{format_hotkey(mods, vk)} confisquerait la majuscule "
                f"« {vk_name(vk)} » dans toutes les applications.")
    if mods & (MOD_CONTROL | MOD_ALT) == (MOD_CONTROL | MOD_ALT) \
            and not mods & (MOD_SHIFT | MOD_WIN):
        from keyboard import backend as w32
        produced = w32.altgr_char_of_vk(vk, hkl)
        if produced:
            return (f"{format_hotkey(mods, vk)} équivaut à AltGr+{vk_name(vk)}, "
                    f"qui tape « {produced} » sur ce clavier : ce caractère "
                    "serait confisqué partout, y compris dans les envois de "
                    "l'outil.")
    return None
