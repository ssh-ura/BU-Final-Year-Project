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

### `migrate_names.py`

Adds `first_name` and `last_name` columns to the `user` table and fills them for existing users by copying from each user's `FactFind` row where one exists.

**Run it when:**

- You've cloned the repo and you have a pre-existing `instance/mortgage_hive.db` from before names were taken at registration.
- You've restored `instance/mortgage_hive.db` from a backup before that change.

**You do NOT need to run it when:**

- You've deleted `instance/mortgage_hive.db` the next app start runs `db.create_all()` and creates a fresh table with both columns already present.
- You're continuing to develop on the existing dev DB the script has already been run against it.

Users with no fact-find (or whose fact-find has no name) are left blank; `User.display_name()` falls back to email until they update.

```
./venv/bin/python migrate_names.py
```

## Run the demo

`seed_demo.py` creates one adviser and four clients spread across the case-stage lifecycle, plus five mortgages spanning every renewal alert band and a believable audit trail. It only touches rows whose email matches `demo_*@example.com`, so any real dev users are left alone.

To recreate the demo environment:

1. Start the app once so `db.create_all()` runs:

   ```
   flask --app main run
   ```

   Press Ctrl+C once you see `Running on http://...`.

2. Seed the demo data:

   ```
   ./venv/bin/python seed_demo.py --reset
   ```

   Re-running without `--reset` is idempotent — already-seeded users are left in place.

3. Re-run the app:

   ```
   flask --app main run
   ```

Credentials (all use password `demo1234`):

| Role | Email | Lands on |
|---|---|---|
| Adviser | `demo_adviser@example.com` | Dashboard with the four demo clients |
| Client (no fact-find) | `demo_client_new@example.com` | Step 1 — Complete fact-find |
| Client (no docs) | `demo_client_docs@example.com` | Step 2 — Upload documents |
| Client (awaiting e-sign) | `demo_client_esign@example.com` | Step 3 — Sign documents |
| Client (recommendation issued) | `demo_client_review@example.com` | Step 4 — Adviser progress, 5 mortgages |
