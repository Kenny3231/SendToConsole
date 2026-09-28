# -*- mode: python ; coding: utf-8 -*-
"""
Spécification PyInstaller pour SendToConsole.

    py -V:3.12 -m PyInstaller build/SendToConsole.spec --noconfirm

Par défaut : construction en DOSSIER (onedir) -> dist/SendToConsole/SendToConsole.exe

Pourquoi pas onefile pour cet outil :
  - onefile réextrait TOUT le contenu dans %TEMP%\\_MEIxxxx à CHAQUE lancement,
    puis le supprime à la fermeture. Pour un utilitaire résident lancé au
    démarrage de la session, c'est plusieurs secondes perdues à chaque fois.
  - c'est aussi le profil de fichier que les antivirus signalent le plus
    volontiers (exécutable auto-extractible non signé).
  - en onedir, seul ce qui est réellement chargé est lu depuis le disque :
    démarrage quasi instantané.

Le mode onefile reste disponible pour livrer un fichier unique :
    $env:STC_ONEFILE = "1"   avant de lancer PyInstaller
    (ou  .\\build\\build.ps1 -OneFile)

Élagage des binaires : voir PRUNE ci-dessous. Pour construire sans élagage
(diagnostic) :  $env:STC_NO_PRUNE = "1"  (ou  .\\build\\build.ps1 -FullQt)

Note : le fichier d'entrées chiffrées est écrit À CÔTÉ de l'exécutable
(voir security/local_encrypted.py). En onedir il se retrouve donc DANS le
dossier de l'application, à côté de SendToConsole.exe.
"""

import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
ICON = ROOT / "assets" / "sendtoconsole.ico"

ONEFILE = os.environ.get("STC_ONEFILE", "") not in ("", "0")
PRUNE = os.environ.get("STC_NO_PRUNE", "") in ("", "0")

a = Analysis(
    [str(ROOT / "src" / "main.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    # Aucune donnée embarquée : les icônes sont dessinées par le code et le
    # thème est un gabarit Python. Rien à extraire au démarrage.
    datas=[],
    hiddenimports=["psutil"],
    hookspath=[],
    runtime_hooks=[],
    # Modules Qt inutiles ici : les exclure évite d'embarquer leurs bindings.
    # ATTENTION : ces excludes portent sur les MODULES Python, pas sur les
    # DLL Qt, qui sont tirées en transitif par le hook PySide6 et doivent
    # être retirées à la main (voir PRUNE plus bas).
    excludes=[
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
        "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSensors",
        "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
        "tkinter", "unittest", "pydoc_data",
    ],
    noarchive=False,
)

# ---------------------------------------------------------------- élagage --
# Constaté sur le build du 31/08 : malgré les excludes ci-dessus, le paquet
# embarquait encore Qt6Qml/Qt6Quick (tirés par le clavier virtuel), Qt6Pdf
# (tiré par le greffon d'image qpdf), le clavier virtuel lui-même, le
# rendu OpenGL logiciel de Mesa, et 100 fichiers de traduction Qt que
# l'application ne charge jamais (aucun QTranslator dans main.py).
#
# Chaque motif est comparé en minuscules au nom de destination de l'entrée.
# Pour remettre un élément en cas de souci : commenter sa ligne.
DROP_BINARIES = [
    # QML / Quick : uniquement nécessaires au clavier virtuel, lui-même inutile
    # pour un outil qui SIMULE les frappes au lieu d'en recevoir.
    "qt6qml", "qt6quick",
    "qt6virtualkeyboard", "qtvirtualkeyboardplugin",

    # Moteur PDF : tiré par le greffon d'image qpdf. On n'ouvre aucun PDF.
    "qt6pdf", "imageformats\\qpdf",

    # Rendu OpenGL logiciel (Mesa, ~20 Mo à lui seul). Une interface QtWidgets
    # est rendue par le moteur raster, pas par OpenGL.
    # ⚠️ PREMIER ÉLÉMENT À REMETTRE si l'affichage devient noir ou instable
    #    dans une session RDP ou une VM sans pilote graphique.
    "opengl32sw",

    # Formats d'image que l'outil n'affiche jamais (icônes macOS, Targa,
    # TIFF, bitmaps sans fil, WebP). qgif / qico / qjpeg / qsvg sont gardés.
    "imageformats\\qicns", "imageformats\\qtga", "imageformats\\qtiff",
    "imageformats\\qwbmp", "imageformats\\qwebp",

    # Entrée tactile TUIO (protocole UDP pour tables tactiles).
    "qtuiotouchplugin",

    # QtNetwork (v0.6.4) : seul QLocalServer/QLocalSocket (pipe nommé local,
    # instance unique) est utilisé. Pas de TLS ni de QNetworkInformation :
    # backends TLS et OpenSSL (repris du PATH du poste, donc build non
    # reproductible) inutiles. NE PAS viser "libcrypto-3.dll" (sans -x64) :
    # requis par _hashlib (hashlib.sha256).
    "plugins\\tls\\", "plugins\\networkinformation\\",
    "libssl-3-x64", "libcrypto-3-x64",
]

# Traductions Qt : aucune n'est chargée (pas de QTranslator), les libellés
# sont écrits en dur en français dans le code.
DROP_DATAS = [
    "pyside6\\translations\\",
]


def _keep(entry, patterns):
    name = str(entry[0]).replace("/", "\\").lower()
    return not any(p in name for p in patterns)


if PRUNE:
    before = len(a.binaries) + len(a.datas)
    a.binaries = [e for e in a.binaries if _keep(e, DROP_BINARIES)]
    a.datas = [e for e in a.datas if _keep(e, DROP_DATAS)]
    after = len(a.binaries) + len(a.datas)
    print(f"[SendToConsole] elagage : {before - after} entrees retirees "
          f"({before} -> {after})")
else:
    print("[SendToConsole] elagage DESACTIVE (STC_NO_PRUNE)")

pyz = PYZ(a.pure)

_common = dict(
    name="SendToConsole",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,              # application fenêtrée, pas de console noire
    disable_windowed_traceback=False,
    icon=str(ICON) if ICON.exists() else None,
    version=None,
)

if ONEFILE:
    print("[SendToConsole] mode ONEFILE")
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        runtime_tmpdir=None,
        **_common,
    )
else:
    print("[SendToConsole] mode ONEDIR (dossier)")
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        **_common,
    )
    coll = COLLECT(
        exe, a.binaries, a.datas,
        strip=False, upx=False,
        name="SendToConsole",
    )
