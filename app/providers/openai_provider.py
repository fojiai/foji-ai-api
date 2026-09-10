import logging
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.exceptions import ProviderException
from app.services.credentials_service import get_credential

logger = logging.getLogger(__name__)

_FALLBACK_MODEL_ID = "gpt-5.4-nano"

# Cache client keyed by API key to reuse connection pool when key hasn't changed.
_client = None
_client_key = ""


async def _get_client() -> AsyncOpenAI:
    global _client, _client_key
    api_key = await get_credential("OPENAI_API_KEY")
    if not api_key:
        api_key = get_settings().openai_api_key
    if _client is None or _client_key != api_key:
        _client = AsyncOpenAI(api_key=api_key)
        _client_key = api_key
    return _client


class OpenAIProvider:
    provider_name = "openai"

    def __init__(self, model_id: str = _FALLBACK_MODEL_ID) -> None:
        self._model_id = model_id

    async def stream_chat(
        self,
        messages: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        client = await _get_client()
        full_messages = [{"role": "system", "content": system_prompt}, *messages]
        try:
            stream = await client.chat.completions.create(
                model=self._model_id,
                messages=full_messages,
                stream=True,
                # The gpt-5 family requires max_completion_tokens (max_tokens is
                # rejected) and only accepts the default temperature, so we send
                # neither the old parameter name nor an explicit temperature.
                # max_completion_tokens is also accepted by the older 4o models,
                # so this stays correct if model_id is pointed back at one.
                max_completion_tokens=2048,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as exc:
            logger.exception("OpenAI provider error (model=%s)", self._model_id)
            raise ProviderException(f"OpenAI error: {exc}") from exc
