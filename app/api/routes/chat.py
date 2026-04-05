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

    # 7. DB-first provider selection — all active models, shuffled for failover
    providers = await _provider_router.select_all(db)

    return EventSourceResponse(
        _stream(providers, system_prompt, messages, session_id, req.message, agent.id, agent.company_id),
        media_type="text/event-stream",
        ping=0,  # Disable sse_starlette's internal ping (we handle our own events)
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _stream(
    providers: list,
    system_prompt: str,
    messages: list[dict],
    session_id: str,
    user_message: str,
    agent_id: int,
    company_id: int,
) -> AsyncIterator[str]:
    last_error: Exception | None = None

    for i, provider in enumerate(providers):
        collected: list[str] = []
        try:
            logger.debug(
                "Trying provider %d/%d: %s", i + 1, len(providers), provider.provider_name
            )
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

    # All providers failed
    if isinstance(last_error, TimeoutError):
        yield json.dumps({"error": "Response timed out. Please try again.", "done": True})
    else:
        logger.error("All %d provider(s) failed. Last error: %s", len(providers), last_error)
        yield json.dumps({"error": f"AI provider error: {last_error}", "done": True})
