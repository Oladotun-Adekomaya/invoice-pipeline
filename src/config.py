from decimal import Decimal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    database_url: str
    auto_approve_threshold: Decimal = Decimal("5000.00")
    slack_webhook_url: str = "placeholder"
    intake_watch_dir: str = "./incoming"
    log_level: str = "INFO"
    environment: str = "development"
    tesseract_cmd: str = ""
    anthropic_api_key: str = ""


settings: Settings = Settings() # type: ignore