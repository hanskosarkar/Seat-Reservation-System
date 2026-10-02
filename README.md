# Seat Reservation Service

FastAPI + asyncpg + Postgres. Built in stages.

## Status
- [x] Stage 0: skeleton, config, DB pool, migrations, Docker

## Run locally
```bash
docker compose up --build
```
The app starts on http://localhost:8000 and applies the schema on startup.

Without Docker:
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     
uvicorn app.main:app --reload
```
