"""
Internal API — called only by foji-worker (Lambda functions).

Authenticated via X-Internal-Key header (shared secret, not user JWT).
These endpoints are NOT exposed publicly and should be behind a VPC/security group
in production. The internal_api_key provides a software-layer fallback.

Current endpoints:
  POST /internal/whatsapp/chat  — synchronous chat for WhatsApp relay
"""

import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.services.agent_service import AgentService
from app.services.chat_history import ChatHistoryService
from app.services.file_context import FileContextService
from app.services.prompt_builder import PromptBuilder
from app.services.rate_limit_service import (
    RateLimitExceededException,
    RateLimitService,
    SubscriptionInactiveException,
)
from app.providers.router import ProviderRouter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", tags=["Internal"])


# ── Auth ─────────────────────────────────────────────────────────────────────

def verify_internal_key(x_internal_key: str | None = Header(default=None)) -> None:
    """Validates the shared internal API key."""
    expected = get_settings().internal_api_key
    if not x_internal_key or not expected or not secrets.compare_digest(x_internal_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal key")


# ── Schema ────────────────────────────────────────────────────────────────────

class WhatsAppChatRequest(BaseModel):
    agent_token: str
    session_id: str  # typically "wa:<phone_number>" — namespaced by caller
    message: str
    sender_phone: str | None = None  # the wa_id, used to create the CRM contact
    profile_name: str | None = None  # the sender's WhatsApp display name


class WhatsAppChatResponse(BaseModel):
    reply: str
    session_id: str


async def _capture_whatsapp_lead(
    agent_id: int, session_id: str, phone: str, profile_name: str | None
) -> None:
    """
    Register a WhatsApp sender as a lead in FojiApi, which dedupes it into a
    Contact by normalized phone. Never raises — the chat reply matters more.
    """
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{settings.foji_api_base_url}/api/leads/internal",
                json={
                    "agentId": agent_id,
                    "sessionId": session_id,
                    "name": profile_name or None,
                    "email": None,
                    "phone": phone,
                    "source": "whatsapp",
                },
                headers={"X-Internal-Key": settings.foji_api_internal_key},
            )
        if not resp.is_success:
            logger.warning(
                "WhatsApp lead capture returned %d for agent %d", resp.status_code, agent_id
            )
    except Exception as exc:
        logger.warning("WhatsApp lead capture failed for agent %d: %s", agent_id, exc)


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post(
    "/whatsapp/chat",
    response_model=WhatsAppChatResponse,
    dependencies=[Depends(verify_internal_key)],
    summary="Synchronous chat for WhatsApp relay",
    description=(
        "Called by foji-worker's WhatsApp Lambda handler. "
        "Returns the full assistant reply as a plain string (no streaming). "
        "History is loaded from DynamoDB and saved back after the response."
    ),
)
async def whatsapp_chat(
    body: WhatsAppChatRequest,
    db: AsyncSession = Depends(get_db),
) -> WhatsAppChatResponse:
    # 1. Load agent
    agent_svc = AgentService(db)
    agent = await agent_svc.get_by_token(body.agent_token)

    # 2. Load chat history (also tells us whether this is a new conversation)
    history_svc = ChatHistoryService()
    history = await history_svc.load(body.session_id)

    # 2b. Enforce subscription + monthly limits on WhatsApp too, before any
    # expensive work. This path used to skip RateLimitService entirely, so
    # WhatsApp traffic was unmetered and a company that downgraded off a WhatsApp
    # plan kept the channel working (Agent.WhatsAppEnabled stays set on downgrade
    # and nothing re-checked the plan).
    rate_limit_svc = RateLimitService()
    try:
        plan = await rate_limit_svc.require_serving_plan(db, agent.company_id)
        if not plan.has_whats_app:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="The current plan does not include WhatsApp.",
            )
        await rate_limit_svc.check(db, agent.company_id, is_new_session=not history)
    except SubscriptionInactiveException as exc:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc))
    except RateLimitExceededException as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))

    # 2c. First message of a conversation → capture it as a lead so the sender
    # becomes a deduped CRM contact and the conversation shows up on their
    # timeline. Best-effort: a CRM hiccup must never cost the user a reply.
    if not history and body.sender_phone:
        await _capture_whatsapp_lead(agent.id, body.session_id, body.sender_phone, body.profile_name)

    # 3. Build file context
    file_ctx_svc = FileContextService()
    file_context = await file_ctx_svc.build(agent)

    # 4. Build prompt
    system_prompt, messages = PromptBuilder().build(
        agent=agent,
        user_message=body.message,
        history=history,
        file_context=file_context,
    )

    # 5. Select provider and collect full response (no streaming for WA)
    provider = await ProviderRouter().select(db)
    logger.info(
        "WhatsApp chat: session=%s agent_id=%d provider=%s",
        body.session_id,
        agent.id,
        provider.provider_name,
    )

    chunks: list[str] = []
    async for chunk in provider.stream_chat(messages, system_prompt):
        chunks.append(chunk)
    reply = "".join(chunks).strip()

    if not reply:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Provider returned an empty response",
        )

    # 6. Save history (best-effort — never fail the request)
    try:
        await history_svc.save(
            session_id=body.session_id,
            user_message=body.message,
            assistant_message=reply,
            provider=provider.provider_name,
            agent_id=agent.id,
            company_id=agent.company_id,
        )
    except Exception:
        logger.warning("Failed to save WhatsApp chat history for session=%s", body.session_id)

    return WhatsAppChatResponse(reply=reply, session_id=body.session_id)


