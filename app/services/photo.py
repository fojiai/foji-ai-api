"""Photos customers send (WhatsApp and the website widget), checked the same way."""

import base64
import binascii


class InvalidPhoto(ValueError):
    """The payload isn't valid base64."""


def decode_photo(image_base64: str | None, image_mime: str | None, max_bytes: int) -> tuple[bytes | None, str]:
    """
    Returns (image bytes, mime). The bytes are None when there's no photo, or when
    it's too big or not an image — the caller then answers the text alone rather
    than failing the whole message. Raises InvalidPhoto for malformed base64.
    """
    mime = (image_mime or "image/jpeg").split(";")[0].strip().lower()
    if not image_base64:
        return None, mime
    try:
        data = base64.b64decode(image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidPhoto("Invalid image.") from exc
    if len(data) > max_bytes or not mime.startswith("image/"):
        return None, mime
    return data, mime


def history_text(caption: str) -> str:
    """How a photo message is kept in the text-only chat history."""
    return f"[imagem] {caption}".strip()


# What the model reads as the message when a photo came without any text.
NO_CAPTION_MESSAGE = "[The customer sent this photo without any text.]"
