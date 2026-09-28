"""
Speech-to-text for WhatsApp voice notes.

Brazilians send áudios constantly; an agent that ignores them feels broken, not
just robotic. The voice note is transcribed and then answered like any other
message.

OpenAI first, Gemini as the fallback — the same two providers the chat already
uses, reusing their clients and credentials.
"""

import logging
import re

from google.genai import types

from app.core.config import get_settings
from app.providers import gemini_provider, openai_provider

logger = logging.getLogger(__name__)

# OpenAI infers the format from the file extension, so give it the right one.
# WhatsApp voice notes are "audio/ogg; codecs=opus".
_EXTENSIONS = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
    "audio/flac": "flac",
    "audio/aac": "aac",
    "audio/amr": "amr",
}

_ISO_LANGUAGE = {"PtBr": "pt", "En": "en", "Es": "es"}

# A transcript that is only a placeholder ("[inaudível]", "(silence)", "...")
# means there was no usable speech.
_NO_SPEECH = re.compile(r"^[\s\[\]().…\-_*]*(?:inaud[ií]vel|inaudible|sil[eê]ncio|silence|music|m[uú]sica|no speech)?[\s\[\]().…\-_*]*$", re.IGNORECASE)


class TranscriptionError(Exception):
    """Every provider failed; the audio could not be transcribed."""


def _base_mime(mime: str | None) -> str:
    return (mime or "audio/ogg").split(";")[0].strip().lower()


def _clean(text: str | None) -> str:
    text = (text or "").strip()
    return "" if _NO_SPEECH.match(text) else text


async def transcribe(audio: bytes, mime: str | None, agent_language: str | None) -> str:
    """
    Return what was said, or "" when there's no intelligible speech.
    Raises TranscriptionError only when every provider fails.
    """
    base = _base_mime(mime)
    language = _ISO_LANGUAGE.get(agent_language or "", None)

    try:
        return _clean(await _transcribe_openai(audio, base, language))
    except Exception as exc:  # noqa: BLE001 — any failure falls through to Gemini
        logger.warning("OpenAI transcription failed (%s): %s — trying Gemini", base, exc)

    try:
        return _clean(await _transcribe_gemini(audio, base, language))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini transcription failed (%s): %s", base, exc)

    raise TranscriptionError(f"Could not transcribe {base} audio")


async def _transcribe_openai(audio: bytes, base_mime: str, language: str | None) -> str:
    client = await openai_provider._get_client()
    ext = _EXTENSIONS.get(base_mime, "ogg")
    kwargs = {"language": language} if language else {}
    result = await client.audio.transcriptions.create(
        model=get_settings().transcription_openai_model,
        file=(f"voice.{ext}", audio, base_mime),
        **kwargs,
    )
    return result.text


async def _transcribe_gemini(audio: bytes, base_mime: str, language: str | None) -> str:
    client = await gemini_provider._get_client()
    hint = f" The speaker most likely uses the language with ISO code '{language}'." if language else ""
    response = await client.aio.models.generate_content(
        model=get_settings().transcription_gemini_model,
        contents=[
            types.Part.from_bytes(data=audio, mime_type=base_mime),
            "Transcribe this voice message word for word, in the language it is spoken in."
            + hint
            + " Output only the transcription. If there is no intelligible speech, output nothing.",
        ],
    )
    return response.text
