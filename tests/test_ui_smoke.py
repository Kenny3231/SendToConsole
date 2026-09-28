"""Test de fumee de l'interface : MainWindow et la palette se construisent.

Qt offscreen, backend clavier factice (voir conftest.py) : aucune vraie
fenetre affichee, aucune vraie frappe. Les reglages QSettings sont rediriges
vers un dossier temporaire pour ne jamais toucher ceux de l'utilisateur.
"""

import pytest

from core.models import QuickEntry
from security.memory_provider import MemoryProvider


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """QSettings(org, app) -> fichier INI temporaire (pas le registre reel).

    setDefaultFormat() ne suffit PAS : le constructeur QSettings(org, app)
    utilise toujours NativeFormat (registre sous Windows). On remplace donc
    la classe dans chaque module UI qui la construit."""
    QtCore = pytest.importorskip("PySide6.QtCore")
    ini = str(tmp_path / "settings.ini")

    class _IniSettings(QtCore.QSettings):
        def __init__(self, *args, **kwargs):
            super().__init__(ini, QtCore.QSettings.Format.IniFormat)

    try:
        import ui.main_window
        import ui.palette
    except ImportError as exc:
        pytest.skip(f"Interface Qt indisponible dans cet environnement : {exc}")
    for module in (ui.main_window, ui.palette):
        monkeypatch.setattr(module, "QSettings", _IniSettings)
    yield ini


def test_regression_settings_isolated_from_real_registry(isolated_settings):
    """Un test UI ecrivait ui/theme dans HKCU\\Software\\SendToConsole."""
    import ui.main_window
    s = ui.main_window.QSettings("SendToConsole", "SendToConsole")
    from pathlib import Path
    assert Path(s.fileName()) == Path(isolated_settings)


@pytest.fixture
def window(qapp, isolated_settings):
    try:
        from ui.main_window import MainWindow
    except ImportError as exc:      # libs graphiques absentes (libEGL...)
        pytest.skip(f"Interface Qt indisponible dans cet environnement : {exc}")

    provider = MemoryProvider()
    provider.unlock("")
    win = MainWindow(provider=provider)
    yield win
    win.shutdown()
    win._foreground_timer.stop()
    win._pick_timer.stop()
    win.deleteLater()


def test_main_window_builds(window):
    from ui.main_window import APP_TITLE, APP_VERSION
    assert window.windowTitle() == f"{APP_TITLE} v{APP_VERSION}"
    assert window.entries == []
    assert window.palette_window is not None


def test_main_window_show_hide(window, qapp):
    window.show()
    qapp.processEvents()
    assert window.isVisible()
    window.hide()
    qapp.processEvents()
    assert not window.isVisible()


def test_close_hides_instead_of_quitting(window, qapp):
    """Fermer replie dans le tray : la fenetre n'est pas detruite et les
    raccourcis globaux restent actifs (CONTRIBUTING.md, tray)."""
    window.show()
    qapp.processEvents()
    window.close()
    qapp.processEvents()
    assert not window.isVisible()


def test_regression_log_never_shows_content_characters(window):
    """Audit v0.6.1 : l'apercu de conversion affichait les 60 premiers
    caracteres du contenu, et les diagnostics listaient les caracteres
    non tapes. Le contenu peut etre un mot de passe : seuls des decomptes
    doivent apparaitre dans le journal."""
    secret = "S3cr3tPw☃Ж"
    window.content_edit.setPlainText(secret)
    window._preview_conversion()
    window._on_diagnostics({"☃"}, {"Ж"})

    log = window.log_edit.toPlainText()
    assert "S3cr3t" not in log
    assert "☃" not in log and "Ж" not in log
    assert "NON TAPÉS : 1 caractère(s)" in log


def test_palette_does_not_take_focus(window, qapp):
    """La palette est une fenetre Tool avec WA_ShowWithoutActivating. Le vrai
    garde-fou (WS_EX_NOACTIVATE sur le HWND) n'est pas testable en offscreen :
    ce plugin active toute fenetre affichee, donc qapp.activeWindow() n'y est
    pas significatif. La validation reste manuelle (keyboard-injection.md)."""
    from PySide6.QtCore import Qt

    palette = window.palette_window
    palette.refresh([QuickEntry(label="Test", text="motdepasse")])

    assert palette.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert palette.windowFlags() & Qt.WindowType.Tool

    palette.show()
    qapp.processEvents()
    assert palette.isVisible()

    palette.hide()
    qapp.processEvents()
    assert not palette.isVisible()


