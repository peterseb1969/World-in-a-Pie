"""
Configuration for the Reporting Sync service.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Service info
    service_name: str = "wip-reporting-sync"
    debug: bool = False

    # NATS configuration
    nats_url: str = Field(default="nats://localhost:4222", alias="NATS_URL")
    nats_stream_name: str = Field(default="WIP_EVENTS", alias="NATS_STREAM_NAME")
    nats_consumer_name: str = Field(default="reporting-sync", alias="NATS_CONSUMER_NAME")
    nats_durable_name: str = Field(default="reporting-sync-durable", alias="NATS_DURABLE_NAME")

    # PostgreSQL configuration
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="wip_reporting", alias="POSTGRES_DB")
    postgres_user: str = Field(default="wip", alias="POSTGRES_USER")
    postgres_password: str = Field(default="wip_dev_password", alias="POSTGRES_PASSWORD")
    postgres_pool_min: int = Field(default=2, alias="POSTGRES_POOL_MIN")
    postgres_pool_max: int = Field(default=10, alias="POSTGRES_POOL_MAX")

    # Template Store API (for fetching template definitions)
    template_store_url: str = Field(
        default="http://localhost:8003", alias="TEMPLATE_STORE_URL"
    )

    # Document Store API (for batch sync)
    document_store_url: str = Field(
        default="http://localhost:8004", alias="DOCUMENT_STORE_URL"
    )

    api_key: str = Field(default="dev_master_key_for_testing", alias="API_KEY")

    # Sync configuration
    batch_size: int = Field(default=100, alias="BATCH_SIZE")
    retry_attempts: int = Field(default=3, alias="RETRY_ATTEMPTS")
    retry_delay_ms: int = Field(default=1000, alias="RETRY_DELAY_MS")

    @property
    def postgres_dsn(self) -> str:
        """PostgreSQL connection string."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_async_dsn(self) -> str:
        """PostgreSQL async connection string for asyncpg."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


# Global settings instance
settings = Settings()
