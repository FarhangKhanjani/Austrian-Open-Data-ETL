-- One row per pipeline execution. Written at start (status 'running') and updated at
-- the end, so a crashed run is still visible as 'running' with no finished_at.
CREATE TABLE IF NOT EXISTS ops.pipeline_run (
    run_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    status        text        NOT NULL DEFAULT 'running',
    mode          text        NOT NULL,
    window_start  date,
    window_end    date,
    rows_read     integer     NOT NULL DEFAULT 0,
    rows_loaded   integer     NOT NULL DEFAULT 0,
    rows_rejected integer     NOT NULL DEFAULT 0,
    triggered_by  text        NOT NULL DEFAULT 'local',
    git_sha       text,
    error_message text,
    CONSTRAINT pipeline_run_status_chk
        CHECK (status IN ('running', 'success', 'partial', 'failed')),
    CONSTRAINT pipeline_run_mode_chk
        CHECK (mode IN ('daily', 'backfill')),
    CONSTRAINT pipeline_run_window_chk
        CHECK (window_start IS NULL OR window_end IS NULL OR window_start <= window_end),
    CONSTRAINT pipeline_run_finished_chk
        CHECK (finished_at IS NULL OR finished_at >= started_at),
    CONSTRAINT pipeline_run_counts_chk
        CHECK (rows_read >= 0 AND rows_loaded >= 0 AND rows_rejected >= 0)
);

-- Per-source breakdown of a run. Lets one source fail (status 'partial' on the run)
-- while the others still load, and feeds the GitHub Actions job summary.
CREATE TABLE IF NOT EXISTS ops.pipeline_run_source (
    run_id        bigint  NOT NULL REFERENCES ops.pipeline_run (run_id) ON DELETE CASCADE,
    source        text    NOT NULL,
    status        text    NOT NULL,
    rows_read     integer NOT NULL DEFAULT 0,
    rows_loaded   integer NOT NULL DEFAULT 0,
    rows_rejected integer NOT NULL DEFAULT 0,
    error_message text,
    PRIMARY KEY (run_id, source),
    CONSTRAINT pipeline_run_source_source_chk
        CHECK (source IN ('geosphere', 'econtrol', 'statistik_austria')),
    CONSTRAINT pipeline_run_source_status_chk
        CHECK (status IN ('success', 'failed', 'skipped'))
);
