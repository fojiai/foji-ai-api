import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import chat, internal, widget
from app.core.config import get_settings
import app.models  # noqa: F401 — register all SQLAlchemy models so relationships resolve

_settings = get_settings()
_log_level = getattr(logging, _settings.log_level.upper(), logging.INFO)
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
# Quiet noisy third-party loggers regardless of app log level
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title="Foji AI — Chat API",
    version="1.0.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
)

# CORS — widget and management UI origins
_cors_kwargs: dict = {
    "allow_origins": settings.allowed_origins,
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if settings.allowed_origin_regex:
    _cors_kwargs["allow_origin_regex"] = settings.allowed_origin_regex
app.add_middleware(CORSMiddleware, **_cors_kwargs)


# ── Routers ──────────────────────────────────────────────────────────────────
app.include_router(chat.router, prefix="/api/v1", tags=["Chat"])
app.include_router(widget.router, prefix="/api/v1", tags=["Widget"])
app.include_router(internal.router, prefix="/api/v1", tags=["Internal"])


# ── Health ───────────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"], include_in_schema=False)
async def health():
    return JSONResponse({"status": "ok", "service": "foji-ai-api"})


# ── Startup log ──────────────────────────────────────────────────────────────
@app.on_event("startup")
async def on_startup():
    logger.info(
        "foji-ai-api started | env=%s | cors_origins=%s | cors_regex=%s",
        settings.environment,
        settings.allowed_origins,
        settings.allowed_origin_regex,
    )
