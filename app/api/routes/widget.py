"""
Widget-facing endpoints — public, auth via agent token only.

GET  /api/v1/widget/agent-info   Agent name, description, language
GET  /api/v1/widget/files        List of ready files for the agent
POST /api/v1/widget/lead         Capture a lead (name/email/phone) from the widget
POST /api/v1/widget/handoff      Request human handoff — notifies the company team
"""

import logging

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.exceptions import AgentInactiveException, AgentNotFoundException
from app.services.agent_service import AgentService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/widget")


async def _get_agent_token(x_agent_token: str = Header(...)) -> str:
    if not x_agent_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-Agent-Token header is required.")
    return x_agent_token


@router.get("/agent-info")
async def get_agent_info(
    agent_token: str = Depends(_get_agent_token),
    db: AsyncSession = Depends(get_db),
):
    svc = AgentService(db)
    try:
        return await svc.get_widget_info(agent_token)
    except AgentNotFoundException:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    except AgentInactiveException:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent is inactive.")


@router.get("/files")
async def get_agent_files(
    agent_token: str = Depends(_get_agent_token),
    db: AsyncSession = Depends(get_db),
):
    svc = AgentService(db)
    try:
        agent = await svc.get_by_token(agent_token)
    except AgentNotFoundException:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    except AgentInactiveException:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent is inactive.")

    ready_files = [
        {
            "id": f.id,
            "file_name": f.file_name,
            "file_size_bytes": f.file_size_bytes,
            "processing_status": f.processing_status,
        }
        for f in agent.files
        if f.processing_status == "Ready"
    ]
    return {"files": ready_files, "total": len(ready_files)}


class LeadRequest(BaseModel):
    session_id: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    source: str = "widget"


@router.post("/lead", status_code=status.HTTP_201_CREATED)
async def capture_lead(
    body: LeadRequest,
    agent_token: str = Depends(_get_agent_token),
    db: AsyncSession = Depends(get_db),
):
    svc = AgentService(db)
    try:
        agent = await svc.get_by_token(agent_token)
    except AgentNotFoundException:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    except AgentInactiveException:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent is inactive.")

    if not agent.lead_capture_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Lead capture is not enabled for this agent.")

    if not any([body.name, body.email, body.phone]):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one of name, email, or phone is required.")

    # Write the lead + dedup/upsert the CRM contact via FojiApi (sole owner of the Postgres write).
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{settings.foji_api_base_url}/api/leads/internal",
                json={
                    "agentId": agent.id,
                    "sessionId": body.session_id,
                    "name": body.name or None,
                    "email": body.email or None,
                    "phone": body.phone or None,
                    "source": body.source,
                },
                headers={"X-Internal-Key": settings.foji_api_internal_key},
            )
    except Exception as exc:
        logger.warning("Failed to capture lead via FojiApi for agent %d: %s", agent.id, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Lead capture is temporarily unavailable.")

    if not resp.is_success:
        logger.warning("FojiApi lead capture returned %d for agent %d", resp.status_code, agent.id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Lead capture failed.")

    data = resp.json()
    return {"id": data.get("id"), "session_id": data.get("session_id", body.session_id)}


class HandoffRequest(BaseModel):
    session_id: str
    user_message: str | None = None
    source: str = "widget"


@router.post("/handoff", status_code=status.HTTP_201_CREATED)
async def request_handoff(
    body: HandoffRequest,
    agent_token: str = Depends(_get_agent_token),
    db: AsyncSession = Depends(get_db),
):
    svc = AgentService(db)
    try:
        agent = await svc.get_by_token(agent_token)
    except AgentNotFoundException:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    except AgentInactiveException:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent is inactive.")

    if not agent.handoff_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Human handoff is not enabled for this agent.")

    # Notify FojiApi internal endpoint — triggers email notification and creates the HandoffEvent record
    settings = get_settings()
    handoff_id = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{settings.foji_api_base_url}/api/handoffs/internal",
                json={
                    "agentId": agent.id,
                    "sessionId": body.session_id,
                    "userMessage": body.user_message,
                    "source": body.source,
                },
                headers={"X-Internal-Key": settings.foji_api_internal_key},
            )
            if resp.is_success:
                handoff_id = resp.json().get("id")
    except Exception as exc:
        logger.warning("Failed to notify FojiApi of handoff for agent %d: %s", agent.id, exc)

    return {
        "session_id": body.session_id,
        "handoff_id": handoff_id,
        "handoff_message": agent.handoff_message,
    }
