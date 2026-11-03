"""Bounded, metadata-free preview images for generated concepts only."""

import base64
import binascii
import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_STORED_BYTES = 512 * 1024
MAX_PIXELS = 12_000_000


def compact_concept_image(encoded: str) -> str:
    try:
        if not isinstance(encoded, str) or len(encoded) > ((MAX_INPUT_BYTES + 2) // 3) * 4:
            raise ValueError("Image input exceeds limit")
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_INPUT_BYTES:
            raise ValueError("Image input exceeds limit")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if (
                    source.format not in {"JPEG", "PNG", "WEBP"}
                    or getattr(source, "n_frames", 1) != 1
                    or source.width * source.height > MAX_PIXELS
                ):
                    raise ValueError("Unsupported concept image")
                source.load()
                oriented = ImageOps.exif_transpose(source).convert("RGBA")
                oriented.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                image = Image.new("RGB", oriented.size, "white")
                image.paste(oriented, mask=oriented.getchannel("A"))
                for side, quality in ((1024, 90), (1024, 80), (1024, 70), (768, 80), (640, 70)):
                    image.thumbnail((side, side), Image.Resampling.LANCZOS)
                    output = io.BytesIO()
                    image.save(output, format="JPEG", quality=quality, optimize=True)
                    data = output.getvalue()
                    if len(data) <= MAX_STORED_BYTES:
                        return base64.b64encode(data).decode("ascii")
        raise ValueError("Image output exceeds limit")
    except (
        ValueError,
        OSError,
        binascii.Error,
        UnidentifiedImageError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise ValueError("Invalid or oversized concept image") from exc
