import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AgentInactiveException, AgentNotFoundException
from app.models.agent import Agent


class AgentService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_token(self, agent_token: str) -> Agent:
        """Load agent by its public token, eagerly loading company and files."""
        result = await self._db.execute(
            select(Agent)
            .where(Agent.agent_token == agent_token)
            .options(
                selectinload(Agent.company),
                selectinload(Agent.files),
            )
        )
        agent = result.scalar_one_or_none()

        if agent is None:
            raise AgentNotFoundException(f"No agent found for token: {agent_token}")

        if not agent.is_active:
            raise AgentInactiveException(f"Agent '{agent.name}' is currently inactive.")

        return agent

    async def get_widget_info(self, agent_token: str) -> dict:
        """Return only public-safe fields for the widget to display."""
        agent = await self.get_by_token(agent_token)

        # Parse conversation starters from JSON string to list
        starters = None
        if agent.conversation_starters:
            try:
                starters = json.loads(agent.conversation_starters)
            except (json.JSONDecodeError, TypeError):
                starters = None

        return {
            "name": agent.name,
            "description": agent.description,
            "industry_type": agent.industry_type,
            "agent_language": agent.agent_language,
            "whats_app_enabled": agent.whats_app_enabled,
            "welcome_message": agent.welcome_message,
            "conversation_starters": starters,
            "widget_primary_color": agent.widget_primary_color,
            "widget_title": agent.widget_title,
            "widget_placeholder": agent.widget_placeholder,
            "widget_position": agent.widget_position,
        }
