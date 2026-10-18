"""
Central configuration, loaded from environment variables / .env file.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- LLM ---
    groq_api_key: str = "MIO_GROQ_API_KEY"
    architect_model: str = "MIO_ARCHITECT_MODEL"
    coder_model: str = "MIO_CODER_MODEL"
    reviewer_model: str = "MIO_REVIEWER_MODEL"
    qa_model: str = "MIO_QA_MODEL"

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"
    session_ttl_seconds: int = 60 * 60 * 24 * 7  # 7 days

    # --- Sandbox ---
    docker_sandbox_image: str = "mio-sandbox-python:latest"
    sandbox_timeout_seconds: int = 30
    sandbox_mem_limit: str = "256m"
    sandbox_cpu_quota: int = 50000  # 50% of one core

    # --- App ---
    cors_origins: list[str] = ["*"]
    max_review_cycles: int = 3  # coder <-> reviewer/QA revision loop cap to 3

    model_config = SettingsConfigDict(env_file=".env", env_prefix="MIO_")


settings = Settings()
