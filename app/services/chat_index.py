"""
Index of chats for the owner's "Histórico de conversas" (FojiApi's ChatSessions
table; FojiApi owns the schema).

The messages stay in DynamoDB. This only records that a chat exists, which
agent had it, on which channel, and when it last moved, so FojiApi can list a
company's chats without scanning DynamoDB. Written on every saved exchange;
best-effort, a failure here never touches the reply.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Attr
from sqlalchemy import text

from app.core.database import get_session_factory

logger = logging.getLogger(__name__)

_PREVIEW_CHARS = 300

_UPSERT = text(
    """
    INSERT INTO "ChatSessions"
        ("CompanyId", "AgentId", "Channel", "SessionId", "ContactWaId", "ContactName",
         "StartedAt", "LastMessageAt", "LastPreview", "CreatedAt", "UpdatedAt")
    VALUES (:company_id, :agent_id, :channel, :session_id, :contact_wa_id, :contact_name,
            :started_at, :last_at, :preview, :now, :now)
    ON CONFLICT ("AgentId", "SessionId") DO UPDATE SET
        "LastMessageAt" = GREATEST("ChatSessions"."LastMessageAt", EXCLUDED."LastMessageAt"),
        "StartedAt" = LEAST("ChatSessions"."StartedAt", EXCLUDED."StartedAt"),
        "LastPreview" = CASE WHEN EXCLUDED."LastMessageAt" >= "ChatSessions"."LastMessageAt"
                             THEN EXCLUDED."LastPreview" ELSE "ChatSessions"."LastPreview" END,
        "ContactName" = COALESCE(EXCLUDED."ContactName", "ChatSessions"."ContactName"),
        "UpdatedAt" = EXCLUDED."UpdatedAt"
    """
)


def channel_of(session_id: str) -> str:
    return "whatsapp" if session_id.startswith("wa:") else "site"


def wa_id_of(session_id: str) -> str | None:
    """"wa:<phone>" (old) or "wa:<agent_id>:<phone>" (current) → the phone."""
    if not session_id.startswith("wa:"):
        return None
    return session_id.rsplit(":", 1)[-1] or None


def _row(session_id: str, agent_id: int, company_id: int, started: datetime, last: datetime,
         preview: str | None, contact_name: str | None) -> dict:
    return {
        "company_id": company_id,
        "agent_id": agent_id,
        "channel": channel_of(session_id),
        "session_id": session_id[:100],
        "contact_wa_id": (wa_id_of(session_id) or "")[:30] or None,
        "contact_name": (contact_name or "")[:200] or None,
        "started_at": started,
        "last_at": last,
        "preview": (preview or "").strip()[:_PREVIEW_CHARS] or None,
        "now": datetime.now(timezone.utc),
    }


async def record_exchange(
    session_id: str, agent_id: int, company_id: int, last_message: str, contact_name: str | None = None
) -> None:
    now = datetime.now(timezone.utc)
    try:
        async with get_session_factory()() as db:
            await db.execute(_UPSERT, _row(session_id, agent_id, company_id, now, now, last_message, contact_name))
            await db.commit()
    except Exception:
        # The table may not exist yet if this deployed before FojiApi's migration.
        logger.warning("Could not index chat %s for the history list", session_id, exc_info=True)


# ── One-time backfill ────────────────────────────────────────────────────────

_BACKFILL_LOCK = 734_221_901  # arbitrary pg advisory-lock id for this job


async def backfill_if_empty(table) -> None:
    """
    Index the chats already in DynamoDB (the last 90 days) the first time this
    runs against an empty ChatSessions table. Several API tasks may start at
    once: an advisory lock lets only one of them do it, and the upsert makes a
    repeat harmless anyway.
    """
    try:
        async with get_session_factory()() as db:
            got = (await db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _BACKFILL_LOCK})).scalar()
            if not got:
                return
            try:
                empty = (await db.execute(text('SELECT NOT EXISTS (SELECT 1 FROM "ChatSessions")'))).scalar()
                if not empty:
                    return
                agents = {
                    r[0]: r[1]
                    for r in (await db.execute(text('SELECT "Id", "CompanyId" FROM "Agents"'))).all()
                }
                groups = await asyncio.to_thread(_scan_sessions, table)
                rows = []
                for (session_id, agent_id), g in groups.items():
                    if agent_id not in agents:
                        continue  # agent deleted since
                    rows.append(_row(session_id, agent_id, agents[agent_id], g["first"], g["last"], g["preview"], None))
                for i in range(0, len(rows), 500):
                    await db.execute(_UPSERT, rows[i : i + 500])
                await db.commit()
                logger.info("Chat history index backfilled with %d chats", len(rows))
            finally:
                await db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _BACKFILL_LOCK})
                await db.commit()
    except Exception:
        logger.warning("Chat history backfill skipped", exc_info=True)


def _scan_sessions(table) -> dict:
    groups: dict = defaultdict(lambda: {"first": None, "last": None, "preview": None, "last_ms": -1})
    kwargs = {
        "ProjectionExpression": "session_id, #ts, agent_id, content",
        "ExpressionAttributeNames": {"#ts": "timestamp"},
        "FilterExpression": Attr("agent_id").exists(),
    }
    while True:
        resp = table.scan(**kwargs)
        for item in resp.get("Items", []):
            key = (str(item["session_id"]), int(item["agent_id"]))
            ms = int(item["timestamp"])
            at = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
            g = groups[key]
            if g["first"] is None or at < g["first"]:
                g["first"] = at
            if ms > g["last_ms"]:
                g["last_ms"], g["last"], g["preview"] = ms, at, str(item.get("content") or "")
        last = resp.get("LastEvaluatedKey")
        if not last:
            return groups
        kwargs["ExclusiveStartKey"] = last
