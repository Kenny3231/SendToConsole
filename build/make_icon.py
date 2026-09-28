"""
make_icon.py - Produit assets/sendtoconsole.ico depuis les tracés de
src/ui/icons.py.

Ce fichier .ico ne sert qu'à PyInstaller (icône de l'exécutable et des
raccourcis Windows). L'application, elle, dessine ses icônes à l'exécution.

    python build/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtGui import QGuiApplication                          # noqa: E402

from ui.icons import ICON_SIZES, app_pixmap                        # noqa: E402


def main() -> int:
    # Un QGuiApplication est nécessaire avant tout dessin dans un QPixmap.
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)

    import tempfile
    from PIL import Image

    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        for size in ICON_SIZES:
            png = Path(tmp) / f"{size}.png"
            app_pixmap(size).save(str(png), "PNG")
            frames.append(Image.open(png).convert("RGBA"))
        frames = [frame.copy() for frame in frames]   # avant suppression du dossier

    assets = ROOT / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    target = assets / "sendtoconsole.ico"

    # Pillow écrit toutes les résolutions dans un seul .ico.
    frames[-1].save(target, format="ICO",
                    sizes=[(s, s) for s in ICON_SIZES])
    frames[-1].save(assets / "sendtoconsole.png", format="PNG")

    print(f"Icône écrite : {target}")
    print(f"Résolutions  : {', '.join(str(s) for s in ICON_SIZES)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
