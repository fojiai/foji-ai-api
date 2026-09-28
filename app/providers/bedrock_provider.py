import asyncio
import logging
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor

import boto3

from app.core.config import get_settings
from app.core.exceptions import ProviderException
from app.providers.base import message_images
from app.services.credentials_service import get_credential

logger = logging.getLogger(__name__)

_FALLBACK_MODEL_ID = "amazon.nova-2-lite-v1:0"

# Dedicated thread pool for Bedrock calls — prevents starving the default executor.
# Max 10 concurrent Bedrock streams per ECS task.
_bedrock_pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="bedrock")

_client = None
_client_key = ""


async def _get_client():
    global _client, _client_key
    access_key = await get_credential("AWS_ACCESS_KEY_ID")
    secret_key = await get_credential("AWS_SECRET_ACCESS_KEY")
    region = await get_credential("AWS_BEDROCK_REGION")

    settings = get_settings()
    if not access_key:
        access_key = settings.aws_access_key_id
    if not secret_key:
        secret_key = settings.aws_secret_access_key
    if not region:
        region = settings.aws_bedrock_region

    cache_key = f"{access_key}:{region}"
    if _client is None or _client_key != cache_key:
        _client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
        )
        _client_key = cache_key
    return _client


# The Converse API names the image format rather than taking a MIME type.
_BEDROCK_IMAGE_FORMATS = {"image/jpeg": "jpeg", "image/jpg": "jpeg", "image/png": "png",
                          "image/webp": "webp", "image/gif": "gif"}


def _image_blocks(message: dict) -> list[dict]:
    blocks = []
    for data, mime in message_images(message):
        fmt = _BEDROCK_IMAGE_FORMATS.get(mime.split(";")[0].strip().lower())
        if fmt:  # anything else Converse can't take — send the text alone
            blocks.append({"image": {"format": fmt, "source": {"bytes": data}}})
    return blocks


class BedrockProvider:
    provider_name = "bedrock"

    def __init__(self, model_id: str = _FALLBACK_MODEL_ID) -> None:
        self._model_id = model_id

    async def stream_chat(
        self,
        messages: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        client = await _get_client()
        bedrock_messages = [
            {"role": m["role"], "content": [{"text": m["content"]}, *_image_blocks(m)]}
            for m in messages
        ]

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | Exception | None] = asyncio.Queue()

        def _sync_stream():
            try:
                response = client.converse_stream(
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
