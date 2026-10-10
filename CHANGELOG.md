# Journal des versions

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
versions selon [SemVer](https://semver.org/lang/fr/).

## [0.7.5] - 2026-10-10

### Ajouté
- Envoi rapide : une entrée peut être activée ou désactivée, par la case de
  sa cellule « État » (souris ou Espace) ou par la case « Active » de sa
  fiche. Désactivée,
  elle n'apparaît plus dans la palette et son raccourci global est libéré ;
  elle reste dans le coffre et peut toujours être envoyée depuis la table.
  Les entrées des coffres existants sont actives.

## [0.7.4] - 2026-10-10

### Sécurité
- Coffre ouvert à deux endroits (autre session, autre poste, clé USB,
  synchronisation) : l'enregistrement refuse désormais d'écraser une version
  modifiée ailleurs depuis l'ouverture. L'outil propose de recharger le
  coffre, d'écraser avec les entrées de la session, ou de reporter.
  Auparavant, le dernier qui enregistrait effaçait en silence les
  modifications de l'autre. Un coffre supprimé ailleurs n'est plus recréé
  en silence, et « Quitter » avertit s'il reste des entrées non
  enregistrées.
- Un coffre d'un format plus récent est refusé à l'ouverture au lieu d'être
  lu puis réécrit dans l'ancien format.
- Les raccourcis dangereux enregistrés par une version antérieure (Ctrl+V,
  Maj+lettre, AltGr+touche produisant un caractère…) ne sont plus activés au
  chargement : l'entrée apparaît « refusé » avec la raison dans le journal.
  Même contrôle pour les raccourcis d'action relus au démarrage.
- Option « Notifications discrètes » (Options > Apparence) : la bulle de
  capture n'affiche plus le titre de la fenêtre cible, qui contient souvent
  le nom de l'hôte et reste dans l'historique des notifications de Windows.

### Ajouté
- Propriétés de version de l'exécutable (Explorateur > Propriétés >
  Détails), tirées de la version de l'application.

### Corrigé
- Un raccourci pressé pendant le compte à rebours d'un envoi annonçait
  « envoi mis en pause » alors que rien n'était mis en pause.
- « Reprendre » redonne le focus à la fenêtre cible : l'envoi se remettait
  aussitôt en pause automatique.
- Raccourci global pressé pendant un envoi : son Alt n'active plus la barre
  de menus de la cible, et la reprise attend le relâchement des touches.
- Thème sombre : barre de défilement plus contrastée.

## [0.7.3] - 2026-09-29

### Corrigé
- Envoi rapide avec « Envoyer ENTRÉE après le texte » : quand l'ENTRÉE
  finale faisait changer le focus (connexion validée, boîte fermée), l'envoi
  restait bloqué en pause automatique sans plus rien à taper, et tout
  raccourci suivant était refusé. L'envoi se termine désormais dès la
  dernière frappe.

## [0.7.2] - 2026-09-28

### Interne
- Découpage de la fenêtre principale : `ui/main_window.py` passe de 1 658 à
  258 lignes ; les méthodes sont réparties par domaine dans `ui/window/`
  (envoi, coffre, options, cible, contenu, clavier, envoi rapide,
  raccourcis). Déplacement à l'identique (88 méthodes vérifiées par
  comparaison d'arbre syntaxique), aucun changement de comportement.

## [0.7.1] - 2026-09-28

### Sécurité
- Raccourcis des entrées d'envoi rapide : si l'outil a le focus (ou si
  aucune fenêtre n'est au premier plan), l'envoi est refusé et signalé par
  une bulle, au lieu de partir dans la dernière fenêtre utilisée. Même règle
  que « Coller » ; le clic sur la palette garde ce repli, voulu.

## [0.7.0] - 2026-09-28

### Ajouté
- **Coller par frappe** : nouveau raccourci global « Coller le presse-papiers
  (frappe) », en tête des raccourcis dans les options. Copiez normalement
  (Ctrl+C), cliquez dans la console, pressez le raccourci : le texte copié y
  est tapé, même là où le collage est bloqué.
- Garde-fous : double appui pour un texte de plusieurs lignes, pas d'ENTRÉE
  sur la dernière ligne, caractères de contrôle et invisibles refusés, aucune
  frappe si l'outil a le focus, refus signalés par une bulle.

### Sécurité
- Le presse-papiers n'est jamais écrit : il est seulement lu, au raccourci.
  Contenu jamais journalisé, séquence de frappe libérée en fin d'envoi, texte
  des entrées exclu de leur représentation (`repr`).
- Ctrl+C, Ctrl+V, Ctrl+X et variantes ne peuvent plus être choisis comme
  raccourcis de l'outil (ils seraient confisqués dans toutes les applications).

## [0.6.6] - 2026-09-28

### Corrigé
- Palette flottante : si Windows refuse le mode « sans focus », un
  avertissement apparaît désormais dans le Journal (l'échec passait
  inaperçu et la palette pouvait voler le focus de la console).
- Détection d'instance unique : la relance ne pouvait plus manquer son
  message sur un disque lent.
- Robustesse : gardes explicites à la place d'`assert`, seules les erreurs
  attendues sont ignorées lors de la liste des processus (analyse bandit).
- `SHA256SUMS.txt` des releases en fins de ligne LF (`sha256sum -c`).

### Dépendances
- PyInstaller 6.22.3, pyinstaller-hooks-contrib 2026.7 ; outils de
  développement épinglés. La CI construit désormais l'exécutable à chaque
  modification.

## [0.6.5] - 2026-09-28

Première version publique.

### Fonctionnalités
- Envoi de texte par frappe clavier simulée (scancodes, AltGr, repli
  Unicode) vers une fenêtre choisie, avec compte à rebours, pause, reprise,
  arrêt et garde-fou de focus.
- Conversion vers la disposition clavier de la console cible et détection de
  la disposition de la fenêtre visée.
- Envoi rapide : entrées associées à des raccourcis globaux, palette
  flottante qui ne prend jamais le focus.
- Raccourcis globaux d'action : cibler la fenêtre active, afficher / masquer
  la palette.
- Coffre chiffré (Fernet, PBKDF2-SHA256 600 000 itérations), ou mode sans
  enregistrement.
- Choix du coffre au lancement (coffres récents, ouvrir un coffre existant,
  en créer un), mémorisation du dernier coffre, « Ouvrir un autre coffre » et
  « Enregistrer sous » sans jamais écraser un fichier existant.
- Avertissement quand la fenêtre cible tourne en administrateur, instance
  unique, icône dans la barre système, thèmes clair et sombre.

### Sécurité
- Écriture atomique et exclusive du coffre, DACL NTFS restreinte,
  en-tête borné contre les fichiers altérés.
- Aucun secret dans les journaux, le registre ni le presse-papiers.
- Releases construites par la CI, avec empreintes SHA-256 et attestation de
  provenance.

[0.7.2]: https://github.com/Kenny3231/SendToConsole/releases/tag/v0.7.2
[0.7.1]: https://github.com/Kenny3231/SendToConsole/releases/tag/v0.7.1
[0.7.0]: https://github.com/Kenny3231/SendToConsole/releases/tag/v0.7.0
[0.6.6]: https://github.com/Kenny3231/SendToConsole/releases/tag/v0.6.6
[0.6.5]: https://github.com/Kenny3231/SendToConsole/releases/tag/v0.6.5
