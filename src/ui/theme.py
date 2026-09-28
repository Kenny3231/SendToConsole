"""
theme.py - Themes clair et sombre.

La feuille de style est un gabarit unique dont les couleurs sont des
variables : deux jeux de valeurs suffisent donc a produire les deux themes,
au lieu de maintenir deux fichiers .qss qui divergeraient a la première
retouche. On utilise string.Template ($nom) et non str.format, parce que le
QSS est plein d'accolades.

Chaque thème pose AUSSI une QPalette complete : sans cela, les widgets
natifs (QLineEdit, QTableWidget, QSpinBox...) heritent des couleurs du
thème de Windows - texte blanc en mode sombre - et deviennent illisibles
sur nos fonds.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from string import Template

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPolygonF
from PySide6.QtWidgets import QApplication, QPushButton

LIGHT = "clair"
DARK = "sombre"

TOKENS: dict[str, dict[str, str]] = {
    LIGHT: {
        "bg": "#f4f5f7",
        "surface": "#ffffff",
        "surface_alt": "#f7f8fa",
        "input_bg": "#ffffff",
        "border": "#d9dce1",
        "text": "#1f2329",
        "text_strong": "#2b2f36",
        "text_muted": "#6b7280",
        "placeholder": "#9aa0ab",
        "accent": "#4a6cf7",
        "accent_hover": "#3a5ce0",
        "accent_pressed": "#2f4bc4",
        "on_accent": "#ffffff",
        "danger": "#e5484d",
        "danger_hover": "#c93f43",
        "on_danger": "#ffffff",
        "header_bg": "#f0f1f4",
        "disabled_bg": "#c7ccd6",
        "disabled_text": "#f0f0f0",
        "disabled_field_bg": "#f0f1f4",
        "disabled_field_text": "#a6abb5",
        "grid": "#eceef1",
        "target_text": "#2b6f4a",
        "target_bg": "#e8f5ee",
        "target_border": "#a8d5b5",
        "warn_bg": "#fff4cc",
        "warn_border": "#e6cf7a",
        "warn_text": "#92400e",
        "tab_bg": "#e9ebf0",
        "tab_text": "#4b525e",
        "pal_btn_bg": "#eef1fb",
        "pal_btn_border": "#c9d2f0",
        "pal_btn_text": "#2b3557",
        "pal_btn_hover": "#dde3fa",
        "arrow_bg": "#e4e8f6",
        "arrow_hover": "#cfd6ee",
        "arrow_disabled_bg": "#f1f2f6",
        "arrow_disabled_text": "#bcc2cf",
        "arrow_glyph": "#3a4561",
        "secondary_hover": "#eef1fb",
        "palette_border": "#b9c0cc",
    },
    DARK: {
        "bg": "#1b1e24",
        "surface": "#22262e",
        "surface_alt": "#272c35",
        "input_bg": "#191c22",
        "border": "#3a4049",
        "text": "#e6e9ee",
        "text_strong": "#f2f4f8",
        "text_muted": "#9aa3b0",
        "placeholder": "#6f7885",
        "accent": "#6b86ff",
        "accent_hover": "#5a76f0",
        "accent_pressed": "#4a66e0",
        "on_accent": "#12151c",
        "danger": "#e5484d",
        "danger_hover": "#c93f43",
        "on_danger": "#ffffff",
        "header_bg": "#2a2f39",
        "disabled_bg": "#343a45",
        "disabled_text": "#727b88",
        "disabled_field_bg": "#22262e",
        "disabled_field_text": "#6f7885",
        "grid": "#31363f",
        "target_text": "#6ee7a8",
        "target_bg": "#1e3329",
        "target_border": "#3b6b52",
        "warn_bg": "#3d3418",
        "warn_border": "#6e5f2a",
        "warn_text": "#f0d98a",
        "tab_bg": "#262b34",
        "tab_text": "#a8b0bd",
        "pal_btn_bg": "#2b313d",
        "pal_btn_border": "#3f4859",
        "pal_btn_text": "#d5dcea",
        "pal_btn_hover": "#343c4b",
        "arrow_bg": "#3e4a60",
        "arrow_hover": "#4f5e7a",
        "arrow_disabled_bg": "#262a33",
        "arrow_disabled_text": "#565f6d",
        "arrow_glyph": "#e6e9ee",
        "secondary_hover": "#2b313d",
        "palette_border": "#454c59",
    },
}

QSS = Template("""
* {
    font-family: "Segoe UI", sans-serif;
    font-size: 13px;
    color: $text;
}

QMainWindow, QDialog { background-color: $bg; }

QLabel { color: $text_strong; }

QGroupBox {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px;
    font-weight: 600;
    color: $text_strong;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: $accent;
}

