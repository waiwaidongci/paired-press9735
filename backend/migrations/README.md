Schema v1 is defined by the SQLAlchemy models and created at startup via Base.metadata.create_all. For this local small application no destructive migration is run. Back up named db-data before modifying schemas.

Schema v2 adds the pairing workflow: tables `process_readings`, `pairing_previews`, `active_pairings` and the column `runs.pairing_preview_id`. New tables come from `create_all`; the added column is applied by `src/config/schema.py:ensure_schema`, an idempotent `ALTER TABLE ... ADD COLUMN` run at startup, so an existing v1 volume keeps working. No data is rewritten.
