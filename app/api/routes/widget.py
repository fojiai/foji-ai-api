"""
Widget-facing endpoints — public, auth via agent token only.

GET /api/v1/widget/agent-info   Agent name, description, language
GET /api/v1/widget/files        List of ready files for the agent
"""

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AgentInactiveException, AgentNotFoundException
from app.services.agent_service import AgentService

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
