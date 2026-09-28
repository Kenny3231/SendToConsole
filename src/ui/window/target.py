"""Onglet Cible : liste des fenetres, selection, capture (bouton ou
raccourci), avertissement administrateur, suivi du premier plan.

Partie de MainWindow (ui/main_window.py), deplacee telle quelle
lors du decoupage de la v0.7.2.
"""

from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton,
    QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from keyboard import backend as w32
from ui.theme import mark_secondary


class TargetMixin:
    """Methodes de MainWindow (voir la docstring du module)."""

    # ------------------------------------------------------- fenetre cible --
    def _build_target_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        row = QHBoxLayout()
        self.window_filter = QLineEdit()
        self.window_filter.setPlaceholderText("Filtrer par processus ou titre...")
        self.window_filter.textChanged.connect(self._refresh_windows)
        row.addWidget(self.window_filter, stretch=1)

        refresh_btn = mark_secondary(QPushButton("Rafraîchir"))
        refresh_btn.clicked.connect(self._refresh_windows)
        row.addWidget(refresh_btn)

        self.capture_btn = QPushButton("Capturer la fenêtre active (3 s)")
        self.capture_btn.clicked.connect(self._start_capture)
        row.addWidget(self.capture_btn)
        layout.addLayout(row)

        self.windows_table = QTableWidget(0, 4)
        self.windows_table.setHorizontalHeaderLabels(["Processus", "PID", "HWND", "Titre"])
        self.windows_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        self.windows_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.windows_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.windows_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.windows_table.itemSelectionChanged.connect(self._on_window_selected)
        layout.addWidget(self.windows_table, stretch=1)

        self.target_label = QLabel("Fenêtre de travail : (aucune)")
        self.target_label.setObjectName("targetLabel")
        layout.addWidget(self.target_label)

        self.target_layout_label = QLabel("")
        self.target_layout_label.setObjectName("hintLabel")
        layout.addWidget(self.target_layout_label)

        # Fenetre lancee en administrateur : SendInput y est bloque en
        # silence (UIPI). Masque tant que la cible est normale.
        self.target_elevation_label = QLabel("")
        self.target_elevation_label.setObjectName("hintLabel")
        self.target_elevation_label.setProperty("warn", True)
        self.target_elevation_label.setWordWrap(True)
        self.target_elevation_label.hide()
        layout.addWidget(self.target_elevation_label)
        return tab

    def _update_bar_target(self) -> None:
        if self._target_hwnd:
            t = self._target_title
            t = (t[:40] + "\u2026") if len(t) > 40 else t
            admin = "  ⚠ administrateur" if self._target_elevation else ""
            self.bar_target_label.setText(
                f"Cible : {t}  (0x{self._target_hwnd:X}){admin}")
            self.bar_target_label.setProperty("active", True)
        else:
            self.bar_target_label.setText("Cible : (aucune)")
            self.bar_target_label.setProperty("active", False)
        self.bar_target_label.style().unpolish(self.bar_target_label)
        self.bar_target_label.style().polish(self.bar_target_label)

    # ============================================================ fenetres --
    def _refresh_windows(self) -> None:
        needle = self.window_filter.text().strip().lower()
        windows = w32.list_windows()
        if needle:
            windows = [w for w in windows
                       if needle in w.process_name.lower() or needle in w.title.lower()]

        self.windows_table.setRowCount(0)
        for win in windows:
            row = self.windows_table.rowCount()
            self.windows_table.insertRow(row)
            self.windows_table.setItem(row, 0, QTableWidgetItem(win.process_name))
            self.windows_table.setItem(row, 1, QTableWidgetItem(str(win.pid)))
            self.windows_table.setItem(row, 2, QTableWidgetItem(f"0x{win.hwnd:X}"))
            title_item = QTableWidgetItem(win.title)
            title_item.setData(Qt.ItemDataRole.UserRole, win.hwnd)
            self.windows_table.setItem(row, 3, title_item)
        # La fenetre de travail reste selectionnee apres un rafraichissement
        # ou un filtrage (sans redeclencher _set_target).
        self._select_target_row(notify=False)

    def _find_window_row(self, hwnd: int) -> int:
        for row in range(self.windows_table.rowCount()):
            item = self.windows_table.item(row, 3)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == hwnd:
                return row
        return -1

    def _select_target_row(self, notify: bool = True) -> bool:
        """Selectionne la ligne de la fenetre de travail, comme un clic.
        Rend False si elle n'est pas dans la liste (fenetre fermee...)."""
        row = self._find_window_row(self._target_hwnd) if self._target_hwnd else -1
        if row < 0:
            return False
        was_blocked = self.windows_table.signalsBlocked()
        if not notify:
            self.windows_table.blockSignals(True)
        try:
            self.windows_table.selectRow(row)
            self.windows_table.scrollToItem(self.windows_table.item(row, 3))
        finally:
            self.windows_table.blockSignals(was_blocked)
        return True

    def _on_window_selected(self) -> None:
        rows = self.windows_table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.windows_table.item(rows[0].row(), 3)
        self._set_target(item.data(Qt.ItemDataRole.UserRole), item.text())

    def _set_target(self, hwnd: int, title: str) -> None:
        changed = hwnd != self._target_hwnd
        self._target_hwnd = hwnd
        self._target_title = title
        self.target_label.setText(f"Fenêtre de travail : {title}  (0x{hwnd:X})")
        self._update_target_layout_label()
        self._target_elevation = self._elevation_warning(hwnd)
        self.target_elevation_label.setText(self._target_elevation)
        self.target_elevation_label.setVisible(bool(self._target_elevation))
        if changed and self._target_elevation:
            self._log(self._target_elevation, "WARN")
        self._update_bar_target()
        self._sync_buttons()

    def _elevation_warning(self, hwnd: int) -> str:
        """Avertissement si `hwnd` tourne a une integrite superieure a
        l'outil (console « en administrateur ») : Windows y bloque SendInput
        SANS erreur, rien n'est tape. Chaine vide sinon. Avertir seulement :
        une detection « probable » ne doit jamais bloquer un envoi."""
        state = w32.is_elevated_above_us(hwnd)
        if state is False:
            return ""
        if state:
            return ("⚠ Cette fenêtre tourne en administrateur : Windows "
                    "bloque les frappes simulées d'un programme non "
                    "administrateur, rien n'y sera tapé. Relance "
                    "SendToConsole en administrateur (clic droit > "
                    "Exécuter en tant qu'administrateur).")
        return ("⚠ Cette fenêtre tourne peut-être en administrateur (ou sous "
                "un autre compte Windows). Si rien n'y est tapé, relance "
                "SendToConsole en administrateur (clic droit > Exécuter en "
                "tant qu'administrateur).")

    def _warn_if_elevated(self, hwnd: int) -> None:
        """Rappel au moment d'envoyer (l'envoi part quand meme)."""
        warning = self._elevation_warning(hwnd)
        if warning:
            self._log(warning, "WARN")
            self._set_status("Attention : cible administrateur, les frappes "
                             "seront probablement bloquées.")

    def _update_target_layout_label(self) -> None:
        if not self._target_hwnd:
            self.target_layout_label.setText("")
            return

        target_hkl = w32.layout_of_window(self._target_hwnd)
        own_hkl = w32.current_layout()
        target_name = w32.layout_name(target_hkl)

        if target_hkl and own_hkl and target_hkl != own_hkl:
            self.target_layout_label.setText(
                f"⚠ Clavier de cette fenêtre : {target_name} — différent du "
                f"tien ({w32.layout_name(own_hkl)}). Les frappes sont "
                f"calculées pour {target_name}.")
            self.target_layout_label.setProperty("warn", True)
        else:
            self.target_layout_label.setText(
                f"Clavier de cette fenêtre : {target_name}")
            self.target_layout_label.setProperty("warn", False)

        self.target_layout_label.style().unpolish(self.target_layout_label)
        self.target_layout_label.style().polish(self.target_layout_label)

    def _start_capture(self) -> None:
        self._pick_left = 3
        self.capture_btn.setEnabled(False)
        self._set_status(f"Clique sur la fenêtre à cibler… {self._pick_left}")
        self._pick_timer.start()

    def _pick_tick(self) -> None:
        self._pick_left -= 1
        if self._pick_left > 0:
            self._set_status(f"Clique sur la fenêtre à cibler… {self._pick_left}")
            return

        self._pick_timer.stop()
        self.capture_btn.setEnabled(True)
        self._capture_window(w32.get_foreground_window())

    def _capture_window(self, hwnd: int, from_hotkey: bool = False) -> bool:
        """Prend `hwnd` comme fenetre de travail et la selectionne dans la
        liste Cible, comme si l'operateur avait clique sa ligne. Commun au
        bouton (apres compte a rebours) et au raccourci global."""
        if not hwnd:
            self._set_status("Capture annulée : aucune fenêtre au premier plan.")
            self._log("Capture annulée (aucune fenêtre au premier plan).", "WARN")
            self._notify("Capture annulée : aucune fenêtre au premier plan.",
                         from_hotkey)
            return False
        if w32.belongs_to_process(hwnd, self._own_pid):
            self._set_status("Capture annulée : l'outil était au premier plan.")
            self._log("Capture annulée (fenêtre de l'outil au premier plan).", "WARN")
            self._notify("Capture annulée : l'outil était au premier plan.",
                         from_hotkey)
            return False

        title = w32.title_of(hwnd) or "(sans titre)"
        self._set_target(hwnd, title)
        self._refresh_windows()
        if self._find_window_row(hwnd) < 0 and self.window_filter.text():
            # Le filtre masquait la fenetre capturee : on le vide
            # (textChanged relance _refresh_windows, qui la selectionne).
            self.window_filter.clear()
        if not self._select_target_row():
            self._log("Fenêtre capturée absente de la liste (fenêtre "
                      "outil ou sans titre) : elle reste la cible.", "WARN")
        self._log(f"Capture : 0x{hwnd:X} - {title}")
        admin = ""
        if self._target_elevation:
            admin = (" (administrateur : frappes bloquées)"
                     if "tourne en administrateur" in self._target_elevation
                     else " (administrateur ? frappes peut-être bloquées)")
        self._set_status(f"Cible : {title}{admin}")
        self._notify(f"Cible : {title}{admin}", from_hotkey)
        return True

    def _track_foreground(self) -> None:
        """Memorise en continu la dernière fenêtre active exterieure a l'outil."""
        hwnd = w32.get_foreground_window()
        if not hwnd:
            return
        if w32.belongs_to_process(hwnd, self._own_pid):
            return
        self._last_foreground = hwnd
