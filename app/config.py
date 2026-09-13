from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _csv(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Srizon Message API")
    environment: str = os.getenv("ENVIRONMENT", "development")
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/srizon_message?sslmode=disable",
    )
    jwt_secret: str = os.getenv("JWT_SECRET", "change-me")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))
    cors_origins: tuple[str, ...] = tuple(_csv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173"))
    redis_url: str = os.getenv("REDIS_URL", "")
    public_group_id: str = os.getenv("PUBLIC_GROUP_ID", "public_lounge")
    public_group_name: str = os.getenv("PUBLIC_GROUP_NAME", "Public Lounge")


settings = Settings()
