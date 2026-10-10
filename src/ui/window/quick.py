"""Onglet Envoi rapide : table des entrees, ajout / modification /
suppression, raccourcis des entrees, palette, envoi rapide.

Partie de MainWindow (ui/main_window.py), deplacee telle quelle
lors du decoupage de la v0.7.2.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from core.hotkeys import format_hotkey, unsafe_hotkey_reason
from core.models import QuickEntry
from keyboard import backend as w32
from ui.quick_entry_dialog import QuickEntryDialog
from ui.theme import mark_secondary


class QuickSendMixin:
    """Methodes de MainWindow (voir la docstring du module)."""

    # ------------------------------------------------------- envoi rapide --
    def _build_quick_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        explain = QLabel(
            "Entrées courtes (mots de passe, licences) associées à un "
            "raccourci clavier GLOBAL : il répond même quand le focus est "
            "dans la console distante. Un même raccourci ne peut pas être "
            "attribué deux fois.")
        explain.setWordWrap(True)
        explain.setObjectName("hintLabel")
        layout.addWidget(explain)

        self.quick_table = QTableWidget(0, 4)
        self.quick_table.setHorizontalHeaderLabels(
            ["Libellé", "Raccourci", "ENTRÉE", "État"])
        self.quick_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.quick_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.quick_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.quick_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.quick_table.doubleClicked.connect(self._edit_entry)
        self.quick_table.itemChanged.connect(self._on_quick_item_changed)
        self.quick_table.itemSelectionChanged.connect(self._sync_buttons)
        # Voir la remarque sur content_edit : sans ce minimum bas, cette page
        # etait la plus exigeante des trois et imposait sa hauteur.
        self.quick_table.setMinimumHeight(60)
        layout.addWidget(self.quick_table, stretch=1)

        row = QHBoxLayout()
        add_btn = QPushButton("Ajouter")
        add_btn.clicked.connect(self._add_entry)
        self.edit_entry_btn = mark_secondary(QPushButton("Modifier"))
        self.edit_entry_btn.clicked.connect(self._edit_entry)
        self.del_entry_btn = mark_secondary(QPushButton("Supprimer"))
        self.del_entry_btn.clicked.connect(self._delete_entry)
        self.send_entry_btn = mark_secondary(
            QPushButton("Envoyer la sélection (3 s)"))
        self.send_entry_btn.clicked.connect(self._send_selected_entry)
        row.addWidget(add_btn)
        row.addWidget(self.edit_entry_btn)
        row.addWidget(self.del_entry_btn)
        row.addWidget(self.send_entry_btn)
        row.addStretch(1)

        self.palette_btn = mark_secondary(QPushButton())
        self.palette_btn.clicked.connect(self._toggle_palette)
        row.addWidget(self.palette_btn)
        self._sync_palette_button(self.palette_window.isVisible())

        self.orient_btn = mark_secondary(QPushButton())
        self.orient_btn.setToolTip(
            "Palette en colonne ou en ligne (aussi possible depuis son bandeau)")
        self.orient_btn.clicked.connect(self.palette_window.toggle_orientation)
        row.addWidget(self.orient_btn)
        layout.addLayout(row)

        self.palette_window.orientation_changed.connect(
            lambda _v: self._update_orient_button())
        self._update_orient_button()
        return tab

    def _update_orient_button(self) -> None:
        vertical = self.palette_window.orientation == "vertical"
        self.orient_btn.setText(
            "Palette en ligne" if vertical else "Palette en colonne")

    def _refresh_quick_table(self) -> None:
        self.quick_table.setRowCount(0)
        for entry in self.entries:
            row = self.quick_table.rowCount()
            self.quick_table.insertRow(row)

            label_item = QTableWidgetItem(entry.label)
            label_item.setData(Qt.ItemDataRole.UserRole, entry)
            self.quick_table.setItem(row, 0, label_item)
            self.quick_table.setItem(
                row, 1, QTableWidgetItem(format_hotkey(entry.mods, entry.vk)))
            self.quick_table.setItem(
                row, 2, QTableWidgetItem("oui" if entry.final_enter else "non"))

            # Case native : souris, Espace, et theme pris en charge par Qt.
            state_item = QTableWidgetItem(self._state_text(entry))
            state_item.setFlags(state_item.flags()
                                | Qt.ItemFlag.ItemIsUserCheckable)
            state_item.setCheckState(Qt.CheckState.Checked if entry.enabled
                                     else Qt.CheckState.Unchecked)
            state_item.setToolTip("Cocher : présente dans la palette, "
                                  "raccourci actif")
            self.quick_table.setItem(row, 3, state_item)
            self._mark_row_enabled(row, entry.enabled)

        self.palette_window.refresh(self.entries)
        self._sync_buttons()

    @staticmethod
    def _state_text(entry: QuickEntry) -> str:
        if not entry.enabled:
            return "désactivé"
        if entry.has_hotkey and not entry.registered:
            return "refusé"
        return "actif"

    def _on_quick_item_changed(self, item: QTableWidgetItem) -> None:
        """Case « État » cochée / décochée : désactivée, l'entrée quitte la
        palette et son raccourci global est libéré."""
        if item.column() != 3:
            return
        label_item = self.quick_table.item(item.row(), 0)
        entry = label_item.data(Qt.ItemDataRole.UserRole) if label_item else None
        enabled = item.checkState() == Qt.CheckState.Checked
        if entry is None or enabled == entry.enabled:
            return                      # remplissage de la table
        entry.enabled = enabled
        if enabled:
            self.hotkeys.register(entry)
        else:
            self.hotkeys.unregister(entry)
        # Hors du signal : l'enregistrement peut recharger toute la table
        # (conflit de coffre), qui ne doit pas changer pendant son itemChanged.
        QTimer.singleShot(0, lambda: self._finish_toggle(entry))

    def _finish_toggle(self, entry: QuickEntry) -> None:
        self._save_entries()
        if not any(e is entry for e in self.entries):
            return                      # coffre rechargé : table déjà à jour
        self._sync_entry_row(entry)
        refused = " (raccourci refusé)" if self._state_text(
            entry) == "refusé" else ""
        self._log(f"Entrée « {entry.label} » "
                  f"{'activée' if entry.enabled else 'désactivée'}{refused}.")

    def _mark_row_enabled(self, row: int, enabled: bool) -> None:
        """Ligne d'une entrée désactivée en italique (aucune couleur en dur :
        lisible dans les deux thèmes)."""
        for column in range(self.quick_table.columnCount()):
            item = self.quick_table.item(row, column)
            if item is not None:
                font = item.font()
                font.setItalic(not enabled)
                item.setFont(font)

    def _sync_entry_row(self, entry: QuickEntry) -> None:
        """Met à jour sur place la ligne de `entry` (sélection conservée) et
        la palette. Rien si l'entrée a disparu (coffre rechargé)."""
        self.palette_window.refresh(self.entries)
        for row in range(self.quick_table.rowCount()):
            label_item = self.quick_table.item(row, 0)
            if label_item and label_item.data(Qt.ItemDataRole.UserRole) is entry:
                state_item = self.quick_table.item(row, 3)
                self.quick_table.blockSignals(True)
                try:
                    state_item.setText(self._state_text(entry))
                    state_item.setCheckState(
                        Qt.CheckState.Checked if entry.enabled
                        else Qt.CheckState.Unchecked)
                    self._mark_row_enabled(row, entry.enabled)
                finally:
                    self.quick_table.blockSignals(False)
                return

    def _selected_entry(self) -> Optional[QuickEntry]:
        rows = self.quick_table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.quick_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def _add_entry(self) -> None:
        dlg = QuickEntryDialog(self.entries, parent=self,
                               reserved=self._app_hotkeys)
        if not self._exec_entry_dialog(dlg):
            return
        entry = dlg.result_entry()
        self.entries.append(entry)
        self.hotkeys.register(entry)
        self._save_entries()
        self._refresh_quick_table()
        self._log(f"Entrée ajoutée : {entry.label}")

    def _edit_entry(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        dlg = QuickEntryDialog(self.entries, entry=entry, parent=self,
                               reserved=self._app_hotkeys)
        if not self._exec_entry_dialog(dlg):
            return

        updated = dlg.result_entry()
        self.hotkeys.unregister(entry)
        index = self.entries.index(entry)
        self.entries[index] = updated
        self.hotkeys.register(updated)
        self._save_entries()
        self._refresh_quick_table()
        self._log(f"Entrée modifiée : {updated.label}")

    def _delete_entry(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if QMessageBox.question(
                self, "Supprimer",
                f"Supprimer l'entrée « {entry.label} » ?") != QMessageBox.StandardButton.Yes:
            return
        self.hotkeys.unregister(entry)
        self.entries.remove(entry)
        self._save_entries()
        self._refresh_quick_table()
        self._log(f"Entrée supprimée : {entry.label}")

    def _send_selected_entry(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        self._quick_send(entry, countdown=3)

    def _on_hotkey(self, entry: QuickEntry, hwnd: int) -> None:
        if self._pause_for_hotkey_during_send():
            return
        # hwnd a ete releve dans le filtre d'evenements, a l'instant meme du
        # raccourci : c'est la fenêtre que l'operateur regardait.
        if not self._is_foreign_target(hwnd):
            # Comme pour « Coller » : pas de repli sur la derniere fenetre de
            # travail (reserve au clic sur la palette), le secret partirait
            # dans une fenetre que l'operateur ne regarde pas.
            reason = "raccourci pressé hors d'une fenêtre cible"
            self._deny(reason)
            self._notify(f"Envoi rapide refusé : {reason}", from_hotkey=True)
            return
        self._quick_send(entry, countdown=0, hwnd=hwnd)

    def _is_foreign_target(self, hwnd: int) -> bool:
        """Fenetre valide pour une frappe declenchee par raccourci : non nulle
        (transition d'activation) et exterieure a l'outil."""
        return bool(hwnd) and not w32.belongs_to_process(hwnd, self._own_pid)

    def _pause_for_hotkey_during_send(self) -> bool:
        """Un raccourci global presse PENDANT un envoi : ses modificateurs
        (Ctrl/Alt) sont physiquement tenus pendant que l'outil tape, et
        chaque caractere injecte deviendrait Ctrl+lettre ou Alt+lettre dans
        la cible. On met l'envoi en pause (Reprendre pour continuer) et on
        ignore le raccourci. Rend True si c'etait le cas."""
        if not self.controller.is_running:
            return False
        # Masque d'Alt (regle 2bis) dans TOUS les etats : sinon son relachement
        # active la barre de menus de la cible, meme avant la premiere frappe.
        w32.release_held_modifiers()
        if self.controller.pause():
            self._deny("raccourci pressé pendant un envoi : envoi mis en "
                       "pause (Reprendre pour continuer)")
        else:
            # Compte a rebours, relachement des touches, reprise, deja en
            # pause : chaque depart de frappe passe par _begin_sending, qui
            # attend le relachement physique des modificateurs.
            self._deny("un envoi est déjà en pause"
                       if self.controller.state == "paused"
                       else "un envoi est déjà en cours")
        return True

    def _exec_entry_dialog(self, dlg: QuickEntryDialog) -> bool:
        """Ouvre le dialogue d'entree, raccourcis globaux suspendus pendant
        la saisie de la combinaison. Rend True si valide."""
        self._suspend_during_capture(dlg.hotkey_edit)
        try:
            return dlg.exec() == QuickEntryDialog.DialogCode.Accepted
        finally:
            self.hotkeys.resume()

    def _on_palette_click(self, entry: QuickEntry) -> None:
        self._quick_send(entry, countdown=0)

    def _on_hotkey_refused(self, entry: QuickEntry) -> None:
        reason = (unsafe_hotkey_reason(entry.mods, entry.vk,
                                       w32.current_layout())
                  or "déjà pris par une autre application.")
        self._log(
            f"Raccourci {format_hotkey(entry.mods, entry.vk)} de l'entrée "
            f"« {entry.label} » refusé : {reason}", "WARN")

    def _quick_send(self, entry: QuickEntry, countdown: int,
                    hwnd: int = 0) -> None:
        """Envoi rapide : on ne force pas le premier plan. Le curseur est déjà
        dans le champ voulu au moment ou le raccourci tombe.

        'hwnd' : fenêtre visee relevee par l'appelant a l'instant du
        declenchement. A defaut, on lit le premier plan maintenant."""
        if self.controller.is_running:
            self._deny("un envoi est déjà en cours")
            return
        if not entry.text:
            self._deny(f"l'entrée « {entry.label} » est vide")
            return

        if not hwnd:
            hwnd = w32.get_foreground_window()
        restore = False
        if not hwnd or w32.belongs_to_process(hwnd, self._own_pid):
            # Le focus est sur l'outil (clic sur la palette) : on retombe sur
            # la derniere fenetre de travail connue et on lui rend le focus.
            hwnd = self._last_foreground
            restore = True

        if not hwnd:
            self._deny("aucune fenêtre de travail identifiée")
            return

        built = self._build(entry.text, final_enter=entry.final_enter,
                            target_hwnd=hwnd)
        if built is None:
            return

        target_title = w32.title_of(hwnd) or f"0x{hwnd:X}"
        self._log(f"Envoi rapide « {entry.label} » vers {target_title}, "
                  f"clavier {w32.layout_name(self.converter.hkl_local)}.")
        self._warn_if_elevated(hwnd)
        self.controller.start(built.tokens, self.converter.hkl_local, hwnd,
                              countdown=countdown, force_foreground=False,
                              restore_focus=restore)

    def _toggle_palette(self) -> None:
        if self.palette_window.isVisible():
            self.palette_window.hide()
        else:
            self.palette_window.refresh(self.entries)
            self.palette_window.show()
        # Le libellé n'est plus pose ici : il suit la visibilité reelle de la
        # palette (signal visibility_changed), sans quoi une fermeture par la
        # croix de la palette laissait le bouton bloque sur « Masquer ».

    def _sync_palette_button(self, visible: bool) -> None:
        self.palette_btn.setText(
            "Masquer la palette" if visible else "Afficher la palette")
