"""
hotkey_edit.py - Champ qui capture une combinaison de touches.

L'utilisateur clique dedans puis presse la combinaison voulue (ex. AltGr+F1).
On stocke les modificateurs au format Win32 (RegisterHotKey), pas au format
Qt, pour pouvoir les passer tels quels au gestionnaire de raccourcis.

Cas AltGr : Windows le rapporte comme Ctrl gauche + Alt droite. Qt voit donc
Ctrl+Alt, ce qui est exactement ce que RegisterHotKey attend pour reconnaitre
AltGr - la combinaison est donc affichee "AltGr+..." pour rester lisible.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QLineEdit

from core.hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, format_hotkey

# Touches qui ne sont que des modificateurs : elles ne peuvent pas servir de
# touche principale.
_MODIFIER_KEYS = {
    Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
    Qt.Key.Key_Meta, Qt.Key.Key_AltGr, Qt.Key.Key_CapsLock,
    Qt.Key.Key_NumLock, Qt.Key.Key_ScrollLock,
}


def qt_key_to_vk(key: int) -> int:
    """Code de touche virtuelle Windows correspondant a une touche Qt."""
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
        return 0x70 + (key - Qt.Key.Key_F1)
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
        return 0x41 + (key - Qt.Key.Key_A)
    if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        return 0x30 + (key - Qt.Key.Key_0)

    table = {
        Qt.Key.Key_Space: 0x20, Qt.Key.Key_Return: 0x0D,
        Qt.Key.Key_Enter: 0x0D, Qt.Key.Key_Tab: 0x09,
        Qt.Key.Key_Backspace: 0x08, Qt.Key.Key_Insert: 0x2D,
        Qt.Key.Key_Delete: 0x2E, Qt.Key.Key_Home: 0x24,
        Qt.Key.Key_End: 0x23, Qt.Key.Key_PageUp: 0x21,
        Qt.Key.Key_PageDown: 0x22, Qt.Key.Key_Left: 0x25,
        Qt.Key.Key_Up: 0x26, Qt.Key.Key_Right: 0x27, Qt.Key.Key_Down: 0x28,
    }
    return table.get(key, 0)


class HotkeyEdit(QLineEdit):
    """Champ en lecture seule qui capture une combinaison au clavier."""

    hotkey_changed = Signal(int, int)   # (mods, vk)
    # Saisie en cours : les raccourcis globaux de l'outil doivent etre
    # suspendus, sinon Windows consomme une combinaison deja enregistree
    # avant ce champ (et declenche l'entree correspondante).
    capture_started = Signal()
    capture_ended = Signal()

    def __init__(self, mods: int = 0, vk: int = 0, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.mods = mods
        self.vk = vk
        self._refresh()

    def _refresh(self) -> None:
        self.setText(format_hotkey(self.mods, self.vk))

    def set_hotkey(self, mods: int, vk: int) -> None:
        self.mods, self.vk = mods, vk
        self._refresh()
        self.hotkey_changed.emit(mods, vk)

    def clear_hotkey(self) -> None:
        self.set_hotkey(0, 0)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.capture_started.emit()
        self.setPlaceholderText("Presse la combinaison...")
        self.selectAll()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.capture_ended.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()

        if key in (Qt.Key.Key_Escape,):
            self.clearFocus()
            return
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.clear_hotkey()
            return
        if key in _MODIFIER_KEYS:
            return   # on attend une vraie touche

        vk = qt_key_to_vk(key)
        if vk == 0:
            return

        qt_mods = event.modifiers()
        mods = 0
        if qt_mods & Qt.KeyboardModifier.ControlModifier:
            mods |= MOD_CONTROL
        if qt_mods & Qt.KeyboardModifier.ShiftModifier:
            mods |= MOD_SHIFT
        if qt_mods & Qt.KeyboardModifier.AltModifier:
            mods |= MOD_ALT
        if qt_mods & Qt.KeyboardModifier.MetaModifier:
            mods |= MOD_WIN

        if mods == 0:
            # Une touche seule ferait un raccourci global bien trop intrusif :
            # elle serait capturee dans toutes les applications.
            self.setText("(il faut au moins un modificateur)")
            return

        self.set_hotkey(mods, vk)
