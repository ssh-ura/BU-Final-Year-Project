# BU-Final-Year-Project

## Database migrations

### `migrate_stages.py`

Adds `case_stage` and `stage_updated_at` columns to the `user` table and backfills `case_stage` for existing clients (derived from their fact-find / docs / e-sign state).

**Run it when:**

- You've cloned the repo onto a new machine and you have a pre-existing `instance/mortgage_hive.db` from before the workflow feature was added.
- You've restored `instance/mortgage_hive.db` from a backup taken before the workflow feature.
- You manually created a `User` row with `role="client"` outside the normal `/register` flow.

**You do NOT need to run it when:**

- You've deleted `instance/mortgage_hive.db` the next app start runs `db.create_all()` and creates a fresh table with all current columns (including `case_stage` and `stage_updated_at`). No `User` rows exist, so the backfill loop has nothing to do; new clients pick up their stage on `/register`.
- A fresh database is being created by `db.create_all()` the new columns are part of `models.py` so they're added automatically.
- You're continuing to develop on the existing dev DB the script has already been run against it.

The script is safe to run repeatedly: it checks for the columns before `ALTER TABLE`ing, and skips any client who already has a `case_stage`, re-running is safe but does nothing.

```
./venv/bin/python migrate_stages.py
```
