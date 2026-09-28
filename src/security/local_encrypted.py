"""
local_encrypted.py - Stockage local chiffre des entrees d'envoi rapide.

Format du fichier (JSON en clair pour l'entete, charge utile chiffree) :

    {
      "version": 1,
      "kdf": "pbkdf2-sha256",
      "iterations": 600000,
      "salt": "<base64>",
      "payload": "<jeton Fernet>"
    }

Le master password n'est jamais ecrit : seul le sel l'est. La cle est
derivee par PBKDF2-HMAC-SHA256 puis utilisee par Fernet (AES-128-CBC +
HMAC-SHA256), qui authentifie le contenu - un fichier modifie est rejete
plutot que dechiffre en silence.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from pathlib import Path, PureWindowsPath
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from core.models import QuickEntry
from security.file_acl import restrict_to_current_user
from security.provider import SecretProvider, SecretProviderError

FORMAT_VERSION = 1
# Cout volontairement eleve : le fichier est sur disque, une attaque hors
# ligne est le scenario a rendre couteux.
PBKDF2_ITERATIONS = 600_000
# Bornes acceptees pour `iterations` lu dans l'entete (non authentifie a ce
# stade) : plancher contre un abaissement du cout (downgrade), plafond contre
# un fichier piege qui bloquerait l'application (deni de service).
# Plancher = exigence de SECURITY.md (600k) : tous les coffres ont ete crees a
# ce cout depuis la v0.6.0, un en-tete plus bas ne peut donc etre qu'altere.
MIN_PBKDF2_ITERATIONS = 600_000
MAX_PBKDF2_ITERATIONS = 10_000_000
# Cout des fichiers anterieurs sans champ `iterations` : fige, il ne doit pas
# suivre une future hausse de PBKDF2_ITERATIONS (sinon ils ne s'ouvriraient plus).
LEGACY_PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16


STORE_NAME = "entries.enc"


def app_dir() -> Path:
    """Dossier de l'application : celui du .exe une fois empaquete, sinon la
    racine du projet (le parent de src/)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def _is_writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".stc-write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def appdata_store_path() -> Path:
    """Ancien emplacement : %APPDATA%\\SendToConsole\\entries.enc."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "SendToConsole" / STORE_NAME
    return Path.home() / ".config" / "sendtoconsole" / STORE_NAME


def default_store_path() -> Path:
    """A cote de l'application, et seulement a defaut dans %APPDATA%.

    %APPDATA% paraissait le choix evident, mais le Python du Microsoft Store
    y redirige silencieusement les ecritures vers un dossier virtualise :
    le chemin annonce ne menait nulle part dans l'Explorateur et le fichier
    restait introuvable. A cote de l'application, l'emplacement est visible,
    verifiable, et l'outil devient portable.
    """
    folder = app_dir()
    if _is_writable(folder):
        return folder / STORE_NAME
    return appdata_store_path()


def _package_family_name(full_name: str) -> str:
    """Nom de FAMILLE a partir du nom complet d'un paquet.

    Sous WindowsApps les dossiers portent le nom complet, versionne :
        PythonSoftwareFoundation.Python.3.12_3.12.2800.0_x64__qbz5n2kfra8p0
    alors que sous AppData\\Local\\Packages ils portent le nom de famille :
        PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0
    Confondre les deux donne un chemin qui n'existe pas.
    """
    name = full_name.split("_", 1)[0]
    publisher = full_name.rsplit("__", 1)[-1] if "__" in full_name else ""
    return f"{name}_{publisher}" if publisher else full_name


def _store_package_roots() -> list[PureWindowsPath]:
    """Racines candidates du paquet sous le Python du Microsoft Store."""
    roots: list[PureWindowsPath] = []
    local = os.environ.get("LOCALAPPDATA")

    for candidate in (sys.prefix, getattr(sys, "base_prefix", ""), sys.executable):
        text = str(candidate).replace("/", "\\")

        marker = "\\Packages\\"
        if marker in text and "PythonSoftwareFoundation.Python" in text:
            head, _, tail = text.partition(marker)
            package = tail.split("\\", 1)[0]
            roots.append(PureWindowsPath(head) / "Packages" / package)

        if "\\WindowsApps\\PythonSoftwareFoundation.Python" in text and local:
            full = text.split("\\WindowsApps\\", 1)[1].split("\\", 1)[0]
            # Les deux formes sont tentees : le nom de famille est le bon,
            # mais mieux vaut ne rien exclure quand on cherche un fichier.
            for name in (_package_family_name(full), full):
                root = PureWindowsPath(local) / "Packages" / name
                if root not in roots:
                    roots.append(root)

    return roots


def legacy_store_candidates() -> list[Path]:
    """Tous les emplacements ou un ancien fichier a pu atterrir.

    Sert a retrouver - et migrer - un stockage cree par une version
    precedente, y compris celui qu'a avale la virtualisation du Python du
    Microsoft Store.
    """
    candidates: list[Path] = [appdata_store_path()]
    for real in redirected_store_paths(appdata_store_path()):
        candidates.append(Path(str(real)))

    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def find_existing_store(preferred: Path) -> Optional[Path]:
    """Premier emplacement contenant reellement un fichier de stockage."""
    if preferred.exists():
        return preferred
    for candidate in legacy_store_candidates():
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


def redirected_store_paths(path: "Path | str") -> list[PureWindowsPath]:
    """Toutes les redirections possibles pour ce chemin (voir ci-dessous)."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []

    target = str(path).replace("/", "\\")
    base = str(appdata).replace("/", "\\").rstrip("\\")
    if not target.lower().startswith(base.lower() + "\\"):
        return []

    relative = target[len(base) + 1:]
    return [root / "LocalCache" / "Roaming" / relative
            for root in _store_package_roots()]


