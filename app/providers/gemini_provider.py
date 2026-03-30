import logging
from collections.abc import AsyncIterator

from google import genai
from google.genai import types

from app.core.config import get_settings
from app.core.exceptions import ProviderException
from app.services.credentials_service import get_credential

logger = logging.getLogger(__name__)

_FALLBACK_MODEL_ID = "gemini-flash-lite-latest"

_client = None
_client_key = ""


async def _get_client() -> genai.Client:
    global _client, _client_key
    api_key = await get_credential("GEMINI_API_KEY")
    if not api_key:
        api_key = get_settings().gemini_api_key
    if _client is None or _client_key != api_key:
        _client = genai.Client(api_key=api_key)
        _client_key = api_key
    return _client


class GeminiProvider:
    provider_name = "gemini"

    def __init__(self, model_id: str = _FALLBACK_MODEL_ID) -> None:
        self._model_id = model_id

    async def stream_chat(
        self,
        messages: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        client = await _get_client()
        # Gemini uses "user"/"model" roles, not "user"/"assistant"
        gemini_contents = [
            types.Content(
                role="user" if m["role"] == "user" else "model",
                parts=[types.Part(text=m["content"])],
            )
            for m in messages
        ]
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.7,
            max_output_tokens=2048,
        )
        try:
            async for chunk in await client.aio.models.generate_content_stream(
                model=self._model_id,
                contents=gemini_contents,
                config=config,
            ):
                if chunk.text:
                    yield chunk.text
        except Exception as exc:
            logger.exception("Gemini provider error (model=%s)", self._model_id)
            raise ProviderException(f"Gemini error: {exc}") from exc
