from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Seat:
    show_id: UUID
    seat_label: str
    status: str  # available | held | confirmed
    reservation_id: UUID | None
    user_id: str | None

    @classmethod
    def from_record(cls, r) -> "Seat":
        return cls(
            show_id=r["show_id"],
            seat_label=r["seat_label"],
            status=r["status"],
            reservation_id=r["reservation_id"],
            user_id=r["user_id"],
        )
