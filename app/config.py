from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    JWT_SECRET: str = "dev-secret-change-in-prod"
    JWT_ALGORITHM: str = "HS256"
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/neomarket"

    # Moderation service
    MODERATION_URL: str = "http://moderation-service:8000"
    MODERATION_SERVICE_KEY: str = "dev-service-key"
    B2B_SERVICE_KEY: str = "dev-b2b-service-key"
    B2B_BASE_URL: str = "http://localhost:8000"
    B2B_HTTP_TIMEOUT: float = 5.0
    SERVICE_KEY: str = "dev-service-key"

    # B2B service (called by B2C checkout)
    B2B_URL: str = "http://b2b-service:8000"

    # B2B → Moderation (входящие события от B2B)
    B2B_TO_MOD_KEY: str = "dev-b2b-to-mod-key"

    # Moderation → B2B (исходящие запросы к B2B API)
    MOD_TO_B2B_KEY: str = "dev-mod-to-b2b-key"

    # B2C service — для каскадных событий
    B2C_URL: str = "http://b2c-service:8000"
    B2C_SERVICE_KEY: str = "dev-b2c-service-key"

    class Config:
        env_file = ".env"


settings = Settings()