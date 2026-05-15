from sqlalchemy import inspect, text

from main import app
from models import User, FactFind, db


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
        _ensure_column("user", "first_name", "ALTER TABLE user ADD COLUMN first_name VARCHAR(80)")
        _ensure_column("user", "last_name", "ALTER TABLE user ADD COLUMN last_name VARCHAR(80)")

        print("Backfilling names from FactFind where available...")
        users = User.query.all()
        backfilled = 0
        skipped = 0
        for user in users:
            if user.first_name and user.last_name:
                continue
            fact_find = FactFind.query.filter_by(user_id=user.id).first()
            if fact_find is None or not fact_find.first_name or not fact_find.last_name:
                print(f"  - {user.email}: no fact-find name available, leaving blank")
                skipped += 1
                continue
            user.first_name = fact_find.first_name
            user.last_name = fact_find.last_name
            print(f"  + {user.email} -> {user.first_name} {user.last_name}")
            backfilled += 1
        db.session.commit()
        print(f"Done. {backfilled} backfilled, {skipped} left blank.")


if __name__ == "__main__":
    run()
