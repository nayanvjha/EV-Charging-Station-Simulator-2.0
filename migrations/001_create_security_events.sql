-- Up migration
BEGIN;

CREATE TABLE IF NOT EXISTS security_events (
    id BIGSERIAL PRIMARY KEY NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    charge_point_id VARCHAR(100) NOT NULL,
    event_type VARCHAR(100) NOT NULL CHECK (char_length(event_type) > 0),
    severity SMALLINT NOT NULL CHECK (severity BETWEEN 1 AND 10),
    description TEXT NOT NULL,
    raw_message JSONB NULL
);

CREATE INDEX IF NOT EXISTS idx_security_events_charge_point_id
    ON security_events (charge_point_id);

CREATE INDEX IF NOT EXISTS idx_security_events_timestamp_desc
    ON security_events (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_security_events_cp_timestamp_desc
    ON security_events (charge_point_id, timestamp DESC);

COMMIT;

-- Down migration
BEGIN;

DROP INDEX IF EXISTS idx_security_events_cp_timestamp_desc;
DROP INDEX IF EXISTS idx_security_events_timestamp_desc;
DROP INDEX IF EXISTS idx_security_events_charge_point_id;
DROP TABLE IF EXISTS security_events;

COMMIT;
