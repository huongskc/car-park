from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BASE_DIR.parent

class Settings(BaseSettings):
    APP_NAME: str = "Smart Parking Backend"
    APP_ENV: str = "development"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str = ""
    DB_NAME: str = "smart_parking_db"

    MQTT_BROKER_HOST: str = "localhost"
    MQTT_BROKER_PORT: int = 1883
    MQTT_CLIENT_ID: str = "smart_parking_backend"
    MQTT_USERNAME: Optional[str] = "backend_admin"
    MQTT_PASSWORD: Optional[str] = None
    MQTT_BASE_TOPIC: str = "parking"
    SITE_ID: str = "PTIT_ZONE_1"

    JWT_SECRET_KEY: str = "replace_with_a_secure_random_secret_key_minimum_32_chars"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    INTERNAL_ALPR_API_KEY: str = ""
    ALPR_SERVICE_URL: str = "http://127.0.0.1:8001"
    ALPR_TIMEOUT_SEC: float = 60.0
    ENABLE_MOCK_ALPR: bool = False

    PARKING_FLAT_FEE: float = 20000.0
    SERVO_OPEN_TIMEOUT_SEC: int = 10
    HEARTBEAT_TIMEOUT_SEC: int = 30
    DISTANCE_THRESHOLD_CM: float = 15.0
    ENTRY_GRACE_PERIOD_SEC: int = 180
    TESTING: bool = False

    model_config = SettingsConfigDict(
        env_file=(BASE_DIR / ".env", ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def DATABASE_URL(self) -> str:
        from urllib.parse import quote_plus
        pwd = quote_plus(self.DB_PASSWORD) if self.DB_PASSWORD else ""
        return (
            f"mysql+pymysql://{self.DB_USER}:{pwd}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"
        )

settings = Settings()