def redirected_store_path(path: "Path | str") -> Optional[PureWindowsPath]:
    """Emplacement REEL du fichier quand Windows virtualise les ecritures.

    Le Python du Microsoft Store tourne dans un conteneur d'application :
    Windows redirige ses ecritures dans AppData vers un dossier propre au
    paquet. Le programme croit ecrire dans %APPDATA%\\SendToConsole, mais le
    fichier atterrit sous
    %LOCALAPPDATA%\\Packages\\<paquet>\\LocalCache\\Roaming\\SendToConsole -
    d'ou un chemin affiche qui ne mene nulle part dans l'Explorateur.

    Renvoie None quand aucune redirection n'a lieu (Python normal, .exe
    PyInstaller). La comparaison porte sur les chaines et non sur des objets
    Path : le calcul concerne des chemins Windows, il doit rester juste quel
    que soit le systeme qui l'execute.
    """
    paths = redirected_store_paths(path)
    if not paths:
        return None
    # Celle qui existe vraiment fait foi ; a defaut, la premiere (le nom de
    # famille du paquet, qui est la forme correcte).
    for candidate in paths:
        try:
            if Path(str(candidate)).exists():
                return candidate
        except OSError:
            continue
    return paths[0]


def _rename_no_replace(source: Path, target: Path) -> None:
    """Renomme `source` en `target` ; FileExistsError si `target` existe.
    Windows : os.rename n'écrase jamais (atomique). Ailleurs, rename
    écraserait : lien dur (échoue si la cible existe) puis suppression."""
    if os.name == "nt":
        os.rename(source, target)
    else:
        os.link(source, target)
        os.unlink(source)


