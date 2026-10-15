"""Types de fichiers : extensions connues et détection du vrai type (magic bytes)."""

from __future__ import annotations

import struct

# ---------------------------------------------------------------------------
# « Familles » détaillées. Chaque fichier reçoit un *kind* (photo, music, pdf…)
# appartenant à un *groupe* (image, audio, document…).
# ---------------------------------------------------------------------------
KIND_GROUP = {
    "photo": "image",
    "screenshot": "image",
    "design": "image",
    "image": "image",
    "movie": "video",
    "episode": "video",
    "home_video": "video",
    "video": "video",
    "subtitle": "video",
    "music": "audio",
    "audio": "audio",
    "pdf": "document",
    "text": "document",
    "spreadsheet": "document",
    "presentation": "document",
    "ebook": "document",
    "misc_doc": "document",
    "archive": "archive",
    "disk_image": "archive",
    "installer": "software",
    "code": "code",
    "font": "font",
    "model3d": "model3d",
    "shortcut": "shortcut",
    "technical": "technical",
    "unknown": "unknown",
    # dossiers traités d'un bloc
    "code_project": "bundle",
    "software": "bundle",
    "app_data": "bundle",
    "kept_folder": "bundle",
}

KINDS = list(KIND_GROUP)
GROUPS = sorted(set(KIND_GROUP.values()))
BUNDLE_KINDS = {"code_project", "software", "app_data", "kept_folder"}


def _exts(text: str) -> set[str]:
    return set(text.split())


RAW_EXT = _exts("cr2 cr3 crw nef nrw arw srf sr2 dng orf rw2 raf pef srw x3f 3fr erf kdc mrw mos iiq rwl dcr k25")
IMAGE_EXT = _exts("jpg jpeg jpe jfif png gif bmp dib tif tiff webp heic heif avif ico jxl jp2 tga pcx") | RAW_EXT
DESIGN_EXT = _exts(
    "psd psb ai xcf svg svgz kra afphoto afdesign afpub sketch fig eps indd cdr clip procreate xd ora"
)
VIDEO_EXT = _exts(
    "mp4 m4v mkv avi mov wmv flv webm mpg mpeg m2ts mts ts vob 3gp 3g2 ogv rm rmvb asf divx f4v m2v mk3d"
)
SUBTITLE_EXT = _exts("srt sub idx ass ssa vtt sup smi")
AUDIO_EXT = _exts(
    "mp3 flac wav m4a m4b aac ogg oga opus wma aiff aif aifc alac ape wv mka mid midi amr dsf dff ac3 dts spx caf"
)
PDF_EXT = {"pdf"}
TEXT_EXT = _exts("doc docx docm dot dotx odt ott rtf txt md markdown pages wpd wps tex rst nfo")
SPREADSHEET_EXT = _exts("xls xlsx xlsm xlsb xlt xltx ods ots csv tsv numbers")
PRESENTATION_EXT = _exts("ppt pptx pptm pps ppsx pot potx odp otp key")
EBOOK_EXT = _exts("epub mobi azw azw3 kfx fb2 djvu cbz cbr cb7 lit lrf ibooks")
MISC_DOC_EXT = _exts("ics vcf torrent gpx kml kmz one msg eml oft xps oxps chm mht mhtml")
ARCHIVE_EXT = _exts("zip rar 7z tar gz tgz bz2 tbz2 xz txz zst lz lzma lzh arj cab z ace")
DISK_IMAGE_EXT = _exts("iso img dmg nrg mdf mds cue bin vcd")
INSTALLER_EXT = _exts("exe msi msix msixbundle appx appxbundle")
CODE_EXT = _exts(
    "py pyw ipynb js mjs cjs jsx tsx java kt kts scala c cc cpp cxx h hh hpp hxx cs vb fs go rs rb php swift "
    "m mm dart lua pl pm r jl sh bash zsh fish bat cmd ps1 psm1 psd1 vbs ahk sql html htm xhtml css scss "
    "sass less vue svelte json jsonc yaml yml toml xml xsd xsl ini cfg conf env gradle cmake mk asm s "
    "groovy clj ex exs erl hs ml elm nim zig v sol"
)
FONT_EXT = _exts("ttf otf woff woff2 fon fnt pfb pfm ttc")
MODEL3D_EXT = _exts(
    "blend fbx obj stl 3mf max ma mb c4d skp dae gltf glb 3ds ply usd usda usdc usdz abc step stp iges igs "
    "f3d sldprt sldasm dwg dxf ifc lwo x3d"
)
SHORTCUT_EXT = _exts("lnk url webloc desktop website")
TECHNICAL_EXT = _exts(
    "dll sys ocx drv cpl mui pak dat sav db sqlite sqlite3 db3 log cache tmp temp bak old lock lck manifest cat "
    "inf pdb lib a o so dylib pyd pyc pyo class jar war asset assets resource resources resx bundle unity3d "
    "uasset uexp umap upk vpk bsa ba2 esp esm esl wad bnk wem pck acf vdf sig cer crt pem key p12 pfx reg "
    "msp mst ldb sst vhd vhdx vmdk vdi qcow2 nvram vmx vmsd vmem dmp etl evtx blf regtrans-ms ini_ "
    "thumbs crdownload part partial download opdownload ess skse fos dmp mdmp swp swo lnk~ ldf mdf ndf "
    "nupkg whl gem crx xpi apk aab ipa deb rpm efi rom"
)
# Fichiers en cours de téléchargement / temporaires : jamais touchés
PARTIAL_EXT = _exts("crdownload part partial download opdownload !ut !qb tmp temp filepart")
ALWAYS_SKIP_NAMES = {"desktop.ini", "thumbs.db", "ehthumbs.db", "ehthumbs_vista.db", ".ds_store", "icon\r", "autorun.inf"}

