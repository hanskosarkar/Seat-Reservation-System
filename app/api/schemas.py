"""Pydantic request/response models (the API contract)."""
from pydantic import BaseModel, Field, StrictInt, field_validator

from app import config


class TokenRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)

    @field_validator("user_id")
    @classmethod
    def strip_user_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("user_id must not be blank")
        return v


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    expires_in: int  # seconds


class CreateShowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    seats: list[str] = Field(min_length=1, max_length=config.MAX_SEATS_PER_SHOW)
    price_paise: StrictInt = Field(ge=0)  # strict: floats are rejected
    per_user_limit: StrictInt = Field(default=4, ge=1, le=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("seats")
    @classmethod
    def clean_seats(cls, seats: list[str]) -> list[str]:
        cleaned = [s.strip() for s in seats]
        for label in cleaned:
            if not label:
                raise ValueError("seat labels must not be blank")
            if len(label) > config.MAX_SEAT_LABEL_LENGTH:
                raise ValueError(
                    f"seat label longer than {config.MAX_SEAT_LABEL_LENGTH} characters"
                )
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("seat labels must be unique")
        return cleaned


class SeatOut(BaseModel):
    seat: str
    status: str  # available | held | confirmed


class ShowResponse(BaseModel):
    id: str
    name: str
    price_paise: int
    per_user_limit: int
    total_seats: int
    available: int
    held: int
    confirmed: int
    seats: list[SeatOut]

class ReserveRequest(BaseModel):
    seats: list[str] = Field(min_length=1, max_length=1)
    idempotency_key: str = Field(min_length=1, max_length=255)


class ReservationResponse(BaseModel):
    reservation_id: str
    show_id: str
    user_id: str
    seats: list[str]
    amount_paise: int
    status: str