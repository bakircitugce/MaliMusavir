from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MaliAsistan"
    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./mali_asistan.sqlite3"
    storage_dir: str = "./storage"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key_id: str = "minio"
    s3_secret_access_key: str = "miniosecret"
    s3_bucket_name: str = "documents"
    secret_key: str = "development-only-change-me"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
