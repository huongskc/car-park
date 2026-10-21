"""Keep every pytest database operation inside the disposable MySQL test database."""

import os
from pathlib import Path

import pytest


TEST_DB_NAME = "smart_parking_test"
configured_db = os.environ.get("DB_NAME")
if configured_db and configured_db != TEST_DB_NAME:
    raise pytest.UsageError(
        f"Refusing to run tests with DB_NAME={configured_db!r}; use {TEST_DB_NAME!r}."
    )

# Environment variables take precedence over the application's .env files.
os.environ["DB_NAME"] = TEST_DB_NAME
os.environ["TESTING"] = "true"


@pytest.fixture(autouse=True)
def isolated_test_database(monkeypatch):
    """Rebuild only the dedicated database before each test function."""
    import pymysql
    from pymysql.constants import CLIENT

    from app.config import settings
    from app.database import engine
    from app.services.mqtt_service import mqtt_service

    if settings.DB_NAME != TEST_DB_NAME or engine.url.database != TEST_DB_NAME:
        pytest.fail("Test database guard rejected a non-test connection", pytrace=False)

    sql_dir = Path(__file__).resolve().parents[2] / "database"
    connection = pymysql.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        charset="utf8mb4",
        client_flag=CLIENT.MULTI_STATEMENTS,
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            for filename in ("schema.sql", "seed.sql"):
                script = (sql_dir / filename).read_text(encoding="utf-8")
                script = script.replace("`smart_parking_db`", f"`{TEST_DB_NAME}`")
                cursor.execute(script)
                while cursor.nextset():
                    pass
    finally:
        connection.close()

    # Tests which enter the app lifespan must not contact a real broker.
    monkeypatch.setattr(mqtt_service, "start", lambda: None)
    monkeypatch.setattr(mqtt_service, "stop", lambda: None)