def test_regression_unmapped_log_shows_count_only(window, monkeypatch):
    """Chemin _build : les caracteres sans correspondance etaient listes."""
    import ui.main_window as mw
    from keyboard.engine import BuildResult
    monkeypatch.setattr(
        mw, "build_tokens",
        lambda text, **kw: BuildResult([("x",)], 1, {"☃", "Ж"}, set()))
    window._build("S3cr3tPw☃Ж", False, 0)
    log = window.log_edit.toPlainText()
    assert "2 caractère(s)" in log
    assert "☃" not in log and "Ж" not in log and "S3cr3t" not in log


def test_edit_delete_buttons_need_a_selection(window, qapp):
    window.entries = [QuickEntry(label="A", text="x")]
    window._refresh_quick_table()
    window.quick_table.clearSelection()
    qapp.processEvents()
    assert not window.edit_entry_btn.isEnabled()
    assert not window.del_entry_btn.isEnabled()
    window.quick_table.selectRow(0)
    qapp.processEvents()
    assert window.edit_entry_btn.isEnabled()
    assert window.del_entry_btn.isEnabled()


def test_arrow_png_falls_back_to_none_and_rebuilds_corrupt_file(
        qapp, tmp_path, monkeypatch):
    from ui import theme
    # Dossier temporaire introuvable : pas de flèche, pas d'exception.
    def _no_tmp():
        raise FileNotFoundError("aucun dossier temporaire")
    monkeypatch.setattr(theme, "_arrow_dir", _no_tmp)
    assert theme._arrow_url("up", "#123456") == "none"

    # PNG tronqué (autre instance interrompue) : redessiné.
    monkeypatch.setattr(theme, "_arrow_dir", lambda: tmp_path)
    corrupt = tmp_path / "arrow_up_123456.png"
    corrupt.write_bytes(b"")
    url = theme._arrow_url("up", "#123456")
    assert url.startswith("url(") and corrupt.stat().st_size > 0
    assert not list(tmp_path.glob("*.tmp"))
    for name in ("clair", "sombre"):
        assert "$" not in theme.stylesheet(name)


# --- Raccourcis d'action et capture (v0.6.3) ---------------------------------

def _selected_hwnd(window):
    from PySide6.QtCore import Qt
    rows = window.windows_table.selectionModel().selectedRows()
    if not rows:
        return None
    return window.windows_table.item(rows[0].row(), 3).data(
        Qt.ItemDataRole.UserRole)


def test_capture_selects_row_like_a_click(window):
    assert window._capture_window(1002) is True
    assert window._target_hwnd == 1002
    assert _selected_hwnd(window) == 1002


def test_capture_clears_filter_hiding_the_window(window):
    window.window_filter.setText("notepad")
    assert window._capture_window(1002) is True
    assert window.window_filter.text() == ""
    assert _selected_hwnd(window) == 1002


def test_refresh_keeps_target_selected(window):
    window._capture_window(1003)
    window._refresh_windows()
    assert _selected_hwnd(window) == 1003


def test_capture_refuses_own_window(window, monkeypatch):
    from keyboard import backend as w32
    monkeypatch.setattr(w32, "belongs_to_process", lambda hwnd, pid: True)
    assert window._capture_window(1001) is False
    assert window._target_hwnd == 0


def test_capture_hotkey_uses_window_at_hotkey_time(window):
    from core.hotkeys import ACTION_CAPTURE_TARGET
    window._on_action_hotkey(ACTION_CAPTURE_TARGET, 1003)
    assert window._target_hwnd == 1003 and _selected_hwnd(window) == 1003


def test_hidden_window_notifies_tray_on_capture(window):
    got = []
    window.notify.connect(got.append)
    window.hide()
    window._capture_window(1002)
    assert got and "Connexion Bureau" in got[0]


def test_palette_hotkey_toggles_palette(window, qapp):
    from core.hotkeys import ACTION_TOGGLE_PALETTE
    window._on_action_hotkey(ACTION_TOGGLE_PALETTE, 0)
    qapp.processEvents()
    assert window.palette_window.isVisible()
    window._on_action_hotkey(ACTION_TOGGLE_PALETTE, 0)
    qapp.processEvents()
    assert not window.palette_window.isVisible()


