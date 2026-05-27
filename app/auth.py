from datetime import UTC, datetime, timedelta
import hmac

import jwt
from fastapi import Response

from app.config import get_settings

settings = get_settings()


def verify_admin_credentials(username: str, password: str) -> bool:
    username_match = hmac.compare_digest(username, settings.admin_username)
    password_match = hmac.compare_digest(password, settings.admin_password)
    return username_match and password_match


def create_access_token(subject: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_expire_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.jwt_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.app_env.lower() == "production",
        max_age=settings.jwt_expire_hours * 3600,
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(settings.jwt_cookie_name)