# Ordre important : premier ensemble qui contient l'extension.
_EXT_TABLE: list[tuple[set[str], str]] = [
    (IMAGE_EXT, "image"),
    (DESIGN_EXT, "design"),
    (VIDEO_EXT, "video"),
    (SUBTITLE_EXT, "subtitle"),
    (AUDIO_EXT, "audio"),
    (PDF_EXT, "pdf"),
    (TEXT_EXT, "text"),
    (SPREADSHEET_EXT, "spreadsheet"),
    (PRESENTATION_EXT, "presentation"),
    (EBOOK_EXT, "ebook"),
    (MISC_DOC_EXT, "misc_doc"),
    (ARCHIVE_EXT, "archive"),
    (DISK_IMAGE_EXT, "disk_image"),
    (INSTALLER_EXT, "installer"),
    (FONT_EXT, "font"),
    (MODEL3D_EXT, "model3d"),
    (SHORTCUT_EXT, "shortcut"),
    (CODE_EXT, "code"),
    (TECHNICAL_EXT, "technical"),
]
EXT_KIND: dict[str, str] = {}
for _set, _kind in _EXT_TABLE:
    for _e in _set:
        EXT_KIND.setdefault(_e, _kind)

# Fichiers « utilisateur » (photos, documents…) par opposition aux fichiers techniques
USER_GROUPS = {"image", "video", "audio", "document", "archive", "font", "model3d"}


def kind_from_ext(ext: str) -> str:
    return EXT_KIND.get(ext.lower().lstrip("."), "unknown")


def group_of(kind: str) -> str:
    return KIND_GROUP.get(kind, "unknown")


def is_user_kind(kind: str) -> bool:
    return group_of(kind) in USER_GROUPS


def is_technical_or_unknown(kind: str) -> bool:
    return kind in ("technical", "unknown")


# ---------------------------------------------------------------------------
# Détection par signature (magic bytes)
# ---------------------------------------------------------------------------
SNIFF_BYTES = 4096

