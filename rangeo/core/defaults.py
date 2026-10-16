"""Règles par défaut : elles produisent l'arborescence de rangement « idéale »."""

from __future__ import annotations

from .rules import new_id

FOLDERS = {
    "fr": {
        "images": "Images", "photos": "Photos", "screenshots": "Captures d'écran",
        "design": "Images retouchées & design", "other_images": "Autres images",
        "videos": "Vidéos", "movies": "Films", "series": "Séries", "season": "Saison",
        "home_videos": "Vidéos perso", "other_videos": "Autres vidéos", "subtitles": "Sous-titres",
        "music": "Musique", "audio": "Audio",
        "documents": "Documents", "pdf": "PDF", "texts": "Textes", "sheets": "Tableurs",
        "slides": "Présentations", "ebooks": "Livres numériques", "misc_docs": "Divers",
        "archives": "Archives", "disk_images": "Images disque",
        "software": "Logiciels", "installers": "Installateurs",
        "code": "Projets de code", "loose_code": "Fichiers isolés",
        "fonts": "Polices", "model3d": "Modèles 3D", "shortcuts": "Raccourcis & liens",
        "unsorted": "_À trier", "duplicates": "_Doublons", "games": "Jeux", "roms": "ROMs",
    },
    "en": {
        "images": "Pictures", "photos": "Photos", "screenshots": "Screenshots",
        "design": "Edited & design", "other_images": "Other pictures",
        "videos": "Videos", "movies": "Movies", "series": "TV Shows", "season": "Season",
        "home_videos": "Home videos", "other_videos": "Other videos", "subtitles": "Subtitles",
        "music": "Music", "audio": "Audio",
        "documents": "Documents", "pdf": "PDF", "texts": "Texts", "sheets": "Spreadsheets",
        "slides": "Presentations", "ebooks": "eBooks", "misc_docs": "Misc",
        "archives": "Archives", "disk_images": "Disk images",
        "software": "Software", "installers": "Installers",
        "code": "Code projects", "loose_code": "Loose files",
        "fonts": "Fonts", "model3d": "3D models", "shortcuts": "Shortcuts & links",
        "unsorted": "_To sort", "duplicates": "_Duplicates", "games": "Games", "roms": "ROMs",
    },
}

RULE_NAMES = {
    "fr": {
        "code_project": "Projets de code (déplacés en entier)", "software": "Logiciels et jeux détectés (laissés en place)",
        "kept": "Dossiers « ne pas éclater » (laissés en place)", "screenshot": "Captures d'écran", "photo": "Photos",
        "design": "Images retouchées & design", "image": "Autres images", "episode": "Séries",
        "movie": "Films", "home_video": "Vidéos perso", "video": "Autres vidéos", "subtitle": "Sous-titres seuls",
        "music": "Musique", "audio": "Audio (enregistrements, sons)", "pdf": "PDF", "text": "Textes",
        "spreadsheet": "Tableurs", "presentation": "Présentations", "ebook": "Livres numériques",
        "misc_doc": "Documents divers", "archive": "Archives", "disk_image": "Images disque",
        "installer": "Installateurs", "code": "Fichiers de code isolés", "font": "Polices",
        "model3d": "Modèles 3D", "shortcut": "Raccourcis", "rom": "Jeux d'émulateur (ROMs)",
        "technical": "Fichiers techniques (laissés en place)",
        "unknown": "Fichiers non reconnus",
    },
    "en": {
        "code_project": "Code projects (moved as a whole)", "software": "Detected software & games (left in place)",
        "kept": "“Keep whole” folders (left in place)", "screenshot": "Screenshots", "photo": "Photos",
        "design": "Edited & design", "image": "Other pictures", "episode": "TV shows", "movie": "Movies",
        "home_video": "Home videos", "video": "Other videos", "subtitle": "Lone subtitles", "music": "Music",
        "audio": "Audio (recordings, sounds)", "pdf": "PDF", "text": "Texts", "spreadsheet": "Spreadsheets",
        "presentation": "Presentations", "ebook": "eBooks", "misc_doc": "Misc documents", "archive": "Archives",
        "disk_image": "Disk images", "installer": "Installers", "code": "Loose code files", "font": "Fonts",
        "model3d": "3D models", "shortcut": "Shortcuts", "rom": "Emulator games (ROMs)",
        "technical": "Technical files (left in place)",
        "unknown": "Unrecognized files",
    },
}


