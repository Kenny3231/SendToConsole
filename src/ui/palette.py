"""
palette.py - Palette flottante des entrées d'envoi rapide.

Contrainte centrale, heritee du script PowerShell d'origine : cette fenêtre ne doit JAMAIS
prendre le focus. Sans cela, cliquer un bouton de la palette activerait la
palette, donc sortirait le curseur du champ de saisie de la console -
exactement ce qu'on cherche a eviter.

Deux mecanismes combines :
  - Qt : drapeau Tool + attribut WA_ShowWithoutActivating ;
  - Windows : style etendu WS_EX_NOACTIVATE pose directement sur le HWND,
    seul moyen réellement fiable (c'est ce que faisait NoActivateForm).

Disposition en colonne ou en ligne, au choix, et fleches aux deux bouts pour
atteindre les entrées qui depassent du cadre.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import (
    QPoint, QRect, QRectF, QSettings, Qt, QTimer, Signal)
from PySide6.QtGui import QBrush, QColor, QCursor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from core.hotkeys import format_hotkey
from ui import theme
from core.models import QuickEntry

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

VERTICAL = "vertical"
HORIZONTAL = "horizontal"

#: Epaisseur de la zone sensible au redimensionnement le long des bords.
#: La fenetre est sans bordure : Windows ne fournit donc aucune poignee, il
#: faut la gerer nous-memes.
RESIZE_MARGIN = 6

#: Rayon des coins du panneau flottant.
PANEL_RADIUS = 10

#: Largeur maximale d'un bouton en disposition horizontale (mode horizontal).
MAX_BUTTON_WIDTH = 170

#: Hauteur fixe des boutons d'entrée.
BTN_H = 44

#: Largeur fixe des boutons en disposition horizontale.
BTN_W_HORIZ = 160


class DragHandle(QFrame):
    """Bandeau : poignee de deplacement, bascule d'orientation, fermeture."""

    close_requested = Signal()
    orientation_toggled = Signal()

    def __init__(self, parent: "QuickPalette") -> None:
        super().__init__(parent)
        self.setObjectName("paletteHandle")
        self._palette_window = parent
        self._drag_offset: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 3, 3)
        layout.setSpacing(4)

        self.title = QLabel("Envoi rapide")
        self.title.setObjectName("paletteTitle")
        layout.addWidget(self.title)
        layout.addStretch(1)

        self.orient_btn = QPushButton("⇅")
        self.orient_btn.setObjectName("paletteHandleBtn")
        self.orient_btn.setFixedSize(22, 22)
        self.orient_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.orient_btn.setToolTip("Basculer en ligne / en colonne")
        self.orient_btn.setCursor(Qt.CursorShape.ArrowCursor)
        self.orient_btn.clicked.connect(self.orientation_toggled.emit)
        layout.addWidget(self.orient_btn)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("paletteCloseBtn")
        close_btn.setFixedSize(22, 22)
        close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_btn.setCursor(Qt.CursorShape.ArrowCursor)
        close_btn.clicked.connect(self.close_requested.emit)
        layout.addWidget(close_btn)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (event.globalPosition().toPoint()
                                 - self._palette_window.frameGeometry().topLeft())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None:
            self._palette_window.move(
                event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None


def _is_native_windows() -> bool:
    """Vraie fenêtre Windows (pas le plugin Qt offscreen des tests)."""
    from PySide6.QtGui import QGuiApplication
    return sys.platform == "win32" and QGuiApplication.platformName() == "windows"


def set_no_activate_style(hwnd: int) -> bool:
    """Ajoute WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW au HWND puis relit le style.
    Rend True seulement si WS_EX_NOACTIVATE est effectivement actif."""
    import ctypes
    from ctypes import wintypes

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except OSError:
        return False
    user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_long)
    user32.SetWindowLongW.restype = ctypes.c_long

    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    # c_long est signe : WS_EX_NOACTIVATE (bit 27) tient dans l'entier positif.
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                          style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
    return bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_NOACTIVATE)


