"""Application settings, read from environment variables with safe local defaults."""
import os

DATABASE_URL: str = os.environ.get(
    "DATABASE_URL", "postgresql://seat:seat@localhost:5432/seatdb"
)

JWT_SECRET: str = os.environ.get("JWT_SECRET", "dev-only-secret-change-me")
JWT_ALGORITHM: str = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_EXPIRY_MINUTES: int = int(os.environ.get("JWT_EXPIRY_MINUTES", "1440"))

# asyncpg pool. Requests beyond DB_POOL_MAX queue inside the pool
DB_POOL_MIN: int = int(os.environ.get("DB_POOL_MIN", "2"))
DB_POOL_MAX: int = int(os.environ.get("DB_POOL_MAX", "20"))
DB_CONNECT_TIMEOUT_SECONDS: float = float(
    os.environ.get("DB_CONNECT_TIMEOUT_SECONDS", "30")
)
DB_ACQUIRE_TIMEOUT_SECONDS: float = float(
    os.environ.get("DB_ACQUIRE_TIMEOUT_SECONDS", "60")
)
DB_COMMAND_TIMEOUT_SECONDS: float = float(
    os.environ.get("DB_COMMAND_TIMEOUT_SECONDS", "60")
)
DB_CONNECT_RETRIES: int = int(os.environ.get("DB_CONNECT_RETRIES", "10"))

# readiness probe: short on purpose so it never hangs behind a busy pool
READY_CHECK_TIMEOUT_SECONDS: float = float(
    os.environ.get("READY_CHECK_TIMEOUT_SECONDS", "2")
)

# input limits
MAX_SEATS_PER_SHOW: int = int(os.environ.get("MAX_SEATS_PER_SHOW", "5000"))
MAX_SEAT_LABEL_LENGTH: int = int(os.environ.get("MAX_SEAT_LABEL_LENGTH", "20"))
MAX_SEATS_PER_REQUEST: int = int(os.environ.get("MAX_SEATS_PER_REQUEST", "100"))
MAX_IDEMPOTENCY_KEY_LENGTH: int = int(
    os.environ.get("MAX_IDEMPOTENCY_KEY_LENGTH", "200")
)

LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
PORT: int = int(os.environ.get("PORT", "8000"))
