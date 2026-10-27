# Rangeo — rangement de disques pour Windows

Rangeo range automatiquement vos fichiers (photos, vidéos, musique, documents…) dans une arborescence claire,
**sans jamais perdre un fichier** : vous voyez tout avant, chaque opération est enregistrée, et tout peut être annulé.

Version 2 : doublons ressemblants (images, musiques, vidéos), renommage en masse, analyse et nettoyage du disque,
lieu des photos (GPS), rangements planifiés, disques externes, menu clic droit, installation, ligne de commande.

---

## 1. Lancer Rangeo

**Installateur** : lancez `Rangeo-<version>-Installation.exe` (page *Releases* du dépôt, ou `installer.bat` pour
le construire).

**Version prête à l'emploi** : double-cliquez sur `dist\Rangeo\Rangeo.exe`.

- Pour l'installer proprement (menu Démarrer, raccourci Bureau, désinstallation depuis Windows) :
  Paramètres → *Intégration Windows* → **Installer Rangeo sur cet ordinateur**.
- Sans installation, vous pouvez aussi copier le dossier `dist\Rangeo` où vous voulez.
- Si Windows affiche « Windows a protégé votre ordinateur » : *Informations complémentaires* → *Exécuter quand même*
  (le programme n'est pas signé numériquement).

**Version « code source »** : double-cliquez sur `lancer.bat`. **Refaire l'exe** : double-cliquez sur `build.bat`
(il lance aussi les tests).

## 2. Essayer sans risque

Avant de ranger vos vrais disques, faites un essai sur un faux dossier en désordre :

```
.venv\Scripts\python.exe tools\dataset.py
```

Cela crée **« Rangeo - dossier de test »** sur votre Bureau (photos, musiques, films, documents, un jeu, un projet de
code, des doublons…). Rangez-le avec Rangeo, regardez le résultat, puis annulez depuis l'Historique.

## 3. Utilisation

| Page | À quoi elle sert |
|---|---|
| **Accueil** | Glissez un dossier ou un disque pour commencer. Statistiques et activité récente. |
| **Ranger** | 1) choisir les dossiers, 2) choisir où ranger, 3) choisir le profil → **Analyser** (rien n'est modifié) → **Aperçu** (avant/après, miniatures, écoute des musiques et vidéos, vous pouvez décocher) → **Lancer le rangement**. |
| **Doublons** | 4 modes : fichiers **identiques**, **images ressemblantes** (redimensionnées, recompressées, converties), **musiques identiques** (même morceau en MP3/FLAC/AAC…), **vidéos ressemblantes**. La meilleure qualité est gardée par défaut ; les copies vont dans `_Doublons`, à la corbeille, ou deviennent des liens physiques. |
| **Renommer** | Renommage en masse avec aperçu avant/après : date de prise de vue (`2024-03-12 14h32`), lieu GPS, nettoyage des « (1) » et « Copie de », majuscules, accents, numérotation, rechercher/remplacer… |
| **Analyse** | Carte de l'espace (façon WizTree), gros fichiers, vieux fichiers, fichiers temporaires, dossiers vides, installateurs obsolètes, fichiers abîmés, statistiques. Nettoyage par la corbeille, annulable. |
| **Règles** | Les règles de rangement (SI … ALORS …), modifiables visuellement. Onglet *Arborescence* pour renommer/créer des dossiers. Bouton *Tester sur un fichier*. Import/export JSON ou YAML. |
| **Automatisation** | *Surveillance* d'un dossier (ex. Téléchargements), *Planification* (tous les soirs, chaque semaine… même Rangeo fermé), *Disques externes* (proposer ou ranger automatiquement une clé USB / carte SD au branchement). |
| **Historique** | Toutes les sessions : **Annuler**, **Reprendre**, **Réessayer les erreurs**, **Exporter le rapport**. |
| **Paramètres** | Langue, thème, conflits de noms, dates, doublons, exclusions, dossiers protégés, vitesse, menu clic droit, installation… |

**Menu clic droit** (à activer dans les Paramètres) : clic droit sur un dossier ou un disque → *Ranger avec Rangeo*
(sous Windows 11 : *Afficher d'autres options*).

### Arborescence par défaut

```
Rangement/
├── Images/   Photos/{année}/{année}-{mois} {Mois}/ · Captures d'écran/{année}/ · Images retouchées & design/ · Autres images/
├── Vidéos/   Films/{Titre (année)}/ · Séries/{Série}/Saison {nn}/ · Vidéos perso/{année}/{année}-{mois}/ · Autres vidéos/
├── Musique/{Artiste}/{Album}/« 01 - Titre.mp3 »
├── Audio/ (enregistrements, sons sans tags)
├── Documents/   PDF/{année}/ · Textes/ · Tableurs/ · Présentations/ · Livres numériques/ · Divers/
├── Archives/ (+ Images disque/)
├── Logiciels/Installateurs/{logiciel}/
├── Jeux/ROMs/{console}/   (jeux d'émulateur isolés ; les collections restent en place)
├── Projets de code/{projet}/   (déplacés en entier)
├── Polices/ · Modèles 3D/ · Raccourcis & liens/
├── _À trier/     (fichiers non reconnus : jamais perdus)
└── _Doublons/    (copies identiques)
```

### Variables des règles et du renommage

`{annee}` `{mois}` `{mois_nom}` `{jour}` `{heure}` `{minute}` `{seconde}` `{type}` `{ext}` `{nom}` `{nom_complet}`
`{dossier}` `{pays}` `{ville}` `{appareil}` `{artiste}` `{album}` `{titre}` `{piste}` `{annee_sortie}` `{serie}`
`{saison}` `{episode}` `{genre}` `{logiciel}` `{version}` `{console}` `{compteur}`

- Un passage entre crochets disparaît si une de ses variables est vide : `Films/{titre}[ ({annee_sortie})]`.
- Exemple avec le GPS : `Images/Photos/{annee}/{pays}/{ville}`.
- Pour le renommage : si une variable manque, le fichier garde son nom d'origine.
- Condition « contenu du texte » : classer des .txt, .docx, .odt… selon les mots qu'ils contiennent.

### Ligne de commande

```
Rangeo sort "D:\Téléchargements" --dest "D:\Rangement" --dry-run   (voir sans rien faire)
Rangeo sort "D:\Téléchargements" --in-place --yes
Rangeo duplicates "D:\Photos" --mode images --action move --yes
Rangeo history
Rangeo undo 12 --yes
```

## 4. Sécurité : ce que Rangeo garantit

- **Aperçu obligatoire** : rien ne bouge avant votre validation (sauf automatisations que vous avez activées).
- **Journal** : le plan complet est écrit sur le disque *avant* le premier déplacement.
- **Annulation** de n'importe quelle session (rangement, doublons, renommage, nettoyage), même après un redémarrage.
- **Reprise après coupure** : au démarrage, Rangeo propose de reprendre ou de tout annuler.
- **Copies vérifiées** entre deux disques : copie → lecture et comparaison de l'empreinte → seulement ensuite
  suppression de l'original. La date et les attributs sont conservés.
- **Jamais de suppression définitive** : corbeille Windows, ou dossier `Rangeo - Corbeille` à la racine des clés USB
  et disques sans corbeille.
- **Dossiers système interdits** (Windows, Program Files, AppData…). **Jeux et logiciels protégés** : un dossier qui
  contient un programme reste entier (y compris les jeux sans .dll, les dossiers `MonJeu_Data` de Unity, les dossiers
  `bin/` + `data/`), et si vous choisissez directement le dossier d'un jeu, rien n'est touché. Sauvegardes de jeux,
  collections de ROMs et projets de code sont aussi gardés entiers.
- **Fichiers ouverts** dans un autre programme : réessayés, puis signalés (jamais forcés).
- **Disque débranché / plein** : arrêt propre, reprise possible.
- Chemins longs (> 260 caractères), noms Unicode, fichiers en lecture seule, fichiers OneDrive non téléchargés : gérés.

## 5. Questions fréquentes

**Un dossier n'a pas été rangé, pourquoi ?** Regardez l'onglet *Laissés en place* de l'aperçu : Rangeo y explique
pourquoi (logiciel détecté, projet de code, données d'application…). Bouton *Ranger quand même ce dossier* si besoin.

**Comment empêcher Rangeo de toucher à un dossier ?** Paramètres → *Exclusions* (jamais parcouru) ou
*Dossiers « ne pas éclater »* (gardé entier). Astuce : un fichier vide nommé `.rangeo-keep` dans un dossier le garde
entier.

**Où sont mes réglages ?** Dans `%APPDATA%\Rangeo` (réglages, profils, journal, cache, journaux techniques).
Paramètres → *Exporter la configuration* pour les sauvegarder (JSON ou YAML).

**Comment désinstaller ?** Si Rangeo est installé : « Ajouter ou supprimer des programmes » → Rangeo, ou Paramètres →
*Désinstaller Rangeo*. Sinon, supprimez simplement son dossier.

**Diagnostic** : `Rangeo.exe --selftest C:\un\dossier` vérifie toutes les fonctions (résultat dans `selftest.json`).

**Données géographiques** : GeoNames (https://www.geonames.org, licence CC BY 4.0) — tout fonctionne hors ligne.

## 6. Pour le développement

```
.venv\Scripts\python.exe -m pytest              # 98 tests (reprise après crash, annulation octet par octet, jeux, tests de torture…)
.venv\Scripts\python.exe tools\benchmark.py 100000   # mesure des performances
.venv\Scripts\python.exe tools\ui_screenshots.py     # captures de toutes les pages
```

Structure : `rangeo/core` (moteur, sans interface) · `rangeo/ui` (interface PySide6) · `rangeo/i18n` (fr.json,
en.json) · `rangeo/cli.py` (ligne de commande) · `tests` · `tools`.

Performances mesurées (SSD, 100 000 fichiers) : scan 1,4 s · analyse 12 s (2 s avec le cache) · plan 5 s ·
rangement 28 s · annulation 26 s.
