from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    JWT_SECRET: str = "dev-secret-change-in-prod"
    JWT_ALGORITHM: str = "HS256"
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/neomarket"

    # Moderation service
    MODERATION_URL: str = "http://moderation-service:8000"
    MODERATION_SERVICE_KEY: str = "dev-service-key"
    B2B_SERVICE_KEY: str = "dev-b2b-service-key"

    class Config:
        env_file = ".env"


settings = Settings()