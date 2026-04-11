from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://localhost/primed"
    secret_key: str = "CHANGE-ME-IN-PRODUCTION"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings():
    return Settings()
