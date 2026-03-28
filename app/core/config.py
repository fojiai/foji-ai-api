from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.ssm import load_ssm_params

# Load SSM params into env vars before Settings is ever instantiated.
load_ssm_params()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    environment: str = "development"
    log_level: str = "INFO"
    port: int = 8000
    cors_origins: str = "http://localhost:3000,http://localhost:4321"

    # Database (shared with FojiApi, read-only role recommended)
    database_url: str

    # Auth
    internal_api_key: str

    # AI Providers
    openai_api_key: str = ""
    gemini_api_key: str = ""

    # AWS
    aws_region: str = "us-east-1"
    aws_bedrock_region: str = "us-east-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_dynamodb_table: str = "foji-chats-dev"
    aws_s3_bucket: str = ""

    # Database pool
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle: int = 1800

    # Chat behaviour
    chat_history_max_messages: int = 20
    file_context_max_chars: int = 50_000
    file_context_chunk_size: int = 4_000
    file_context_chunk_overlap: int = 200

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