QPushButton {
    background-color: $accent;
    color: $on_accent;
    border: none;
    border-radius: 6px;
    padding: 6px 14px;
}

QPushButton:hover { background-color: $accent_hover; }
QPushButton:pressed { background-color: $accent_pressed; }

QPushButton:disabled {
    background-color: $disabled_bg;
    color: $disabled_text;
}

/* Action secondaire : contour neutre, pour que l'action principale de
   chaque zone (Démarrer, Capturer, Ajouter, Enregistrer...) ressorte. */
QPushButton[secondary="true"] {
    background-color: $surface;
    color: $text_strong;
    border: 1px solid $border;
    padding: 5px 13px;
}
QPushButton[secondary="true"]:hover {
    background-color: $secondary_hover;
    border-color: $accent;
}
QPushButton[secondary="true"]:pressed { background-color: $tab_bg; }
QPushButton[secondary="true"]:disabled {
    background-color: $disabled_field_bg;
    color: $disabled_field_text;
    border-color: $grid;
}

/* Contenu à taper et journal : police à chasse fixe (indentation, colonnes).
   La règle « * » ci-dessus l'emporterait sur un simple setFont(). */
QPlainTextEdit#monoEdit {
    font-family: "Consolas", "Courier New", monospace;
    font-size: 13px;
}

/* Bouton icône seule : carré, sans texte, l'icône fait tout le travail. */
QPushButton#iconButton {
    background-color: $accent;
    border-radius: 6px;
    padding: 0;
    margin: -2px 4px 0px 0px;
}

QPushButton#iconButton:hover { background-color: $accent_hover; }
QPushButton#iconButton:pressed { background-color: $accent_pressed; }

/* Texte toujours blanc sur le rouge : en thème sombre, la couleur
   "sur accent" est presque noire et deviendrait illisible ici. */
QPushButton#stopButton { background-color: $danger; color: $on_danger; }
QPushButton#stopButton:hover { background-color: $danger_hover; color: $on_danger; }
QPushButton#stopButton:disabled {
    background-color: $disabled_bg;
    color: $disabled_text;
}

QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit {
    background-color: $input_bg;
    color: $text;
    border: 1px solid $border;
    border-radius: 5px;
    padding: 4px 6px;
}

QLineEdit:focus, QSpinBox:focus, QComboBox:focus,
QTextEdit:focus, QPlainTextEdit:focus { border: 1px solid $accent; }


QSpinBox { padding-right: 24px; }

QSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 22px;
    background-color: $arrow_bg;
    border-left: 1px solid $border;
    border-bottom: 1px solid $border;
    border-top-right-radius: 4px;
}
QSpinBox::up-button:hover    { background-color: $arrow_hover; }
QSpinBox::up-button:pressed  { background-color: $accent; }
QSpinBox::up-button:disabled { background-color: $arrow_disabled_bg; }

QSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 22px;
    background-color: $arrow_bg;
    border-left: 1px solid $border;
    border-top: 1px solid $border;
    border-bottom-right-radius: 4px;
}
QSpinBox::down-button:hover    { background-color: $arrow_hover; }
QSpinBox::down-button:pressed  { background-color: $accent; }
QSpinBox::down-button:disabled { background-color: $arrow_disabled_bg; }

QSpinBox::up-arrow   { image: $arrow_up_image;   width: 9px; height: 9px; }
QSpinBox::down-arrow { image: $arrow_down_image; width: 9px; height: 9px; }
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off {
    image: $arrow_up_disabled_image; }
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off {
    image: $arrow_down_disabled_image; }

/* Sans règle explicite, Fusion dessine le bouton de liste avec un liseré
   3D sombre (couleur Shadow de la palette) qui jure sur les deux thèmes. */
QComboBox { padding-right: 24px; }
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 22px;
    border: none;
}
QComboBox::down-arrow { image: $arrow_down_image; width: 9px; height: 9px; }
QComboBox::down-arrow:disabled { image: $arrow_down_disabled_image; }

QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled,
QTextEdit:disabled, QPlainTextEdit:disabled {
    background-color: $disabled_field_bg;
    color: $disabled_field_text;
}

QComboBox QAbstractItemView {
    background-color: $surface;
    color: $text;
    selection-background-color: $accent;
    selection-color: $on_accent;
    border: 1px solid $border;
}

QTableWidget {
    background-color: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    gridline-color: $grid;
    alternate-background-color: $surface_alt;
}

QTableWidget::item { color: $text; padding: 2px; }

QTableWidget::item:selected {
    background-color: $accent;
    color: $on_accent;
}

