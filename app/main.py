import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import chat, internal, widget
from app.core.config import get_settings
import app.models  # noqa: F401 — register all SQLAlchemy models so relationships resolve

logging.basicConfig(
    level=logging.DEBUG if not get_settings().is_production else logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title="Foji AI — Chat API",
    version="1.0.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
)

# CORS — widget and management UI origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    logger.info("foji-ai-api started | env=%s", settings.environment)
