"""
POST /api/v1/chat — SSE streaming chat.

Flow:
  1. Validate agent token
  2. Resolve/create session_id
  3. Load chat history (DynamoDB)
  4. Build file context
  5. Build prompt payload
  6. DB-first provider selection (ModelSelectorService → random AIModel row)
  7. Stream chunks as SSE
  8. Persist history (best-effort)
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

STREAM_TIMEOUT_SECONDS = 300  # 5 minutes max per chat stream

from app.core.database import get_db
from app.core.exceptions import AgentInactiveException, AgentNotFoundException, ProviderException
from app.providers.router import ProviderRouter
from app.services.agent_service import AgentService
from app.services.chat_history import ChatHistoryService
from app.services.file_context import FileContextService
from app.services.prompt_builder import PromptBuilder
from app.services.rate_limit_service import RateLimitExceededException, RateLimitService

router = APIRouter()
logger = logging.getLogger(__name__)

_provider_router = ProviderRouter()
_file_context_svc = FileContextService()
_prompt_builder = PromptBuilder()
_history_svc = ChatHistoryService()
_rate_limit_svc = RateLimitService()


class ChatRequest(BaseModel):
    agent_token: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=8000)
    session_id: str | None = Field(default=None)


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
    is_new_session = req.session_id is None
    session_id = req.session_id or _history_svc.new_session_id()

    # 3. Check monthly rate limits (soft-enforce via DailyStats, up to 24h lag)
    try:
        await _rate_limit_svc.check(db, agent.company_id, is_new_session)
    except RateLimitExceededException as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        )

    # 4. Load history
    history = await _history_svc.load(session_id)

    # 5. File context
    file_context = await _file_context_svc.build(agent)

    # 6. Prompt
    system_prompt, messages = _prompt_builder.build(agent, req.message, history, file_context)

    # 7. DB-first provider selection — passes db so ModelSelectorService can query AIModels
    provider = await _provider_router.select(db)

    return EventSourceResponse(
        _stream(provider, system_prompt, messages, session_id, req.message, agent.id, agent.company_id),
        media_type="text/event-stream",
    )


async def _stream(
    provider,
    system_prompt: str,
    messages: list[dict],
    session_id: str,
    user_message: str,
    agent_id: int,
    company_id: int,
) -> AsyncIterator[str]:
    collected: list[str] = []
    try:
        async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
            async for chunk in provider.stream_chat(messages, system_prompt):
                collected.append(chunk)
                yield json.dumps({"chunk": chunk})

        full_response = "".join(collected)

        # 8. Persist history — best-effort, never breaks the response
        try:
            await _history_svc.save(
                session_id, user_message, full_response,
                provider.provider_name, agent_id, company_id
            )
        except Exception:
            logger.exception("Failed to persist chat history — continuing")

        yield json.dumps({"done": True, "session_id": session_id})

    except TimeoutError:
        logger.warning("Stream timed out after %ds for session=%s", STREAM_TIMEOUT_SECONDS, session_id)
        yield json.dumps({"error": "Response timed out. Please try again.", "done": True})
    except ProviderException as exc:
        logger.error("Provider error during stream: %s", exc)
        yield json.dumps({"error": "AI provider error. Please try again.", "done": True})
    except Exception:
        logger.exception("Unexpected error during stream")
        yield json.dumps({"error": "An unexpected error occurred.", "done": True})
