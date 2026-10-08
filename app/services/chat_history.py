"""
DynamoDB-backed chat history.

Table schema:
  PK:  session_id    (String)
  SK:  timestamp     (Number — epoch ms, ensures chronological sort)
  Attributes:
    role, content, provider, agent_id, company_id
    input_tokens, output_tokens   — for analytics aggregation
    date_partition                — YYYY-MM-DD, used by the nightly analytics Lambda to scan
    conversation_start            — True on the first exchange of a conversation;
                                    the analytics Lambda counts conversations by it
  TTL: expires_at    (90 days from creation, Unix epoch seconds)

Conversations
  A session_id is long-lived (a WhatsApp number is "wa:<phone>" forever), so a
  session is not a conversation. A conversation ends silently after a stretch of
  inactivity: the next message starts a new one, with a fresh greeting and none
  of the stale context. No "this chat has ended" message is ever sent.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key

from app.core.config import get_settings
from app.services.chat_index import record_exchange

logger = logging.getLogger(__name__)

_TTL_SECONDS = 90 * 24 * 3600  # 90 days


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token (BPE average)."""
    return max(1, len(text) // 4)


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class Conversation:
    """The current conversation on a session, as the prompt should see it."""

    messages: list[ChatMessage]
    # No messages in the current conversation: this message starts a new one.
    is_new: bool
    # There were earlier messages on this session, just not in this conversation
    # (they came before the inactivity gap). "Oi de novo" rather than "Oi".
    is_returning: bool


def _split_current_conversation(
    items_newest_first: list[dict],
    now_ms: int,
    idle_timeout_ms: int | None,
    max_messages: int,
) -> Conversation:
    """
    Keep only the messages that belong to the conversation still in progress.

    Walks back from the newest message and stops at the first gap longer than the
    idle timeout. If even the newest message is older than the timeout, the
    conversation is over and the next message starts a fresh one.
    """
    if not items_newest_first:
        return Conversation(messages=[], is_new=True, is_returning=False)

    newest_ts = int(items_newest_first[0]["timestamp"])
    if idle_timeout_ms is not None and now_ms - newest_ts > idle_timeout_ms:
        return Conversation(messages=[], is_new=True, is_returning=True)

    kept = [items_newest_first[0]]
    for newer, older in zip(items_newest_first, items_newest_first[1:]):
        if idle_timeout_ms is not None and int(newer["timestamp"]) - int(older["timestamp"]) > idle_timeout_ms:
            break
        kept.append(older)

    kept = kept[:max_messages]
    kept.reverse()  # oldest first, as the model reads it
    return Conversation(
        messages=[ChatMessage(role=i["role"], content=i["content"]) for i in kept],
        is_new=False,
        is_returning=False,
    )


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

    async def purge_company(self, company_id: int) -> int:
        """Delete every chat message belonging to a company.

        Used when a company is deleted, so its conversation history is actually
        gone rather than waiting out the 90-day TTL. There is no index on
        company_id, so this scans the table and batch-deletes the matches by
        their (session_id, timestamp) keys. A scan is fine here: deletion is a
        rare, one-off operation, not a hot path.
        """
        return await asyncio.to_thread(self._purge_company_sync, company_id)

    def _purge_company_sync(self, company_id: int) -> int:
        deleted = 0
        scan_kwargs = {
            "FilterExpression": Attr("company_id").eq(company_id),
            # Only the key attributes are needed to delete.
            "ProjectionExpression": "session_id, #ts",
            "ExpressionAttributeNames": {"#ts": "timestamp"},
        }
        while True:
            response = self._table.scan(**scan_kwargs)
            items = response.get("Items", [])
            if items:
                with self._table.batch_writer() as batch:
                    for item in items:
                        batch.delete_item(
                            Key={"session_id": item["session_id"], "timestamp": item["timestamp"]}
                        )
                deleted += len(items)
            last = response.get("LastEvaluatedKey")
            if not last:
                break
            scan_kwargs["ExclusiveStartKey"] = last
        logger.info("Purged %d chat messages for company %d", deleted, company_id)
        return deleted

    async def load(self, session_id: str) -> list[ChatMessage]:
        """Return the last N messages for this session, oldest first (no idle cut-off)."""
        return (await self.load_conversation(session_id, idle_timeout_seconds=None)).messages

    async def load_conversation(
        self, session_id: str, idle_timeout_seconds: int | None, agent_id: int | None = None
    ) -> Conversation:
        """
        Return the conversation in progress on this session.

        Reads newest-first and stops after N+1 items. The old version read
        oldest-first and sliced the tail, but a DynamoDB query returns at most
        1 MB per call, so on a long-lived session (a WhatsApp number with months
        of messages) that tail was the oldest messages, not the latest ones.
        """
        try:
            response = await asyncio.to_thread(
                self._table.query,
                **self._query_kwargs(session_id, agent_id),
            )
            return _split_current_conversation(
                response.get("Items", []),
                now_ms=int(time.time() * 1000),
                idle_timeout_ms=idle_timeout_seconds * 1000 if idle_timeout_seconds else None,
                max_messages=self._max_messages,
            )
        except Exception:
            logger.exception("Failed to load chat history for session %s", session_id)
            return Conversation(messages=[], is_new=True, is_returning=False)

    def _query_kwargs(self, session_id: str, agent_id: int | None) -> dict:
        kwargs = {
            "KeyConditionExpression": Key("session_id").eq(session_id),
            "ScanIndexForward": False,  # newest first
            # One past the cap, so a gap just beyond the window is still visible.
            "Limit": self._max_messages + 1,
        }
        if agent_id is not None:
            # A filter applies after Limit, so without a cap the page could be
            # all someone else's messages. Old shared WhatsApp keys are rare and
            # short-lived (90-day TTL); read a wider page for them.
            kwargs["FilterExpression"] = Attr("agent_id").eq(agent_id)
            kwargs["Limit"] = 200
        return kwargs

    async def messages_for(self, session_id: str, agent_id: int, limit: int = 1000) -> list[dict]:
        """The whole stored log of one chat, oldest first, for the history screen."""
        def run() -> list[dict]:
            out: list[dict] = []
            kwargs = {
                "KeyConditionExpression": Key("session_id").eq(session_id),
                "FilterExpression": Attr("agent_id").eq(agent_id),
                "ScanIndexForward": True,
            }
            while len(out) < limit:
                resp = self._table.query(**kwargs)
                for item in resp.get("Items", []):
                    out.append({
                        "role": str(item.get("role", "")),
                        "content": str(item.get("content", "")),
                        "timestamp": int(item["timestamp"]),
                    })
                last = resp.get("LastEvaluatedKey")
                if not last:
                    break
                kwargs["ExclusiveStartKey"] = last
            return out[:limit]

        return await asyncio.to_thread(run)

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
        conversation_start: bool = False,
        contact_name: str | None = None,
    ) -> None:
        """Persist the user/assistant pair. Fire-and-forget friendly.

        conversation_start marks the exchange that opens a conversation; the
        nightly analytics Lambda counts conversations by it.
        """
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
                "conversation_start": conversation_start,
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
                "conversation_start": conversation_start,
            },
        ]

        try:
            await asyncio.to_thread(self._write_batch, items)
        except Exception:
            logger.exception("Failed to save chat history for session %s", session_id)
            return

        await record_exchange(session_id, agent_id, company_id, assistant_message, contact_name)

    def _write_batch(self, items: list[dict]) -> None:
        """Sync batch write — runs in a thread via asyncio.to_thread."""
        with self._table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)
