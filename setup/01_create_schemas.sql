-- Run once in the Databricks SQL Editor (left sidebar -> SQL Editor).
-- If your catalog is not called "workspace", change it here and in databricks.yml.
USE CATALOG workspace;

CREATE SCHEMA IF NOT EXISTS bronze COMMENT 'Raw data exactly as received';
CREATE SCHEMA IF NOT EXISTS silver COMMENT 'Cleaned, typed, tested';
CREATE SCHEMA IF NOT EXISTS gold   COMMENT 'Facts, dimensions and metrics for dashboards and the agent';

-- A Volume is a folder for files (not tables). Raw snapshots land here.
CREATE VOLUME IF NOT EXISTS bronze.raw COMMENT 'Raw files: Google Sheet JSON snapshots, uploads';

SHOW SCHEMAS;
