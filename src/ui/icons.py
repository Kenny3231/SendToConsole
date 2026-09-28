"""
icons.py - Icônes dessinées par le code.

Rien n'est chargé depuis un fichier : dans un .exe produit par PyInstaller
en un seul fichier, les ressources sont extraites dans un dossier temporaire
dont le chemin change à chaque lancement. Dessiner les icônes évite
complètement ce problème - et elles restent nettes à toutes les tailles.

Le fichier .ico du dossier assets/ ne sert qu'à PyInstaller, pour l'icône de
l'exécutable lui-même ; il est produit par build/make_icon.py à partir des
mêmes tracés.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QIcon, QPainter, QPainterPath, QPixmap,
)

#: Tailles fournies : Windows pioche celle qui lui convient selon le contexte
#: (barre de titre, barre des tâches, Alt+Tab, Explorateur).
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)

#: Violet : aucune application système Windows ne l'emploie, l'icône se
#: repère donc immédiatement dans la barre des tâches. Le bleu et le chevron
#: « > » ont été écartés : c'est exactement l'icône de PowerShell.
KEY_LIGHT = "#a78bfa"
KEY_DARK = "#5b21b6"
GLYPH = "#ffffff"


def _draw_app_icon(painter: QPainter, size: int) -> None:
    """Une clé : ce que l'outil injecte, ce sont des identifiants.

    Le tracé est volontairement massif - anneau large, panneton à deux dents
    épaisses - parce qu'à 16 pixels le moindre détail fin disparaît. La clé
    est dessinée d'un seul tenant puis le trou de l'anneau est soustrait :
    une ellipse peinte par-dessus donnerait un disque de la couleur du fond,
    qui trancherait dès que le dégradé change de teinte.
    """
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # Fond arrondi, dégradé en diagonale.
    from PySide6.QtGui import QLinearGradient
    gradient = QLinearGradient(0, 0, size * 0.35, size)
    gradient.setColorAt(0.0, QColor(KEY_LIGHT))
    gradient.setColorAt(1.0, QColor(KEY_DARK))
    painter.setBrush(QBrush(gradient))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.23, size * 0.23)

    painter.setBrush(QBrush(QColor(GLYPH)))
    painter.setPen(Qt.PenStyle.NoPen)

    centre = QPointF(size * 0.335, size * 0.50)
    key = QPainterPath()
    key.addEllipse(centre, size * 0.205, size * 0.205)      # anneau
    key.addRect(QRectF(size * 0.335, size * 0.445,
                       size * 0.42, size * 0.11))           # tige
    key.addRect(QRectF(size * 0.585, size * 0.445,
                       size * 0.075, size * 0.245))         # dent longue
    key.addRect(QRectF(size * 0.705, size * 0.445,
                       size * 0.075, size * 0.185))         # dent courte

    hole = QPainterPath()
    hole.addEllipse(centre, size * 0.082, size * 0.082)
    painter.drawPath(key.subtracted(hole))


def app_pixmap(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    _draw_app_icon(painter, size)
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    """Icône de l'application, en plusieurs résolutions."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(app_pixmap(size))
    return icon


def gear_icon(color: str, size: int = 20) -> QIcon:
    """Roue crantée, dessinée dans la couleur demandée (suit le thème)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(size / 2.0, size / 2.0)

    teeth = 8
    outer = size * 0.46
    inner = size * 0.32
    hub = size * 0.15

    path = QPainterPath()
    from math import cos, sin, pi
    steps = teeth * 2
    for index in range(steps + 1):
        # Une dent sur deux : on alterne rayon extérieur et rayon intérieur.
        angle = pi * index / teeth
        radius = outer if index % 2 == 0 else inner
        point = QPointF(radius * cos(angle), radius * sin(angle))
        if index == 0:
            path.moveTo(point)
        else:
            path.lineTo(point)
    path.closeSubpath()

    # Le moyeu est retiré du tracé : la roue se lit même très petite.
    hole = QPainterPath()
    hole.addEllipse(QPointF(0, 0), hub, hub)
    painter.setBrush(QBrush(QColor(color)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPath(path.subtracted(hole))
    painter.end()

    return QIcon(pixmap)
