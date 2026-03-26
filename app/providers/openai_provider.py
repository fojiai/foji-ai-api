import logging
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.exceptions import ProviderException

logger = logging.getLogger(__name__)

# Only used if the AIModels table is empty — should never happen in production.
_FALLBACK_MODEL_ID = "gpt-5.4-nano"


class OpenAIProvider:
    provider_name = "openai"

    def __init__(self, model_id: str = _FALLBACK_MODEL_ID) -> None:
        self._model_id = model_id
        self._client = AsyncOpenAI(api_key=get_settings().openai_api_key)

    async def stream_chat(
        self,
        messages: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        full_messages = [{"role": "system", "content": system_prompt}, *messages]
        try:
            stream = await self._client.chat.completions.create(
                model=self._model_id,
                messages=full_messages,
                stream=True,
                temperature=0.7,
                max_tokens=2048,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as exc:
            logger.exception("OpenAI provider error (model=%s)", self._model_id)
            raise ProviderException(f"OpenAI error: {exc}") from exc
