from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Job Board"
    app_env: str = "local"
    app_base_url: str = "http://localhost:8000"

    database_url: str
    redis_url: str

    admin_username: str = "admin"
    admin_password: str | None = None

    jwt_secret: str | None = None
    jwt_expire_hours: int = 12
    jwt_algorithm: str = "HS256"
    jwt_cookie_name: str = "admin_token"

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None

    brevo_api_key: str | None = None
    brevo_from_email: str | None = None
    brevo_from_name: str = "Job Board"

    jobs_cache_ttl_seconds: int = 60
    apply_lock_ttl_seconds: int = 300
    recent_applications_limit: int = 10
    seed_sample_data: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.app_env.lower() != "production":
            return self

        admin_password = (self.admin_password or "").strip()
        jwt_secret = (self.jwt_secret or "").strip()

        if not admin_password:
            raise ValueError(
                "ADMIN_PASSWORD must be set when APP_ENV=production."
            )
        if not jwt_secret:
            raise ValueError("JWT_SECRET must be set when APP_ENV=production.")

        weak_passwords = {"change-me", "changeme", "password", "admin"}
        weak_secrets = {"change-me", "changeme", "secret"}

        if admin_password.lower() in weak_passwords:
            raise ValueError(
                "ADMIN_PASSWORD uses a placeholder or weak value. Set a strong password when APP_ENV=production."
            )
        if jwt_secret.lower() in weak_secrets:
            raise ValueError(
                "JWT_SECRET uses a placeholder or weak value. Set a strong secret when APP_ENV=production."
            )

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
