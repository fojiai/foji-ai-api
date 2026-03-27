"""
DynamoDB-backed chat history.

Table schema:
  PK:  session_id    (String)
  SK:  timestamp     (Number — epoch ms, ensures chronological sort)
  Attributes:
    role, content, provider, agent_id, company_id
    input_tokens, output_tokens   — for analytics aggregation
    date_partition                — YYYY-MM-DD, used by the nightly analytics Lambda to scan
  TTL: expires_at    (90 days from creation, Unix epoch seconds)
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_TTL_SECONDS = 90 * 24 * 3600  # 90 days


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token (BPE average)."""
    return max(1, len(text) // 4)


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str


class ChatHistoryService:
    def __init__(self) -> None:
        cfg = get_settings()
        self._table_name = cfg.aws_dynamodb_table
        self._max_messages = cfg.chat_history_max_messages
        dynamodb = boto3.resource(
            "dynamodb",
            region_name=cfg.aws_region,
            aws_access_key_id=cfg.aws_access_key_id or None,
            aws_secret_access_key=cfg.aws_secret_access_key or None,
        )
        self._table = dynamodb.Table(self._table_name)

    @staticmethod
    def new_session_id() -> str:
        return str(uuid.uuid4())

    async def load(self, session_id: str) -> list[ChatMessage]:
        """Return the last N messages for this session, oldest first."""
        try:
            response = await asyncio.to_thread(
                self._table.query,
                KeyConditionExpression=Key("session_id").eq(session_id),
                ScanIndexForward=True,
            )
            items = response.get("Items", [])
            items = items[-self._max_messages :]
            return [ChatMessage(role=item["role"], content=item["content"]) for item in items]
        except Exception:
            logger.exception("Failed to load chat history for session %s", session_id)
            return []

    async def save(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        provider: str,
        agent_id: int,
        company_id: int,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """Persist the user/assistant pair. Fire-and-forget friendly."""
        now_ms = int(time.time() * 1000)
        expires_at = int(time.time()) + _TTL_SECONDS
        date_partition = date.today().isoformat()

        actual_input_tokens = input_tokens if input_tokens is not None else _estimate_tokens(user_message)
        actual_output_tokens = output_tokens if output_tokens is not None else _estimate_tokens(assistant_message)

        items = [
            {
                "session_id": session_id,
                "timestamp": Decimal(now_ms),
                "role": "user",
                "content": user_message,
                "provider": provider,
                "agent_id": agent_id,
                "company_id": company_id,
                "input_tokens": actual_input_tokens,
                "output_tokens": 0,
                "date_partition": date_partition,
                "expires_at": expires_at,
            },
            {
                "session_id": session_id,
                "timestamp": Decimal(now_ms + 1),
                "role": "assistant",
                "content": assistant_message,
                "provider": provider,
                "agent_id": agent_id,
                "company_id": company_id,
                "input_tokens": 0,
                "output_tokens": actual_output_tokens,
                "date_partition": date_partition,
                "expires_at": expires_at,
            },
        ]

        try:
            await asyncio.to_thread(self._write_batch, items)
        except Exception:
            logger.exception("Failed to save chat history for session %s", session_id)

    def _write_batch(self, items: list[dict]) -> None:
        """Sync batch write — runs in a thread via asyncio.to_thread."""
        with self._table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)