def test_set_app_hotkey_persists_and_refuses_conflicts(window, isolated_settings):
    from core import hotkeys as hk
    from PySide6.QtCore import QSettings
    combo = (hk.MOD_CONTROL | hk.MOD_ALT, ord("C"))
    assert window.set_app_hotkey(hk.ACTION_CAPTURE_TARGET, *combo) is None
    ini = QSettings(isolated_settings, QSettings.Format.IniFormat)
    assert int(ini.value("hotkeys/capture_target_mods")) == combo[0]
    assert int(ini.value("hotkeys/capture_target_vk")) == combo[1]

    # Meme combinaison pour l'autre action : refus, rien ne change.
    error = window.set_app_hotkey(hk.ACTION_TOGGLE_PALETTE, *combo)
    assert error and "Cibler la fenêtre active" in error
    assert window._app_hotkeys[hk.ACTION_TOGGLE_PALETTE] == (0, 0)

    # Combinaison d'une entree d'envoi rapide : refus.
    window.entries = [QuickEntry(label="root", text="x",
                                 mods=hk.MOD_CONTROL, vk=0x70)]
    assert "root" in window.set_app_hotkey(
        hk.ACTION_TOGGLE_PALETTE, hk.MOD_CONTROL, 0x70)


def test_quick_entry_dialog_refuses_action_hotkey(qapp, monkeypatch):
    from core import hotkeys as hk
    from ui import quick_entry_dialog as qed
    warnings = []
    monkeypatch.setattr(qed.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a[2]))
    dlg = qed.QuickEntryDialog(
        [], reserved={hk.ACTION_TOGGLE_PALETTE: (hk.MOD_CONTROL, ord("P"))})
    dlg.label_edit.setText("x")
    dlg.hotkey_edit.set_hotkey(hk.MOD_CONTROL, ord("P"))
    dlg._on_accept()
    assert warnings and "palette" in warnings[0]
    assert dlg.result() != dlg.DialogCode.Accepted
    dlg.deleteLater()


def test_options_dialog_hotkey_fields(qapp):
    from core import hotkeys as hk
    from ui.options_dialog import OptionsDialog
    dlg = OptionsDialog("clair", "", app_hotkeys={
        hk.ACTION_CAPTURE_TARGET: (hk.MOD_CONTROL, ord("K"))})
    assert dlg.hotkey_edits[hk.ACTION_CAPTURE_TARGET].text() == "Ctrl+K"
    got = []
    dlg.app_hotkey_changed.connect(lambda *a: got.append(a))
    dlg.hotkey_edits[hk.ACTION_TOGGLE_PALETTE].set_hotkey(hk.MOD_ALT, ord("P"))
    assert got == [(hk.ACTION_TOGGLE_PALETTE, hk.MOD_ALT, ord("P"))]
    dlg.set_app_hotkey(hk.ACTION_TOGGLE_PALETTE, 0, 0)   # retour silencieux
    assert len(got) == 1
    dlg.deleteLater()



# --- Relecture v0.6.3 : regle 2bis, envoi en cours, notification ------------

def test_regression_action_hotkey_releases_held_alt(window):
    """Regle 2bis : l'Alt du raccourci, consomme par RegisterHotKey,
    activait la barre de menus de la cible au relachement."""
    from core.hotkeys import ACTION_TOGGLE_PALETTE
    from keyboard import backend as w32
    w32.held_modifiers.append("LAlt")
    window._on_action_hotkey(ACTION_TOGGLE_PALETTE, 1001)
    assert w32.held_modifiers == []
    window.palette_window.hide()


def test_regression_hotkey_during_send_pauses_and_is_ignored(window, monkeypatch):
    """Ctrl/Alt tenus pendant que l'outil tape : Ctrl+lettre dans la cible."""
    from core.hotkeys import ACTION_CAPTURE_TARGET
    from core.inject_controller import InjectController
    monkeypatch.setattr(InjectController, "is_running",
                        property(lambda self: True))
    paused = []
    monkeypatch.setattr(window.controller, "pause", lambda: paused.append(1))

    window._on_action_hotkey(ACTION_CAPTURE_TARGET, 1003)
    assert paused == [1] and window._target_hwnd == 0

    window._on_hotkey(QuickEntry(label="root", text="x"), 1003)
    assert paused == [1, 1]