QHeaderView::section {
    background-color: $header_bg;
    color: $text_strong;
    border: none;
    border-bottom: 1px solid $border;
    padding: 4px;
    font-weight: 600;
}

QCheckBox { color: $text; spacing: 6px; }
QCheckBox:disabled { color: $disabled_field_text; }

QRadioButton { color: $text; spacing: 6px; }

QProgressBar {
    border: 1px solid $border;
    border-radius: 5px;
    text-align: center;
    background-color: $input_bg;
    color: $text;
}

QProgressBar::chunk { background-color: $accent; border-radius: 4px; }

QLabel#statusLabel { color: $text_muted; }

QLabel#statusLabel[denied="true"] {
    color: $on_danger;
    background-color: $danger;
    border-radius: 4px;
    padding: 3px 8px;
    font-weight: 600;
}

QLabel#targetLabel { color: $target_text; font-weight: 600; }

QLabel#hintLabel { color: $text_muted; }

QLabel#hintLabel[warn="true"] {
    color: $warn_text;
    background-color: $warn_bg;
    border: 1px solid $warn_border;
    border-radius: 4px;
    padding: 3px 8px;
    font-weight: 600;
}

QLabel#simBanner {
    background-color: $warn_bg;
    border: 1px solid $warn_border;
    border-radius: 6px;
    padding: 6px 10px;
    color: $warn_text;
}

QTabWidget::pane {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 6px;
    top: -1px;
}

QTabBar::tab {
    background-color: $tab_bg;
    color: $tab_text;
    border: 1px solid $border;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 16px;
    margin-right: 2px;
}

QTabBar::tab:selected {
    background-color: $surface;
    color: $accent;
    font-weight: 600;
}

QScrollBar:vertical, QScrollBar:horizontal {
    background: $surface_alt;
    border: none;
    width: 10px;
    height: 10px;
}

QScrollBar::handle {
    background: $border;
    border-radius: 5px;
    min-height: 24px;
    min-width: 24px;
}

QScrollBar::handle:hover { background: $text_muted; }

QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

/* ---- Palette flottante ---- */

/* Ni fond ni bordure ici : la palette peint elle-meme son panneau arrondi
   (paintEvent). En laisser un ajoutait un second cadre, aux coins carres,
   par-dessus le dessin. */
QWidget#quickPalette { background: transparent; border: none; }

QFrame#paletteHandle {
    background-color: $accent;
    border-radius: 6px;
}

QLabel#paletteTitle {
    color: $on_accent;
    font-weight: 600;
    padding-left: 2px;
}

/* Bouton bascule d'orientation */
QPushButton#paletteHandleBtn {
    background-color: transparent;
    color: $on_accent;
    font-weight: 700;
    padding: 0;
    border-radius: 11px;
}
QPushButton#paletteHandleBtn:hover  { background-color: rgba(255,255,255,0.22); }
QPushButton#paletteHandleBtn:pressed { background-color: rgba(0,0,0,0.18); }

/* Bouton fermeture : rouge au survol pour signal de danger clair */
QPushButton#paletteCloseBtn {
    background-color: transparent;
    color: $on_accent;
    font-weight: 700;
    padding: 0;
    border-radius: 11px;
}
QPushButton#paletteCloseBtn:hover  { background-color: $danger; color: $on_danger; }
QPushButton#paletteCloseBtn:pressed { background-color: $danger_hover; color: $on_danger; }

QPushButton#paletteButton {
    background-color: $pal_btn_bg;
    color: $pal_btn_text;
    border: 1px solid $pal_btn_border;
    border-radius: 6px;
    text-align: center;
    padding: 4px 8px;
}

QPushButton#paletteButton:hover   { background-color: $pal_btn_hover; }
QPushButton#paletteButton:pressed { background-color: $accent_pressed;
                                    color: $on_accent; }

QPushButton#paletteArrow {
    background-color: $arrow_bg;
    color: $pal_btn_text;
    border: none;
    border-radius: 5px;
    font-weight: 700;
    padding: 0;
}

QPushButton#paletteArrow:hover { background-color: $arrow_hover; }

QPushButton#paletteArrow:disabled {
    background-color: $arrow_disabled_bg;
    color: $arrow_disabled_text;
}


/* ───────────────────────── barre persistante d'envoi ───────────────────── */
QFrame#sendBar {
    background-color: $surface;
    border-top: 1px solid $border;
}

QPushButton#startButton {
    background-color: $accent;
    color: $on_accent;
    font-weight: 700;
    border: none;
    border-radius: 5px;
    padding: 4px 14px;
}
QPushButton#startButton:hover    { background-color: $accent_hover; }
QPushButton#startButton:pressed  { background-color: $accent_pressed; }
QPushButton#startButton:disabled {
    background-color: $border;
    color: $text_muted;
}

