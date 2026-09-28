# Contribuer à SendToConsole

Merci de votre intérêt ! Les issues et pull requests sont les bienvenues.
Pour une faille de sécurité, suivez [SECURITY.md](SECURITY.md) (signalement
privé), pas une issue publique.

## Environnement

- Windows 10/11, Python **3.12**.
- `py -3.12 -m pip install -r requirements-dev.txt`
- Tests : `py -3.12 -m pytest`. `tests/conftest.py` force un **clavier
  simulé** (`STC_SIMULATE=1`) et Qt hors écran : la suite n'envoie jamais de
  vraie frappe et peut tourner en CI.
- `cli_test.py` et `src/main.py` envoient de **vraies** frappes : à lancer
  à la main, en connaissance de cause.

## Architecture

```
src/keyboard/  win32_api (Win32 réel) · backend (réel / simulé) · engine · layouts · layout_converter
src/core/      models (QuickEntry) · hotkeys · inject_controller (machine à états QTimer) · single_instance
src/security/  provider (interface) · local_encrypted · memory_provider · file_acl
src/ui/        main_window · palette · options_dialog · quick_entry_dialog · master_password_dialog · store_history · theme · icons
tests/         pytest (clavier simulé, Qt offscreen)
build/         SendToConsole.spec (PyInstaller) · build.ps1 · make_icon.py
docs/          site GitHub Pages
```

- **Stockage** : `SecretProvider` (`src/security/provider.py`) est l'unique
  interface vers les secrets. `LocalEncryptedProvider` (fichier chiffré) et
  `MemoryProvider` (volatil) l'implémentent ; d'autres sources (KeePass,
  Vaultwarden) pourront s'y brancher sans toucher à l'interface ni au moteur.
- **Injection** : clavier uniquement ; l'outil n'écrit jamais dans le
  presse-papiers (l'action « Coller » le lit seulement, dans
  `src/ui/clipboard.py`, et `src/core/paste.py` valide le texte). `SendInput`
  par scancode via `ctypes` ; AltGr = Ctrl gauche + Alt droit étendu (0xE038).
- **Ciblage** par handle de fenêtre (HWND), garde-fou de focus.
- **Palette flottante** : ne prend jamais le focus (`WS_EX_NOACTIVATE`),
  sinon le curseur quitte le champ de la console.
- **Fenêtre** : fermer = cacher dans la barre système ; seul « Quitter »
  libère les raccourcis globaux.

## Règles de sécurité (non négociables)

Voir [SECURITY.md](SECURITY.md). En résumé : jamais de secret en clair sur
disque, dans un journal, un `print`, un `repr` ou `QSettings`, et aucune
écriture dans le presse-papiers (lecture seule dans `src/ui/clipboard.py`) ; écriture du coffre atomique ; tout changement de format =
nouvelle `FORMAT_VERSION` + migration testée, sans jamais casser un coffre
existant.

## Moteur clavier : règles issues de bugs réels

1. **Scancodes selon la disposition de la fenêtre visée**
   (`GetKeyboardLayout` du thread de la fenêtre), pas celle de l'outil :
   Windows mémorise la langue de saisie par application.
2. **Relâcher les modificateurs encore enfoncés** avant la première frappe :
   au déclenchement d'un raccourci global, l'utilisateur tient encore
   Alt/Ctrl (« a » deviendrait Alt+a).
3. **Masquer le relâchement d'Alt** (frappe neutre) puis attendre le
   relâchement physique, sinon l'application cible active sa barre de menus.
4. **Ralentir l'AltGr** : modificateurs, touche et relâchement en trois
   envois séparés, délai minimal après un caractère AltGr.
5. **Jamais de caractère abandonné en silence** : repli Unicode, sinon
   signalement explicite.
6. **Anti-rebond des raccourcis globaux** (400 ms), fenêtre visée relevée à
   l'instant du raccourci.

## Conventions

- Type hints, PEP 8, docstrings sur les API publiques ; libellés de
  l'interface en français.
- Aucun `sleep` bloquant dans le thread GUI : `QTimer` + machine à états.
- Toute fonction exportée par `win32_api.py` existe avec la même signature
  dans la branche simulée de `backend.py` (vérifié par un test).
- `ctypes` : `argtypes` / `restype` toujours déclarés.
- Un bug corrigé = un test `test_regression_*`.

## Validation manuelle

La simulation ne valide pas `SendInput` réel. Après une modification du
moteur clavier, vérifier sous Windows :

1. Bloc-notes en français : envoyer `123@#€|\~{}[]` par raccourci global →
   texte identique.
2. Raccourci `Alt+F1` sur une entrée commençant par `123` → aucun menu
   activé, texte complet.
3. Envoi par la palette → le curseur reste dans le champ de la console.
4. Fenêtre dans une autre disposition → avertissement, résultat correct.
5. Changer de fenêtre en cours d'envoi → pause automatique, rien tapé ailleurs.
6. Console réelle (iDRAC, IPMI, noVNC) si les scancodes sont touchés.

## Publier une version

1. Mettre à jour `APP_VERSION` (`src/ui/main_window.py`) et `CHANGELOG.md`.
2. Créer et pousser le tag : `git tag v0.6.5 && git push origin v0.6.5`.
3. Le workflow `release.yml` teste, construit, calcule les empreintes,
   atteste la provenance et publie la release.