def test_button_capture_on_visible_active_window_does_not_notify(window, qapp):
    got = []
    window.notify.connect(got.append)
    window.show()
    window.activateWindow()
    qapp.processEvents()
    if not window.isActiveWindow():
        pytest.skip("activation de fenetre non geree par ce plugin Qt")
    window._capture_window(1002)                     # bouton
    assert got == []
    window._capture_window(1003, from_hotkey=True)   # raccourci, fenetre active
    assert got == []
    window.hide()


def test_hotkey_capture_notifies_when_window_is_behind(window, monkeypatch):
    got = []
    window.notify.connect(got.append)
    monkeypatch.setattr(window, "isVisible", lambda: True)
    monkeypatch.setattr(window, "isActiveWindow", lambda: False)
    window._capture_window(1002)                     # bouton : pas de bulle
    assert got == []
    window._capture_window(1003, from_hotkey=True)   # raccourci : bulle
    assert got and "Bloc-notes" in got[0]


def test_set_app_hotkey_refuses_altgr_character(window):
    from core import hotkeys as hk
    error = window.set_app_hotkey(hk.ACTION_CAPTURE_TARGET,
                                  hk.MOD_CONTROL | hk.MOD_ALT, ord("0"))
    assert error and "@" in error
    assert window._app_hotkeys[hk.ACTION_CAPTURE_TARGET] == (0, 0)


