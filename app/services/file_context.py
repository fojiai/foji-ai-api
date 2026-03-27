"""
FileContextService — S3-first chunk loading.

Flow:
  1. Find all Ready files with s3_chunks_key set.
  2. Load chunks.jsonl from S3 via S3ContentService.
  3. Concatenate chunk texts up to max_chars cap.

No DB text blobs are read — all content lives in S3.
"""

import logging

from app.core.config import get_settings
from app.models.agent import Agent
from app.services.s3_content_service import S3ContentService

logger = logging.getLogger(__name__)

_READY_STATUS = "Ready"


class FileContextService:
    def __init__(self) -> None:
        cfg = get_settings()
        self._max_chars = cfg.file_context_max_chars
        self._s3 = S3ContentService()

    async def build(self, agent: Agent) -> str:
        """
        Load all chunk texts for the agent's Ready files from S3
        and return a single concatenated context string, capped at max_chars.
        """
        ready_files = [
            f for f in agent.files
            if f.processing_status == _READY_STATUS and f.s3_chunks_key
        ]

        if not ready_files:
            return ""

        context_parts: list[str] = []
        total_chars = 0

        for file in ready_files:
            if total_chars >= self._max_chars:
                break

            chunks = await self._s3.read_chunks(file.s3_chunks_key)
            if not chunks:
                logger.warning(
                    "No chunks loaded from S3 for file_id=%s key=%s",
                    file.id, file.s3_chunks_key
                )
                continue

            for chunk in chunks:
                text = chunk.get("text", "")
                if not text:
                    continue
                remaining = self._max_chars - total_chars
                if len(text) > remaining:
                    context_parts.append(text[:remaining])
                    total_chars = self._max_chars
                    break
                context_parts.append(text)
                total_chars += len(text)

            if total_chars >= self._max_chars:
                break

        return "\n\n---\n\n".join(context_parts)
