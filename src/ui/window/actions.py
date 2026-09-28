"""Raccourcis globaux d'action : Coller le presse-papiers (frappe),
cibler la fenetre active, palette ; enregistrement et reglage.

Partie de MainWindow (ui/main_window.py), deplacee telle quelle
lors du decoupage de la v0.7.2.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QMessageBox,
)

from core.hotkeys import (
    ACTION_CAPTURE_TARGET, ACTION_LABELS, ACTION_PASTE_CLIPBOARD,
    ACTION_TOGGLE_PALETTE, format_hotkey, hotkey_owner, unsafe_hotkey_reason,
)
from core.models import QuickEntry
from core.paste import EMPTY_REASON, PastePlan, prepare_paste
from keyboard import backend as w32
from ui.options_dialog import OptionsDialog

#: Délai (s) pour confirmer par un second appui le collage de plusieurs lignes.
PASTE_CONFIRM_S = 5


class ActionsMixin:
    """Methodes de MainWindow (voir la docstring du module)."""

    def _on_action_hotkey(self, action: str, hwnd: int) -> None:
        # hwnd releve a l'instant du raccourci : la fenetre que l'operateur
        # regardait (celle a cibler).
        if self._pause_for_hotkey_during_send():
            return
        # Regle 2bis : RegisterHotKey consomme la touche ; au relachement
        # d'Alt, la cible activerait sa barre de menus et l'envoi suivant
        # perdrait ses premiers caracteres. Masque Ctrl + relachement.
        w32.release_held_modifiers()
        if action == ACTION_PASTE_CLIPBOARD:
            # Hors du filtre natif (WM_HOTKEY) : lire le presse-papiers peut
            # bloquer quand son proprietaire differe le rendu (RDP, Office).
            # La fenetre visee est deja relevee, le report est sans risque.
            QTimer.singleShot(0, lambda h=hwnd: self._paste_clipboard(h))
        elif action == ACTION_CAPTURE_TARGET:
            self._capture_window(hwnd, from_hotkey=True)
        elif action == ACTION_TOGGLE_PALETTE:
            self._toggle_palette()

    def _paste_clipboard(self, hwnd: int, retried: bool = False) -> None:
        """« Coller » la ou le vrai collage est bloque : le texte copie
        (Ctrl+C) est TAPE dans la fenetre ou le raccourci a ete presse, par
        le meme chemin qu'une entree d'envoi rapide (conversion clavier,
        garde-fou de focus, Pause/Stop). Lecture seule du presse-papiers ;
        le texte est traite comme un secret (jamais affiche ni journalise).

        Garde-fous (une frappe n'a pas les protections d'un vrai collage) :
        jamais de repli sur une autre fenetre, caracteres de controle
        refuses, pas d'ENTREE sur la derniere ligne, et un texte de
        plusieurs lignes (chacune validee par ENTREE) demande un second
        appui du raccourci."""
        if not self._is_foreign_target(hwnd):
            # Contrairement a la palette, aucun repli sur la derniere fenetre
            # de travail : le contenu (souvent un secret) partirait dans une
            # fenetre que l'operateur ne regarde pas.
            self._refuse_paste("raccourci pressé hors d'une fenêtre cible")
            return
        from ui.clipboard import read_clipboard_text
        raw = read_clipboard_text()
        if not raw and not retried:
            # Presse-papiers peut-etre verrouille un instant par une autre
            # application : une seconde lecture avant de conclure « vide ».
            QTimer.singleShot(
                120, lambda: self._paste_clipboard(hwnd, retried=True))
            return
        plan, reason = prepare_paste(raw)
        del raw
        if plan is None:
            self._refuse_paste(reason or EMPTY_REASON)
            return
        if plan.lines > 1 and not self._confirm_multiline_paste(plan, hwnd):
            return
        entry = QuickEntry(label="Presse-papiers", text=plan.text, secret=True,
                           final_enter=False)
        self._quick_send(entry, countdown=0, hwnd=hwnd)

    def _refuse_paste(self, reason: str) -> None:
        """Refus visible meme fenetre cachee (bulle du tray) : l'operateur
        regarde sa console, pas l'outil."""
        self._deny(reason)
        self._notify(f"Collage refusé : {reason}", from_hotkey=True)

    def _confirm_multiline_paste(self, plan: PastePlan, hwnd: int) -> bool:
        """Plusieurs lignes = plusieurs commandes executees (ENTREE apres
        chacune) : premier appui = avertissement, second appui dans le delai,
        meme fenetre et meme texte = frappe. Seule une empreinte HMAC (cle
        aleatoire de la session) du texte est gardee, jamais le texte."""
        digest = hmac.new(self._paste_key, plan.text.encode("utf-8"),
                          hashlib.sha256).digest()
        now = time.monotonic()
        pending = self._paste_pending
        self._paste_pending = None
        if (pending is not None and hmac.compare_digest(pending[0], digest)
                and pending[1] == hwnd and now <= pending[2]):
            return True
        self._paste_pending = (digest, hwnd, now + PASTE_CONFIRM_S)
        message = (f"{plan.lines} lignes à coller : chaque ligne sera validée "
                   "par ENTRÉE. Presse de nouveau le raccourci dans les "
                   f"{PASTE_CONFIRM_S} s pour confirmer.")
        self._log(f"Collage de {plan.lines} lignes en attente de confirmation.",
                  "WARN")
        self.status_label.setText(message)
        self._notify(message, from_hotkey=True)
        return False

    def register_app_hotkeys(self) -> None:
        """A appeler une fois le HWND attache (main.py)."""
        for action, (mods, vk) in self._app_hotkeys.items():
            if not (mods and vk):
                continue
            owner = hotkey_owner(self.entries, {}, mods, vk)
            if owner is not None:
                self._log(
                    f"Raccourci {format_hotkey(mods, vk)} "
                    f"(« {ACTION_LABELS[action]} ») inactif : déjà attribué à "
                    f"{owner}.", "WARN")
            elif not self.hotkeys.register_action(action, mods, vk):
                self._log(
                    f"Raccourci {format_hotkey(mods, vk)} "
                    f"(« {ACTION_LABELS[action]} ») refusé : déjà pris par "
                    "une autre application.", "WARN")

    def _refused_actions(self) -> set[str]:
        """Actions reglees mais non actives (refus au demarrage)."""
        if not self.hotkeys.is_attached:
            return set()
        return {action for action, (mods, vk) in self._app_hotkeys.items()
                if mods and vk and not self.hotkeys.is_action_registered(action)}

    def _suspend_during_capture(self, *edits) -> None:
        """Raccourcis globaux suspendus tant qu'un champ de saisie de
        combinaison a le focus (voir HotkeyManager.suspend)."""
        for edit in edits:
            edit.capture_started.connect(self.hotkeys.suspend)
            edit.capture_ended.connect(self.hotkeys.resume)

    def set_app_hotkey(self, action: str, mods: int, vk: int) -> Optional[str]:
        """Change le raccourci d'une action. Rend None si c'est fait, sinon
        la raison du refus (l'ancien raccourci est alors conserve)."""
        old = self._app_hotkeys.get(action, (0, 0))
        unchanged = (mods, vk) == tuple(old)
        if unchanged and (not (mods and vk) or not self.hotkeys.is_attached
                          or self.hotkeys.is_action_registered(action)):
            return None
        unsafe = unsafe_hotkey_reason(mods, vk, w32.current_layout())
        if unsafe is not None:
            return unsafe
        owner = hotkey_owner(self.entries, self._app_hotkeys, mods, vk,
                             exclude_action=action)
        if owner is not None:
            return f"{format_hotkey(mods, vk)} est déjà attribué à {owner}."
        # Enregistre seulement si l'outil est attache a un HWND (sinon, ce
        # sera fait par register_app_hotkeys au demarrage). En cas de refus,
        # register_action a laisse l'ancien raccourci actif.
        if self.hotkeys.is_attached and not self.hotkeys.register_action(
                action, mods, vk):
            return (f"{format_hotkey(mods, vk)} est déjà pris par une autre "
                    "application.")
        self._app_hotkeys[action] = (mods, vk)
        self._settings.setValue(f"hotkeys/{action}_mods", mods)
        self._settings.setValue(f"hotkeys/{action}_vk", vk)
        self._log(f"Raccourci « {ACTION_LABELS[action]} » : "
                  f"{format_hotkey(mods, vk)}.")
        return None

    def _on_options_hotkey(self, dialog: OptionsDialog, action: str,
                           mods: int, vk: int) -> None:
        error = self.set_app_hotkey(action, mods, vk)
        if error is not None:
            QMessageBox.warning(dialog, "Raccourci refusé",
                                f"{error}\n\nChoisis une autre combinaison.")
            dialog.set_app_hotkey(action, *self._app_hotkeys[action])
        dialog.set_refused_actions(self._refused_actions())
