"""Machinerie d'envoi commune : barre d'envoi, construction des
jetons, pause, refus, etat, progression, diagnostics, journal,
notifications du tray.

Partie de MainWindow (ui/main_window.py), deplacee telle quelle
lors du decoupage de la v0.7.2.
"""

from __future__ import annotations


from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QSizePolicy, QSpinBox, QVBoxLayout,
)

from keyboard import backend as w32
from keyboard.engine import build_tokens
from ui.theme import mark_secondary


class SendingMixin:
    """Methodes de MainWindow (voir la docstring du module)."""

    # ------------------------------------------------------------- actions --
    def _build_send_bar(self) -> QFrame:
        # Barre persistante ancree en bas, accessible depuis tous les onglets.
        bar = QFrame()
        bar.setObjectName("sendBar")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(3)

        # Ligne 1 : délais + boutons d'envoi
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        char_label = QLabel("Car :")
        row1.addWidget(char_label)
        self.char_delay_spin = QSpinBox()
        self.char_delay_spin.setRange(1, 1000)
        self.char_delay_spin.setValue(15)
        self.char_delay_spin.setSuffix(" ms")
        self.char_delay_spin.setMinimumWidth(86)
        self.char_delay_spin.valueChanged.connect(self._update_estimate)
        row1.addWidget(self.char_delay_spin)
        for widget in (char_label, self.char_delay_spin):
            widget.setToolTip("Délai entre deux caractères tapés")

        line_label = QLabel("Ligne :")
        row1.addWidget(line_label)
        self.line_delay_spin = QSpinBox()
        self.line_delay_spin.setRange(0, 10000)
        self.line_delay_spin.setValue(300)
        self.line_delay_spin.setSuffix(" ms")
        self.line_delay_spin.setMinimumWidth(96)
        self.line_delay_spin.valueChanged.connect(self._update_estimate)
        row1.addWidget(self.line_delay_spin)
        for widget in (line_label, self.line_delay_spin):
            widget.setToolTip("Pause après chaque ligne (après ENTRÉE)")

        countdown_label = QLabel("Délai :")
        row1.addWidget(countdown_label)
        self.countdown_spin = QSpinBox()
        self.countdown_spin.setRange(0, 60)
        self.countdown_spin.setValue(5)
        self.countdown_spin.setSuffix(" s")
        self.countdown_spin.setMinimumWidth(72)
        row1.addWidget(self.countdown_spin)
        for widget in (countdown_label, self.countdown_spin):
            widget.setToolTip(
                "Compte à rebours avant le début de l'envoi du contenu, "
                "le temps de placer le curseur dans la console")

        row1.addStretch(1)

        self.start_btn = QPushButton("\u25b6\u2002Démarrer")
        self.start_btn.setObjectName("startButton")
        self.start_btn.clicked.connect(self._start_content_send)
        row1.addWidget(self.start_btn)

        self.pause_btn = mark_secondary(QPushButton("Pause"))
        self.pause_btn.clicked.connect(self._toggle_pause)
        row1.addWidget(self.pause_btn)

        self.stop_btn = QPushButton("\u25a0\u2002Stop")
        self.stop_btn.setObjectName("stopButton")
        self.stop_btn.clicked.connect(lambda: self.controller.stop())
        row1.addWidget(self.stop_btn)
        layout.addLayout(row1)

        # Ligne 2 : indicateur cible + progression + statut + garde focus
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        self.bar_target_label = QLabel("Cible : (aucune)")
        self.bar_target_label.setObjectName("barTargetLabel")
        row2.addWidget(self.bar_target_label, stretch=2)

        self.progress = QProgressBar()
        self.progress.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Fixed)
        row2.addWidget(self.progress, stretch=1)

        self.status_label = QLabel("Prêt.")
        self.status_label.setObjectName("statusLabel")
        row2.addWidget(self.status_label, stretch=2)

        self.guard_chk = QCheckBox("Garde focus")
        self.guard_chk.setChecked(True)
        self.guard_chk.setToolTip(
            "Met l'envoi en pause si le focus quitte la fenêtre cible")
        self.guard_chk.toggled.connect(
            lambda v: setattr(self.controller, "guard_focus", v))
        row2.addWidget(self.guard_chk)
        layout.addLayout(row2)

        self._restore_delays()
        return bar

    def _notify(self, message: str, from_hotkey: bool = False) -> None:
        """Retour visible quand l'operateur ne regarde pas l'outil : bulle du
        tray, via main.py. Depuis un raccourci global, il est dans la console
        (fenetre cachee OU simplement derriere) ; depuis le bouton, seulement
        si la fenetre est cachee."""
        hidden = not self.isVisible() or self.isMinimized()
        if hidden or (from_hotkey and not self.isActiveWindow()):
            self.notify.emit(message)

    # ============================================================== commun --
    def _build(self, text: str, final_enter: bool, target_hwnd: int,
               skip_comments: bool = False, skip_empty: bool = False):
        # Reference de calcul des scancodes = disposition de la FENETRE VISEE.
        # Se fier a la notre produisait des caracteres faux des que Windows
        # avait memorise une langue de saisie differente pour cette
        # application.
        hkl_target = w32.layout_of_window(target_hwnd) or w32.current_layout()
        self.converter.hkl_local = hkl_target

        built = build_tokens(
            text,
            char_delay_ms=self.char_delay_spin.value(),
            line_delay_ms=self.line_delay_spin.value(),
            skip_comments=skip_comments,
            skip_empty=skip_empty,
            final_enter=final_enter,
            convert=self.converter.active,
            convert_fn=self.converter.convert,
            hkl_local=hkl_target,
        )
        if built is None:
            self._set_status("Rien à envoyer.")
            return None

        if built.unmapped:
            self._log(
                f"{len(built.unmapped)} caractère(s) distinct(s) sans "
                "correspondance sur la disposition cible, envoyés tels quels.",
                "WARN")

        self.progress.setMaximum(len(built.tokens))
        self.progress.setValue(0)
        return built

    def _toggle_pause(self) -> None:
        if self.controller.state == "paused":
            self.controller.resume()
        else:
            self.controller.pause()

    def _deny(self, reason: str) -> None:
        # Un refus doit se voir : le journal seul passe inapercu quand
        # l'opérateur regarde sa console et non l'outil.
        self.status_label.setProperty("denied", True)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.setText(f"Envoi rapide refusé - {reason}")
        self._log(f"Envoi rapide refusé : {reason}", "WARN")
        QTimer.singleShot(4000, self._clear_denied)

    def _clear_denied(self) -> None:
        self.status_label.setProperty("denied", False)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(done)

    def _on_diagnostics(self, fallback: set, failed: set) -> None:
        """Aucun caractère ne doit disparaitre en silence : c'est ainsi qu'un
        mot de passe arrive tronque sans que personne ne comprenne pourquoi.
        Seul le NOMBRE est affiché : le texte envoyé peut être un secret."""
        if fallback:
            self._log(
                f"{len(fallback)} caractère(s) distinct(s) sans touche sur la "
                "disposition de la cible, injectés en Unicode (une console "
                "distante peut les ignorer).", "WARN")
        if failed:
            self._log(
                f"NON TAPÉS : {len(failed)} caractère(s) distinct(s) — le "
                "texte reçu est incomplet.", "ERR")
            self._set_status(
                f"Attention : {len(failed)} caractère(s) n'ont pas pu être tapés.")

    def _on_finished(self) -> None:
        self._log("Envoi terminé.")
        self._sync_buttons()

    def _on_failed(self, message: str) -> None:
        self._set_status(f"Erreur : {message}")
        self._log(message, "ERR")
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        running = self.controller.is_running
        paused = self.controller.state == "paused"

        self.start_btn.setEnabled(not running and bool(self._target_hwnd))
        self.pause_btn.setEnabled(running)
        self.pause_btn.setText("Reprendre" if paused else "Pause")
        self.stop_btn.setEnabled(running)
        self.capture_btn.setEnabled(not running)
        has_entry = self._selected_entry() is not None
        self.send_entry_btn.setEnabled(not running and has_entry)
        # Sans sélection, Modifier/Supprimer ne faisaient rien, en silence.
        self.edit_entry_btn.setEnabled(has_entry)
        self.del_entry_btn.setEnabled(has_entry)

    def _log(self, message: str, level: str = "INFO") -> None:
        from datetime import datetime
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_edit.appendPlainText(f"[{stamp}] {level:<4} {message}")
