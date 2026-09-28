#!/usr/bin/env python3
"""
cli_test.py - Validation en ligne de commande du moteur d'injection clavier,
avant de brancher l'interface PySide6.

Usage :
    python cli_test.py

IMPORTANT : ce script envoie de VRAIES frappes clavier. Il s'execute
uniquement sous Windows (user32.dll / dwmapi.dll via ctypes), lance a la main
dans un PowerShell/CMD.

Dependance optionnelle : psutil (pip install psutil) pour afficher le nom
des processus proprietaires des fenetres ; sans lui, le nom affiche est '?'.
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")

from keyboard import win32_api as w32          # noqa: E402
from keyboard.engine import build_tokens, send_all_blocking  # noqa: E402


def list_and_pick_window():
    windows = w32.list_windows()
    if not windows:
        print("Aucune fenetre trouvee.")
        return None

    print(f"\n{len(windows)} fenetre(s) :\n")
    for idx, win in enumerate(windows):
        print(f"  [{idx:>3}] {win.process_name:<20} PID={win.pid:<8} "
              f"HWND=0x{win.hwnd:X}  {win.title}")

    raw = input("\nNumero de la fenetre cible (Entree pour annuler) : ").strip()
    if not raw:
        return None
    try:
        return windows[int(raw)]
    except (ValueError, IndexError):
        print("Choix invalide.")
        return None


def main() -> None:
    print("=== SendToConsole - test du moteur clavier ===")
    target = list_and_pick_window()
    if target is None:
        return

    text = input("Texte a envoyer (\\n litteral = retour ligne) : ")
    text = text.replace("\\n", "\n")

    char_delay = 15
    line_delay = 300
    countdown = 5

    built = build_tokens(
        text, char_delay_ms=char_delay, line_delay_ms=line_delay,
        final_enter=False, convert=False,
    )
    if built is None:
        print("Rien a envoyer.")
        return

    hkl_local = w32.current_layout()
    # Le calcul AltGr depend de hkl_local : on reconstruit apres l'avoir lu.
    built = build_tokens(
        text, char_delay_ms=char_delay, line_delay_ms=line_delay,
        final_enter=False, convert=False, hkl_local=hkl_local,
    )

    print(f"\n{built.line_count} ligne(s), {len(built.tokens)} jeton(s).")
    if built.alt_gr_chars:
        print(f"Caracteres AltGr detectes : {' '.join(sorted(built.alt_gr_chars))}")

    print("\nActive la fenetre cible et place le curseur dans le bon champ.")
    for remaining in range(countdown, 0, -1):
        print(f"  Envoi dans {remaining} s...", end="\r")
        time.sleep(1)
    print(" " * 40, end="\r")

    if not w32.force_foreground(target.hwnd):
        print("Impossible d'activer la fenetre cible - abandon.")
        return

    def progress(done: int, total: int) -> None:
        print(f"  Envoi... {done}/{total}", end="\r")

    send_all_blocking(built.tokens, hkl_local, on_progress=progress)
    print("\nTermine. Verifie la console : aucun caractere ne doit avoir saute.")


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Ce script doit etre execute sous Windows (pas dans le bac a "
              "sable Linux de Cowork).")
        sys.exit(1)
    main()
