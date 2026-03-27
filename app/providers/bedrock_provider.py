import asyncio
import logging
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor

import boto3

from app.core.config import get_settings
from app.core.exceptions import ProviderException

logger = logging.getLogger(__name__)

_FALLBACK_MODEL_ID = "amazon.nova-2-lite-v1:0"

# Dedicated thread pool for Bedrock calls — prevents starving the default executor.
# Max 10 concurrent Bedrock streams per ECS task.
_bedrock_pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="bedrock")

# Singleton client — reused across all requests to preserve connection pool.
_client = None


def _get_client():
    global _client
    if _client is None:
        settings = get_settings()
        _client = boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_bedrock_region,
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
        )
    return _client


class BedrockProvider:
    provider_name = "bedrock"

    def __init__(self, model_id: str = _FALLBACK_MODEL_ID) -> None:
        self._model_id = model_id
        self._client = _get_client()

    async def stream_chat(
        self,
        messages: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        bedrock_messages = [
            {"role": m["role"], "content": [{"text": m["content"]}]}
            for m in messages
        ]

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | Exception | None] = asyncio.Queue()

        def _sync_stream():
            try:
                response = self._client.converse_stream(
                    modelId=self._model_id,
                    system=[{"text": system_prompt}],
                    messages=bedrock_messages,
                    inferenceConfig={"maxTokens": 2048, "temperature": 0.7},
                )
                stream = response.get("stream")
                if not stream:
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ProviderException("No stream returned from Bedrock"),
                    )
                    return

                for event in stream:
                    if "contentBlockDelta" in event:
                        text = event["contentBlockDelta"].get("delta", {}).get("text", "")
                        if text:
                            loop.call_soon_threadsafe(queue.put_nowait, text)
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        future = loop.run_in_executor(_bedrock_pool, _sync_stream)

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise ProviderException(f"Bedrock error: {item}") from item
                yield item
        except ProviderException:
            raise
        except Exception as exc:
            logger.exception("Bedrock provider error (model=%s)", self._model_id)
            raise ProviderException(f"Bedrock error: {exc}") from exc
        finally:
            await future
