"""
layout_converter.py - Conversion de disposition clavier.

Ce qui transite jusqu'a la console distante, ce sont des SCANCODES, pas des
caracteres. Le caractere qui arrive vaut :

    disposition_cible( scancode_produit_par_la_disposition_locale )

Deux modes en V1 (le 3e du script PowerShell d'origine (historique git 68f1fc4), la table calibree empiriquement,
viendra plus tard) :

  NONE   : aucune conversion. Correct seulement si la disposition locale et
           celle de la console sont identiques.
  LAYOUT : disposition cible choisie dans la liste. Chaine
           VkKeyScanEx -> MapVirtualKeyEx -> ToUnicodeEx pour determiner
           quel caractere taper localement.
"""

from __future__ import annotations

from typing import Optional

from . import backend as w32
from .layouts import KNOWN_LAYOUTS

MODE_NONE = "none"
MODE_LAYOUT = "layout"


class LayoutConverter:
    def __init__(self) -> None:
        self.mode: str = MODE_NONE
        self.hkl_local: int = 0
        self.hkl_target: int = 0
        self.target_name: str = ""

    def refresh_local(self) -> None:
        self.hkl_local = w32.current_layout()

    def set_mode(self, mode: str) -> None:
        self.mode = mode

    def set_target_layout(self, klid: str, name: str = "") -> bool:
        """klid : identifiant hexadecimal, ex. '0000040C'. Renvoie False si la
        disposition n'est pas disponible sur ce poste."""
        hkl = w32.load_keyboard_layout(klid)
        self.target_name = name
        if not hkl:
            self.hkl_target = 0
            return False
        self.hkl_target = hkl
        return True

    def set_target_by_index(self, index: int) -> bool:
        if not (0 <= index < len(KNOWN_LAYOUTS)):
            return False
        name, klid = KNOWN_LAYOUTS[index]
        return self.set_target_layout(klid, name)

    @property
    def active(self) -> bool:
        return self.mode != MODE_NONE

    def convert(self, ch: str) -> Optional[str]:
        """Caractere a taper localement pour obtenir 'ch' sur la console.
        None si aucune correspondance (l'appelant enverra tel quel et le
        signalera)."""
        if self.mode == MODE_NONE:
            return ch
        if self.mode == MODE_LAYOUT:
            if not self.hkl_target or not self.hkl_local:
                return ch
            return w32.translate_char(ch, self.hkl_target, self.hkl_local)
        return ch

    def preview(self, text: str) -> tuple[str, set[str]]:
        """Rend (texte reellement frappe, caracteres sans correspondance)."""
        out: list[str] = []
        missing: set[str] = set()
        for ch in text:
            conv = self.convert(ch)
            if not conv:
                missing.add(ch)
                out.append("?")
            else:
                out.append(conv)
        return "".join(out), missing
