import logging
from collections.abc import AsyncIterator

import boto3

from app.core.config import get_settings
from app.core.exceptions import ProviderException

logger = logging.getLogger(__name__)

# Only used if the AIModels table is empty — should never happen in production.
_FALLBACK_MODEL_ID = "amazon.nova-2-lite-v1:0"


class BedrockProvider:
    provider_name = "bedrock"

    def __init__(self, model_id: str = _FALLBACK_MODEL_ID) -> None:
        self._model_id = model_id
        settings = get_settings()
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_bedrock_region,
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
        )

    async def stream_chat(
        self,
        messages: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        bedrock_messages = [
            {"role": m["role"], "content": [{"text": m["content"]}]}
            for m in messages
        ]
        try:
            response = self._client.converse_stream(
                modelId=self._model_id,
                system=[{"text": system_prompt}],
                messages=bedrock_messages,
                inferenceConfig={"maxTokens": 2048, "temperature": 0.7},
            )
            stream = response.get("stream")
            if not stream:
                raise ProviderException("No stream returned from Bedrock")

            for event in stream:
                if "contentBlockDelta" in event:
                    text = event["contentBlockDelta"].get("delta", {}).get("text", "")
                    if text:
                        yield text
        except ProviderException:
            raise
        except Exception as exc:
            logger.exception("Bedrock provider error (model=%s)", self._model_id)
            raise ProviderException(f"Bedrock error: {exc}") from exc