_FAMILIES = [
    _exts("jpg jpeg jpe jfif"),
    _exts("tif tiff") | RAW_EXT,
    _exts("mp4 m4v m4a m4b m4p mov 3gp 3g2 f4v heic heif avif cr3 mp4video"),
    _exts("mkv webm mka mk3d"),
    _exts("ogg oga ogv opus spx"),
    _exts(
        "zip docx docm dotx xlsx xlsm xltx pptx pptm ppsx potx odt ott ods ots odp otp epub jar war apk aab "
        "cbz xpi kmz 3mf vsdx ipa appx appxbundle msix msixbundle nupkg whl sketch ora kra xd idml numbers "
        "pages key usdz mxl crx afphoto afdesign"
    ),
    _exts("doc dot xls xlt ppt pot pps msi msp mst msg pub vsd mpp db one"),
    _exts("exe dll sys ocx cpl scr drv mui efi com ax pyd"),
    _exts("wmv wma asf"),
    _exts("mpg mpeg vob m2v"),
    _exts("ts mts m2ts tp"),
    _exts("mp3 aac"),
    _exts("gz tgz"),
    _exts("bz2 tbz2"),
    _exts("xz txz"),
    _exts("ico cur"),
    _exts("psd psb"),
    _exts("ttf ttc"),
    _exts("djvu djv"),
    _exts("aiff aif aifc"),
    _exts("mid midi"),
    _exts("eps ai"),  # les .ai récents sont des PDF
    _exts("pdf ai"),
]
_TEXT_EXT = (
    CODE_EXT | SUBTITLE_EXT | _exts("txt md markdown csv tsv rtf svg html htm xml json nfo ini cfg log ics vcf gpx kml")
    | _exts("srt ass ssa vtt url desktop reg tex rst")
)


def _isobmff_ext(head: bytes) -> str:
    brand = head[8:12]
    if brand in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"):
        return "heic"
    if brand in (b"avif", b"avis"):
        return "avif"
    if brand == b"crx ":
        return "cr3"
    if brand == b"qt  ":
        return "mov"
    if brand in (b"M4A ", b"M4B ", b"M4P "):
        return "m4a"
    if brand.startswith(b"3g"):
        return "3gp"
    return "mp4"


def _zip_ext(head: bytes) -> str:
    if b"mimetypeapplication/epub+zip" in head:
        return "epub"
    if b"mimetypeapplication/vnd.oasis.opendocument.text" in head:
        return "odt"
    if b"mimetypeapplication/vnd.oasis.opendocument.spreadsheet" in head:
        return "ods"
    if b"mimetypeapplication/vnd.oasis.opendocument.presentation" in head:
        return "odp"
    if b"word/" in head:
        return "docx"
    if b"xl/" in head:
        return "xlsx"
    if b"ppt/" in head:
        return "pptx"
    if b"AndroidManifest.xml" in head:
        return "apk"
    if b"META-INF/MANIFEST.MF" in head:
        return "jar"
    return "zip"


def _pe_ext(head: bytes) -> str:
    try:
        offset = struct.unpack_from("<I", head, 0x3C)[0]
        if head[offset:offset + 4] == b"PE\x00\x00":
            characteristics = struct.unpack_from("<H", head, offset + 22)[0]
            return "dll" if characteristics & 0x2000 else "exe"
    except (struct.error, IndexError):
        pass
    return "exe"


def _looks_text(head: bytes) -> bool:
    if not head:
        return False
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True  # UTF-16 avec BOM
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError as exc:
        if exc.start > len(head) - 4:  # caractère coupé en fin de bloc
            return True
    # Texte Windows-1252 : très peu d'octets de contrôle
    controls = sum(1 for b in head if b < 9 or 13 < b < 32)
    return controls == 0


