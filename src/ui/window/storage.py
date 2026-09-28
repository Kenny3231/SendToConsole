"""Coffre : bandeau, chemins, ouverture d'un autre coffre, « Enregistrer
sous », bascule en memoire, reinitialisation, chargement et
enregistrement des entrees.

Partie de MainWindow (ui/main_window.py), deplacee telle quelle
lors du decoupage de la v0.7.2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog, QMessageBox,
)

from security.local_encrypted import (
    LocalEncryptedProvider, redirected_store_path)
from security.provider import SecretProvider, SecretProviderError
from ui.master_password_dialog import STORE_FILTER
from ui.store_history import (
    LAST_KEY, recent_stores, remember_store, same_path, with_store_suffix)


class StorageMixin:
    """Methodes de MainWindow (voir la docstring du module)."""

    # ------------------------------------------------------------ options --
    @property
    def _is_volatile(self) -> bool:
        return getattr(self.provider, "volatile", False)

    def _update_storage_banner(self) -> None:
        if self._is_volatile:
            self.storage_banner.setText(
                "Mode sans enregistrement : les entrées d'envoi rapide "
                "disparaîtront à la fermeture.")
            self.storage_banner.setProperty("warn", True)
            self.storage_banner.setVisible(True)
        else:
            self.storage_banner.setVisible(False)
            self.storage_banner.setProperty("warn", False)
        self.storage_banner.style().unpolish(self.storage_banner)
        self.storage_banner.style().polish(self.storage_banner)

    def _store_paths(self) -> tuple[str, str]:
        """(chemin annonce, chemin réel) - les deux différent quand Windows
        virtualise les écritures du Python du Microsoft Store."""
        path = getattr(self.provider, "path", None)
        if path is None:
            return "(non défini)", ""
        real = redirected_store_path(path)
        return str(path), str(real) if real else ""

    def _open_store_folder(self) -> None:
        store_path, real_path = self._store_paths()
        target = Path(real_path or store_path).parent
        target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _save_store_as(self, parent_dialog=None) -> None:
        """« Enregistrer sous » : jamais sur un fichier existant (le
        dialogue propose alors de l'ouvrir à la place)."""
        owner = parent_dialog or self
        current = getattr(self.provider, "path", None)
        if current is None or self._is_volatile:
            return
        chosen, _filter = QFileDialog.getSaveFileName(
            owner, "Enregistrer le coffre sous", str(current.parent),
            STORE_FILTER, options=QFileDialog.Option.DontConfirmOverwrite)
        if not chosen:
            return
        path = with_store_suffix(Path(chosen))
        if same_path(path, current):
            return
        if path.exists():
            box = QMessageBox(
                QMessageBox.Icon.Question, "Fichier existant",
                f"Un fichier existe déjà :\n{path}\n\n"
                "Il n'est jamais écrasé. Pour utiliser ce coffre, ouvre-le "
                "avec son propre master password.", parent=owner)
            open_btn = box.addButton("Ouvrir ce coffre",
                                     QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is open_btn:
                self._switch_store(parent_dialog, preselect=path)
            return
        if self.set_store_path(path, owner) and parent_dialog is not None:
            parent_dialog._refresh_path_label(*self._store_paths())

    def set_store_path(self, path: Path, owner=None) -> bool:
        """Copie le coffre vers `path` (NOUVEAU fichier) et l'utilise
        désormais, avec le master password courant ; l'ancien fichier est
        laissé en place. Un fichier existant n'est jamais écrasé."""
        owner = owner or self
        save_as = getattr(self.provider, "save_as", None)
        if self.provider is None or self._is_volatile or save_as is None:
            return False
        previous = self.provider.path
        if same_path(path, previous):
            return True
        if path.exists():
            QMessageBox.critical(
                owner, "Emplacement refusé",
                f"Un fichier existe déjà : {path}\nIl n'est jamais écrasé.")
            return False
        self._acl_warned = False        # nouveau fichier : nouvel avertissement
        try:
            # Création exclusive ; le chemin ne change qu'après succès.
            save_as(path, self.entries)
        except (SecretProviderError, OSError) as exc:
            QMessageBox.critical(owner, "Emplacement refusé", str(exc))
            return False
        remember_store(self._settings, path)
        self._log(f"Coffre enregistré sous {path} (l'ancien fichier "
                  f"{previous} est conservé).")
        self._warn_if_acl_not_restricted()
        return True

    def _switch_store(self, parent_dialog=None,
                      preselect: Optional[Path] = None) -> None:
        """Ouvre un autre coffre (existant ou nouveau) et l'utilise. Depuis
        le mode sans enregistrement, les entrées de la session y sont
        ajoutées."""
        owner = parent_dialog or self
        current = None if self._is_volatile else getattr(
            self.provider, "path", None)
        start = preselect or current
        if start is None:
            stored = self._settings.value(LAST_KEY)
            start = Path(str(stored)) if stored else None
        provider = LocalEncryptedProvider(start)
        missing_last = (start if start is not None and preselect is None
                        and current is None and not provider.exists else None)

        from ui.master_password_dialog import MasterPasswordDialog
        dialog = MasterPasswordDialog(
            provider, first_run=not provider.exists, allow_memory_mode=False,
            parent=owner, choose_store=True,
            recent_stores=recent_stores(self._settings),
            missing_last=missing_last, current_store=current)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        chosen = dialog.provider
        dialog.deleteLater()           # ne garde aucun champ de mot de passe
        if not accepted:
            return
        if current is not None and same_path(chosen.path, current):
            # Coffre actuel revalidé : rien à changer (la clé en double est
            # retirée de la mémoire).
            if chosen is not self.provider:
                chosen.lock()
            return
        was_volatile = self._is_volatile
        if not self.adopt_provider(chosen, owner):
            return
        if parent_dialog is None:
            return
        if was_volatile:
            parent_dialog.close()      # ses boutons visaient le mode mémoire
        else:
            parent_dialog._refresh_path_label(*self._store_paths())

    def adopt_provider(self, provider: SecretProvider, owner=None) -> bool:
        """Utilise `provider` (déjà déverrouillé) à la place du coffre
        courant. Les entrées d'un mode sans enregistrement sont AJOUTÉES au
        coffre, jamais à la place de son contenu."""
        carried = list(self.entries) if self._is_volatile else []
        try:
            entries = provider.load()
            if carried:
                entries = entries + carried
                provider.save(entries)
        except SecretProviderError as exc:
            provider.lock()
            QMessageBox.critical(owner or self, "Coffre inutilisable", str(exc))
            return False

        # Les raccourcis globaux des anciennes entrées taperaient sinon
        # encore leurs secrets.
        self.hotkeys.unregister_all(self.entries)
        old = self.provider
        if old is not None and old is not provider:
            old.lock()                  # clé de l'ancien coffre hors mémoire
        self.provider = provider
        self._acl_warned = False        # nouveau coffre : nouvel avertissement
        self.entries = entries
        self.hotkeys.register_all(self.entries)
        self._refresh_quick_table()
        self._update_storage_banner()
        path = getattr(provider, "path", None)
        if path is not None:
            remember_store(self._settings, path)
        added = f", dont {len(carried)} de la session" if carried else ""
        self._log(f"Coffre ouvert : {path} ({len(entries)} entrée(s){added}).")
        self._warn_if_acl_not_restricted()
        return True

    def _reset_master_password(self, parent_dialog=None) -> None:
        owner = parent_dialog or self
        confirm = QMessageBox.warning(
            owner, "Réinitialiser le master password",
            "Toutes les entrées d'envoi rapide vont être définitivement "
            "supprimées, et un nouveau master password te sera demandé.\n\n"
            "Cette action est irréversible. Continuer ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return

        if not self.do_reset_master_password():
            return

        # Redemander tout de suite un mot de passe : sans stockage
        # deverrouille, l'onglet Envoi rapide serait inutilisable.
        from ui.master_password_dialog import MasterPasswordDialog
        dialog = MasterPasswordDialog(self.provider, first_run=True, parent=owner)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        choice = dialog.choice
        dialog.deleteLater()           # ne garde aucun champ de mot de passe
        if accepted and choice == "unlock":
            self._load_entries()
            self._log("Nouveau master password défini, stockage vide.")
            return
        # « Continuer sans enregistrer » (etait ignore : le journal annoncait
        # a tort un nouveau master password) ou dialogue ferme : on bascule
        # en memoire volatile, sinon l'Envoi rapide resterait inutilisable.
        # Options > « Activer l'enregistrement chiffré » permet d'y revenir.
        self.use_memory_storage()
        self._log("Stockage chiffré supprimé ; mode sans enregistrement "
                  "(les entrées disparaîtront à la fermeture).", "WARN")
        if parent_dialog is not None:
            parent_dialog.close()      # ses boutons visaient l'ancien coffre

    def use_memory_storage(self) -> None:
        """Bascule sur le stockage volatil (bandeau permanent)."""
        from security.memory_provider import MemoryProvider
        # Sinon un raccourci global resterait enregistre et taperait encore
        # le secret d'une entree qui n'existe plus.
        self.hotkeys.unregister_all(self.entries)
        provider = MemoryProvider()
        provider.unlock("")
        self.provider = provider
        self._acl_warned = False
        self.entries = []
        self._refresh_quick_table()
        self._update_storage_banner()

    def do_reset_master_password(self) -> bool:
        """Partie effective, sans dialogue - appelable par les tests."""
        if self.provider is None:
            return False
        try:
            self.hotkeys.unregister_all(self.entries)
            self.entries = []
            self.provider.reset()
        except SecretProviderError as exc:
            QMessageBox.critical(self, "Réinitialisation impossible", str(exc))
            return False
        self._refresh_quick_table()
        self._log("Stockage chiffré supprimé.", "WARN")
        return True

    # ======================================================== envoi rapide --
    def _load_entries(self) -> None:
        if self.provider is None or not self.provider.is_unlocked():
            self._refresh_quick_table()
            return
        try:
            self.entries = self.provider.load()
        except SecretProviderError as exc:
            QMessageBox.critical(self, "Chargement impossible", str(exc))
            self.entries = []
        self.hotkeys.register_all(self.entries)
        self._refresh_quick_table()
        self._log(f"{len(self.entries)} entrée(s) d'envoi rapide chargée(s).")
        self._warn_if_acl_not_restricted()

    def _save_entries(self) -> None:
        if self.provider is None or not self.provider.is_unlocked():
            return
        try:
            self.provider.save(self.entries)
        except SecretProviderError as exc:
            QMessageBox.critical(self, "Enregistrement impossible", str(exc))
            return
        self._warn_if_acl_not_restricted()

    def _warn_if_acl_not_restricted(self) -> None:
        """Le coffre garde ses droits hérités (clé USB FAT32, partage réseau,
        refus Windows) : un autre compte local pourrait le copier. Signalé une
        seule fois par session, sans bloquer."""
        if (getattr(self.provider, "acl_restricted", None) is False
                and not self._acl_warned):
            self._acl_warned = True
            self._log(
                "Droits du fichier de stockage non restreints au compte "
                "courant (support sans ACL NTFS ?) : d'autres comptes locaux "
                "pourraient le copier.", "WARN")
