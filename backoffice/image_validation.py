from io import BytesIO

from django.core.files.uploadedfile import UploadedFile
from PIL import Image, UnidentifiedImageError

ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# Contraintes assouplies (formats paysage fréquents : téléphone, bannières)
IMAGE_RULES = {
    "hero": {
        "label": "Image hero",
        "min_width": 200,
        "max_width": 1600,
        "min_height": 150,
        "max_height": 900,
        "max_bytes": 3_000_000,
        "hint": "JPG, PNG ou WebP — entre 200×150 et 1600×900 px — 3 Mo max (ex. 800×400 paysage)",
    },
    "ad": {
        "label": "Visuel publicitaire",
        "min_width": 600,
        "max_width": 1920,
        "min_height": 200,
        "max_height": 800,
        "max_bytes": 3_000_000,
        "hint": "JPG, PNG ou WebP — entre 600×200 et 1920×800 px (bannière) — 3 Mo max",
    },
    "default": {
        "label": "Image",
        "min_width": 50,
        "max_width": 2000,
        "min_height": 50,
        "max_height": 2000,
        "max_bytes": 3_000_000,
        "hint": "JPG, PNG ou WebP — 3 Mo max",
    },
}


def validate_uploaded_image(uploaded: UploadedFile | None, rule_key: str = "default") -> str | None:
    """
    Valide format, poids et dimensions.
    Retourne un message d'erreur en français, ou None si OK.
    """
    if not uploaded:
        return None

    rules = IMAGE_RULES.get(rule_key, IMAGE_RULES["default"])
    name = (uploaded.name or "").lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    content_type = (getattr(uploaded, "content_type", "") or "").lower()

    if ext and ext not in ALLOWED_IMAGE_EXTENSIONS:
        return (
            f"Mauvais format : utilisez JPG, PNG ou WebP "
            f"(fichier reçu : {ext or 'inconnu'})."
        )
    if content_type and content_type not in ALLOWED_IMAGE_CONTENT_TYPES and not content_type.startswith(
        "image/"
    ):
        return "Mauvais format : le fichier n’est pas une image valide (JPG, PNG ou WebP)."

    size = getattr(uploaded, "size", None) or 0
    if size > rules["max_bytes"]:
        max_mo = rules["max_bytes"] / 1_000_000
        return (
            f"Taille dépassée : maximum {max_mo:.0f} Mo "
            f"(fichier : {size / 1_000_000:.2f} Mo)."
        )

    try:
        uploaded.seek(0)
        raw = uploaded.read()
        uploaded.seek(0)
        with Image.open(BytesIO(raw)) as img:
            width, height = img.size
            img.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        return "Mauvais format : impossible de lire l’image (fichier corrompu ou non supporté)."

    if width < rules["min_width"] or height < rules["min_height"]:
        return (
            f"Dimensions trop petites : minimum {rules['min_width']}×{rules['min_height']} px "
            f"(image : {width}×{height} px)."
        )
    if width > rules["max_width"] or height > rules["max_height"]:
        return (
            f"Dimensions trop grandes : maximum {rules['max_width']}×{rules['max_height']} px "
            f"(image : {width}×{height} px)."
        )
    return None
