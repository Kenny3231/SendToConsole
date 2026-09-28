"""Onglet Clavier cible : mode de conversion, disposition de la
console, apercu.

Partie de MainWindow (ui/main_window.py), deplacee telle quelle
lors du decoupage de la v0.7.2.
"""

from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

from keyboard import backend as w32
from keyboard.layout_converter import MODE_LAYOUT, MODE_NONE
from keyboard.layouts import KNOWN_LAYOUTS
from ui.theme import mark_secondary

# Échantillon de l'aperçu de conversion (jamais le contenu réel : secret possible).
PREVIEW_SAMPLE = "azerty AZERTY 0123 @#|\\{}[]~`^¨éèàùç€"


class KeyboardTabMixin:
    """Methodes de MainWindow (voir la docstring du module)."""

    # ------------------------------------------------------ clavier cible --
    def _build_keyboard_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        explain = QLabel(
            "Ce qui arrive dans la console, ce sont des scancodes, pas des "
            "caractères : si la console n'utilise pas la même disposition que "
            "ton poste, les caractères spéciaux sortent faux. Choisis ici la "
            "disposition de la console distante.")
        explain.setWordWrap(True)
        explain.setObjectName("hintLabel")
        layout.addWidget(explain)

        # Formulaire : les deux listes démarrent à la même abscisse.
        form = QFormLayout()
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignVCenter)
        self.conv_mode_combo = QComboBox()
        self.conv_mode_combo.addItems([
            "Aucune - la console a la même disposition que mon poste",
            "Convertir vers la disposition de la console",
        ])
        self.conv_mode_combo.currentIndexChanged.connect(self._on_conv_mode_changed)
        form.addRow("Conversion :", self.conv_mode_combo)

        self.layout_combo = QComboBox()
        self.layout_combo.addItems([name for name, _ in KNOWN_LAYOUTS])
        self.layout_combo.setEnabled(False)
        self.layout_combo.currentIndexChanged.connect(self._on_target_layout_changed)
        form.addRow("Disposition de la console :", self.layout_combo)
        layout.addLayout(form)

        self.local_layout_label = QLabel()
        self.local_layout_label.setObjectName("hintLabel")
        layout.addWidget(self.local_layout_label)

        preview_btn = mark_secondary(QPushButton("Aperçu de la conversion"))
        preview_btn.clicked.connect(self._preview_conversion)
        preview_row = QHBoxLayout()
        preview_row.addWidget(preview_btn)
        preview_row.addStretch(1)
        layout.addLayout(preview_row)

        layout.addStretch(1)
        self._update_local_layout_label()
        return tab

    # ======================================================= clavier cible --
    def _update_local_layout_label(self) -> None:
        self.converter.refresh_local()
        self.local_layout_label.setText(
            f"Disposition détectée sur ce poste : 0x{self.converter.hkl_local:X}"
            if w32.IS_REAL else
            "Disposition locale indisponible en mode simulation.")

    def _on_conv_mode_changed(self, index: int) -> None:
        self.converter.set_mode(MODE_LAYOUT if index == 1 else MODE_NONE)
        self.layout_combo.setEnabled(index == 1)
        if index == 1:
            self._on_target_layout_changed(self.layout_combo.currentIndex())

    def _on_target_layout_changed(self, index: int) -> None:
        if self.converter.mode != MODE_LAYOUT:
            return
        name = KNOWN_LAYOUTS[index][0]
        if self.converter.set_target_by_index(index):
            self._log(f"Disposition de la console : {name}")
        else:
            self._log(f"Disposition « {name} » indisponible sur ce poste.", "ERR")

    def _preview_conversion(self) -> None:
        """Aperçu sur un échantillon FIXE : le contenu à envoyer peut être un
        secret, il n'apparait jamais dans le journal (seul un décompte)."""
        typed, missing = self.converter.preview(PREVIEW_SAMPLE)
        self._log(f"Aperçu - voulu  : {PREVIEW_SAMPLE}")
        self._log(f"Aperçu - frappé : {typed}")
        if missing:
            self._log(f"Sans correspondance : {' '.join(sorted(missing))}", "WARN")
        _, content_missing = self.converter.preview(self.content_edit.toPlainText())
        if content_missing:
            self._log(
                f"Contenu à envoyer : {len(content_missing)} caractère(s) "
                "distinct(s) sans correspondance sur la disposition cible.",
                "WARN")
