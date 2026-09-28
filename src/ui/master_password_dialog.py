"""Demande du master password au démarrage (déverrouillage du stockage).

Avec `choose_store=True`, le dialogue permet aussi de choisir le coffre :
un coffre récent, un coffre existant (« Ouvrir… ») ou un nouveau fichier
(« Nouveau… »). Un fichier existant n'est jamais écrasé : il est ouvert avec
son propre master password.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSizePolicy, QVBoxLayout,
)

from security.local_encrypted import LocalEncryptedProvider
from security.provider import SecretProvider, SecretProviderError
from ui.store_history import same_path, with_store_suffix
from ui.theme import mark_secondary

STORE_FILTER = "Coffre chiffré (*.enc);;Tous les fichiers (*.*)"


class MasterPasswordDialog(QDialog):
    """`choice` vaut "unlock" (stockage chiffré ouvert) ou "memory"
    (l'opérateur a choisi de travailler sans rien enregistrer).

    Après "unlock", `provider` est le coffre effectivement déverrouillé (il
    peut différer de celui passé au constructeur si l'opérateur en a choisi
    un autre)."""

    def __init__(self, provider: SecretProvider, first_run: bool,
                 allow_memory_mode: bool = True, parent=None, *,
                 choose_store: bool = False,
                 recent_stores: Iterable[Path] = (),
                 missing_last: Optional[Path] = None,
                 current_store: Optional[Path] = None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._first_run = first_run
        self._missing_last = missing_last
        self._current_store = current_store     # coffre déjà ouvert (Options)
        self.choice = "unlock"
        self.setWindowTitle("SendToConsole - déverrouillage")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        store_path: Optional[Path] = getattr(provider, "path", None)
        self.store_combo: Optional[QComboBox] = None
        self.missing_label = QLabel(
            "Le dernier coffre utilisé est introuvable (fichier déplacé, "
            "clé USB débranchée ?). « Ouvrir… » pour le retrouver, ou "
            "choisis un master password pour créer un coffre vide ici.",
            self)
        self.missing_label.setObjectName("hintLabel")
        self.missing_label.setProperty("warn", True)
        self.missing_label.setWordWrap(True)
        self.missing_label.hide()

        if choose_store and store_path is not None:
            store_row = QHBoxLayout()
            store_row.addWidget(QLabel("Coffre :"))
            self.store_combo = QComboBox()
            self.store_combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.store_combo.setMinimumContentsLength(30)
            self.store_combo.setSizePolicy(QSizePolicy.Policy.Expanding,
                                           QSizePolicy.Policy.Fixed)
            self._add_store_item(store_path)
            for recent in recent_stores:
                # Un coffre récent disparu (clé USB débranchée...) n'est pas
                # proposé : le choisir mènerait à en créer un nouveau.
                if recent.exists():
                    self._add_store_item(recent)
            self.store_combo.setCurrentIndex(0)
            self.store_combo.setToolTip(str(store_path))
            self.store_combo.currentIndexChanged.connect(self._on_store_index)
            store_row.addWidget(self.store_combo, stretch=1)

            self.open_store_btn = mark_secondary(QPushButton("Ouvrir…"))
            self.open_store_btn.setToolTip(
                "Utiliser un coffre existant (fichier .enc).")
            self.open_store_btn.clicked.connect(self._browse_existing)
            store_row.addWidget(self.open_store_btn)
            self.new_store_btn = mark_secondary(QPushButton("Nouveau…"))
            self.new_store_btn.setToolTip(
                "Créer un nouveau coffre vide. Un fichier existant n'est "
                "jamais écrasé.")
            self.new_store_btn.clicked.connect(self._browse_new)
            # Sinon Entrée, après une annulation de l'explorateur, le rouvre
            # au lieu de valider le mot de passe.
            self.open_store_btn.setAutoDefault(False)
            self.new_store_btn.setAutoDefault(False)
            store_row.addWidget(self.new_store_btn)
            layout.addLayout(store_row)
            layout.addWidget(self.missing_label)
        elif store_path is not None:
            path_label = QLabel(f"Coffre : {store_path}")
            path_label.setObjectName("hintLabel")
            path_label.setWordWrap(True)
            layout.addWidget(path_label)

        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("Master password")
        show_chk = QCheckBox("Afficher")
        show_chk.toggled.connect(self._toggle_visibility)
        pwd_row = QHBoxLayout()
        pwd_row.addWidget(self.password_edit, stretch=1)
        pwd_row.addWidget(show_chk)
        layout.addLayout(pwd_row)

        self.confirm_edit = QLineEdit()
        self.confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_edit.setPlaceholderText("Confirmation")
        layout.addWidget(self.confirm_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_btn.setIcon(QIcon())
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_btn.setText("Quitter" if parent is None else "Annuler")
        cancel_btn.setIcon(QIcon())
        mark_secondary(cancel_btn)

        if allow_memory_mode:
            # Sans mot de passe, l'outil reste utilisable : les entrees vivent
            # alors en memoire et disparaissent a la fermeture. C'est le
            # comportement du script PowerShell d'origine.
            skip_btn = buttons.addButton(
                "Continuer sans enregistrer",
                QDialogButtonBox.ButtonRole.ActionRole)
            skip_btn.setIcon(QIcon())
            mark_secondary(skip_btn)
            skip_btn.clicked.connect(self._choose_memory)

        buttons.accepted.connect(self._try_unlock)
        buttons.rejected.connect(self.reject)

        if allow_memory_mode:
            memory_help = QLabel(
                "« Continuer sans enregistrer » : l'outil fonctionne "
                "normalement, mais les entrées d'envoi rapide ne sont pas "
                "enregistrées et disparaissent à la fermeture.")
            memory_help.setWordWrap(True)
            memory_help.setObjectName("hintLabel")
            layout.addWidget(memory_help)

        layout.addWidget(buttons)

        # Entrée valide via le bouton par défaut : relier aussi returnPressed
        # lançait deux déverrouillages (deux PBKDF2, deux messages d'erreur).
        self._refresh_mode()
        # Le mot de passe se tape sans cliquer : le focus ne doit pas être
        # sur la liste des coffres (la recherche clavier en changerait).
        self.password_edit.setFocus()

    # ------------------------------------------------------------- coffre --
    @property
    def provider(self) -> SecretProvider:
        return self._provider

    @property
    def first_run(self) -> bool:
        return self._first_run

    def _item_text(self, path: Path) -> str:
        # Nom du fichier d'abord : c'est lui qui distingue les coffres, et
        # la fin d'un chemin long serait rognée par la liste.
        text = f"{path.name} — {path.parent}"
        if self._current_store is not None and same_path(path, self._current_store):
            return f"{text}  (actuel)"
        if path.exists():
            return text
        if self._missing_last is not None and same_path(path, self._missing_last):
            return f"{text}  (introuvable)"
        return f"{text}  (nouveau)"

    def _add_store_item(self, path: Path) -> int:
        """Ajoute `path` à la liste s'il n'y est pas ; rend son index."""
        combo = self.store_combo
        if combo is None:
            raise RuntimeError("Sélecteur de coffre absent (choose_store=False).")
        for index in range(combo.count()):
            if same_path(combo.itemData(index), path):
                return index
        combo.addItem(self._item_text(path), str(path))
        index = combo.count() - 1
        combo.setItemData(index, str(path), Qt.ItemDataRole.ToolTipRole)
        return index

    def _on_store_index(self, index: int) -> None:
        if index < 0 or self.store_combo is None:
            return
        self._use_store(Path(self.store_combo.itemData(index)))

    def select_store(self, path: Path) -> None:
        """Choisit `path` comme coffre (ajouté à la liste au besoin)."""
        if self.store_combo is None:
            return
        index = self._add_store_item(path)
        if index == self.store_combo.currentIndex():
            self._use_store(path)
        else:
            self.store_combo.setCurrentIndex(index)   # -> _on_store_index

    def _use_store(self, path: Path) -> None:
        current = getattr(self._provider, "path", None)
        if current is None or not same_path(current, path):
            self._provider = LocalEncryptedProvider(path)
        self._first_run = not self._provider.exists
        # Le mot de passe tapé visait l'autre coffre.
        self._clear_passwords()
        if self.store_combo is not None:
            self.store_combo.setToolTip(str(path))
        self._refresh_mode()
        self.password_edit.setFocus()

    def _clear_passwords(self) -> None:
        self.password_edit.clear()
        self.confirm_edit.clear()

    def _start_dir(self) -> str:
        path = getattr(self._provider, "path", None)
        return str(path.parent) if path is not None else ""

    def _browse_existing(self) -> None:
        chosen, _filter = QFileDialog.getOpenFileName(
            self, "Ouvrir un coffre existant", self._start_dir(), STORE_FILTER)
        if chosen:
            self.select_store(Path(chosen))

    def _browse_new(self) -> None:
        # Pas de confirmation d'ecrasement de Qt : un fichier existant n'est
        # de toute facon jamais ecrase, il est propose a l'ouverture.
        chosen, _filter = QFileDialog.getSaveFileName(
            self, "Créer un nouveau coffre", self._start_dir(), STORE_FILTER,
            options=QFileDialog.Option.DontConfirmOverwrite)
        if not chosen:
            return
        path = with_store_suffix(Path(chosen))
        if path.exists():
            answer = QMessageBox.question(
                self, "Coffre existant",
                f"Un fichier existe déjà :\n{path}\n\n"
                "Il ne sera pas écrasé. L'ouvrir avec son master password ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.select_store(path)

    def _refresh_mode(self) -> None:
        path = getattr(self._provider, "path", None)
        missing = (self._first_run and self._missing_last is not None
                   and path is not None and same_path(path, self._missing_last))
        self.missing_label.setVisible(missing)

        if self._first_run:
            message = (
                "Nouveau coffre : choisis un master password.\n\n"
                "Il chiffre le fichier où seront enregistrées tes entrées "
                "d'envoi rapide. Il n'est stocké nulle part : s'il est perdu, "
                "les entrées le sont aussi."
            )
            if self.store_combo is not None:
                message += ("\n\nTu as déjà un coffre ? « Ouvrir… » pour le "
                            "choisir.")
        else:
            message = "Master password pour déverrouiller tes entrées d'envoi rapide."
        self.message_label.setText(message)
        self.confirm_edit.setVisible(self._first_run)
        self.ok_btn.setText("Créer" if self._first_run else "Déverrouiller")
        self.adjustSize()

    # ----------------------------------------------------------- actions --
    def _choose_memory(self) -> None:
        self._clear_passwords()
        self.choice = "memory"
        self.accept()

    def _toggle_visibility(self, visible: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.password_edit.setEchoMode(mode)
        self.confirm_edit.setEchoMode(mode)

    def _try_unlock(self) -> None:
        password = self.password_edit.text()
        if not password:
            QMessageBox.warning(self, "Mot de passe vide",
                                "Saisis un master password.")
            return

        if self._first_run and password != self.confirm_edit.text():
            QMessageBox.warning(self, "Confirmation différente",
                                "Les deux saisies ne correspondent pas.")
            return

        # Le fichier a pu disparaître (clé USB retirée) depuis le choix : en
        # mode ouverture, unlock() créerait alors un coffre vide avec un mot
        # de passe saisi une seule fois.
        if not self._first_run and not self._provider.exists:
            self._first_run = True
            self._refresh_mode()
            QMessageBox.warning(
                self, "Coffre introuvable",
                "Le coffre choisi n'existe plus. Choisis-en un autre, ou "
                "confirme le master password pour créer un coffre vide ici.")
            self.confirm_edit.setFocus()
            return

        try:
            self._provider.unlock(password)
        except SecretProviderError as exc:
            del password
            self._clear_passwords()
            QMessageBox.critical(self, "Déverrouillage impossible", str(exc))
            self.password_edit.setFocus()
            return
        del password

        # Le dialogue peut survivre (parent, référence de l'appelant) : ne
        # pas y laisser le master password.
        self._clear_passwords()
        self.choice = "unlock"
        self.accept()

    def reject(self) -> None:
        self._clear_passwords()
        super().reject()
