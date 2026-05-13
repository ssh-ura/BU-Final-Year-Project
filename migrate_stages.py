"""One-shot migration: add case_stage + stage_updated_at columns and backfill.

Run once after pulling the workflow feature:

    python migrate_stages.py
"""
from datetime import datetime, timezone

from sqlalchemy import inspect, text

from main import app
from models import User, db
from workflow import derive_initial_stage


def _ensure_column(table, column, ddl):
    inspector = inspect(db.engine)
    existing = set()
    for column_info in inspector.get_columns(table):
        existing.add(column_info["name"])
    if column in existing:
        print(f"  - {table}.{column} already exists, skipping ALTER")
        return
    print(f"  + adding {table}.{column}")
    with db.engine.begin() as conn:
        conn.execute(text(ddl))


def run():
    with app.app_context():
        print("Ensuring columns exist...")
        _ensure_column("user", "case_stage", "ALTER TABLE user ADD COLUMN case_stage VARCHAR(40)")
        _ensure_column("user", "stage_updated_at", "ALTER TABLE user ADD COLUMN stage_updated_at DATETIME")

        print("Backfilling case_stage for existing clients...")
        clients = User.query.filter_by(role="client").all()
        now = datetime.now(timezone.utc)
        updated = 0
        for user in clients:
            if user.case_stage:
                continue
            user.case_stage = derive_initial_stage(user)
            user.stage_updated_at = now
            updated += 1
            print(f"  - {user.email} -> {user.case_stage}")
        db.session.commit()
        print(f"Done. {updated} client(s) backfilled, {len(clients) - updated} already had a stage.")


if __name__ == "__main__":
    run()
