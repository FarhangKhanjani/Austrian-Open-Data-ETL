-- Every HTTP response the pipeline receives, stored unchanged.
--
-- Append-only by design: each fetch is its own row, even if the body is identical to an
-- earlier fetch. For snapshot sources like E-Control, *when* we fetched is part of the
-- data, so collapsing identical bodies would lose information. content_hash still lets
-- the pipeline detect "nothing changed" cheaply and skip re-transforming.
--
-- body is text, not jsonb: jsonb reorders keys and drops whitespace, so it would not be
-- "exactly as received", and one of the sources (Statistik Austria) is CSV anyway.
-- For ad hoc debugging of JSON sources, body::jsonb works in a query.
CREATE TABLE IF NOT EXISTS raw.api_response (
    raw_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id         bigint      NOT NULL REFERENCES ops.pipeline_run (run_id),
    source         text        NOT NULL,
    endpoint       text        NOT NULL,
    source_url     text        NOT NULL,
    request_params jsonb       NOT NULL DEFAULT '{}'::jsonb,
    http_status    smallint    NOT NULL,
    content_type   text,
    body           text        NOT NULL,
    content_hash   char(64)    NOT NULL,
    fetched_at     timestamptz NOT NULL,
    CONSTRAINT api_response_source_chk
        CHECK (source IN ('geosphere', 'econtrol', 'statistik_austria')),
    CONSTRAINT api_response_hash_chk
        CHECK (content_hash ~ '^[0-9a-f]{64}$')
);

-- "Latest response for this source and endpoint" is the lookup for change detection
-- and for replaying transformations.
CREATE INDEX IF NOT EXISTS api_response_source_endpoint_fetched_idx
    ON raw.api_response (source, endpoint, fetched_at DESC);

-- Rows that failed validation, with the reasons. Nothing is silently dropped: every
-- rejected record can be traced back to the exact response it came from.
CREATE TABLE IF NOT EXISTS ops.quarantine (
    quarantine_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id         bigint      NOT NULL REFERENCES ops.pipeline_run (run_id),
    raw_id         bigint      REFERENCES raw.api_response (raw_id),
    source         text        NOT NULL,
    target_table   text        NOT NULL,
    record         jsonb       NOT NULL,
    reasons        text[]      NOT NULL,
    quarantined_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT quarantine_reasons_chk CHECK (cardinality(reasons) > 0)
);

CREATE INDEX IF NOT EXISTS quarantine_run_idx ON ops.quarantine (run_id);