# ── CRM email drafting ─────────────────────────────────────────────────────────

class DraftEmailRequest(BaseModel):
    agent_token: str
    contact_name: str | None = None
    goal: str
    tone: str | None = None


class DraftEmailResponse(BaseModel):
    subject: str
    body: str


def _split_subject(text: str) -> tuple[str, str]:
    """Parse a 'Subject: ...' first line from the model output; return (subject, body)."""
    stripped = text.strip()
    lines = stripped.split("\n", 1)
    first = lines[0].strip()
    if first.lower().startswith("subject:"):
        subject = first[len("subject:"):].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        return subject or "Proposal", body or stripped
    return "Proposal", stripped


@router.post(
    "/crm/draft-email",
    response_model=DraftEmailResponse,
    dependencies=[Depends(verify_internal_key)],
    summary="Draft a CRM proposal / follow-up email",
    description="Generates a subject + body for a sales email. Called by FojiApi on behalf of the dashboard.",
)
async def draft_email(
    body: DraftEmailRequest,
    db: AsyncSession = Depends(get_db),
) -> DraftEmailResponse:
    # Validate the agent token (scopes the request to a real company/agent).
    agent_svc = AgentService(db)
    await agent_svc.get_by_token(body.agent_token)

    provider = await ProviderRouter().select(db)

    system_prompt = (
        "You write concise, warm, professional sales and follow-up emails for a business. "
        "Output ONLY the email, nothing else. The first line MUST be exactly 'Subject: <subject line>'. "
        "Then one blank line, then the email body in plain text (no markdown, no placeholders like [Name]). "
        "Keep it under 180 words. Use a generic sign-off; do not invent a sender name or company. "
        "Write in the same language as the goal described by the user."
    )
    user = f"Recipient name: {body.contact_name or 'the customer'}\nGoal of the email: {body.goal}"
    if body.tone:
        user += f"\nDesired tone: {body.tone}"

    chunks: list[str] = []
    async for chunk in provider.stream_chat([{"role": "user", "content": user}], system_prompt):
        chunks.append(chunk)
    text = "".join(chunks).strip()

    if not text:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Provider returned an empty draft",
        )

    subject, email_body = _split_subject(text)
    return DraftEmailResponse(subject=subject, body=email_body)


class PurgeChatHistoryRequest(BaseModel):
    company_id: int


@router.post(
    "/chat-history/purge",
    dependencies=[Depends(verify_internal_key)],
    summary="Delete all chat history for a company",
    description=(
        "Called by FojiApi when a company is deleted, so its WhatsApp and widget "
        "conversation history is erased immediately instead of waiting out the "
        "90-day TTL. Returns how many messages were deleted."
    ),
)
async def purge_chat_history(body: PurgeChatHistoryRequest) -> dict:
    deleted = await ChatHistoryService().purge_company(body.company_id)
    return {"deleted": deleted}
