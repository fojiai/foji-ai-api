from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WhatsAppMessage(Base):
    """
    Read-only view of FojiApi's shared-inbox messages (FojiApi owns the schema).

    In hybrid mode the inbox thread is the conversation — it holds the customer's
    messages, the AI's replies and anything the team wrote — so the AI reads its
    history from here rather than from its own DynamoDB log, which never sees
    what a person said.

    Only columns that predate hybrid mode are mapped, so this can't break if the
    AI API deploys before FojiApi's migration runs.
    """

    __tablename__ = "WhatsAppMessages"

    id: Mapped[int] = mapped_column("Id", Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column("ConversationId", Integer)
    direction: Mapped[str] = mapped_column("Direction", String(20))  # "Inbound" | "Outbound"
    body: Mapped[str] = mapped_column("Body", String(4096))
    message_type: Mapped[str] = mapped_column("MessageType", String(20))
    wam_id: Mapped[str | None] = mapped_column("WamId", String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime(timezone=True))