def _kind_is(kind: str) -> list[dict]:
    return [{"field": "kind", "op": "is", "value": kind}]


def default_rules(lang: str = "fr") -> list[dict]:
    f = FOLDERS.get(lang, FOLDERS["fr"])
    n = RULE_NAMES.get(lang, RULE_NAMES["fr"])
    fr = lang == "fr"
    y = "{annee}" if fr else "{year}"
    m = "{mois}" if fr else "{month}"
    mn = "{mois_nom}" if fr else "{month_name}"

    def rule(key, conditions, action, target="", rename=""):
        return {
            "id": new_id(),
            "name": n[key],
            "enabled": True,
            "match": "all",
            "conditions": conditions,
            "action": action,
            "target": target,
            "rename": rename,
            "builtin": key,
        }

    return [
        # pour un dossier entier, la cible est le dossier qui va le contenir
        rule("code_project", _kind_is("code_project"), "move", f"{f['code']}"),
        rule("software", [{"field": "kind", "op": "in_list", "value": "software, app_data, rom_collection"}],
             "ignore"),
        rule("kept", _kind_is("kept_folder"), "ignore"),
        rule("screenshot", _kind_is("screenshot"), "move", f"{f['images']}/{f['screenshots']}/{y}"),
        rule("photo", _kind_is("photo"), "move", f"{f['images']}/{f['photos']}/{y}/{y}-{m} {mn}"),
        rule("design", _kind_is("design"), "move", f"{f['images']}/{f['design']}"),
        rule("image", _kind_is("image"), "move", f"{f['images']}/{f['other_images']}"),
        rule("episode", _kind_is("episode"), "move",
             f"{f['videos']}/{f['series']}/" + ("{serie}" if fr else "{series}") + f"/{f['season']} "
             + ("{saison}" if fr else "{season}")),
        rule("movie", _kind_is("movie"), "move",
             f"{f['videos']}/{f['movies']}/" + ("{titre}[ ({annee_sortie})]" if fr else "{title}[ ({release_year})]")),
        rule("home_video", _kind_is("home_video"), "move", f"{f['videos']}/{f['home_videos']}/{y}/{y}-{m}"),
        rule("video", _kind_is("video"), "move", f"{f['videos']}/{f['other_videos']}"),
        rule("subtitle", _kind_is("subtitle"), "move", f"{f['videos']}/{f['subtitles']}"),
        rule("music", _kind_is("music"), "move",
             f"{f['music']}/" + ("{artiste}/{album}" if fr else "{artist}/{album}"),
             "[{disque}-]{piste} - {titre}" if fr else "[{disc}-]{track} - {title}"),
        rule("audio", _kind_is("audio"), "move", f"{f['audio']}"),
        rule("pdf", _kind_is("pdf"), "move", f"{f['documents']}/{f['pdf']}/{y}"),
        rule("text", _kind_is("text"), "move", f"{f['documents']}/{f['texts']}"),
        rule("spreadsheet", _kind_is("spreadsheet"), "move", f"{f['documents']}/{f['sheets']}"),
        rule("presentation", _kind_is("presentation"), "move", f"{f['documents']}/{f['slides']}"),
        rule("ebook", _kind_is("ebook"), "move", f"{f['documents']}/{f['ebooks']}"),
        rule("misc_doc", _kind_is("misc_doc"), "move", f"{f['documents']}/{f['misc_docs']}"),
        rule("archive", _kind_is("archive"), "move", f"{f['archives']}"),
        rule("disk_image", _kind_is("disk_image"), "move", f"{f['archives']}/{f['disk_images']}"),
        rule("installer", _kind_is("installer"), "move",
             f"{f['software']}/{f['installers']}/" + ("{logiciel}" if fr else "{software}")),
        rule("code", _kind_is("code"), "move", f"{f['code']}/{f['loose_code']}"),
        rule("font", _kind_is("font"), "move", f"{f['fonts']}"),
        rule("model3d", _kind_is("model3d"), "move", f"{f['model3d']}"),
        rule("shortcut", _kind_is("shortcut"), "move", f"{f['shortcuts']}"),
        rule("rom", _kind_is("rom"), "move", f"{f['games']}/{f['roms']}/{{console}}"),
        rule("technical", _kind_is("technical"), "ignore"),
        rule("unknown", _kind_is("unknown"), "move", f"{f['unsorted']}"),
    ]


