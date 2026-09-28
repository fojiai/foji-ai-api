from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable


def message_images(message: dict) -> list[tuple[bytes, str]]:
    """
    Images attached to a message, as (bytes, mime) pairs.

    A message is {"role", "content"} plus an optional "images" list of
    {"data": bytes, "mime": str} — a photo the customer sent. Each provider turns
    these into its own image block; providers must never forward the "images"
    key itself.
    """
    return [(img["data"], img["mime"]) for img in message.get("images") or []]


@runtime_checkable
class AIProvider(Protocol):
    """
    Common interface for all AI providers.
    Each provider must implement stream_chat and return an async iterator of text chunks.

    Messages may carry images (see message_images); providers attach them to
    the model's input in their own format.
    """

    provider_name: str

    async def stream_chat(
        self,
        messages: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        """
        Yield text chunks as they arrive from the model.
        Raises ProviderException on upstream errors.
        """
        ...
