-- Stage 0 schema. Safe to run on every startup (IF NOT EXISTS everywhere).
-- Money is integer paise (bigint) - never floats.

CREATE TABLE IF NOT EXISTS shows (
    id              uuid PRIMARY KEY,
    name            text        NOT NULL,
    price_paise     bigint      NOT NULL CHECK (price_paise >= 0),
    per_user_limit  integer     NOT NULL DEFAULT 4 CHECK (per_user_limit > 0),
    total_seats     integer     NOT NULL CHECK (total_seats > 0),
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reservations (
    id               uuid PRIMARY KEY,
    show_id          uuid        NOT NULL REFERENCES shows(id),
    user_id          text        NOT NULL,          -- from JWT `sub`, never from body
    idempotency_key  text        NOT NULL,
    request_hash     text        NOT NULL,          -- hash of sorted seat list
    seats            text[]      NOT NULL,
    amount_paise     bigint      NOT NULL CHECK (amount_paise >= 0),
    status           text        NOT NULL DEFAULT 'confirmed'
                                 CHECK (status IN ('confirmed', 'cancelled')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    -- exactly-once: one reservation per (user, key)
    CONSTRAINT uq_reservation_user_key UNIQUE (user_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS seats (
    show_id         uuid        NOT NULL REFERENCES shows(id),
    seat_label      text        NOT NULL,
    status          text        NOT NULL DEFAULT 'available'
                                CHECK (status IN ('available', 'held', 'confirmed')),
    reservation_id  uuid        NULL REFERENCES reservations(id),
    user_id         text        NULL,               -- denormalised for fast limit counts
    -- a seat exists once per show: makes duplicate seats impossible
    PRIMARY KEY (show_id, seat_label)
);

-- per-user limit counts and per-show status counts
CREATE INDEX IF NOT EXISTS idx_seats_show_user
    ON seats (show_id, user_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_seats_show_status
    ON seats (show_id, status);
CREATE INDEX IF NOT EXISTS idx_reservations_show
    ON reservations (show_id);
