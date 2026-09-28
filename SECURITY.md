# Sécurité

## Signaler une vulnérabilité

Merci de **ne pas ouvrir d'issue publique** pour une faille de sécurité.
Utilisez le signalement privé de GitHub :
**[Security → Report a vulnerability](https://github.com/Kenny3231/SendToConsole/security/advisories/new)**.

Indiquez la version concernée, les étapes pour reproduire et l'impact
estimé. Un premier retour est visé sous 7 jours.

## Versions suivies

Seule la **dernière release** reçoit les correctifs de sécurité.

## Ce que SendToConsole protège

SendToConsole manipule des mots de passe d'administration. Les garanties
ci-dessous sont des exigences du projet, vérifiées par la suite de tests
(`tests/test_storage.py`, `tests/test_hygiene.py`).

| Garantie | Mise en œuvre |
|---|---|
| Aucun secret en clair sur disque | Coffre chiffré avec [Fernet](https://cryptography.io/en/latest/fernet/) (AES-128-CBC + HMAC-SHA256, authentifié). |
| Clé jamais stockée | Dérivée du master password par PBKDF2-HMAC-SHA256, **600 000 itérations**, sel aléatoire de 16 octets par coffre. |
| Fichier altéré rejeté | Authentification Fernet ; nombre d'itérations lu dans l'en-tête borné à [600 000 ; 10 000 000] (ni affaiblissement, ni fichier piégé qui bloquerait l'application). |
| Pas de coffre corrompu | Écriture atomique : fichier temporaire exclusif, `fsync`, relecture, puis renommage. |
| Pas d'écrasement | Création d'un coffre et « Enregistrer sous » par renommage exclusif : un fichier existant n'est jamais remplacé. |
| Accès limité au fichier | DACL NTFS protégée : votre compte, SYSTEM et Administrateurs uniquement (sur NTFS). |
| Pas de fuite annexe | Jamais de secret dans le journal, le registre (`QSettings`), un message d'erreur, ni le presse-papiers. |
| Presse-papiers jamais écrit | Lu seulement quand vous pressez le raccourci « Coller », puis tapé ; jamais journalisé. Caractères de contrôle et invisibles refusés, pas d'ENTRÉE sur la dernière ligne, double appui pour plusieurs lignes, jamais de frappe dans une autre fenêtre que celle au premier plan. |
| Frappe au bon endroit | Ciblage par handle de fenêtre ; pause automatique si le focus quitte la cible. |

Aucune connexion réseau : l'application n'a ni serveur, ni télémétrie, ni
mise à jour automatique.

## Ce qu'il ne protège pas (modèle de menace)

- **Un logiciel malveillant qui tourne sous votre compte** pendant que le
  coffre est ouvert : il peut lire la mémoire du processus ou capter les
  frappes simulées. Aucun outil de ce type ne peut s'en prémunir.
- **La mémoire du processus** : un `str` Python ne peut pas être effacé de
  façon garantie. Les champs de saisie sont vidés après usage, mais le master
  password et les secrets déchiffrés restent en mémoire tant que
  l'application tourne (vidage mémoire, fichier d'échange, hibernation).
- **Un master password faible** : PBKDF2 ralentit une attaque hors ligne sur
  un coffre volé, sans la rendre impossible. Choisissez une phrase longue.
- **Un coffre sur clé USB (FAT32/exFAT) ou un partage réseau** : pas de
  droits NTFS restreints ; seule la force du master password protège le
  fichier en cas de perte.
- **Écritures concurrentes** : un même coffre ouvert depuis deux postes ou
  deux sessions — le dernier qui enregistre l'emporte.
- **Copies** : « Enregistrer sous » crée une copie avec la même clé ; changer
  plus tard le master password d'un coffre ne change pas celui des copies.
- **Ce que vous copiez** : le presse-papiers de Windows est lisible par les
  autres programmes de votre session et conservé dans l'historique (Win+V),
  voire synchronisé si vous l'avez activé. L'action « Coller » ne l'écrit
  pas, mais ne peut pas protéger ce que vous y avez mis : pour un mot de
  passe récurrent, préférez une entrée d'envoi rapide (coffre chiffré).
- **Métadonnées** : les chemins des coffres récents sont mémorisés en clair
  dans le registre (`HKCU\Software\SendToConsole`). Ce ne sont que des
  chemins, jamais des secrets, mais un nom de dossier peut être parlant.

## Installer l'application de façon sûre

- Décompressez-la dans un dossier modifiable **par votre compte seulement**
  (par exemple `%LOCALAPPDATA%\Programs`). Si d'autres comptes peuvent
  modifier le dossier, ils peuvent remplacer l'exécutable.
- Vérifiez l'empreinte SHA-256 de l'archive et son attestation de provenance
  (voir le [README](README.md#installation)).
- L'exécutable n'est pas signé numériquement (avertissement SmartScreen
  possible au premier lancement).

## Chaîne de production

- Les releases sont construites par GitHub Actions sur une machine vierge,
  à partir du tag, après passage de la suite de tests.
- Chaque archive est accompagnée de son empreinte SHA-256 et d'une
  attestation de provenance (Sigstore) vérifiable avec
  `gh attestation verify`.
- Dépendances épinglées (`==`) ; Dependabot surveille les mises à jour.
