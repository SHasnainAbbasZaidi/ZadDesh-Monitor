from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any, List
import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    base_url: str = "http://localhost:8000"
    workers: int = 1
    cors_origins: List[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
    ]


class DatabaseConfig(BaseModel):
    url: str = "sqlite:///./zaddesh-live.db"
    echo: bool = False
    pool_size: int = 20
    max_overflow: int = 10


class SecurityConfig(BaseModel):
    jwt_secret: str = Field(default_factory=lambda: secrets.token_hex(32), min_length=32, repr=False)
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60
    encryption_key: str | None = Field(default=None, min_length=32, repr=False)
    allow_registration: bool = False


class CollectorConfig(BaseModel):
    enrollment_token: str | None = Field(default=None, min_length=32, repr=False)
    shared_secret: str | None = Field(default=None, min_length=32, repr=False)
    batch_size: int = 1000
    flush_interval_seconds: int = 5


class RetentionConfig(BaseModel):
    raw_days: int = 14
    rollup_1m_days: int = 30
    rollup_5m_days: int = 180
    rollup_1h_days: int = 730
    rollup_1d_days: int = 1825


class LogsConfig(BaseModel):
    mongo_uri: str | None = Field(default=None, repr=False)
    database: str = "zaddesh_logs"
    ingest_token: str | None = Field(default=None, min_length=32, repr=False)
    retention_days: int = Field(default=30, ge=1, le=3650)


class FeaturesConfig(BaseModel):
    incident_workspace: bool = False
    network_diagram_beta: bool = False
    topology_allowed_ranges: list[str] = Field(default_factory=list)
    event_retention_days: int = Field(default=0, ge=0, le=3650)
    incident_webhook_url: str | None = Field(default=None, repr=False)


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"


def find_config_file() -> Path | None:
    env_path = os.getenv("ZADDESH_CONFIG_FILE")
    if env_path:
        path = Path(env_path)
        if not path.is_file():
            raise FileNotFoundError(f"Configured ZadDesh YAML file does not exist: {path}")
        return path

    search_paths = [
        Path.cwd() / "config" / "zaddesh.yaml",
        Path.cwd() / "zaddesh.yaml",
        Path.cwd().parent / "config" / "zaddesh.yaml",
        Path("/etc/zaddesh/zaddesh.yaml"),
    ]
    for p in search_paths:
        if p.exists():
            return p
    return None


def load_yaml_config() -> dict[str, Any]:
    path = find_config_file()
    if path and path.is_file():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if data is None:
                return {}
            if not isinstance(data, dict):
                raise ValueError("ZadDesh configuration must be a YAML mapping")
            return data
    return {}


class AppSettings(BaseSettings):
    demo_mode: bool = False
    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    collector: CollectorConfig = Field(default_factory=CollectorConfig)
    logs: LogsConfig = Field(default_factory=LogsConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    model_config = SettingsConfigDict(
        env_prefix="ZADDESH_",
        env_nested_delimiter="__",
        extra="ignore",
        hide_input_in_errors=True,
    )

    @classmethod
    def settings_customise_sources(
        cls, settings_cls: type[BaseSettings], init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource, dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return env_settings, init_settings, dotenv_settings, file_secret_settings

    @classmethod
    def load(cls) -> "AppSettings":
        yaml_data = load_yaml_config()
        return cls(**yaml_data)


settings = AppSettings.load()

