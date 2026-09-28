"""
main.py - Point d'entree. Deverrouillage du stockage chiffre, icone dans la
barre systeme (tray) et fenetre principale.

La fenetre se cache au lieu de se fermer (clic sur la croix) ; seul
"Quitter" dans le menu du tray termine vraiment l'application - c'est ce qui
permet aux raccourcis globaux de rester actifs en arriere-plan.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QDialog, QMenu, QMessageBox, QSystemTrayIcon,
)

from security.local_encrypted import (
    LocalEncryptedProvider, default_store_path, find_existing_store)
from security.memory_provider import MemoryProvider
from core.single_instance import SingleInstance
from ui.main_window import APP_TITLE, MainWindow
from ui.master_password_dialog import MasterPasswordDialog
from ui.store_history import recent_stores, remember_store
from ui import icons
from ui.theme import LIGHT, apply_theme


def build_tray_icon(app: QApplication, window: MainWindow) -> QSystemTrayIcon:
    tray = QSystemTrayIcon(icons.app_icon(), app)
    tray.setToolTip(APP_TITLE)

    def toggle_window() -> None:
        if window.isVisible():
            window.hide()
        else:
            window.show()
            window.raise_()
            window.activateWindow()

    def quit_app() -> None:
        window.shutdown()
        app.quit()

    menu = QMenu()

    toggle_action = QAction("Afficher / Masquer", app)
    toggle_action.triggered.connect(toggle_window)
    menu.addAction(toggle_action)

    palette_action = QAction("Palette d'envoi rapide", app)
    palette_action.triggered.connect(window._toggle_palette)
    menu.addAction(palette_action)

    menu.addSeparator()

    quit_action = QAction("Quitter", app)
    quit_action.triggered.connect(quit_app)
    menu.addAction(quit_action)

    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: toggle_window()
        if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    return tray


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setOrganizationName(APP_TITLE)
    app.setQuitOnLastWindowClosed(False)
    # Icône de la barre de titre, de la barre des tâches et d'Alt+Tab.
    app.setWindowIcon(icons.app_icon())

    # Une seule instance : une seconde demande a la premiere de s'afficher
    # puis s'arrete (sinon les deux se disputeraient les raccourcis globaux).
    instance = SingleInstance(parent=app)
    if instance.notify_existing():
        return 0
    if not instance.listen():
        QMessageBox.information(
            None, APP_TITLE,
            "SendToConsole est déjà lancé (icône dans la barre système, "
            "ou saisie du master password en cours).")
        return 0

    # Theme choisi par l'operateur dans Options, relu a chaque lancement.
    apply_theme(app, str(QSettings(APP_TITLE, APP_TITLE).value("ui/theme", LIGHT)))

    settings = QSettings(APP_TITLE, APP_TITLE)
    stored = settings.value("storage/path")
    target = Path(str(stored)) if stored else default_store_path()

    # Une version precedente enregistrait dans %APPDATA%, ou le Python du
    # Microsoft Store redirigeait silencieusement le fichier. On le recupere
    # au nouvel emplacement plutot que de laisser l'operateur devant un
    # stockage vide et ses entrees introuvables. Seulement quand aucun coffre
    # n'a encore ete choisi : sinon un dernier coffre introuvable (cle USB
    # debranchee) serait remplace en silence par ce vieux coffre.
    if not stored and not target.exists():
        legacy = find_existing_store(target)
        if legacy is not None:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy, target)
            except OSError:
                target = legacy       # a defaut, on continue de l'utiliser sur place

    provider = LocalEncryptedProvider(target)
    # Dernier coffre introuvable (cle USB debranchee, fichier deplace) : le
    # dialogue le signale au lieu de proposer en silence d'en creer un vide.
    missing_last = target if stored and not provider.exists else None

    # Le coffre se choisit ici (recents, « Ouvrir… », « Nouveau… ») : des le
    # premier lancement, un coffre existant peut etre repris.
    dialog = MasterPasswordDialog(
        provider, first_run=not provider.exists, choose_store=True,
        recent_stores=recent_stores(settings), missing_last=missing_last)
    # Relance pendant la saisie du master password : on ramene ce dialogue.
    raise_dialog = lambda: (dialog.raise_(), dialog.activateWindow())  # noqa: E731
    instance.activation_requested.connect(raise_dialog)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    instance.activation_requested.disconnect(raise_dialog)
    if not accepted:
        return 0

    # « Continuer sans enregistrer » : l'outil reste pleinement utilisable,
    # les entrees vivent seulement en memoire.
    if dialog.choice == "memory":
        provider = MemoryProvider()
    else:
        # Le coffre effectivement ouvert sera propose au prochain lancement.
        provider = dialog.provider
        remember_store(settings, provider.path)
    dialog.deleteLater()               # ne garde aucun champ de mot de passe

    window = MainWindow(provider=provider)
    instance.activation_requested.connect(window.bring_to_front)

    # Les raccourcis globaux sont enregistres sur le HWND de la fenetre
    # principale, et WM_HOTKEY est intercepte par un filtre d'evenements natif.
    window.show()
    window.hotkeys.attach(int(window.winId()))
    app.installNativeEventFilter(window.hotkeys)
    window.hotkeys.register_all(window.entries)
    window.register_app_hotkeys()
    window._refresh_quick_table()

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.warning(
            window, "Barre système indisponible",
            "Aucune barre système détectée : l'outil restera une fenêtre "
            "ordinaire.")
        tray = None
    else:
        tray = build_tray_icon(app, window)
        tray.show()
        # Retour des raccourcis globaux quand la fenetre est cachee.
        window.notify.connect(lambda message: tray.showMessage(
            APP_TITLE, message, QSystemTrayIcon.MessageIcon.Information, 2500))

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
