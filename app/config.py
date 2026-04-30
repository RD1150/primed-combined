from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://localhost/primed"
    secret_key: str = "CHANGE-ME-IN-PRODUCTION"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id: str = ""  # Set this after creating a price in Stripe  # Rachel - default female voice
    resend_api_key: str = ""
    email_from: str = "Primed <noreply@primed.today>"
    app_base_url: str = "https://primed-api.onrender.com"
    password_reset_token_expire_minutes: int = 30
    admin_emails: str = ""  # comma-separated list of emails that bypass the paywall

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings():
    return Settings()
