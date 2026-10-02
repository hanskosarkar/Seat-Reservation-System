from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Reservation:
    id: UUID
    show_id: UUID
    user_id: str
    idempotency_key: str
    request_hash: str
    seats: list[str]
    amount_paise: int
    status: str  # confirmed | cancelled
    created_at: datetime

    @classmethod
    def from_record(cls, r) -> "Reservation":
        return cls(
            id=r["id"],
            show_id=r["show_id"],
            user_id=r["user_id"],
            idempotency_key=r["idempotency_key"],
            request_hash=r["request_hash"],
            seats=list(r["seats"]),
            amount_paise=r["amount_paise"],
            status=r["status"],
            created_at=r["created_at"],
        )
