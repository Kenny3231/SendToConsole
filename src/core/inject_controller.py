"""
inject_controller.py - Envoi non bloquant des jetons de frappe, pilote par
QTimer : equivalent PySide6 du Timer WinForms $timer.Add_Tick du script PowerShell d'origine (historique git 68f1fc4).
Memes etats (idle / countdown / resuming / sending / paused) et meme
garde-fou : pause automatique si le focus quitte la fenetre cible.

Deux modes d'envoi, comme dans le script PowerShell d'origine :

  - Envoi de contenu : la fenetre cible est choisie dans la liste, et on
    force son passage au premier plan avant de taper.
  - Envoi rapide (raccourci ou palette) : on ne force PAS le premier plan.
    Au moment ou le raccourci est presse, le curseur est deja dans le champ
    voulu - deplacer le focus casserait justement ce qu'on cherche a faire.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from keyboard import backend as w32
from keyboard import engine
from keyboard.engine import Token, send_token


class InjectController(QObject):
    status_changed = Signal(str)
    progress_changed = Signal(int, int)     # (fait, total)
    finished = Signal()
    failed = Signal(str)
    state_changed = Signal(str)
    paused_on_focus_loss = Signal()
    #: (caracteres passes par le repli Unicode, caracteres non tapes du tout)
    diagnostics = Signal(object, object)
    #: Modificateurs encore enfonces qu'il a fallu relacher avant de taper.
    modifiers_released = Signal(object)

    #: Attente du relachement physique des touches du raccourci.
    SETTLE_TICK_MS = 40
    SETTLE_MAX_TICKS = 50      # 2 s au maximum, puis on tape quand meme
    SETTLE_GRACE_MS = 150      # respiration finale avant la premiere frappe

    def __init__(self, guard_focus: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.guard_focus = guard_focus
        self.send_method = engine.METHOD_SCANCODE
        self._settle_left = 0
        self._tokens: list[Token] = []
        self._index = 0
        self._hkl_local = 0
        self._target_hwnd = 0
        self._countdown = 0
        self._state = "idle"
        self._force_foreground = True

    # ------------------------------------------------------------- etat --
    @property
    def state(self) -> str:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state != "idle"

    def _set_state(self, value: str) -> None:
        self._state = value
        self.state_changed.emit(value)

    # ------------------------------------------------------------ pilotage --
    def start(self, tokens: list[Token], hkl_target: int, target_hwnd: int,
              countdown: int = 0, force_foreground: bool = True,
              restore_focus: bool = False) -> None:
        """hkl_target : disposition de la FENETRE VISEE (layout_of_window).
        restore_focus : le focus est passe a l'outil (clic sur la palette),
        il faut le rendre a la fenetre de travail avant la premiere frappe."""
        if self.is_running:
            self.failed.emit("Un envoi est déjà en cours.")
            return
        if not tokens:
            self.failed.emit("Rien à envoyer.")
            return
        if not target_hwnd:
            self.failed.emit("Aucune fenêtre de travail identifiée.")
            return

        self._tokens = tokens
        self._index = 0
        self._hkl_local = hkl_target
        self._target_hwnd = target_hwnd
        self._force_foreground = force_foreground
        engine.reset_diagnostics()

        if countdown > 0:
            self._countdown = countdown
            self._set_state("countdown")
            self.status_changed.emit(f"Envoi dans {countdown} s...")
            self._timer.start(1000)
            return

        if restore_focus:
            # L'attente passe par l'etat 'resuming' : un sleep bloquant dans
            # un gestionnaire d'evenement est exactement ce qu'il faut eviter.
            if not w32.force_foreground(target_hwnd):
                self.failed.emit("Retour du focus vers la fenêtre impossible.")
                return
            self._set_state("resuming")
            self.status_changed.emit("Retour du focus...")
            self._timer.start(250)
            return

        self._begin_sending()

    def stop(self, reason: str = "demande utilisateur") -> None:
        if not self.is_running:
            return
        self._timer.stop()
        self._set_state("idle")
        self.status_changed.emit(f"Arrêté - {reason}")

    def pause(self) -> None:
        if self._state != "sending":
            return
        self._timer.stop()
        self._set_state("paused")
        self.status_changed.emit("En pause.")

    def resume(self) -> None:
        if self._state != "paused":
            return
        self._set_state("sending")
        self.status_changed.emit("Envoi repris.")
        self._timer.start(50)

    # ------------------------------------------------------------ interne --
    def _begin_sending(self) -> None:
        if self._force_foreground:
            if not w32.force_foreground(self._target_hwnd):
                self._set_state("idle")
                self.failed.emit("Activation de la fenêtre cible impossible.")
                return

        # Quand l'envoi vient d'un raccourci global, l'operateur tient encore
        # Alt ou Ctrl : sans ce relachement, chaque lettre tapee ensuite est
        # lue comme un raccourci et ne s'ecrit pas.
        released = w32.release_held_modifiers()
        if released:
            self.modifiers_released.emit(released)
            # On attend en plus que l'operateur lache PHYSIQUEMENT les
            # touches : son propre relachement, s'il tombait en pleine
            # frappe, reactiverait l'etat modificateur qu'on vient d'annuler.
            self._settle_left = self.SETTLE_MAX_TICKS
            self._set_state("settling")
            self.status_changed.emit("Relâche les touches du raccourci...")
            self._timer.start(self.SETTLE_TICK_MS)
            return

        self._set_state("sending")
        self.status_changed.emit("Envoi en cours...")
        self._timer.start(50)

    def _tick(self) -> None:
        if self._state == "settling":
            self._settle_left -= 1
            still_down = w32.modifiers_down()
            if still_down and self._settle_left > 0:
                return
            # Toutes relachees (ou delai epuise) : on laisse un dernier
            # instant a l'application pour se remettre au neutre.
            self._set_state("sending")
            self.status_changed.emit("Envoi en cours...")
            self._timer.start(self.SETTLE_GRACE_MS)
            return

        if self._state == "countdown":
            self._countdown -= 1
            if self._countdown > 0:
                self.status_changed.emit(f"Envoi dans {self._countdown} s...")
                return
            if not self._force_foreground:
                # En envoi rapide, la cible est la fenetre ou l'operateur
                # vient de placer son curseur : on la relit - et avec elle sa
                # disposition clavier, qui sert a calculer les scancodes.
                self._target_hwnd = w32.get_foreground_window()
                hkl = w32.layout_of_window(self._target_hwnd)
                if hkl:
                    self._hkl_local = hkl
            self._begin_sending()
            return

        if self._state == "resuming":
            self._set_state("sending")
            self.status_changed.emit("Envoi en cours...")
            self._timer.setInterval(50)
            return

        if self._state != "sending":
            return

        if self.guard_focus and w32.get_foreground_window() != self._target_hwnd:
            self._timer.stop()
            self._set_state("paused")
            self.paused_on_focus_loss.emit()
            self.status_changed.emit(
                "PAUSE AUTO - le focus a quitté la fenêtre cible.")
            return

        if self._index >= len(self._tokens):
            self._timer.stop()
            self._set_state("idle")
            self.status_changed.emit("Terminé.")
            self.diagnostics.emit(set(engine.fallback_chars),
                                  set(engine.failed_chars))
            self.finished.emit()
            return

        tk = self._tokens[self._index]
        send_token(tk, self._hkl_local, method=self.send_method)

        self._index += 1
        self.progress_changed.emit(self._index, len(self._tokens))
        self._timer.setInterval(max(1, tk.delay_ms))
