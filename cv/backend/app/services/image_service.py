from __future__ import annotations
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from PIL import Image, UnidentifiedImageError

ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg"}
ALLOWED_FORMATS = {"PNG", "JPEG"}


BLACK_LEVEL = 8  # luma below this is treated as black
BLANK_BLACK_SHARE = 0.98  # a dark but real scene stays well under this


class ImageValidationError(ValueError):
    pass


class BlankFrameError(ImageValidationError):
    pass


def is_blank_frame(image: Image.Image) -> bool:
    """True when almost nothing is visible. The VLM invents figures and objects when given a black frame."""
    histogram = image.convert("L").histogram()
    return sum(histogram[:BLACK_LEVEL]) / sum(histogram) >= BLANK_BLACK_SHARE


@dataclass(frozen=True)
class ValidatedImage:
    image: Image.Image
    width: int
    height: int
    content_type: str


def validate_image(data: bytes, content_type: str | None, max_bytes: int, max_dimension: int) -> ValidatedImage:
    if not data:
        raise ImageValidationError("Image upload is empty.")
    if len(data) > max_bytes:
        raise ImageValidationError(f"Image exceeds the {max_bytes} byte upload limit.")
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ImageValidationError("Only PNG and JPEG images are accepted.")
    try:
        with Image.open(BytesIO(data)) as candidate:
            if candidate.width > max_dimension or candidate.height > max_dimension:
                raise ImageValidationError(f"Image dimensions must not exceed {max_dimension}px.")
            candidate.verify()
        image = Image.open(BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageValidationError("Upload is not a valid decodable image.") from exc
    if image.format not in ALLOWED_FORMATS:
        raise ImageValidationError("Decoded image format must be PNG or JPEG.")
    if image.width > max_dimension or image.height > max_dimension:
        raise ImageValidationError(f"Image dimensions must not exceed {max_dimension}px.")
    # RGB avoids unsupported palette/alpha inputs downstream without changing original dimensions.
    return ValidatedImage(image.convert("RGB"), image.width, image.height, content_type)


def resize_for_model(image: Image.Image, max_edge: int) -> Image.Image:
    if max(image.size) <= max_edge:
        return image
    result = image.copy()
    result.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    return result


def save_jpeg(image: Image.Image, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="JPEG", quality=88, optimize=True)
