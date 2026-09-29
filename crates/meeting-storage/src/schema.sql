CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_seconds INTEGER,
    title TEXT,
    ai_note TEXT NOT NULL DEFAULT '',
    minutes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'completed', 'aborted')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meetings_started_at_id_desc
    ON meetings(started_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS meeting_turns (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    speaker TEXT NOT NULL,
    text TEXT NOT NULL,
    speaker_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(meeting_id, sequence)
);

CREATE TABLE IF NOT EXISTS reply_suggestions (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    target_turn_id TEXT NOT NULL REFERENCES meeting_turns(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    agent_id TEXT NOT NULL,
    agent_label TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(target_turn_id, sequence)
);

CREATE TABLE IF NOT EXISTS recording_assets (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('other', 'self')),
    relative_path TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'wav'
        CHECK(format IN ('wav', 'mp3', 'ogg', 'flac', 'webm')),
    sample_rate INTEGER NOT NULL DEFAULT 16000,
    channels INTEGER NOT NULL DEFAULT 1,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    size_bytes INTEGER,
    UNIQUE(meeting_id, role)
);