QLabel#barTargetLabel {
    color: $text_muted;
    font-style: italic;
    padding: 2px 4px;
}
QLabel#barTargetLabel[active="true"] {
    color: $target_text;
    font-style: normal;
    font-weight: 600;
    background-color: $target_bg;
    border: 1px solid $target_border;
    border-radius: 4px;
    padding: 2px 6px;
}

QLabel#paletteEmpty { color: $placeholder; padding: 16px 8px; }
""")


def _arrow_dir() -> Path:
    """Dossier des flèches dessinées (fichiers PNG anodins, aucune donnée).
    Calculé à l'usage, pas à l'import : sans dossier temporaire inscriptible,
    gettempdir() lève, et l'application ne doit pas en mourir."""
    return Path(tempfile.gettempdir()) / "SendToConsole-ui"


def _arrow_url(direction: str, color: str) -> str:
    """Flèche de QSpinBox/QComboBox, dessinée puis écrite en PNG.

    Le QSS n'accepte qu'un chemin de fichier ou de ressource : une URI
    « data: » (ancienne approche) n'est jamais chargée par Qt, et les
    flèches restaient invisibles. Même logique que icons.py (tracé par le
    code, rien à embarquer), mais le QSS impose de passer par un fichier.
    En cas d'échec d'écriture, « none » : pas de flèche, rien de cassé.
    """
    name = f"arrow_{direction}_{color.lstrip('#')}.png"
    try:
        path = _arrow_dir() / name
    except OSError:
        return "none"
    # Fichier absent, tronqué (autre instance interrompue) ou remplacé par
    # autre chose qu'une image : on le redessine.
    if not path.exists() or QImage(str(path)).isNull():
        size = 18                       # 2x : net en haute densité
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        if direction == "up":
            points = [QPointF(9, 3), QPointF(17, 15), QPointF(1, 15)]
        else:
            points = [QPointF(1, 3), QPointF(17, 3), QPointF(9, 15)]
        painter.drawPolygon(QPolygonF(points))
        painter.end()
        # Écriture atomique : deux instances simultanées ne voient jamais
        # un PNG à moitié écrit.
        tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not image.save(str(tmp), "PNG"):
                return "none"
            os.replace(tmp, path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return "none"
    return f'url("{path.as_posix()}")'


def stylesheet(theme: str) -> str:
    values = dict(TOKENS.get(theme, TOKENS[LIGHT]))
    values["arrow_up_image"] = _arrow_url("up", values["arrow_glyph"])
    values["arrow_down_image"] = _arrow_url("down", values["arrow_glyph"])
    values["arrow_down_disabled_image"] = _arrow_url(
        "down", values["arrow_disabled_text"])
    values["arrow_up_disabled_image"] = _arrow_url(
        "up", values["arrow_disabled_text"])
    return QSS.substitute(values)


def mark_secondary(button: QPushButton) -> QPushButton:
    """Style « action secondaire » (contour neutre) ; renvoie le bouton."""
    button.setProperty("secondary", True)
    return button


def build_palette(theme: str) -> QPalette:
    t = TOKENS.get(theme, TOKENS[LIGHT])
    palette = QPalette()
    text = QColor(t["text"])

    palette.setColor(QPalette.ColorRole.Window, QColor(t["bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, QColor(t["input_bg"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(t["surface_alt"]))
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, QColor(t["accent"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(t["text"]))
    # Utilisé par Fusion pour la couleur des flèches QSpinBox/QComboBox :
    palette.setColor(QPalette.ColorRole.Shadow, QColor(t["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(t["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(t["on_accent"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(t["surface"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(t["placeholder"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
                     QColor(t["disabled_field_text"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText,
                     QColor(t["disabled_field_text"]))
    return palette


#: Theme actif, pour les widgets qui peignent eux-memes (la palette
#: flottante, dont les coins arrondis ne peuvent pas venir du QSS).
_ACTIVE = LIGHT


def active_theme() -> str:
    return _ACTIVE


def token(name: str) -> str:
    return TOKENS.get(_ACTIVE, TOKENS[LIGHT]).get(name, "#000000")


def apply_theme(app: QApplication, theme: str) -> str:
    """Applique le thème et renvoie son nom (retombe sur clair si inconnu).

    Le style Fusion est impose : c'est lui qui rend le forcage de palette
    fiable sur toutes les versions de Windows, quel que soit le thème du
    système.
    """
    global _ACTIVE
    if theme not in TOKENS:
        theme = LIGHT
    _ACTIVE = theme
    app.setStyle("Fusion")
    app.setPalette(build_palette(theme))
    app.setStyleSheet(stylesheet(theme))
    return theme
