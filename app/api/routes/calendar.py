"""
POST /api/v1/calendar/book — Book an appointment on the agent's Google Calendar.

Called by the widget after the user selects a slot and fills in their details.
Auth: X-Agent-Token (same as /chat). No Foji DB writes — event lives in Google Calendar only.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AgentInactiveException, AgentNotFoundException
from app.services.agent_service import AgentService
from app.services.google_calendar_service import GoogleCalendarService

router = APIRouter()
logger = logging.getLogger(__name__)

_calendar_svc = GoogleCalendarService()


class BookingRequest(BaseModel):
    agent_token: str = Field(..., min_length=1)
    attendee_name: str = Field(..., min_length=1, max_length=200)
    attendee_email: str = Field(..., min_length=3, max_length=254)
    slot_start: str = Field(..., description="ISO 8601 UTC datetime")
    slot_end: str = Field(..., description="ISO 8601 UTC datetime")
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("attendee_email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email address")
        return v.lower().strip()


class BookingResponse(BaseModel):
    event_id: str
    html_link: str
    message: str


@router.post("/calendar/book", response_model=BookingResponse)
async def book_appointment(
    req: BookingRequest,
    db: AsyncSession = Depends(get_db),
) -> BookingResponse:
    # 1. Load and validate agent
    agent_svc = AgentService(db)
    try:
        agent = await agent_svc.get_by_token(req.agent_token)
    except AgentNotFoundException:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    except AgentInactiveException:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent is inactive.")

    # 2. Verify calendar is connected
    if not agent.calendar_connection or not agent.calendar_connection.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This agent does not have Google Calendar connected.",
        )

    # 3. Get a valid access token
    try:
        access_token = await _calendar_svc.get_access_token(
            agent.id, agent.calendar_connection.encrypted_refresh_token
        )
    except Exception as exc:
        logger.error("Failed to get calendar access token for agent %d: %s", agent.id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not authenticate with Google Calendar. Please try again later.",
        )

    # 4. Double-booking guard — re-check FreeBusy for this exact window
    try:
        is_free = await _calendar_svc.is_slot_free(access_token, req.slot_start, req.slot_end)
    except Exception as exc:
        logger.warning("FreeBusy check failed for agent %d: %s — proceeding", agent.id, exc)
        is_free = True  # fail open to avoid blocking valid bookings on transient errors

    if not is_free:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That time slot is no longer available. Please choose another time.",
        )

    # 5. Create the event (Google sends invite email automatically with sendUpdates=all)
    try:
        event = await _calendar_svc.create_event(
            access_token=access_token,
            summary=f"Appointment with {req.attendee_name}",
            start_iso=req.slot_start,
            end_iso=req.slot_end,
            attendee_email=req.attendee_email,
            attendee_name=req.attendee_name,
            description=req.notes or "",
        )
    except Exception as exc:
        logger.error("Failed to create calendar event for agent %d: %s", agent.id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to create the appointment. Please try again.",
        )

    logger.info(
        "Appointment booked: agent_id=%d attendee=%s slot_start=%s event_id=%s",
        agent.id, req.attendee_email, req.slot_start, event["id"],
    )

    return BookingResponse(
        event_id=event["id"],
        html_link=event["htmlLink"],
        message="Appointment booked! You will receive a Google Calendar invite at your email.",
    )
