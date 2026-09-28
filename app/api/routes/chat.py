"""
POST /api/v1/chat — SSE streaming chat.

Flow:
  1. Validate agent token
  2. Resolve/create session_id
  3. Load chat history (DynamoDB)
  4. Build file context
  5a. Fetch Google Calendar slots (if connected)
  5b. Build prompt payload (with calendar context if available)
  6. DB-first provider selection (ModelSelectorService → random AIModel row)
  7. Stream chunks as SSE
  8. Parse FOJI_SCHEDULE_SUGGESTION from AI response (if present)
  9. Persist history (best-effort)
  10. Emit calendar_suggestion SSE event (if suggestion found)
"""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

STREAM_TIMEOUT_SECONDS = 300  # 5 minutes max per chat stream

from app.core.config import get_settings
from app.core.database import get_db
from app.core.exceptions import AgentInactiveException, AgentNotFoundException, ProviderException
from app.providers.router import ProviderRouter
from app.services.agent_service import AgentService
from app.services.chat_history import ChatHistoryService
from app.services.file_context import FileContextService
from app.services.google_calendar_service import GoogleCalendarService
from app.services.photo import NO_CAPTION_MESSAGE, InvalidPhoto, decode_photo
from app.services.photo import history_text as photo_history_text
from app.services.prompt_builder import PromptBuilder
from app.services.reply_filter import StreamingReplyFilter
from app.services.rate_limit_service import (
    RateLimitExceededException,
    RateLimitService,
    SubscriptionInactiveException,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_provider_router = ProviderRouter()
_file_context_svc = FileContextService()
_prompt_builder = PromptBuilder(get_settings().business_timezone)
_history_svc = ChatHistoryService()
_rate_limit_svc = RateLimitService()
_calendar_svc = GoogleCalendarService()

# Regex to find and extract the FOJI_SCHEDULE_SUGGESTION JSON block at the end of AI output.
# Matches an optional markdown code fence or bare JSON object.
_SUGGESTION_RE = re.compile(
    r'\n*```json\s*(\{"FOJI_SCHEDULE_SUGGESTION":.+?\})\s*```\s*$'
    r'|'
    r'\n*(\{"FOJI_SCHEDULE_SUGGESTION":.+?\})\s*$',
    re.DOTALL,
)


class ChatRequest(BaseModel):
    agent_token: str = Field(..., min_length=1)
    # May be empty when the visitor sends just a photo.
    message: str = Field(default="", max_length=8000)
    session_id: str | None = Field(default=None)
    # A photo the visitor attached (the widget downsizes it first). ~7 MB of
    # base64 is a hard ceiling on the request; the decoded image is capped at
    # photo_max_bytes.
    image_base64: str | None = Field(default=None, max_length=7_000_000)
    image_mime: str | None = Field(default=None, max_length=50)


@router.post("/chat")
async def chat(req: ChatRequest, db: AsyncSession = Depends(get_db)):
    # 1. Validate agent
    agent_svc = AgentService(db)
    try:
        agent = await agent_svc.get_by_token(req.agent_token)
    except AgentNotFoundException:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    except AgentInactiveException:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent is inactive.")

    # 2. Resolve or create session
    session_id = req.session_id or _history_svc.new_session_id()

    # 3. Load history first — it is what tells us whether this is really a new
    # conversation. Deriving that from `req.session_id is None` trusted the
    # client: the widget is public and the agent token sits in the page, so
    # sending a fresh random session_id on every request made is_new_session
    # permanently False and skipped the conversation cap entirely.
    # A tab left open for hours keeps its session_id, so the conversation ends
    # silently after half an hour idle and the next message starts a new one.
    conversation = await _history_svc.load_conversation(
        session_id,
        idle_timeout_seconds=get_settings().conversation_idle_timeout_widget_seconds,
    )
    history = conversation.messages
    is_new_session = conversation.is_new

    # 4. Check monthly rate limits (soft-enforce via DailyStats, up to 24h lag)
    try:
        plan = await _rate_limit_svc.check(db, agent.company_id, is_new_session)
    except RateLimitExceededException as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        )
    except SubscriptionInactiveException as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=str(exc),
        )

    # 5. File context
    file_context = await _file_context_svc.build(agent)

    # 5a. Calendar slots — pre-fetch if agent has an active calendar connection
    # Gated on the plan as well as the connection: a downgrade (or cancellation)
    # never clears AgentCalendarConnection.IsActive, so checking only the
    # connection kept scheduling alive on plans that don't include it.
    available_slots: list[dict] | None = None
    if plan.has_google_calendar and agent.calendar_connection and agent.calendar_connection.is_active:
        try:
            access_token = await _calendar_svc.get_access_token(
                agent.id, agent.calendar_connection.encrypted_refresh_token
            )
            available_slots = await _calendar_svc.get_available_slots(access_token)
        except Exception:
            logger.warning(
                "Calendar slot fetch failed for agent %d — continuing without calendar context",
                agent.id,
            )

    # 5b. Prompt (with calendar slots if available)
    # A photo: the model sees it; history keeps "[imagem] caption".
    try:
        photo, photo_mime = decode_photo(req.image_base64, req.image_mime, get_settings().photo_max_bytes)
    except InvalidPhoto:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid image.")
    user_message = req.message.strip()
    history_message = photo_history_text(user_message) if photo is not None else user_message
    if photo is not None and not user_message:
        user_message = NO_CAPTION_MESSAGE
    if not user_message:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Empty message.")

    system_prompt, messages = _prompt_builder.build(
        agent, user_message, history, file_context, available_slots, has_photo=photo is not None
    )
    if photo is not None:
        messages[-1]["images"] = [{"data": photo, "mime": photo_mime}]

    # 6. DB-first provider selection — all active models, shuffled for failover
    providers = await _provider_router.select_all(db)

    return EventSourceResponse(
        _stream(
            providers, system_prompt, messages, session_id, history_message,
            agent.id, agent.company_id, conversation_start=conversation.is_new,
        ),
        media_type="text/event-stream",
        ping=0,  # Disable sse_starlette's internal ping (we handle our own events)
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _extract_calendar_suggestion(full_response: str) -> tuple[str, dict | None]:
    """
    Finds and removes the FOJI_SCHEDULE_SUGGESTION JSON block from the end of the AI response.
    Returns (clean_text, suggestion_dict | None).
    """
    match = _SUGGESTION_RE.search(full_response)
    if not match:
        return full_response, None

    json_str = match.group(1) or match.group(2)
    try:
        parsed = json.loads(json_str)
        suggestion = parsed.get("FOJI_SCHEDULE_SUGGESTION")
        if not isinstance(suggestion, dict):
            return full_response, None
        clean_text = full_response[: match.start()].rstrip()
        return clean_text, suggestion
    except (json.JSONDecodeError, Exception):
        return full_response, None


async def _stream(
    providers: list,
    system_prompt: str,
    messages: list[dict],
    session_id: str,
    user_message: str,
    agent_id: int,
    company_id: int,
    conversation_start: bool = False,
) -> AsyncIterator[str]:
    last_error: Exception | None = None
    # True while the widget is showing text from a provider that then failed.
    partial_on_screen = False

    for i, provider in enumerate(providers):
        collected: list[str] = []
        # Holds back only the sentence in progress, so an "according to my
        # documents" phrase is removed before the widget ever renders it.
        reply_filter = StreamingReplyFilter()
        try:
            logger.debug(
                "Trying provider %d/%d: %s", i + 1, len(providers), provider.provider_name
            )
            # The next provider starts its answer from scratch. Without this the
            # widget appended it to the half-answer already shown, so the
            # customer read the start of the reply twice.
            if partial_on_screen:
                yield json.dumps({"reset": True})
                partial_on_screen = False

            async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
                async for chunk in provider.stream_chat(messages, system_prompt):
                    clean_chunk = reply_filter.feed(chunk)
                    if clean_chunk:
                        collected.append(clean_chunk)
                        partial_on_screen = True
                        yield json.dumps({"chunk": clean_chunk})

            tail = reply_filter.flush()
            if tail:
                collected.append(tail)
                yield json.dumps({"chunk": tail})

            full_response = "".join(collected)

            # 8. Extract calendar suggestion before saving to history
            clean_response, calendar_suggestion = _extract_calendar_suggestion(full_response)

            # If the AI included a suggestion block, stream the corrected text chunk
            # (the widget already received chunks including the raw JSON — we tell it to
            # replace the last bubble via a "replace" event so the JSON is never shown)
            if calendar_suggestion:
                yield json.dumps({"replace_last": clean_response})

            # 9. Persist history — best-effort, never breaks the response
            try:
                await _history_svc.save(
                    session_id, user_message, clean_response,
                    provider.provider_name, agent_id, company_id,
                    conversation_start=conversation_start,
                )
            except Exception:
                logger.exception("Failed to persist chat history — continuing")

            yield json.dumps({"done": True, "session_id": session_id})

            # 10. Emit calendar suggestion after done so widget renders text first
            if calendar_suggestion:
                yield json.dumps({"calendar_suggestion": calendar_suggestion})

            return  # success — stop trying providers

        except TimeoutError:
            logger.warning(
                "Provider %s timed out after %ds — %s",
                provider.provider_name, STREAM_TIMEOUT_SECONDS,
                "trying next provider" if i < len(providers) - 1 else "no more providers",
            )
            last_error = TimeoutError(f"{provider.provider_name} timed out")
        except ProviderException as exc:
            logger.warning(
                "Provider %s failed: %s — %s",
                provider.provider_name, exc,
                "trying next provider" if i < len(providers) - 1 else "no more providers",
            )
            last_error = exc
        except Exception as exc:
            logger.exception(
                "Unexpected error with provider %s — %s",
                provider.provider_name,
                "trying next provider" if i < len(providers) - 1 else "no more providers",
            )
            last_error = exc

    # All providers failed. Clear any half-answer, and send a neutral code rather
    # than the provider's error text — that reached the browser, and the widget
    # shows its own friendly, localised message anyway.
    if partial_on_screen:
        yield json.dumps({"reset": True})
    if isinstance(last_error, TimeoutError):
        yield json.dumps({"error": "timeout", "done": True})
    else:
        logger.error("All %d provider(s) failed. Last error: %s", len(providers), last_error)
        yield json.dumps({"error": "unavailable", "done": True})
