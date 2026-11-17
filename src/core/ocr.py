"""Reconnaissance de texte (OCR) d'une capture d'ecran, par RapidOCR.

RapidOCR (paquet rapidocr-onnxruntime) embarque ses modeles : aucun
telechargement, 100 % local. Le moteur est charge a la premiere utilisation
(environ une seconde) puis garde. L'OCR tourne dans un thread : jamais de
calcul long dans le thread GUI. Le texte reconnu est un secret potentiel : il
n'est jamais journalise et les erreurs ne portent que le nom de l'exception.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

_engine = None


def recognize(png: bytes) -> str:
    """Texte reconnu dans une image PNG (une ligne de texte par ligne)."""
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    result, _elapsed = _engine(png)
    return join_lines(result)


def join_lines(result) -> str:
    """RapidOCR rend un fragment par bloc de texte ([boite, texte, score]) :
    une commande « show running-config » sort en trois fragments. Les
    fragments situes a la meme hauteur sont regroupes (tries de gauche a
    droite, separes par une espace) : sinon chaque mot deviendrait une ligne,
    donc un ENTREE a la frappe.
    shortcut : espaces multiples non restituees, l'apercu editable les corrige."""
    items = []
    for box, text, _score in result or []:
        ys = [point[1] for point in box]
        xs = [point[0] for point in box]
        items.append((sum(ys) / len(ys), max(ys) - min(ys), min(xs), str(text)))
    items.sort(key=lambda item: item[0])
    lines: list[list] = []          # [centre y, hauteur, fragments]
    for item in items:
        if lines and abs(item[0] - lines[-1][0]) <= lines[-1][1] / 2:
            lines[-1][2].append(item)
        else:
            lines.append([item[0], item[1], [item]])
    return "\n".join(
        " ".join(frag[3] for frag in sorted(line[2], key=lambda f: f[2]))
        for line in lines)


class OcrJob(QThread):
    """Un OCR en arriere-plan. result_ready(texte, erreur) : erreur est vide
    en cas de succes."""
    result_ready = Signal(str, str)

    def __init__(self, png: bytes, parent=None) -> None:
        super().__init__(parent)
        self._png = png

    def run(self) -> None:
        try:
            text = recognize(self._png)
        except ImportError:
            self.result_ready.emit("", "RapidOCR n'est pas installé")
        except Exception as exc:  # noqa: BLE001 - jamais de texte dans l'erreur
            self.result_ready.emit(
                "", f"échec de la reconnaissance ({type(exc).__name__})")
        else:
            self.result_ready.emit(text, "")
        finally:
            self._png = b""