def migrate_rules(rules: list[dict], lang: str = "fr") -> list[dict]:
    """Ajoute aux profils existants les règles par défaut apparues dans une nouvelle version."""
    if not any(r.get("builtin") for r in rules):
        return rules  # profil entièrement personnalisé : on n'y touche pas
    defaults = default_rules(lang)
    present = {r.get("builtin") for r in rules}
    out = list(rules)
    for i, d in enumerate(defaults):
        key = d["builtin"]
        if key in present:
            continue
        # insérée juste avant la règle par défaut qui la suit
        later = {x["builtin"] for x in defaults[i + 1:]}
        pos = next((j for j, r in enumerate(out) if r.get("builtin") in later), len(out))
        out.insert(pos, d)
        present.add(key)
    old_installers = {"Logiciels/Installateurs", "Software/Installers"}
    for r in out:  # V2 : un sous-dossier par logiciel pour les installateurs
        if r.get("builtin") == "installer" and r.get("target") in old_installers:
            r["target"] += "/{logiciel}" if r["target"].startswith("Logiciels") else "/{software}"
    for r in out:  # V2.1 : préfixe du disque pour les albums en plusieurs disques
        if r.get("builtin") == "music" and r.get("rename") in ("{piste} - {titre}", "{track} - {title}"):
            r["rename"] = "[{disque}-]{piste} - {titre}" if r["rename"].startswith("{piste}") \
                else "[{disc}-]{track} - {title}"
    for r in out:  # les collections de ROMs sont protégées comme les logiciels
        if r.get("builtin") == "software":
            for c in r.get("conditions", []):
                if c.get("field") == "kind" and c.get("op") == "in_list" and "rom_collection" not in str(c.get("value")):
                    c["value"] = f"{c.get('value')}, rom_collection"
    return out


DEFAULT_EXCLUSIONS = [
    "Windows", "Program Files", "Program Files (x86)", "ProgramData", "AppData", "$Recycle.Bin",
    "System Volume Information", "Recovery", "node_modules", ".git", ".svn", ".hg", "venv", ".venv",
    "__pycache__", ".cache", "My Games", "Saved Games", "Mes jeux", "WindowsPowerShell", "PowerShell",
    "Outlook Files", "Fichiers Outlook", "Custom Office Templates", "Modèles Office personnalisés",
    "SteamLibrary", "steamapps", "Steam", "Epic Games", "XboxGames", "Origin Games", "EA Games", "GOG Games",
    "Ubisoft Game Launcher", "Riot Games", "Battle.net", "OneDriveTemp", "Rangeo - Corbeille",
]


def default_profiles(lang: str = "fr") -> list[dict]:
    fr = lang == "fr"
    return [
        {
            "id": "general",
            "name": "Rangement général" if fr else "General sorting",
            "rules": default_rules(lang),
            "sources": [],
            "destination": "",
            "in_place": False,
            "recursive": True,
            "cleanup_empty": True,
            "extra_dirs": [],
        },
        {
            "id": "downloads",
            "name": "Téléchargements" if fr else "Downloads",
            "rules": default_rules(lang),
            "sources": [],
            "destination": "",
            "in_place": True,
            "recursive": False,
            "cleanup_empty": False,
            "extra_dirs": [],
        },
    ]
