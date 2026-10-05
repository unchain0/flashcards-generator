CREATE TABLE IF NOT EXISTS web_users (
    id VARCHAR(32) PRIMARY KEY,
    password_lookup VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(512) NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_web_users_password_lookup ON web_users (password_lookup);

CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(32) NOT NULL REFERENCES web_users (id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_web_sessions_user_id ON web_sessions (user_id);
CREATE INDEX IF NOT EXISTS ix_web_sessions_expires_at ON web_sessions (expires_at);
