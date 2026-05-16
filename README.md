# Mortgage Hive Client Portal

## Overview

A unified client portal that replaces a manual, paper-based client journey with a single digital platform. Clients complete their fact-find online, upload documents, and e-sign forms. Advisers manage cases through an automated workflow with a full audit trail and a renewal-alert dashboard.

## Features

The Flask app conists of six components:

- **Digital Forms**: Four-step online fact-find which includes personal, employment, income and property. Terms of Business, Privacy Notice and Fee Agreement are also provided at the e-signature.
- **Client Portal**: Status view showing fact-find progress, document checklist, e-sign status and current case stage.
- **Automated Workflow**: Case stages advance automatically as clients complete each step; advisers can manually advance cases if needed.
- **Audit Log**: Every user action is logged in the Audit log page with actor, target, IP and user-agent.
- **Renewal Alerts**: Lists mortgages by alert band (expired / urgent / soon / upcoming / future) based on the deals end date.
- **Adviser Dashboard**: Lists all clients which are filterable by case stage, with fact-find %, docs uploaded and e-sign status at the dashboard view.

## Requirements

- Python 3.11+
- Flask 3.1
- Flask-Login
- Flask-SQLAlchemy
- SQLite
- python-dotenv

## Project structure

```
main.py            Flask app, routes, auth
models.py          SQLAlchemy models (User, FactFind, Document, Mortgage, AuditEvent)
workflow.py        Case-stage definitions and auto-advance logic
renewals.py        Mortgage renewal alert banding
audit.py           Audit-event helper
storage.py         Document upload storage (hashing, mime, size)
seed_demo.py       Demo accounts (one adviser + four clients across the lifecycle)
migrate_stages.py  data migration for stages (see below)
migrate_names.py   data migration for names (see below)
templates/         Jinja templates (client + adviser views)
static/            CSS and JS
instance/          SQLite DB
```

## Usage

1. Create and activate a virtual environment, then install dependencies:

   ```
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. Create a `.env` file in the project root:

   ```
   SECRET_KEY=<random-string>
   DATABASE_URL=sqlite:///mortgage_hive.db
   ADVISER_CODE=<invite-code>
   ```

   `ADVISER_CODE` is the invite code required to register as an adviser. Without it, the registration form has no way to grant the adviser role and all sign-ups will be clients. `DATABASE_URL` is optional, it defaults to `sqlite:///mortgage_hive.db`.

3. Run the app — the first start creates `instance/mortgage_hive.db` via `db.create_all()`:

   ```
   flask --app main run
   ```

4. (Optional) Seed demo data — see [the demo](#run-the-demo) below.

## Case-stage lifecycle

Cases move through seven stages:

```
fact_find_in_progress → documents_pending → awaiting_esign → under_review
  → recommendation_issued → application_submitted → completed
```

The first three advance automatically as the client completes each step; from `under_review` onward, the adviser advances the stage manually from the case page.

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

   Re-running without `--reset` is safe to do multiple times as already-seeded users are left in place.

3. Re-run the app:

   ```
   flask --app main run
   ```

Credentials (all use password `demo1234`):

| Role | Email | Lands on |
|---|---|---|
| Adviser | `demo_adviser@example.com` | Dashboard with the four demo clients |
| Client (no fact-find) | `demo_client_new@example.com` | Step 1 Complete fact-find |
| Client (no docs) | `demo_client_docs@example.com` | Step 2 Upload documents |
| Client (awaiting e-sign) | `demo_client_esign@example.com` | Step 3  Sign documents |
| Client (recommendation issued) | `demo_client_review@example.com` | Step 4 Adviser progress, 5 mortgages |
