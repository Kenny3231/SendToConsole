# Composants tiers

L'exécutable publié embarque les composants suivants. Chacun reste soumis à
sa propre licence.

| Composant | Licence | Source |
|---|---|---|
| Qt 6 (via PySide6) | LGPL v3 | https://code.qt.io/ |
| PySide6 / Shiboken6 | LGPL v3 | https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| cryptography | Apache 2.0 ou BSD 3 clauses | https://github.com/pyca/cryptography |
| OpenSSL (livré avec cryptography) | Apache 2.0 | https://www.openssl.org/ |
| cffi | MIT | https://github.com/python-cffi/cffi |
| psutil | BSD 3 clauses | https://github.com/giampaolo/psutil |
| Python 3.12 | PSF License | https://www.python.org/ |
| Chargeur PyInstaller | GPL v2 avec exception (autorise toute licence pour l'application) | https://github.com/pyinstaller/pyinstaller |

## Qt et LGPL v3

Qt et PySide6 sont utilisés sans modification, en **liaison dynamique** : dans
l'archive `win64.zip`, les bibliothèques Qt sont des fichiers DLL distincts
(`_internal\PySide6\`), que vous pouvez remplacer par une version compatible
de votre choix. Le texte de la licence est disponible sur
https://www.gnu.org/licenses/lgpl-3.0.html et le code source de Qt / PySide6
aux adresses ci-dessus. Le code source complet de SendToConsole est dans ce
dépôt, ce qui permet de reconstruire l'application avec une autre version de
Qt.
