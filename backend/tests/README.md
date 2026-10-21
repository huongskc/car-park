# Backend tests

Run from the repository root after installing `backend/requirements.txt`:

```powershell
python -m pytest backend/tests
```

Pytest always targets the disposable MySQL database `smart_parking_test`. The
test fixture recreates its schema and seed before **every test function**. It
refuses to run when the `DB_NAME` environment variable names another database.
The MySQL user configured through `DB_HOST`, `DB_PORT`, `DB_USER`, and
`DB_PASSWORD` (or `.env`) needs permission to create and rebuild this test
database. Never store data that must be kept in `smart_parking_test`.

The MQTT connection is mocked in tests that start the FastAPI lifespan. The
checkout concurrency test still uses real MySQL transactions and row locks.
