"""Safety check for legacy smoke scripts run directly with Python."""


def require_test_database() -> None:
    from app.config import settings
    from app.database import engine

    if settings.DB_NAME != "smart_parking_test" or engine.url.database != "smart_parking_test":
        raise SystemExit(
            "Smoke tests may modify data. Set DB_NAME=smart_parking_test and prepare "
            "the disposable test database before running this script."
        )
