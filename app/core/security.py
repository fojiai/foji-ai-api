from fastapi import Header, HTTPException, status

from app.core.config import get_settings


async def require_internal_api_key(x_internal_api_key: str = Header(...)) -> None:
    """Dependency that protects internal service-to-service endpoints."""
    if x_internal_api_key != get_settings().internal_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal API key.")
