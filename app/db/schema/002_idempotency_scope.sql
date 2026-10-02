-- Idempotency keys are scoped to (show, user, key) instead of (user, key), so a
-- client may reuse the same key on a different show without a false conflict.
-- Safe on every startup: on a fresh database 001 creates the old constraint and
-- this swaps it; afterwards both statements are no-ops.

ALTER TABLE reservations DROP CONSTRAINT IF EXISTS uq_reservation_user_key;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_reservation_show_user_key'
    ) THEN
        ALTER TABLE reservations
            ADD CONSTRAINT uq_reservation_show_user_key
            UNIQUE (show_id, user_id, idempotency_key);
    END IF;
END
$$;