def sniff(head: bytes) -> str | None:
    """Renvoie l'extension correspondant au contenu réel, ou None si inconnu."""
    if len(head) < 4:
        return None
    h = head
    if h.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if h.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if h.startswith(b"%PDF"):
        return "pdf"
    if h[4:8] == b"ftyp":
        return _isobmff_ext(h)
    if h.startswith(b"RIFF") and len(h) >= 12:
        form = h[8:12]
        return {b"WEBP": "webp", b"WAVE": "wav", b"AVI ": "avi"}.get(form)
    if h.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm" if b"webm" in h[:64] else "mkv"
    if h.startswith((b"II*\x00", b"MM\x00*")):
        return "cr2" if h[8:10] == b"CR" else "tif"
    if h.startswith((b"IIRO", b"IIRS", b"IIU\x00")):
        return "orf" if h.startswith(b"IIR") else "rw2"
    if h.startswith(b"FUJIFILMCCD-RAW"):
        return "raf"
    if h.startswith(b"PK\x03\x04"):
        return _zip_ext(h)
    if h.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if h.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if h.startswith(b"\x1f\x8b"):
        return "gz"
    if h.startswith(b"BZh"):
        return "bz2"
    if h.startswith(b"\xfd7zXZ\x00"):
        return "xz"
    if h.startswith(b"\x28\xb5\x2f\xfd"):
        return "zst"
    if len(h) > 262 and h[257:262] == b"ustar":
        return "tar"
    if h.startswith(b"MSCF"):
        return "cab"
    if h.startswith(b"ID3"):
        return "mp3"
    if h.startswith(b"fLaC"):
        return "flac"
    if h.startswith(b"OggS"):
        if b"OpusHead" in h[:128]:
            return "opus"
        if b"theora" in h[:128]:
            return "ogv"
        return "ogg"
    if h.startswith(b"MZ"):
        return _pe_ext(h)
    if h.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "doc"
    if h.startswith(b"{\\rtf"):
        return "rtf"
    if h.startswith(b"8BPS"):
        return "psd"
    if h.startswith(b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"):
        return "wmv"
    if h.startswith(b"FLV\x01"):
        return "flv"
    if h.startswith((b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3")):
        return "mpg"
    if len(h) > 376 and h[0] == 0x47 and h[188] == 0x47 and h[376] == 0x47:
        return "ts"
    if h.startswith(b"AT&TFORM"):
        return "djvu"
    if h.startswith(b"MThd"):
        return "mid"
    if h.startswith(b"FORM") and h[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if h.startswith(b"#!AMR"):
        return "amr"
    if h.startswith(b"MAC "):
        return "ape"
    if h.startswith(b"wvpk"):
        return "wv"
    if h.startswith(b"BLENDER"):
        return "blend"
    if h.startswith(b"glTF"):
        return "glb"
    if h.startswith(b"SQLite format 3\x00"):
        return "sqlite"
    if h.startswith(b"ITSF"):
        return "chm"
    if h.startswith(b"L\x00\x00\x00\x01\x14\x02\x00"):
        return "lnk"
    if h.startswith(b"\x00\x01\x00\x00\x00") and len(h) > 12:
        return "ttf"
    if h.startswith(b"OTTO"):
        return "otf"
    if h.startswith(b"wOFF"):
        return "woff"
    if h.startswith(b"wOF2"):
        return "woff2"
    if h.startswith(b"\x00\x00\x01\x00") and len(h) > 6 and 0 < h[4] < 64:
        return "ico"
    if h.startswith(b"\xff\x0a") or h.startswith(b"\x00\x00\x00\x0cJXL "):
        return "jxl"
    if h.startswith(b"BM") and len(h) > 18 and struct.unpack_from("<I", h, 14)[0] in (12, 40, 52, 56, 108, 124):
        return "bmp"
    if h.startswith(b"d8:announce") or h.startswith(b"d10:created by") or h.startswith(b"d13:announce-list"):
        return "torrent"
    stripped = h.lstrip(b"\xef\xbb\xbf \t\r\n")
    lower = stripped[:512].lower()
    if lower.startswith(b"begin:vcard"):
        return "vcf"
    if lower.startswith(b"begin:vcalendar"):
        return "ics"
    if lower.startswith(b"<?xml") or lower.startswith(b"<svg"):
        return "svg" if b"<svg" in lower else "xml"
    if lower.startswith(b"<!doctype html") or lower.startswith(b"<html"):
        return "html"
    if lower.startswith(b"%!ps"):
        return "eps"
    if (h[:2] == b"\xff\xfb" or h[:2] == b"\xff\xf3" or h[:2] == b"\xff\xf2") and not _looks_text(h):
        return "mp3"
    if _looks_text(h):
        return "txt"
    return None


def compatible(declared: str, real: str) -> bool:
    """Vrai si le contenu réel correspond (à peu près) à l'extension annoncée."""
    declared = declared.lower()
    real = real.lower()
    if declared == real:
        return True
    if real == "txt":
        return declared in _TEXT_EXT or kind_from_ext(declared) in ("text", "code", "technical", "unknown")
    if real in ("xml", "html", "svg") and declared in _TEXT_EXT:
        return True
    for fam in _FAMILIES:
        if declared in fam and real in fam:
            if real in ("heic", "avif") and declared not in ("heic", "heif", "avif"):
                return False
            return True
    return False


def read_head(path: str, size: int = SNIFF_BYTES) -> bytes:
    from .fsutil import xp

    with open(xp(path), "rb") as fh:
        return fh.read(size)
