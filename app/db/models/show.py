from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Show:
    id: UUID
    name: str
    price_paise: int
    per_user_limit: int
    total_seats: int
    created_at: datetime

    @classmethod
    def from_record(cls, r) -> "Show":
        return cls(
            id=r["id"],
            name=r["name"],
            price_paise=r["price_paise"],
            per_user_limit=r["per_user_limit"],
            total_seats=r["total_seats"],
            created_at=r["created_at"],
        )
