# Rangeo — rangement de disques pour Windows

Rangeo range automatiquement vos fichiers (photos, vidéos, musique, documents…) dans une arborescence claire,
**sans jamais perdre un fichier** : vous voyez tout avant, chaque opération est enregistrée, et tout peut être annulé.

---

## 1. Lancer Rangeo

**Version prête à l'emploi** : double-cliquez sur `dist\Rangeo\Rangeo.exe`.

- Vous pouvez copier le dossier `dist\Rangeo` où vous voulez (par exemple `C:\Rangeo`), puis créer un raccourci vers
  `Rangeo.exe` sur le Bureau (clic droit sur `Rangeo.exe` → *Envoyer vers* → *Bureau*).
- Rien à installer : tout est dans le dossier.

**Version « code source »** (pour les mises à jour) : double-cliquez sur `lancer.bat`.

**Refaire l'exe** après une modification : double-cliquez sur `build.bat` (il lance aussi les tests).

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
| **Ranger** | 1) choisir les dossiers, 2) choisir où ranger, 3) choisir le profil → **Analyser** (rien n'est modifié) → **Aperçu** (avant/après, vous pouvez décocher) → **Lancer le rangement**. |
| **Doublons** | Trouve les fichiers strictement identiques. Vous choisissez lequel garder ; les copies vont dans `_Doublons`, à la corbeille, ou deviennent des liens physiques. |
| **Règles** | Les règles de rangement (SI … ALORS …), modifiables visuellement. Onglet *Arborescence* pour renommer/créer des dossiers. Bouton *Tester sur un fichier*. |
| **Surveillance** | Range automatiquement un dossier (ex. Téléchargements) dès qu'un fichier est complètement arrivé. |
| **Historique** | Toutes les sessions : **Annuler**, **Reprendre**, **Réessayer les erreurs**, **Exporter le rapport**. |
| **Paramètres** | Langue, thème, conflits de noms, dates, doublons, exclusions, dossiers protégés, vitesse… |

### Arborescence par défaut

```
Rangement/
├── Images/   Photos/{année}/{année}-{mois} {Mois}/ · Captures d'écran/{année}/ · Images retouchées & design/ · Autres images/
├── Vidéos/   Films/{Titre (année)}/ · Séries/{Série}/Saison {nn}/ · Vidéos perso/{année}/{année}-{mois}/ · Autres vidéos/
├── Musique/{Artiste}/{Album}/« 01 - Titre.mp3 »
├── Audio/ (enregistrements, sons sans tags)
├── Documents/   PDF/{année}/ · Textes/ · Tableurs/ · Présentations/ · Livres numériques/ · Divers/
├── Archives/ (+ Images disque/)
├── Logiciels/Installateurs/
├── Projets de code/{projet}/   (déplacés en entier)
├── Polices/ · Modèles 3D/ · Raccourcis & liens/
├── _À trier/     (fichiers non reconnus : jamais perdus)
└── _Doublons/    (copies identiques)
```

### Variables des règles

`{annee}` `{mois}` `{mois_nom}` `{jour}` `{type}` `{ext}` `{nom}` `{nom_complet}` `{dossier}` `{artiste}` `{album}`
`{titre}` `{piste}` `{annee_sortie}` `{appareil}` `{serie}` `{saison}` `{episode}` `{genre}` `{compteur}`

- Un passage entre crochets disparaît si une de ses variables est vide : `Films/{titre}[ ({annee_sortie})]`.
- Pour le renommage : si une variable manque, le fichier garde son nom d'origine.

## 4. Sécurité : ce que Rangeo garantit

- **Aperçu obligatoire** : rien ne bouge avant votre validation.
- **Journal** : le plan complet est écrit sur le disque *avant* le premier déplacement.
- **Annulation** de n'importe quelle session, même après un redémarrage.
- **Reprise après coupure** : au démarrage, Rangeo propose de reprendre ou de tout annuler.
- **Copies vérifiées** entre deux disques : copie → lecture et comparaison de l'empreinte → seulement ensuite
  suppression de l'original. La date et les attributs sont conservés.
- **Jamais de suppression définitive** : corbeille Windows, ou dossier `Rangeo - Corbeille` à la racine des clés USB
  et disques sans corbeille.
- **Dossiers système interdits** (Windows, Program Files, AppData…), logiciels, jeux, sauvegardes et projets de code
  gardés entiers.
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
Paramètres → *Exporter la configuration* pour les sauvegarder.

**Comment désinstaller ?** Désactivez « Lancer au démarrage de Windows » dans les Paramètres, puis supprimez le dossier
de Rangeo et `%APPDATA%\Rangeo`.

**Diagnostic** : `Rangeo.exe --selftest C:\un\dossier` vérifie toutes les fonctions (résultat dans `selftest.json`).

## 6. Pour le développement

```
.venv\Scripts\python.exe -m pytest              # 29 tests (dont reprise après crash, annulation octet par octet)
.venv\Scripts\python.exe tools\benchmark.py 100000   # mesure des performances
.venv\Scripts\python.exe tools\ui_screenshots.py     # captures de toutes les pages
```

Structure : `rangeo/core` (moteur, sans interface) · `rangeo/ui` (interface PySide6) · `rangeo/i18n` (fr.json,
en.json) · `tests` · `tools`.

Performances mesurées (SSD, 100 000 fichiers) : scan 1,4 s · analyse 12 s (2 s avec le cache) · plan 5 s ·
rangement 28 s · annulation 26 s.