class QuickPalette(QWidget):
    """Fenêtre flottante : un bouton par entrée, un clic = envoi."""

    entry_clicked = Signal(object)      # QuickEntry
    orientation_changed = Signal(str)
    visibility_changed = Signal(bool)
    no_activate_failed = Signal()     # style « sans focus » refusé par Windows

    def __init__(self, orientation: str = VERTICAL, parent=None) -> None:
        super().__init__(None)   # sans parent : fenetre independante
        self._no_activate_warned = False
        self.setWindowTitle("Envoi rapide")
        self.setObjectName("quickPalette")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # Une fenetre sans bordure reste un rectangle : le border-radius du
        # QSS ne decoupe pas la fenetre elle-meme, d'ou les coins carres
        # visibles. On rend donc le fond transparent et on peint soi-meme le
        # panneau arrondi (voir paintEvent).
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._orientation = orientation
        self._entries: list[QuickEntry] = []

        self.setMouseTracking(True)
        self._settings = QSettings("SendToConsole", "SendToConsole")
        self._resize_edges = 0
        self._resize_origin: QRect | None = None
        self._resize_start: QPoint | None = None

        self._root = QVBoxLayout(self)
        # Marge tout autour : c'est la bande que la palette conserve pour
        # elle, sans laquelle les bords seraient couverts par les enfants et
        # le redimensionnement impossible a attraper.
        self._root.setContentsMargins(RESIZE_MARGIN, RESIZE_MARGIN,
                                      RESIZE_MARGIN, RESIZE_MARGIN)
        self._root.setSpacing(0)

        self._handle = DragHandle(self)
        self._handle.close_requested.connect(self.hide)
        self._handle.orientation_toggled.connect(self.toggle_orientation)
        self._root.addWidget(self._handle)

        self._arrow_prev = self._make_arrow("‹")
        self._arrow_next = self._make_arrow("›")
        self._arrow_prev.clicked.connect(lambda: self._scroll_by(-1))
        self._arrow_next.clicked.connect(lambda: self._scroll_by(1))

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(QWidget())

        # Les fleches suivent la plage de defilement plutot qu'un evenement
        # ponctuel : elles apparaissent donc des que le contenu deborde, sans
        # attendre un redimensionnement.
        for bar in (self._scroll.horizontalScrollBar(),
                    self._scroll.verticalScrollBar()):
            bar.rangeChanged.connect(lambda _min, _max: self._update_arrows())
            bar.valueChanged.connect(lambda _v: self._update_arrows())

        self._body: QWidget | None = None
        self._buttons_layout: QVBoxLayout | QHBoxLayout | None = None
        self._apply_orientation()

    # ------------------------------------------------------------ interne --
    def _make_arrow(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("paletteArrow")
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setAutoRepeat(True)
        btn.hide()
        return btn

    def _apply_orientation(self) -> None:
        vertical = self._orientation == VERTICAL

        # Reconstruire la disposition interne des boutons. takeWidget() rend
        # la propriete de l'ancien conteneur avant de le remplacer : sans
        # cela setWidget() le detruit lui-meme et le deleteLater() suivant
        # travaille sur un objet C++ deja libere.
        old_container = self._scroll.takeWidget()

        container = QWidget()
        self._buttons_layout = QVBoxLayout(container) if vertical \
            else QHBoxLayout(container)
        self._buttons_layout.setContentsMargins(8, 8, 8, 8)
        self._buttons_layout.setSpacing(6)
        self._buttons_layout.addStretch(1)
        self._scroll.setWidget(container)
        self._container = container

        if old_container is not None:
            # hide() avant setParent(None) : un widget detache devient une
            # fenetre de premier niveau, et sa destruction ferait croire a Qt
            # que la derniere fenetre vient de se fermer.
            old_container.hide()
            old_container.setParent(None)
            old_container.deleteLater()

        # Les fleches encadrent la zone defilante DANS LE SENS du defilement :
        # au-dessus / au-dessous en colonne, a gauche / a droite en ligne.
        # C'est pour cela que la bande entiere est reconstruite ici.
        old_body = self._body
        if old_body is not None:
            self._root.removeWidget(old_body)
            for child in (self._arrow_prev, self._scroll, self._arrow_next):
                child.hide()
                child.setParent(None)
            old_body.hide()
            old_body.setParent(None)
            old_body.deleteLater()

        self._body = QWidget()
        body_layout = QVBoxLayout(self._body) if vertical \
            else QHBoxLayout(self._body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self._arrow_prev)
        body_layout.addWidget(self._scroll, stretch=1)
        body_layout.addWidget(self._arrow_next)
        self._scroll.show()
        self._root.insertWidget(1, self._body, stretch=1)

        # Les enfants HERITENT du curseur de leur parent : sans un curseur
        # pose explicitement sur eux, la double fleche de redimensionnement
        # restait affichee des qu'on quittait un bord vers l'interieur, la
        # palette ne recevant plus d'evenement de souris.
        self._body.setCursor(Qt.CursorShape.ArrowCursor)
        self._handle.setCursor(Qt.CursorShape.SizeAllCursor)

        if vertical:
            self._arrow_prev.setText("˄")
            self._arrow_next.setText("˅")
            self._arrow_prev.setFixedSize(16777215, 16)
            self._arrow_next.setFixedSize(16777215, 16)
        else:
            self._arrow_prev.setText("‹")
            self._arrow_next.setText("›")
            self._arrow_prev.setFixedSize(18, 16777215)
            self._arrow_next.setFixedSize(18, 16777215)

        self._handle.orient_btn.setText("⇄" if vertical else "⇅")

        # Taille minimale cohérente selon l'orientation.
        if vertical:
            self.setMinimumSize(180, 120)
        else:
            self.setMinimumSize(220, 90)
        self.setMaximumSize(4000, 3000)

        saved = self._settings.value(f"palette/size_{self._orientation}")
        if saved is not None:
            try:
                width, height = (int(v) for v in saved)
                self.resize(max(160, width), max(90, height))
            except (TypeError, ValueError):
                self.resize(*self._default_size(vertical))
        else:
            self.resize(*self._default_size(vertical))

        self.refresh(self._entries)

    @staticmethod
    def _default_size(vertical: bool) -> tuple[int, int]:
        return (300, 340) if vertical else (640, 118)

    def _remember_size(self) -> None:
        self._settings.setValue(f"palette/size_{self._orientation}",
                                [self.width(), self.height()])

    def toggle_orientation(self) -> None:
        self._orientation = HORIZONTAL if self._orientation == VERTICAL else VERTICAL
        self._apply_orientation()
        self.orientation_changed.emit(self._orientation)

    @property
    def orientation(self) -> str:
        return self._orientation

    def set_orientation(self, orientation: str) -> None:
        if orientation not in (VERTICAL, HORIZONTAL):
            return
        if orientation != self._orientation:
            self._orientation = orientation
            self._apply_orientation()

    def _bar(self):
        return (self._scroll.verticalScrollBar()
                if self._orientation == VERTICAL
                else self._scroll.horizontalScrollBar())

    def _scroll_by(self, direction: int) -> None:
        bar = self._bar()
        bar.setValue(bar.value() + direction * max(40, bar.pageStep() // 2))
        self._update_arrows()

    def _update_arrows(self) -> None:
        bar = self._bar()
        overflowing = bar.maximum() > 0
        self._arrow_prev.setVisible(overflowing)
        self._arrow_next.setVisible(overflowing)
        if overflowing:
            self._arrow_prev.setEnabled(bar.value() > bar.minimum())
            self._arrow_next.setEnabled(bar.value() < bar.maximum())

    def paintEvent(self, event) -> None:
        """Peint le panneau arrondi. C'est ce dessin - et non le QSS - qui
        donne ses coins a la fenêtre, puisqu'une fenêtre sans bordure n'est
        pas decoupee par un border-radius."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(QBrush(QColor(theme.token("surface"))))
        painter.setPen(QPen(QColor(theme.token("palette_border")), 1))
        painter.drawRoundedRect(rect, PANEL_RADIUS, PANEL_RADIUS)

        # Grip visuel : 3 points sur le bord redimensionnable.
        dot = QColor(theme.token("palette_border"))
        dot.setAlpha(160)
        painter.setBrush(QBrush(dot))
        painter.setPen(Qt.PenStyle.NoPen)
        r = 2          # rayon des points
        gap = 6        # espace entre centres
        if self._orientation == VERTICAL:
            cx = self.width() // 2
            cy = self.height() - RESIZE_MARGIN + 1
            for dx in (-gap, 0, gap):
                painter.drawEllipse(cx + dx - r, cy - r, r * 2, r * 2)
        else:
            cx = self.width() - RESIZE_MARGIN + 1
            cy = self.height() // 2
            for dy in (-gap, 0, gap):
                painter.drawEllipse(cx - r, cy + dy - r, r * 2, r * 2)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_arrows()

    # ------------------------------------------------ redimensionnement --
    # La fenetre est sans bordure : Qt/Windows ne fournit aucune poignee. On
    # detecte donc soi-meme la proximite d'un bord, on change le curseur, et
    # on suit la souris pendant le glisser.
    EDGE_LEFT, EDGE_RIGHT, EDGE_TOP, EDGE_BOTTOM = 1, 2, 4, 8

    def _edges_at(self, pos: QPoint) -> int:
        """Restreint les bords actifs selon l'orientation :
        vertical  → haut/bas seulement ;
        horizontal → gauche/droite seulement."""
        edges = 0
        if self._orientation == VERTICAL:
            if pos.y() <= RESIZE_MARGIN:
                edges |= self.EDGE_TOP
            if pos.y() >= self.height() - RESIZE_MARGIN:
                edges |= self.EDGE_BOTTOM
        else:
            if pos.x() <= RESIZE_MARGIN:
                edges |= self.EDGE_LEFT
            if pos.x() >= self.width() - RESIZE_MARGIN:
                edges |= self.EDGE_RIGHT
        return edges

    @staticmethod
    def _cursor_for(edges: int) -> Qt.CursorShape:
        horizontal = edges & (QuickPalette.EDGE_LEFT | QuickPalette.EDGE_RIGHT)
        vertical = edges & (QuickPalette.EDGE_TOP | QuickPalette.EDGE_BOTTOM)
        if horizontal and vertical:
            top_left = edges & (QuickPalette.EDGE_LEFT | QuickPalette.EDGE_TOP)
            bottom_right = edges & (QuickPalette.EDGE_RIGHT | QuickPalette.EDGE_BOTTOM)
            if top_left in (5,) or bottom_right in (10,):
                return Qt.CursorShape.SizeFDiagCursor
            return Qt.CursorShape.SizeBDiagCursor
        if horizontal:
            return Qt.CursorShape.SizeHorCursor
        if vertical:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._edges_at(event.position().toPoint())
            if edges:
                self._resize_edges = edges
                self._resize_origin = self.geometry()
                self._resize_start = event.globalPosition().toPoint()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._resize_edges and self._resize_origin and self._resize_start:
            delta = event.globalPosition().toPoint() - self._resize_start
            rect = QRect(self._resize_origin)

            if self._resize_edges & self.EDGE_LEFT:
                rect.setLeft(rect.left() + delta.x())
            if self._resize_edges & self.EDGE_RIGHT:
                rect.setRight(rect.right() + delta.x())
            if self._resize_edges & self.EDGE_TOP:
                rect.setTop(rect.top() + delta.y())
            if self._resize_edges & self.EDGE_BOTTOM:
                rect.setBottom(rect.bottom() + delta.y())

            # Sans ce garde-fou, tirer un bord au-dela du bord oppose
            # retourne la fenetre.
            if rect.width() >= self.minimumWidth() and \
               rect.height() >= self.minimumHeight():
                self.setGeometry(rect)
            return

        edges = self._edges_at(event.position().toPoint())
        if edges:
            self.setCursor(QCursor(self._cursor_for(edges)))
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._resize_edges:
            self._resize_edges = 0
            self._resize_origin = None
            self._resize_start = None
            self._remember_size()
            self._update_arrows()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self.unsetCursor()
        super().leaveEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_no_activate()
        # A cet instant la disposition n'est pas encore calculee : la barre de
        # defilement annonce une plage nulle et les fleches restaient cachees
        # jusqu'au premier redimensionnement. On repasse donc une fois la
        # boucle d'evenements revenue - et rangeChanged prend le relais.
        self._update_arrows()
        QTimer.singleShot(0, self._update_arrows)
        self.visibility_changed.emit(True)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        # La palette peut se fermer par sa propre croix : sans ce signal, le
        # bouton de la fenetre principale restait bloque sur « Masquer ».
        self.visibility_changed.emit(False)

    def _apply_no_activate(self) -> None:
        """Pose WS_EX_NOACTIVATE sur le HWND réel. Les drapeaux Qt seuls ne
        suffisent pas a garantir que la fenêtre ne prend jamais le focus.

        Un échec n'était jamais visible (les appels Win32 ne lèvent pas
        d'exception, ils rendent 0) : le style est relu et, s'il manque,
        `no_activate_failed` est émis une fois pour avertir l'opérateur."""
        if not _is_native_windows():
            return          # plugin offscreen des tests : pas de vrai HWND
        if set_no_activate_style(int(self.winId())):
            return
        if not self._no_activate_warned:
            self._no_activate_warned = True
            self.no_activate_failed.emit()

    # ------------------------------------------------------------ contenu --
    def refresh(self, entries: list[QuickEntry]) -> None:
        self._entries = entries
        layout = self._buttons_layout
        if layout is None:
            return

        while layout.count() > 1:            # on garde le stretch final
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not entries:
            empty = QLabel("Aucune entrée.\nAjoute-en dans l'onglet Envoi rapide.")
            empty.setObjectName("paletteEmpty")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.insertWidget(0, empty)
            self._update_arrows()
            return

        vertical = self._orientation == VERTICAL
        for index, entry in enumerate(entries):
            btn = QPushButton(self._button_text(entry, vertical))
            btn.setObjectName("paletteButton")
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setToolTip(
                f"{entry.label}\nRaccourci : {format_hotkey(entry.mods, entry.vk)}"
                if entry.has_hotkey else entry.label)
            if vertical:
                # Hauteur fixe ; la largeur suit le conteneur.
                btn.setFixedHeight(BTN_H)
                btn.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Fixed)
            else:
                # Taille entièrement fixe : ni étirement ni compression.
                btn.setFixedSize(BTN_W_HORIZ, BTN_H)
                # Le libellé est tronqué pour rester dans la largeur fixe.
                btn.setText(self._elided(btn, entry, vertical))
            btn.clicked.connect(lambda _=False, e=entry: self.entry_clicked.emit(e))
            layout.insertWidget(index, btn)

        self._update_arrows()
        QTimer.singleShot(0, self._update_arrows)   # apres calcul de la disposition

    @staticmethod
    def _elided(button: QPushButton, entry: QuickEntry, vertical: bool) -> str:
        """Tronque le seul libellé ; le raccourci, lui, doit rester lisible."""
        # Polir d'abord : sinon la police du QSS (13 px) n'est pas encore
        # appliquée, la mesure est faite trop petite et le libellé débordait,
        # rogné des deux côtés au lieu d'être terminé par « … ».
        button.ensurePolished()
        metrics = button.fontMetrics()
        available = BTN_W_HORIZ - 24
        label = metrics.elidedText(entry.label, Qt.TextElideMode.ElideRight,
                                   available)
        if not entry.has_hotkey:
            return label
        return f"{label}\n{format_hotkey(entry.mods, entry.vk)}"

    @staticmethod
    def _button_text(entry: QuickEntry, vertical: bool) -> str:
        """Le raccourci est affiche sous le libellé : sans lui, la palette
        n'apprend pas a l'opérateur quelle combinaison déclenche quoi. Sur
        deux lignes dans les deux orientations, sinon un libellé un peu long
        rogne le raccourci."""
        if not entry.has_hotkey:
            return entry.label
        return f"{entry.label}\n{format_hotkey(entry.mods, entry.vk)}"
