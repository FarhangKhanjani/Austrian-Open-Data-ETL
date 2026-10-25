-- Layers of the warehouse. Each layer has one job:
--   raw      payloads exactly as received from the sources (traceability, replay)
--   staging  typed, cleaned, validated rows with primary keys
--   mart     analytics-ready views for Power BI (Milestone 4)
--   ops      pipeline bookkeeping: runs and quarantined rows
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS ops;
