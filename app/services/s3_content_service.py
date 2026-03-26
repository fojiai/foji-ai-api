"""
Centralized S3 content reader for foji-ai-api.

Handles reading extraction artifacts (raw.txt, normalized.txt, chunks.jsonl)
that foji-worker uploads after processing a file.
"""

import json
import logging

import boto3
from botocore.exceptions import ClientError

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class S3ContentService:
    def __init__(self) -> None:
        cfg = get_settings()
        self._bucket = cfg.aws_s3_bucket
        self._client = boto3.client(
            "s3",
            region_name=cfg.aws_region,
            aws_access_key_id=cfg.aws_access_key_id or None,
            aws_secret_access_key=cfg.aws_secret_access_key or None,
        )

    def read_text(self, s3_key: str) -> str | None:
        """Read a plain text file from S3. Returns None if not found."""
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=s3_key)
            return response["Body"].read().decode("utf-8")
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                logger.warning("S3 key not found: %s", s3_key)
                return None
            raise

    def read_chunks(self, s3_key: str) -> list[dict]:
        """
        Read a JSONL chunks file from S3.
        Each line is: {"chunk_index": 0, "text": "...", "token_count": 123}
        Returns empty list if not found.
        """
        raw = self.read_text(s3_key)
        if not raw:
            return []
        chunks = []
        for line in raw.splitlines():
            line = line.strip()
            if line:
                try:
                    chunks.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed chunk line in %s", s3_key)
        return chunks

    @staticmethod
    def build_extraction_path(company_id: int, file_id: int, version: int) -> str:
        """Canonical S3 prefix for a file's extraction artifacts."""
        return f"tenant/{company_id}/files/{file_id}/extractions/{version}"

    @staticmethod
    def chunks_key(company_id: int, file_id: int, version: int) -> str:
        prefix = S3ContentService.build_extraction_path(company_id, file_id, version)
        return f"{prefix}/chunks.jsonl"

    @staticmethod
    def raw_text_key(company_id: int, file_id: int, version: int) -> str:
        prefix = S3ContentService.build_extraction_path(company_id, file_id, version)
        return f"{prefix}/raw.txt"

    @staticmethod
    def normalized_text_key(company_id: int, file_id: int, version: int) -> str:
        prefix = S3ContentService.build_extraction_path(company_id, file_id, version)
        return f"{prefix}/normalized.txt"
