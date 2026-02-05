"""Configuration for the Ingest Gateway service."""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Service info
    service_name: str = "wip-ingest-gateway"
    debug: bool = False

    # NATS configuration
    nats_url: str = Field(default="nats://localhost:4222", alias="NATS_URL")

    # Ingest stream (separate from WIP_EVENTS used for outbound)
    nats_ingest_stream_name: str = Field(
        default="WIP_INGEST",
        alias="NATS_INGEST_STREAM_NAME"
    )
    nats_ingest_consumer_name: str = Field(
        default="ingest-gateway",
        alias="NATS_INGEST_CONSUMER_NAME"
    )
    nats_ingest_durable_name: str = Field(
        default="ingest-gateway-durable",
        alias="NATS_INGEST_DURABLE_NAME"
    )

    # Results stream
    nats_results_stream_name: str = Field(
        default="WIP_INGEST_RESULTS",
        alias="NATS_RESULTS_STREAM_NAME"
    )

    # Target service URLs
    def_store_url: str = Field(
        default="http://localhost:8002",
        alias="DEF_STORE_URL"
    )
    template_store_url: str = Field(
        default="http://localhost:8003",
        alias="TEMPLATE_STORE_URL"
    )
    document_store_url: str = Field(
        default="http://localhost:8004",
        alias="DOCUMENT_STORE_URL"
    )

    # API key for authenticating with target services
    api_key: str = Field(
        default="dev_master_key_for_testing",
        alias="API_KEY"
    )

    # Processing configuration
    batch_size: int = Field(default=100, alias="BATCH_SIZE")
    retry_attempts: int = Field(default=3, alias="RETRY_ATTEMPTS")
    retry_delay_ms: int = Field(default=1000, alias="RETRY_DELAY_MS")
    http_timeout_seconds: float = Field(default=30.0, alias="HTTP_TIMEOUT_SECONDS")

    # Stream configuration
    stream_max_msgs: int = Field(default=1_000_000, alias="STREAM_MAX_MSGS")
    stream_max_bytes: int = Field(
        default=1024 * 1024 * 1024,  # 1GB
        alias="STREAM_MAX_BYTES"
    )
    results_max_age_seconds: int = Field(
        default=60 * 60 * 24 * 7,  # 7 days
        alias="RESULTS_MAX_AGE_SECONDS"
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