class LocalEncryptedProvider(SecretProvider):
    name = "Fichier local chiffre"
    read_only = False

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_store_path()
        self._fernet: Fernet | None = None
        self._salt: bytes | None = None
        self._iterations: int = PBKDF2_ITERATIONS
        # Resultat du dernier durcissement ACL (None = pas encore tente) :
        # l'UI le signale s'il a echoue (le fichier garde ses droits herites).
        self.acl_restricted: bool | None = None

    # ------------------------------------------------------------------ --
    @property
    def exists(self) -> bool:
        return self.path.exists()

    def is_unlocked(self) -> bool:
        return self._fernet is not None

    @staticmethod
    def _derive(password: str, salt: bytes, iterations: int) -> Fernet:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=iterations,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))
        return Fernet(key)

    def unlock(self, secret: str) -> None:
        if not secret:
            raise SecretProviderError("Master password vide.")

        if not self.exists:
            # Premiere utilisation : on cree le stockage avec ce mot de passe.
            self._salt = os.urandom(SALT_BYTES)
            self._iterations = PBKDF2_ITERATIONS
            self._fernet = self._derive(secret, self._salt, self._iterations)
            try:
                # Exclusif : un coffre apparu pendant la derivation (autre
                # session, synchronisation) n'est pas remplace par un vide.
                self._write([], self.path, exclusive=True)
            except BaseException:
                # Rien d'ecrit : ne pas laisser croire a un coffre ouvert.
                self.lock()
                raise
            return

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            salt = base64.b64decode(raw["salt"])
            payload = raw["payload"].encode("ascii")
            # Fichiers anterieurs sans le champ : cout historique.
            iterations = raw.get("iterations", LEGACY_PBKDF2_ITERATIONS)
        except PermissionError as exc:
            raise SecretProviderError(
                f"Accès refusé au fichier de stockage ({self.path}). Il a "
                "peut-être été créé depuis un autre compte Windows ou une "
                "session administrateur : relancez l'outil de la même façon, "
                f"ou rétablissez les droits (icacls \"{self.path}\" /reset)."
            ) from exc
        except OSError as exc:
            raise SecretProviderError(
                f"Lecture impossible du fichier de stockage ({self.path}) : "
                f"{exc.strerror or exc}") from exc
        except (json.JSONDecodeError, KeyError, ValueError,
                TypeError, AttributeError) as exc:
            raise SecretProviderError(
                f"Fichier de stockage illisible : {exc}") from exc

        if (not isinstance(iterations, int) or isinstance(iterations, bool)
                or not MIN_PBKDF2_ITERATIONS <= iterations
                <= MAX_PBKDF2_ITERATIONS):
            raise SecretProviderError(
                "Fichier de stockage refuse : nombre d'iterations PBKDF2 "
                f"invalide (attendu entre {MIN_PBKDF2_ITERATIONS} et "
                f"{MAX_PBKDF2_ITERATIONS}).")

        fernet = self._derive(secret, salt, iterations)
        try:
            fernet.decrypt(payload)
        except InvalidToken as exc:
            raise SecretProviderError("Master password incorrect.") from exc

        self._salt = salt
        self._iterations = iterations
        self._fernet = fernet
        # Un coffre seulement ouvert (jamais reecrit) ou migre par copie
        # garderait sinon ses droits herites, souvent trop larges.
        self.acl_restricted = restrict_to_current_user(self.path, like=self.path)

    def lock(self) -> None:
        self._fernet = None
        self._salt = None
        self._iterations = PBKDF2_ITERATIONS

    def reset(self) -> None:
        """Supprime le fichier chiffre et verrouille. Irreversible : sans le
        master password, le contenu etait de toute facon irrecuperable."""
        self.lock()
        stale = sorted(self.path.parent.glob(f"{self.path.stem}.*.tmp"))
        for path in (self.path, self.path.with_suffix(".tmp"), *stale):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise SecretProviderError(
                    f"Suppression impossible ({path}) : {exc}") from exc

    def load(self) -> list[QuickEntry]:
        if self._fernet is None:
            raise SecretProviderError("Stockage verrouillé.")
        if not self.exists:
            return []

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            data = self._fernet.decrypt(raw["payload"].encode("ascii"))
        except InvalidToken as exc:
            raise SecretProviderError(
                "Fichier de stockage altéré ou master password incorrect.") from exc
        except (OSError, json.JSONDecodeError, KeyError, ValueError,
                TypeError, AttributeError) as exc:
            raise SecretProviderError(
                f"Fichier de stockage illisible : {exc}") from exc

        # Contenu dechiffre : l'erreur ne cite JAMAIS l'exception, et elle est
        # levee HORS du bloc except pour ne pas l'enchainer (__context__ d'une
        # JSONDecodeError contient tout le clair dans .doc).
        invalid = False
        try:
            entries = json.loads(data.decode("utf-8"))
            result = [QuickEntry.from_dict(d) for d in entries]
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError,
                ValueError, TypeError, AttributeError):
            invalid = True
        if invalid:
            raise SecretProviderError(
                "Contenu du stockage invalide (format inattendu).")
        return result

    def save(self, entries: list[QuickEntry]) -> None:
        self._write(entries, self.path, exclusive=False)

    def save_as(self, path: Path, entries: list[QuickEntry]) -> None:
        """Écrit le coffre dans un NOUVEAU fichier `path` puis l'utilise
        désormais (même clé). Un fichier existant n'est jamais écrasé, même
        s'il apparaît pendant l'écriture ; en cas d'échec, `path` reste
        inchangé."""
        self._write(entries, path, exclusive=True)
        self.path = path

    def _write(self, entries: list[QuickEntry], target: Path,
               exclusive: bool) -> None:
        if self._fernet is None or self._salt is None:
            raise SecretProviderError("Stockage verrouillé.")

        payload = self._fernet.encrypt(
            json.dumps([e.to_dict() for e in entries]).encode("utf-8"))

        document = {
            "version": FORMAT_VERSION,
            "kdf": "pbkdf2-sha256",
            # Celles avec lesquelles la cle a ete derivee (jamais changees
            # silencieusement a la reecriture).
            "iterations": self._iterations,
            "salt": base64.b64encode(self._salt).decode("ascii"),
            "payload": payload.decode("ascii"),
        }

        text = json.dumps(document, indent=2)
        # Ecriture atomique : un plantage en cours d'ecriture ne doit pas
        # laisser un fichier tronque, donc illisible et toutes les entrees
        # perdues. Le .tmp est cree en exclusif sous un nom aleatoire (un
        # autre compte ne peut pas le preparer a l'avance), recoit l'ACL
        # AVANT le contenu, est fsync (sinon une coupure de courant peut
        # laisser un fichier renomme mais vide), puis relu : si l'ACL posee
        # nous en a retire l'acces (partage reseau...), on s'arrete avant de
        # remplacer le coffre. replace deplace le .tmp avec sa DACL.
        # `exclusive` (creation, « Enregistrer sous ») : le renommage final
        # echoue si la cible existe, meme apparue pendant l'ecriture (autre
        # session, synchronisation OneDrive...) - jamais d'ecrasement.
        tmp: Path | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=f"{target.stem}.",
                                        suffix=".tmp", dir=target.parent)
            tmp = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                self.acl_restricted = restrict_to_current_user(
                    tmp, like=target)
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            if tmp.read_text(encoding="utf-8") != text:
                raise OSError("relecture du fichier temporaire non conforme")
            if exclusive:
                _rename_no_replace(tmp, target)
            else:
                tmp.replace(target)
            tmp = None
        except FileExistsError as exc:
            raise SecretProviderError(
                f"Un fichier existe déjà : {target}. Il n'est jamais écrasé."
            ) from exc
        except OSError as exc:
            raise SecretProviderError(
                f"Enregistrement impossible ({target}) : "
                f"{exc.strerror or exc}. Le coffre précédent est intact."
            ) from exc
        finally:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
