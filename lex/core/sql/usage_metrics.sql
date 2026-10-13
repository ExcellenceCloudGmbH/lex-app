-- Fleet metrics, phase 1: what the usage recorder writes, in the instance's own database.
--
-- Deliberately not Django models: the framework registers every concrete model under lex/
-- for history tracking and for the model list, and an operational table belongs in
-- neither (the same reason as bitemporal_activation.sql).
--
-- A row holds a UTC day, an internal user id or an API key's name, a product area and a
-- channel: 'api' for API activity (the middleware), 'ui' and 'ui_embed' for the web
-- interface's area events (POST api/usage/area). No time of day: that would add a
-- per-person, per-area, per-day timestamp nothing reads, past the UTC day itself. The
-- lex-analytics collector reads counts from these tables, never rows.
--
-- Design: docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md (§5.3)

CREATE TABLE IF NOT EXISTS lex_usage_daily (
    day         date        NOT NULL,
    actor_kind  text        NOT NULL CHECK (actor_kind IN ('person', 'api_key')),
    actor_id    text        NOT NULL,
    area        text        NOT NULL,
    channel     text        NOT NULL CHECK (channel IN ('api', 'ui', 'ui_embed')),
    PRIMARY KEY (day, actor_kind, actor_id, area, channel),
    -- Only a person uses the interface; an API key appears on the api channel only.
    CHECK (channel = 'api' OR actor_kind = 'person')
);

CREATE TABLE IF NOT EXISTS lex_runtime_versions (
    version        text        PRIMARY KEY,
    first_seen     timestamptz NOT NULL DEFAULT now(),
    last_seen      timestamptz NOT NULL DEFAULT now(),
    usage_enabled  boolean     NOT NULL
);
