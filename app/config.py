from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Job Board"
    app_env: str = "development"
    app_base_url: str = "http://localhost:8000"

    database_url: str
    redis_url: str

    admin_username: str = "admin"
    admin_password: str = "change-me"

    jwt_secret: str = "change-me"
    jwt_expire_hours: int = 12
    jwt_algorithm: str = "HS256"
    jwt_cookie_name: str = "admin_token"

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