def test_hotkey_edit_focus_suspends_global_hotkeys(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFocusEvent
    from ui.hotkey_edit import HotkeyEdit
    edit = HotkeyEdit()
    window._suspend_during_capture(edit)
    edit.focusInEvent(QFocusEvent(QFocusEvent.Type.FocusIn,
                                  Qt.FocusReason.MouseFocusReason))
    assert window.hotkeys.is_suspended
    edit.focusOutEvent(QFocusEvent(QFocusEvent.Type.FocusOut,
                                   Qt.FocusReason.MouseFocusReason))
    assert not window.hotkeys.is_suspended
    edit.deleteLater()


def test_options_shows_refused_actions(qapp):
    from core import hotkeys as hk
    from ui.options_dialog import OptionsDialog
    dlg = OptionsDialog("clair", "", refused_actions={hk.ACTION_TOGGLE_PALETTE})
    assert not dlg.refused_label.isHidden()
    assert "palette" in dlg.refused_label.text()
    dlg.set_refused_actions(set())
    assert dlg.refused_label.isHidden()
    dlg.deleteLater()



# --- v0.6.4 : cible administrateur, reinitialisation, instance unique -------

@pytest.fixture
def elevated(monkeypatch):
    from keyboard import backend as w32
    table = {}
    monkeypatch.setattr(w32, "_FAKE_ELEVATED", table)
    return table


def test_elevated_target_is_flagged(window, elevated):
    elevated[1003] = True
    assert window._capture_window(1003)
    assert not window.target_elevation_label.isHidden()
    assert "tourne en administrateur" in window.target_elevation_label.text()
    assert "administrateur" in window.bar_target_label.text()
    assert "administrateur" in window.log_edit.toPlainText()


def test_probably_elevated_target_says_so(window, elevated):
    elevated[1002] = None                    # acces refuse : probable
    window._capture_window(1002)
    assert "peut-être en administrateur" in \
        window.target_elevation_label.text()
    assert "peut-être bloquées" in window.status_label.text()


def test_normal_target_has_no_admin_warning(window, elevated):
    elevated[1003] = True
    window._capture_window(1003)
    window._capture_window(1001)             # retour a une fenetre normale
    assert window.target_elevation_label.isHidden()
    assert "administrateur" not in window.bar_target_label.text()


def test_regression_reset_then_continue_without_saving(window, tmp_path,
                                                       fast_kdf, monkeypatch):
    """« Continuer sans enregistrer » apres reinitialisation etait ignore :
    le journal annoncait un nouveau master password, stockage verrouille."""
    from PySide6.QtWidgets import QDialog, QMessageBox
    from security.local_encrypted import LocalEncryptedProvider
    import ui.main_window as mw
    import ui.master_password_dialog as mpd

    store = LocalEncryptedProvider(tmp_path / "entries.enc")
    store.unlock("master")
    window.provider = store
    monkeypatch.setattr(mw.QMessageBox, "warning",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)

    class _Dialog:
        def __init__(self, *a, **k):
            self.choice = "memory"

        def exec(self):
            return QDialog.DialogCode.Accepted

        def deleteLater(self):
            pass
    monkeypatch.setattr(mpd, "MasterPasswordDialog", _Dialog)

    window._reset_master_password()
    assert window._is_volatile and window.provider.is_unlocked()
    log = window.log_edit.toPlainText()
    assert "mode sans enregistrement" in log
    assert "Nouveau master password défini" not in log
    assert not (tmp_path / "entries.enc").exists()


def test_memory_storage_switch_releases_hotkeys(window, monkeypatch):
    """Bascule en memoire avec des entrees chargees : leurs raccourcis
    globaux doivent etre liberes (sinon ils taperaient encore le secret)."""
    released = []
    monkeypatch.setattr(window.hotkeys, "unregister_all",
                        lambda entries: released.extend(entries))
    entry = QuickEntry(label="root", text="x", mods=2, vk=0x70)
    window.entries = [entry]
    window.use_memory_storage()
    assert released == [entry] and window.entries == []


def test_bring_to_front_shows_hidden_window(window, qapp):
    window.hide()
    window.bring_to_front()
    qapp.processEvents()
    assert window.isVisible()
    window.hide()


# ------------------------------------------------------ choix du coffre --
def _vault(path, password="pw", labels=()):
    from security.local_encrypted import LocalEncryptedProvider
    store = LocalEncryptedProvider(path)
    store.unlock(password)
    if labels:
        store.save([QuickEntry(label=label, text="x") for label in labels])
    return store


def test_set_store_path_never_overwrites_existing_file(window, tmp_path,
                                                       fast_kdf, monkeypatch):
    import ui.main_window as mw
    current = _vault(tmp_path / "courant.enc", labels=["a"])
    other = _vault(tmp_path / "autre.enc", "autre", labels=["b"])
    other.lock()
    before = (tmp_path / "autre.enc").read_bytes()
    window.provider = current
    window.entries = current.load()
    monkeypatch.setattr(mw.QMessageBox, "critical", lambda *a, **k: None)

    assert not window.set_store_path(tmp_path / "autre.enc")
    assert (tmp_path / "autre.enc").read_bytes() == before
    assert window.provider.path == tmp_path / "courant.enc"


def test_regression_failed_move_keeps_previous_path(window, tmp_path,
                                                    fast_kdf, monkeypatch):
    """Un echec d'ecriture laissait le coffre pointer sur le nouveau chemin :
    les enregistrements suivants visaient un fichier inexistant."""
    import ui.main_window as mw
    from security.provider import SecretProviderError
    current = _vault(tmp_path / "courant.enc")
    window.provider = current
    monkeypatch.setattr(mw.QMessageBox, "critical", lambda *a, **k: None)
    monkeypatch.setattr(current, "_write", lambda *a, **k: (_ for _ in ()).throw(
        SecretProviderError("disque plein")))
    assert not window.set_store_path(tmp_path / "ailleurs.enc")
    assert current.path == tmp_path / "courant.enc"


def test_set_store_path_copies_and_remembers(window, tmp_path, fast_kdf):
    from ui.store_history import recent_stores
    current = _vault(tmp_path / "courant.enc", labels=["a"])
    window.provider = current
    window.entries = current.load()
    assert window.set_store_path(tmp_path / "copie.enc")
    assert (tmp_path / "courant.enc").exists()          # ancien conservé
    assert window.provider.path == tmp_path / "copie.enc"
    assert recent_stores(window._settings)[0] == tmp_path / "copie.enc"


def test_regression_enable_storage_merges_into_existing_vault(
        window, tmp_path, fast_kdf):
    """Activer l'enregistrement depuis le mode memoire sur un coffre EXISTANT
    remplacait son contenu par les seules entrees de la session."""
    window.entries = [QuickEntry(label="session", text="x")]
    existing = _vault(tmp_path / "coffre.enc", labels=["ancien"])
    assert window.adopt_provider(existing)
    labels = sorted(e.label for e in window.entries)
    assert labels == ["ancien", "session"]
    assert sorted(e.label for e in existing.load()) == ["ancien", "session"]
    assert not window._is_volatile


def test_switching_vault_releases_hotkeys_and_locks_old(window, tmp_path,
                                                        fast_kdf, monkeypatch):
    first = _vault(tmp_path / "a.enc", labels=["a"])
    window.provider = first
    window.entries = first.load()
    released = []
    monkeypatch.setattr(window.hotkeys, "unregister_all",
                        lambda entries: released.extend(entries))
    second = _vault(tmp_path / "b.enc", "autre", labels=["b"])
    assert window.adopt_provider(second)
    assert [e.label for e in released] == ["a"]
    assert not first.is_unlocked()
    assert [e.label for e in window.entries] == ["b"]
    # Coffre a coffre : rien n'est recopie de l'un dans l'autre.
    assert [e.label for e in second.load()] == ["b"]


def test_save_as_on_existing_file_offers_to_open_it(window, tmp_path,
                                                    fast_kdf, monkeypatch):
    import ui.main_window as mw
    current = _vault(tmp_path / "courant.enc", labels=["a"])
    window.provider = current
    window.entries = current.load()
    taken = _vault(tmp_path / "pris.enc", "autre")
    taken.lock()
    before = (tmp_path / "pris.enc").read_bytes()
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(tmp_path / "pris"), ""))
    monkeypatch.setattr(mw.QMessageBox, "exec", lambda self: 0)   # Annuler
    window._save_store_as()
    assert (tmp_path / "pris.enc").read_bytes() == before
    assert window.provider.path == tmp_path / "courant.enc"


