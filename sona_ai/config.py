from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_image_model: str = "gpt-image-1"
    allowed_origins: str = "*"
    max_catalog_items: int = 500
    secret_key: str | None = None
    jwt_algorithm: str = 'HS256'
    ai_requests_per_minute: int = 30
    concept_requests_per_minute: int = 5

    model_config = SettingsConfigDict(
        env_file=None,
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def origins(self) -> list[str]:
        return [value.strip() for value in self.allowed_origins.split(",") if value.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

