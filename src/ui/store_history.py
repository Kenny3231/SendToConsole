"""Coffres récemment ouverts : chemins seulement, jamais de secret.

Le dernier coffre ouvert (`storage/path`) est proposé au lancement suivant ;
la liste des récents (`storage/recent`) permet de repasser d'un coffre à
l'autre sans parcourir le disque.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

LAST_KEY = "storage/path"
RECENT_KEY = "storage/recent"
MAX_RECENT = 8
STORE_SUFFIX = ".enc"


def same_path(a: "Path | str", b: "Path | str") -> bool:
    """Égalité de chemins façon Windows (casse et séparateurs ignorés)."""
    return (os.path.normcase(os.path.abspath(str(a)))
            == os.path.normcase(os.path.abspath(str(b))))


def with_store_suffix(path: Path) -> Path:
    """« coffre » saisi sans extension dans le dialogue -> « coffre.enc »."""
    return path if path.suffix else path.with_suffix(STORE_SUFFIX)


def recent_stores(settings: Any) -> list[Path]:
    """Coffres récents, du plus récent au plus ancien (doublons retirés)."""
    raw = settings.value(RECENT_KEY)
    # QSettings rend None (absent), un str (liste d'un élément relue depuis
    # le registre ou un INI) ou une liste.
    if raw is None:
        values: list = []
    elif isinstance(raw, str):
        values = [raw]
    else:
        values = list(raw)
    result: list[Path] = []
    for value in values:
        if not value:
            continue
        path = Path(str(value))
        if not any(same_path(path, known) for known in result):
            result.append(path)
    return result[:MAX_RECENT]


def remember_store(settings: Any, path: Path) -> None:
    """Note `path` comme dernier coffre ouvert et en tête des récents."""
    recent = [path] + [p for p in recent_stores(settings)
                       if not same_path(p, path)]
    settings.setValue(LAST_KEY, str(path))
    settings.setValue(RECENT_KEY, [str(p) for p in recent[:MAX_RECENT]])
