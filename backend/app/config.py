"""Application configuration - all via env vars, no hardcoded secrets."""
from pydantic_settings import BaseSettings
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    app_env: str = "production"
    app_secret: str = "dev-secret"
    jwt_secret: str = "dev-jwt-secret"

    postgres_db: str = "novelforge"
    postgres_user: str = "novelforge"
    postgres_password: str = "novelforge"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    @property
    def database_url(self) -> str:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)

    redis_url: str = "redis://redis:6379/0"

    primary_base_url: str = ""
    primary_api_key: str = ""
    fallback_base_url: str = ""
    fallback_api_key: str = ""

    planner_model: str = "deepseek-v4-flash"
    writer_model: str = "stepfun-ai/step-3.7-flash"
    review_model: str = "deepseek-v4-flash"
    query_model: str = "deepseek-v4-flash"
    ranker_model: str = "deepseek-v4-flash"

    global_llm_concurrency: int = 1
    arq_max_jobs: int = 1
    log_level: str = "INFO"
    raw_provider_retention_days: int = 30

    sqlalchemy_pool_size: int = 3
    sqlalchemy_max_overflow: int = 1

    # NovelForge v8.0 feature flags (env FEATURE_* or defaults)
    feature_library_v2: bool = True
    feature_import_v2: bool = True
    feature_prompt_studio: bool = True

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
