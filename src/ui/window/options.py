"""Dialogue Options et reglages appliques en direct (methode d'envoi,
AltGr, theme).

Partie de MainWindow (ui/main_window.py), deplacee telle quelle
lors du decoupage de la v0.7.2.
"""

from __future__ import annotations


from PySide6.QtWidgets import (
    QApplication,
)

from ui.options_dialog import OptionsDialog
from ui import icons
from ui.theme import apply_theme, token as theme_token


class OptionsMixin:
    """Methodes de MainWindow (voir la docstring du module)."""

    def _open_options(self) -> None:
        store_path, real_path = self._store_paths()
        dialog = OptionsDialog(self._theme, store_path,
                               current_method=self.controller.send_method,
                               altgr_with_ctrl=self._altgr_with_ctrl,
                               volatile=self._is_volatile,
                               real_path=real_path,
                               app_hotkeys=self._app_hotkeys,
                               refused_actions=self._refused_actions(),
                               parent=self)
        self._suspend_during_capture(*dialog.hotkey_edits.values())
        dialog.theme_previewed.connect(self._apply_theme)
        dialog.app_hotkey_changed.connect(
            lambda action, mods, vk: self._on_options_hotkey(
                dialog, action, mods, vk))
        dialog.method_changed.connect(self._apply_send_method)
        dialog.altgr_with_ctrl_changed.connect(self._apply_altgr_with_ctrl)
        dialog.reset_requested.connect(
            lambda: self._reset_master_password(dialog))
        dialog.open_folder_requested.connect(self._open_store_folder)
        dialog.open_store_requested.connect(
            lambda: self._switch_store(dialog))
        dialog.save_as_requested.connect(
            lambda: self._save_store_as(dialog))
        dialog.enable_storage_requested.connect(
            lambda: self._switch_store(dialog))
        try:
            dialog.exec()
        finally:
            self.hotkeys.resume()

    def _apply_send_method(self, method: str) -> None:
        self.controller.send_method = method
        self._settings.setValue("send/method", method)
        self._log(f"Méthode d'envoi : {method}.")

    def _apply_altgr_with_ctrl(self, enabled: bool) -> None:
        self._altgr_with_ctrl = enabled
        w32_module = __import__("keyboard.backend", fromlist=["backend"])
        if getattr(w32_module, "IS_REAL", False):
            from keyboard import win32_api
            win32_api.ALT_GR_WITH_CTRL = enabled
        self._settings.setValue("send/altgr_with_ctrl", enabled)
        self._log(
            f"AltGr envoyé {'avec' if enabled else 'sans'} Ctrl gauche.")

    def _refresh_options_icon(self) -> None:
        """La roue est dessinée, donc recolorée à chaque changement de thème :
        une icône figée deviendrait invisible sur le fond opposé."""
        self.options_btn.setIcon(icons.gear_icon(theme_token("on_accent")))

    def _apply_theme(self, theme: str) -> None:
        app = QApplication.instance()
        if app is not None:
            self._theme = apply_theme(app, theme)
            self._settings.setValue("ui/theme", self._theme)
            self._refresh_options_icon()
