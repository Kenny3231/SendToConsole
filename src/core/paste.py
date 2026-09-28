"""Preparation d'un texte colle (action « Coller le presse-papiers ») avant
sa frappe.

Une frappe simulee n'a pas les protections d'un vrai collage (bracketed
paste des terminaux) : chaque saut de ligne devient ENTREE et chaque
caractere de controle une vraie touche (\\x04 = Ctrl+D, \\x1b = Echap...).
D'ou des refus explicites plutot qu'un nettoyage silencieux, qui modifierait
un mot de passe. Les messages ne donnent que des nombres, jamais le texte.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Optional

#: Au-dela, l'envoi est refuse : taper des dizaines de milliers de
#: caracteres par raccourci serait long et difficile a interrompre a temps ;
#: l'onglet Contenu est fait pour ca.
MAX_PASTE_CHARS = 20_000

#: Seuls controles admis : tabulation (touche TAB) et fins de ligne.
_ALLOWED_CONTROLS = frozenset("\t\r\n")
#: Formats invisibles (bidi, largeur nulle, BOM) et separateurs de ligne /
#: paragraphe Unicode (str.splitlines les traiterait comme des ENTREE).
_INVISIBLE_CATEGORIES = frozenset({"Cf", "Zl", "Zp"})

EMPTY_REASON = "presse-papiers vide, sans texte ou indisponible"


@dataclass(frozen=True)
class PastePlan:
    """Texte pret a taper (lignes separees par \\n, sans ligne vide finale)."""
    text: str = field(repr=False)
    lines: int


def _is_forbidden(ch: str) -> bool:
    category = unicodedata.category(ch)
    if category == "Cc":
        return ch not in _ALLOWED_CONTROLS
    return category in _INVISIBLE_CATEGORIES


def prepare_paste(text: str) -> tuple[Optional[PastePlan], Optional[str]]:
    """(plan, None) si le texte peut etre tape, sinon (None, raison)."""
    if not text or not text.strip():
        return None, EMPTY_REASON

    forbidden = sum(1 for ch in text if _is_forbidden(ch))
    if forbidden:
        return None, (f"{forbidden} caractère(s) de contrôle ou invisible(s) "
                      "dans le presse-papiers : ils deviendraient des touches "
                      "(Ctrl+…, Échap) ou des caractères cachés dans la cible")

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Un saut de ligne (ou une ligne blanche) copie a la fin ne doit jamais
    # valider la derniere ligne par ENTREE.
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return None, EMPTY_REASON

    cleaned = "\n".join(lines)
    if len(cleaned) > MAX_PASTE_CHARS:
        return None, (f"presse-papiers trop long ({len(cleaned)} caractères, "
                      f"maximum {MAX_PASTE_CHARS}) : utiliser l'onglet Contenu")
    return PastePlan(text=cleaned, lines=len(lines)), None