def test_regression_palette_no_activate_failure_is_logged(window, monkeypatch):
    """Si Windows refusait WS_EX_NOACTIVATE, l'echec passait inapercu (les
    appels Win32 rendent 0 sans exception) et la palette volait le focus."""
    import ui.palette as pal
    monkeypatch.setattr(pal, "_is_native_windows", lambda: True)
    monkeypatch.setattr(pal, "set_no_activate_style", lambda hwnd: False)
    window.palette_window._apply_no_activate()
    window.palette_window._apply_no_activate()      # un seul avertissement
    log = window.log_edit.toPlainText()
    assert log.count("mode « sans focus »") == 1
    assert "WARN" in [line.split()[1] for line in log.splitlines()
                      if "sans focus" in line][0]


def test_palette_no_activate_success_is_silent(window, monkeypatch):
    import ui.palette as pal
    calls = []
    monkeypatch.setattr(pal, "_is_native_windows", lambda: True)
    monkeypatch.setattr(pal, "set_no_activate_style",
                        lambda hwnd: calls.append(hwnd) or True)
    window.palette_window._apply_no_activate()
    assert calls and "sans focus" not in window.log_edit.toPlainText()


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Win32")
def test_set_no_activate_style_on_real_hidden_window():
    """Vrai appel Win32 sur une fenetre cachee (jamais affichee : aucun
    effet sur le focus ni le clavier) : le style est pose ET relu."""
    import ctypes
    from ctypes import wintypes
    from ui.palette import (
        GWL_EXSTYLE, WS_EX_NOACTIVATE, set_no_activate_style)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateWindowExW.argtypes = (
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = (wintypes.HWND,)
    user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.GetWindowLongW.restype = ctypes.c_long
    hwnd = user32.CreateWindowExW(0, "STATIC", "stc-test", 0,
                                  0, 0, 10, 10, None, None, None, None)
    assert hwnd, ctypes.get_last_error()
    try:
        assert not user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_NOACTIVATE
        assert set_no_activate_style(hwnd) is True
        assert user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_NOACTIVATE
    finally:
        user32.DestroyWindow(hwnd)


def test_set_no_activate_style_reports_failure_on_invalid_window():
    import sys
    if sys.platform != "win32":
        pytest.skip("Win32")
    from ui.palette import set_no_activate_style
    assert set_no_activate_style(0) is False      # HWND nul : refus relu


# ------------------------------------------- coller le presse-papiers --
@pytest.fixture
def paste(window, qapp, monkeypatch):
    """Presse-papiers simule + capture de l'envoi (aucune vraie frappe).
    L'action est differee (QTimer) : on laisse tourner la boucle Qt."""
    import ui.clipboard as clip
    from core.hotkeys import ACTION_PASTE_CLIPBOARD
    from PySide6.QtCore import QElapsedTimer
    state = {"text": "", "starts": [], "notes": []}
    monkeypatch.setattr(clip, "read_clipboard_text", lambda: state["text"])
    monkeypatch.setattr(window.controller, "start",
                        lambda tokens, hkl, hwnd, **kw: state["starts"].append(
                            (tokens, hwnd, kw)))
    window.notify.connect(state["notes"].append)

    def press(text, hwnd=1001):
        state["text"] = text
        window._on_action_hotkey(ACTION_PASTE_CLIPBOARD, hwnd)
        timer = QElapsedTimer()
        timer.start()
        while timer.elapsed() < 300:          # report 0 ms + relecture 120 ms
            qapp.processEvents()
        return state["starts"]
    press.state = state
    return press


def _typed(tokens):
    return "".join(t.ch if t.kind == "char" else f"<{t.kind}>" for t in tokens)


def test_paste_single_line_types_into_window_under_hotkey(window, paste):
    starts = paste("show running-config\r\n")
    assert len(starts) == 1
    tokens, hwnd, kw = starts[0]
    assert hwnd == 1001 and kw["countdown"] == 0
    # Le saut de ligne copie avec le texte n'ajoute PAS d'ENTREE.
    assert _typed(tokens) == "show running-config"


def test_paste_multiline_needs_second_press(window, paste):
    assert paste("ls -la\r\nwhoami\r\n") == []          # 1er appui : avertit
    assert "2 lignes à coller" in paste.state["notes"][-1]
    starts = paste("ls -la\r\nwhoami\r\n")               # 2e appui : tape
    assert len(starts) == 1
    assert _typed(starts[0][0]) == "ls -la<enter>whoami"


def test_paste_multiline_confirmation_is_tied_to_text_and_window(window, paste):
    paste("a\nb")
    assert paste("a\nc") == []            # texte change : nouvel avertissement
    assert paste("a\nc", hwnd=1002) == [] # autre fenetre : idem
    assert len(paste("a\nc", hwnd=1002)) == 1


def test_regression_paste_trailing_blank_line_not_executed(window, paste):
    """'ls\n\n'.splitlines() = ['ls', ''] : ENTREE etait tapee apres ls."""
    starts = paste("ls\n\n  \n")
    assert len(starts) == 1 and _typed(starts[0][0]) == "ls"


@pytest.mark.parametrize("bad", ["\x04", "\x1b:!sh", "a\x1cb", "a\x85b",
                                 "a b", "pa​ss", "‮txt"])
def test_regression_paste_refuses_control_and_invisible_chars(window, paste, bad):
    """Ils devenaient de vraies touches (Ctrl+D, Echap) ou des ENTREE."""
    assert paste(f"root{bad}") == []
    log = window.log_edit.toPlainText()
    assert "caractère(s) de contrôle ou invisible(s)" in log
    assert bad not in log


def test_paste_allows_tab(window, paste):
    starts = paste("col1\tcol2")
    assert len(starts) == 1 and "<tab>" in _typed(starts[0][0])


def test_paste_never_logs_clipboard_content(window, paste):
    paste("MotDePasse-Secret!42")
    log = window.log_edit.toPlainText()
    assert "MotDePasse-Secret!42" not in log and "Presse-papiers" in log


def test_paste_refuses_empty_clipboard_after_retry(window, paste):
    assert paste("") == [] and paste("  \r\n ") == []
    assert "presse-papiers vide" in window.log_edit.toPlainText()


def test_paste_refusal_notifies_tray_when_window_hidden(window, paste):
    window.hide()
    paste("")
    assert any("Collage refusé" in n for n in paste.state["notes"])


def test_paste_refuses_oversized_clipboard(window, paste):
    from core.paste import MAX_PASTE_CHARS
    assert paste("x" * (MAX_PASTE_CHARS + 1)) == []
    assert "trop long" in window.log_edit.toPlainText()


def test_paste_never_falls_back_to_another_window(window, paste, monkeypatch):
    """Outil au premier plan (ou aucune fenetre) : refus, jamais de frappe
    dans la derniere fenetre de travail."""
    window._last_foreground = 1002
    monkeypatch.setattr(w32_backend(), "belongs_to_process",
                        lambda hwnd, pid: hwnd == 4242)
    assert paste("secret", hwnd=4242) == []
    assert paste("secret", hwnd=0) == []
    assert "hors d'une fenêtre cible" in window.log_edit.toPlainText()


def w32_backend():
    from keyboard import backend
    return backend


def test_paste_hotkey_is_first_in_options_and_persists(window, qapp,
                                                       isolated_settings):
    from PySide6.QtWidgets import QLabel
    from core.hotkeys import ACTION_PASTE_CLIPBOARD, APP_ACTIONS
    from ui.options_dialog import OptionsDialog
    assert APP_ACTIONS[0] == ACTION_PASTE_CLIPBOARD
    dialog = OptionsDialog("light", "x.enc")
    labels = [w.text() for w in dialog.findChildren(QLabel)]
    paste_i = labels.index("Coller le presse-papiers (frappe) :")
    assert paste_i < labels.index("Cibler la fenêtre active :")
    assert window.set_app_hotkey(ACTION_PASTE_CLIPBOARD, 0x0002 | 0x0008,
                                 0x56) is None      # Ctrl+Win+V
    assert window._settings.value(
        f"hotkeys/{ACTION_PASTE_CLIPBOARD}_vk", type=int) == 0x56


@pytest.mark.parametrize("mods,vk", [(0x2, 0x43), (0x2, 0x56), (0x2, 0x58),
                                     (0x6, 0x56), (0x4, 0x2D), (0x2, 0x2D)])
def test_copy_paste_shortcuts_are_refused_as_hotkeys(window, mods, vk):
    """Ctrl+C / Ctrl+V... confisques partout, dont le Ctrl+C qui precede
    l'action Coller."""
    from core.hotkeys import ACTION_PASTE_CLIPBOARD
    reason = window.set_app_hotkey(ACTION_PASTE_CLIPBOARD, mods, vk)
    assert reason is not None and "copier / coller" in reason


def test_quick_entry_repr_hides_text():
    assert "s3cr3t" not in repr(QuickEntry(label="a", text="s3cr3t"))


def test_controller_releases_tokens_after_stop(window):
    from keyboard.engine import Token
    window.controller._tokens = [Token("char", "x", False, 0, 1)]
    window.controller._set_state("sending")
    window.controller.stop()
    assert window.controller._tokens == []


def test_regression_entry_hotkey_never_falls_back_to_another_window(
        window, monkeypatch):
    """Raccourci d'une entree presse alors que l'outil a le focus (ou sans
    fenetre au premier plan) : la frappe partait dans la derniere fenetre de
    travail, que l'operateur ne regardait pas. Refus + bulle, comme Coller."""
    from keyboard import backend
    starts, notes = [], []
    monkeypatch.setattr(window.controller, "start",
                        lambda *a, **k: starts.append(a))
    monkeypatch.setattr(backend, "belongs_to_process",
                        lambda hwnd, pid: hwnd == 4242)
    window.notify.connect(notes.append)
    window._last_foreground = 1002
    entry = QuickEntry(label="root", text="secret")
    window._on_hotkey(entry, 4242)      # l'outil a le focus
    window._on_hotkey(entry, 0)         # aucune fenetre au premier plan
    assert starts == []
    assert "hors d'une fenêtre cible" in window.log_edit.toPlainText()
    window._on_hotkey(entry, 1001)      # vraie fenetre cible : envoye
    assert len(starts) == 1 and starts[0][2] == 1001


def test_palette_click_keeps_fallback_to_last_window(window, monkeypatch):
    """Le clic sur la palette donne le focus a l'outil : le repli sur la
    derniere fenetre de travail est voulu dans ce seul cas."""
    from keyboard import backend
    starts = []
    monkeypatch.setattr(window.controller, "start",
                        lambda *a, **k: starts.append((a, k)))
    monkeypatch.setattr(backend, "get_foreground_window", lambda: 4242)
    monkeypatch.setattr(backend, "belongs_to_process",
                        lambda hwnd, pid: hwnd == 4242)
    window._last_foreground = 1002
    window._on_palette_click(QuickEntry(label="root", text="x"))
    assert len(starts) == 1
    args, kw = starts[0]
    assert args[2] == 1002 and kw["restore_focus"] is True
