"""Onglet Contenu : saisie ou chargement du texte, estimation, envoi.

Partie de MainWindow (ui/main_window.py), deplacee telle quelle
lors du decoupage de la v0.7.2.
"""

from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from keyboard import backend as w32
from ui.theme import mark_secondary


class ContentMixin:
    """Methodes de MainWindow (voir la docstring du module)."""

    # ------------------------------------------------------------ contenu --
    # ------------------------------------------------------------ contenu --
    def _build_content_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        row = QHBoxLayout()
        load_btn = mark_secondary(QPushButton("Charger un fichier…"))
        load_btn.clicked.connect(self._load_file)
        clear_btn = mark_secondary(QPushButton("Vider"))
        clear_btn.clicked.connect(lambda: self.content_edit.clear())
        row.addWidget(load_btn)
        row.addWidget(clear_btn)

        self.file_label = QLabel("(ou tape / colle directement ci-dessous)")
        self.file_label.setObjectName("hintLabel")
        row.addWidget(self.file_label, stretch=1)
        layout.addLayout(row)

        self.content_edit = QPlainTextEdit()
        # Minimum volontairement bas : c'est la hauteur MINIMALE des pages qui
        # decide de la taille de la zone a onglets. Si une page en exige plus
        # que les autres, la zone change de hauteur a chaque changement
        # d'onglet et la fenetre entiere sautille.
        self.content_edit.setMinimumHeight(60)
        self.content_edit.setObjectName("monoEdit")
        self.content_edit.setPlaceholderText(
            "Contenu à taper dans la console, ligne par ligne…")
        self.content_edit.textChanged.connect(self._update_estimate)
        layout.addWidget(self.content_edit, stretch=1)

        options = QHBoxLayout()
        self.skip_comments_chk = QCheckBox("Ignorer les lignes # et ;")
        self.skip_empty_chk = QCheckBox("Ignorer les lignes vides")
        self.final_enter_chk = QCheckBox("ENTRÉE après la dernière ligne")
        self.final_enter_chk.setChecked(True)
        options.addWidget(self.skip_comments_chk)
        options.addWidget(self.skip_empty_chk)
        options.addWidget(self.final_enter_chk)
        options.addStretch(1)
        layout.addLayout(options)

        self.estimate_label = QLabel("Aucun contenu.")
        self.estimate_label.setObjectName("hintLabel")
        layout.addWidget(self.estimate_label)
        return tab

    # ============================================================= contenu --
    def _load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Charger un fichier", "",
            "Fichiers texte (*.txt *.conf *.cfg *.yaml *.yml *.sh *.ini);;"
            "Tous les fichiers (*.*)")
        if not path:
            return
        try:
            content = open(path, "r", encoding="utf-8", errors="replace").read()
        except OSError as exc:
            self._log(f"Lecture impossible : {exc}", "ERR")
            return

        self.content_edit.setPlainText(content)
        self.file_label.setText(os.path.basename(path))
        self._log(f"Fichier chargé : {path}")

        non_ascii = {c for c in content if not (0x20 <= ord(c) <= 0x7E or c in "\t\r\n")}
        if non_ascii:
            self._log(
                f"{len(non_ascii)} caractère(s) non-ASCII : les consoles BMC "
                f"les transmettent souvent mal.", "WARN")

    def _update_estimate(self) -> None:
        text = self.content_edit.toPlainText()
        if not text:
            self.estimate_label.setText("Aucun contenu.")
            return
        lines = text.splitlines()
        chars = sum(len(line) for line in lines)
        ms = chars * self.char_delay_spin.value() + len(lines) * self.line_delay_spin.value()
        self.estimate_label.setText(
            f"{len(lines)} ligne(s) | {chars} caractère(s) | durée estimée ~ {ms / 1000:.1f} s")

    def _start_content_send(self) -> None:
        text = self.content_edit.toPlainText()
        if not text.strip():
            self._set_status("Aucun contenu à envoyer.")
            return
        if not self._target_hwnd:
            self._set_status("Sélectionne d'abord une fenêtre cible.")
            return

        self._update_target_layout_label()
        built = self._build(text, final_enter=self.final_enter_chk.isChecked(),
                            target_hwnd=self._target_hwnd,
                            skip_comments=self.skip_comments_chk.isChecked(),
                            skip_empty=self.skip_empty_chk.isChecked())
        if built is None:
            return

        self._log(f"Envoi du contenu vers « {self._target_title} » "
                  f"({len(built.tokens)} frappe(s)), clavier "
                  f"{w32.layout_name(self.converter.hkl_local)}.")
        self._warn_if_elevated(self._target_hwnd)
        self.controller.start(built.tokens, self.converter.hkl_local,
                              self._target_hwnd,
                              countdown=self.countdown_spin.value(),
                              force_foreground=True)
