-- Password reset support: allow passwordless users (e.g. email-captured) and
-- track when a password was last set so reset tokens can be invalidated after use.

ALTER TABLE users ALTER COLUMN hashed_password DROP NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_set_at TIMESTAMPTZ;

-- Backfill password_set_at for existing users who already have a password,
-- so JWT reset tokens issued for them can be validated against created_at.
UPDATE users SET password_set_at = created_at WHERE hashed_password IS NOT NULL AND password_set_at IS NULL;
