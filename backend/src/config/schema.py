"""Additive schema steps applied at startup.

Base.metadata.create_all only creates missing tables; it never adds columns to an
existing one. Each step below is idempotent and non-destructive.
"""
import logging
from sqlalchemy import inspect,text

STEPS=[('runs','pairing_preview_id','ALTER TABLE runs ADD COLUMN pairing_preview_id INTEGER')]

def ensure_schema(engine):
    inspector=inspect(engine)
    tables=set(inspector.get_table_names())
    with engine.begin() as conn:
        for table,column,statement in STEPS:
            if table not in tables: continue
            if column in {c['name'] for c in inspector.get_columns(table)}: continue
            logging.warning('schema: adding %s.%s',table,column)
            conn.execute(text(statement))
