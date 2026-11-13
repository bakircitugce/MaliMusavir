from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_AUTH_SECRET_BYTES = 32
MIN_PASSWORD_HASH_ITERATIONS = 600_000


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

    # Token imza anahtarı. Varsayılanı YOKTUR: ortam değişkeninden (AUTH_SECRET)
    # okunmak zorundadır, eksik veya kısa ise uygulama açılmaz.
    auth_secret: SecretStr
    access_token_ttl_minutes: int = Field(default=480, ge=5, le=60 * 24)
    password_hash_iterations: int = MIN_PASSWORD_HASH_ITERATIONS

    login_max_failed_attempts: int = Field(default=5, ge=1)
    login_lockout_minutes: int = Field(default=15, ge=1)
    login_rate_limit_per_minute: int = Field(default=10, ge=1)

    # Görsel belge okuma: "tesseract" (varsayılan, hafif) veya "ollama" (yerel görsel
    # dil modeli; güçlü GPU/bellek ister, yalnızca bilinçli olarak açılmalıdır).
    vision_backend: str = Field(default="tesseract", pattern="^(ollama|tesseract)$")
    vision_ollama_url: str = "http://127.0.0.1:11434"
    vision_model: str = "qwen2.5vl:3b"
    vision_max_side: int = Field(default=1600, ge=512, le=4096)
    vision_timeout_seconds: float = Field(default=600, gt=0)
    # Varsayılan kapalı: mükellef görselleri yerel makine dışına gönderilmez.
    vision_allow_remote: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("auth_secret")
    @classmethod
    def _secret_strength(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode("utf-8")) < MIN_AUTH_SECRET_BYTES:
            raise ValueError(
                f"AUTH_SECRET en az {MIN_AUTH_SECRET_BYTES} bayt olmalıdır. "
                'Üretmek için: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return value

    @model_validator(mode="after")
    def _iterations_floor(self) -> "Settings":
        # Düşük iterasyon yalnızca test ortamında (hız için) kabul edilir.
        if self.app_env != "test" and self.password_hash_iterations < MIN_PASSWORD_HASH_ITERATIONS:
            raise ValueError(
                f"PASSWORD_HASH_ITERATIONS en az {MIN_PASSWORD_HASH_ITERATIONS} olmalıdır."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
