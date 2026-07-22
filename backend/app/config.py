"""Application configuration - all via env vars, no hardcoded secrets."""
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "production"
    app_secret: str = "dev-secret"
    jwt_secret: str = "dev-jwt-secret"

    postgres_db: str = "novelforge"
    postgres_user: str = "novelforge"
    postgres_password: str = "novelforge"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@postgres:5432/{self.postgres_db}"
        )

    redis_url: str = "redis://redis:6379/0"

    primary_base_url: str = ""
    primary_api_key: str = ""
    fallback_base_url: str = ""
    fallback_api_key: str = ""

    planner_model: str = "deepseek-ai/deepseek-v4-pro"
    writer_model: str = "stepfun-ai/step-3.7-flash"
    review_model: str = "deepseek-ai/deepseek-v4-pro"
    query_model: str = "deepseek-v4-flash"
    ranker_model: str = "deepseek-v4-flash"

    global_llm_concurrency: int = 1
    arq_max_jobs: int = 1
    log_level: str = "INFO"
    raw_provider_retention_days: int = 30

    sqlalchemy_pool_size: int = 3
    sqlalchemy_max_overflow: int = 1

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
