from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable


@runtime_checkable
class AIProvider(Protocol):
    """
    Common interface for all AI providers.
    Each provider must implement stream_chat and return an async iterator of text chunks.
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
