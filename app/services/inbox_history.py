"""
Conversation history for hybrid mode, read from FojiApi's shared-inbox thread.

The inbox thread is the one place that has everything said in the conversation:
the customer, the AI and any person from the team who stepped in. When a person
hands a conversation back, the AI has to know what they said — its own DynamoDB
log only ever saw its own replies.
"""

import time
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.whatsapp_message import WhatsAppMessage
from app.services.chat_history import Conversation, _split_current_conversation

# What a media message looks like in the history when it had no caption.
_MEDIA_PLACEHOLDERS = {
    "image": "[imagem]",
    "audio": "[áudio]",
    "document": "[documento]",
    "video": "[vídeo]",
    "sticker": "[figurinha]",
}


def _epoch_ms(dt: datetime) -> int:
    if dt.tzinfo is None:  # stored as UTC; be safe if the driver hands back naive
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _content(row: WhatsAppMessage) -> str:
    body = (row.body or "").strip()
    if body:
        return body
    return _MEDIA_PLACEHOLDERS.get(row.message_type or "", f"[{row.message_type or 'mensagem'}]")


async def load_inbox_conversation(
    db: AsyncSession,
    conversation_id: int,
    exclude_wam_id: str | None,
    idle_timeout_seconds: int | None,
    max_messages: int,
) -> Conversation:
    """
    The conversation in progress on an inbox thread, oldest first.

    `exclude_wam_id` is the message being answered right now: the worker records
    it in the inbox before calling us, and it's passed separately as the user
    message, so it mustn't appear in the history too.

    Outbound messages written by a person carry their "Nome:\\n\\n" prefix, which is
    how the model tells a teammate's words from its own.
    """
    stmt = select(WhatsAppMessage).where(WhatsAppMessage.conversation_id == conversation_id)
    if exclude_wam_id:
        stmt = stmt.where(
            or_(WhatsAppMessage.wam_id.is_(None), WhatsAppMessage.wam_id != exclude_wam_id)
        )
    stmt = stmt.order_by(WhatsAppMessage.created_at.desc(), WhatsAppMessage.id.desc()).limit(
        max_messages + 1
    )
    rows = (await db.execute(stmt)).scalars().all()

    items = [
        {
            "timestamp": _epoch_ms(r.created_at),
            "role": "user" if r.direction == "Inbound" else "assistant",
            "content": _content(r),
        }
        for r in rows
    ]
    return _split_current_conversation(
        items,
        now_ms=int(time.time() * 1000),
        idle_timeout_ms=idle_timeout_seconds * 1000 if idle_timeout_seconds else None,
        max_messages=max_messages,
    )
