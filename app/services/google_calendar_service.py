"""
Google Calendar service — reads availability and creates events.
Uses the business owner's OAuth refresh token (stored encrypted in AgentCalendarConnections).
Access tokens are cached in-process with a TTL of expires_in - 60 seconds.
This service never writes to the Foji PostgreSQL database.
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.core.encryption import decrypt_refresh_token

logger = logging.getLogger(__name__)

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
_BUSINESS_HOURS_START = 9   # 09:00 local
_BUSINESS_HOURS_END = 18    # 18:00 local
_MAX_SLOTS = 20

# Module-level access token cache: agent_id → (access_token, expires_at_epoch)
_token_cache: dict[int, tuple[str, float]] = {}


class GoogleCalendarService:
    """Manages Google Calendar API calls on behalf of an agent's connected calendar."""

    async def get_access_token(self, agent_id: int, encrypted_refresh_token: str) -> str:
        """Return a valid access token, refreshing via Google if the cached one has expired."""
        cached = _token_cache.get(agent_id)
        if cached and time.time() < cached[1]:
            return cached[0]

        refresh_token = decrypt_refresh_token(encrypted_refresh_token)
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                _GOOGLE_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )

        if not resp.is_success:
            raise RuntimeError(f"Google token refresh failed: {resp.text}")

        data = resp.json()
        access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))
        _token_cache[agent_id] = (access_token, time.time() + expires_in - 60)
        return access_token

    def invalidate_token_cache(self, agent_id: int) -> None:
        _token_cache.pop(agent_id, None)

    async def get_available_slots(
        self,
        access_token: str,
        days_ahead: int = 7,
        slot_duration_minutes: int = 30,
    ) -> list[dict]:
        """
        Returns up to _MAX_SLOTS free time slots in the next `days_ahead` days,
        filtered to business hours (09:00–18:00) in the calendar's configured timezone.
        """
        now_utc = datetime.now(timezone.utc)
        time_min = now_utc.isoformat()
        time_max = (now_utc + timedelta(days=days_ahead)).isoformat()

        # Fetch calendar timezone
        cal_timezone = await self._get_calendar_timezone(access_token)

        # Fetch busy intervals
        busy_intervals = await self._get_busy_intervals(access_token, time_min, time_max)

        # Generate candidate slots and filter out busy ones
        slots = self._generate_free_slots(
            busy_intervals, now_utc, days_ahead, slot_duration_minutes, cal_timezone
        )
        return slots[:_MAX_SLOTS]

    async def create_event(
        self,
        access_token: str,
        summary: str,
        start_iso: str,
        end_iso: str,
        attendee_email: str,
        attendee_name: str,
        description: str = "",
        calendar_id: str = "primary",
    ) -> dict:
        """Create a calendar event with an attendee. Google automatically sends the invite email."""
        event_body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_iso},
            "end": {"dateTime": end_iso},
            "attendees": [{"email": attendee_email, "displayName": attendee_name}],
            "reminders": {"useDefault": True},
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_CALENDAR_BASE}/calendars/{calendar_id}/events",
                json=event_body,
                params={"sendUpdates": "all"},
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if not resp.is_success:
            raise RuntimeError(f"Failed to create Google Calendar event: {resp.text}")

        data = resp.json()
        return {"id": data["id"], "htmlLink": data.get("htmlLink", "")}

    async def is_slot_free(
        self,
        access_token: str,
        start_iso: str,
        end_iso: str,
        calendar_id: str = "primary",
    ) -> bool:
        """Check whether a specific time window is free on the calendar."""
        busy = await self._get_busy_intervals(access_token, start_iso, end_iso, calendar_id)
        return len(busy) == 0

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _get_calendar_timezone(self, access_token: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{_CALENDAR_BASE}/calendars/primary",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            if resp.is_success:
                return resp.json().get("timeZone", "UTC")
        except Exception:
            pass
        return "UTC"

    async def _get_busy_intervals(
        self,
        access_token: str,
        time_min: str,
        time_max: str,
        calendar_id: str = "primary",
    ) -> list[tuple[datetime, datetime]]:
        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": calendar_id}],
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_CALENDAR_BASE}/freeBusy",
                json=body,
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if not resp.is_success:
            logger.warning("FreeBusy API error: %s", resp.text)
            return []

        data = resp.json()
        busy_list = data.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        result = []
        for interval in busy_list:
            start = datetime.fromisoformat(interval["start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(interval["end"].replace("Z", "+00:00"))
            result.append((start, end))
        return result

    def _generate_free_slots(
        self,
        busy: list[tuple[datetime, datetime]],
        now_utc: datetime,
        days_ahead: int,
        slot_duration_minutes: int,
        cal_timezone: str,
    ) -> list[dict]:
        try:
            tz = ZoneInfo(cal_timezone)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")

        slots: list[dict] = []
        slot_delta = timedelta(minutes=slot_duration_minutes)

        for day_offset in range(days_ahead):
            day = (now_utc + timedelta(days=day_offset)).astimezone(tz).date()
            day_start = datetime(day.year, day.month, day.day, _BUSINESS_HOURS_START, 0, tzinfo=tz)
            day_end = datetime(day.year, day.month, day.day, _BUSINESS_HOURS_END, 0, tzinfo=tz)

            # Skip weekends
            if day.weekday() >= 5:
                continue

            candidate = day_start
            while candidate + slot_delta <= day_end:
                slot_end = candidate + slot_delta
                # Skip slots in the past (with 15-min buffer)
                if candidate.astimezone(timezone.utc) < now_utc + timedelta(minutes=15):
                    candidate += slot_delta
                    continue
                # Check overlap with busy intervals
                is_free = all(
                    slot_end.astimezone(timezone.utc) <= b_start or
                    candidate.astimezone(timezone.utc) >= b_end
                    for b_start, b_end in busy
                )
                if is_free:
                    slots.append({
                        "start": candidate.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                        "end": slot_end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    })
                    if len(slots) >= _MAX_SLOTS:
                        return slots
                candidate += slot_delta

        return slots
