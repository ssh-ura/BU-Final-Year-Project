import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone

from main import app
from models import (
    db,
    User,
    FactFind,
    Document,
    Mortgage,
    AuditEvent,
    DOCUMENT_CATEGORIES,
)
import storage


PASSWORD = "demo1234"
DEMO_EMAIL_PREFIX = "demo_"

DOC_ORIGINAL_FILENAMES = {
    "id": "passport.pdf",
    "proof_of_address": "council_tax_letter.pdf",
    "payslip": "payslip_2026_04.pdf",
    "bank_statement": "bank_statement_2026_04.pdf",
}

PLACEHOLDER_BYTES = b"Demo placeholder document. Not a real PDF.\n"
PLACEHOLDER_SHA256 = hashlib.sha256(PLACEHOLDER_BYTES).hexdigest()

MORTGAGE_FIXTURES = [
    # lender,      product,            rate_type,  rate_pct,  days_offset, balance,   monthly
    ("Halifax",    "2-Year Fixed",     "Fixed",    4.20,      -15,         178000.00, 1080.00),
    ("Nationwide", "3-Year Fixed",     "Fixed",    4.80,       20,         210000.00, 1320.00),
    ("HSBC",       "5-Year Fixed",     "Fixed",    4.50,       75,         156000.00, 970.00),
    ("Barclays",   "2-Year Tracker",   "Tracker",  5.25,      150,         132000.00, 850.00),
    ("Santander",  "5-Year Fixed",     "Fixed",    4.10,      300,         196000.00, 1180.00),
]


def write_event(event_type, *, when, user_id=None, target_type=None, target_id=None, **details):
    """Insert an AuditEvent row with an explicit timestamp (no request context)."""
    event = AuditEvent(
        user_id=user_id,
        event_type=event_type,
        target_type=target_type,
        target_id=target_id,
        ip_address=None,
        user_agent=None,
        metadata_json=json.dumps(details) if details else None,
        created_at=when,
    )
    db.session.add(event)


def reset():
    """Delete every row owned by a demo_*@example.com user."""
    users = User.query.filter(User.email.like(f"{DEMO_EMAIL_PREFIX}%@example.com")).all()
    if not users:
        print("Reset: no demo_* users found.")
        return

    user_ids = []
    for user in users:
        user_ids.append(user.id)

    print(f"Reset: deleting {len(user_ids)} demo user(s) + their data...")

    Document.query.filter(Document.user_id.in_(user_ids)).delete(synchronize_session=False)
    FactFind.query.filter(FactFind.user_id.in_(user_ids)).delete(synchronize_session=False)
    Mortgage.query.filter(Mortgage.user_id.in_(user_ids)).delete(synchronize_session=False)
    AuditEvent.query.filter(
        (AuditEvent.user_id.in_(user_ids))
        | ((AuditEvent.target_type == "user") & (AuditEvent.target_id.in_(user_ids)))
    ).delete(synchronize_session=False)

    # Also clean any user_login_failed rows whose attempted_email is a demo address
    failed_events = AuditEvent.query.filter_by(event_type="user_login_failed").all()
    failed_to_delete = []
    for failed_event in failed_events:
        if not failed_event.metadata_json:
            continue
        try:
            metadata = json.loads(failed_event.metadata_json)
        except ValueError:
            continue
        attempted = metadata.get("attempted_email", "")
        if attempted.startswith(DEMO_EMAIL_PREFIX):
            failed_to_delete.append(failed_event.id)
    if failed_to_delete:
        AuditEvent.query.filter(AuditEvent.id.in_(failed_to_delete)).delete(synchronize_session=False)

    User.query.filter(User.id.in_(user_ids)).delete(synchronize_session=False)

    # Best-effort: delete the demo placeholder files on disk.
    for user_id in user_ids:
        user_dir = storage._user_dir(user_id)
        if user_dir.exists():
            for path in user_dir.glob("demo_*"):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    db.session.commit()
    print(f"  - deleted {len(user_ids)} user(s) + child rows + placeholder files")


def make_user(email, role, case_stage, when):
    """Create a User and write user_registered. Returns (user, was_created)."""
    existing = User.query.filter_by(email=email).first()
    if existing is not None:
        print(f"  - {email}: already exists, skipping")
        return existing, False

    user = User(email=email, role=role)
    user.set_password(PASSWORD)
    if role == "client":
        user.case_stage = case_stage
        user.stage_updated_at = when
    user.created_at = when
    db.session.add(user)
    db.session.flush()
    write_event(
        "user_registered",
        when=when,
        user_id=user.id,
        target_type="user",
        target_id=user.id,
        role=role,
    )
    print(f"  + {email} (role={role}, case_stage={case_stage})")
    return user, True


def make_fact_find(user, when):
    fact_find = FactFind(
        user_id=user.id,
        first_name="Demo",
        last_name=user.email.split("@")[0].replace(DEMO_EMAIL_PREFIX, "").replace("_", " ").title(),
        date_of_birth=datetime(1990, 1, 1).date(),
        phone_number="07000000000",
        current_address="1 Demo Lane, London, EC1A 1AA",
        employment_status="Employed",
        employer_name="ACME Ltd",
        job_title="Software Engineer",
        start_date=datetime(2020, 6, 1).date(),
        contract_type="Permanent",
        annual_salary=55000.0,
        additional_income=2000.0,
        monthly_outgoings=1200.0,
        property_type="Flat",
        purchase_price=325000.0,
        deposit_amount=32500.0,
        mortgage_type="Repayment",
        current_step=4,
        is_complete=True,
        submitted_at=when,
    )
    db.session.add(fact_find)
    db.session.flush()
    write_event(
        "fact_find_submitted",
        when=when,
        user_id=user.id,
        target_type="fact_find",
        target_id=fact_find.id,
    )
    return fact_find


def make_documents(user, when):
    """Create one Document row + a tiny placeholder file per category."""
    user_dir = storage._user_dir(user.id)
    user_dir.mkdir(parents=True, exist_ok=True)

    for offset, (category_key, _label, _help) in enumerate(DOCUMENT_CATEGORIES):
        stored_filename = f"demo_{user.id}_{category_key}.txt"
        target_path = user_dir / stored_filename
        if not target_path.exists():
            target_path.write_bytes(PLACEHOLDER_BYTES)

        uploaded_at = when + timedelta(minutes=offset)
        document = Document(
            user_id=user.id,
            category=category_key,
            original_filename=DOC_ORIGINAL_FILENAMES.get(category_key, f"{category_key}.pdf"),
            stored_filename=stored_filename,
            mime_type="application/pdf",
            size_bytes=len(PLACEHOLDER_BYTES),
            sha256=PLACEHOLDER_SHA256,
            uploaded_at=uploaded_at,
        )
        db.session.add(document)
        db.session.flush()
        write_event(
            "document_uploaded",
            when=uploaded_at,
            user_id=user.id,
            target_type="document",
            target_id=document.id,
            category=category_key,
            original_filename=document.original_filename,
            sha256=PLACEHOLDER_SHA256,
            size_bytes=len(PLACEHOLDER_BYTES),
        )


def make_mortgages(user, base_when):
    today = datetime.now(timezone.utc).date()
    for offset, fixture in enumerate(MORTGAGE_FIXTURES):
        lender, product, rate_type, rate_pct, days_offset, balance, monthly = fixture
        deal_end_date = today + timedelta(days=days_offset)
        created_at = base_when + timedelta(hours=offset)
        mortgage = Mortgage(
            user_id=user.id,
            lender_name=lender,
            product_name=product,
            rate_type=rate_type,
            rate_pct=rate_pct,
            term_years=25,
            monthly_payment=monthly,
            balance_remaining=balance,
            deal_end_date=deal_end_date,
            notes=None,
            created_at=created_at,
            updated_at=created_at,
        )
        db.session.add(mortgage)
        db.session.flush()
        write_event(
            "mortgage_added",
            when=created_at,
            user_id=user.id,
            target_type="mortgage",
            target_id=mortgage.id,
            client_user_id=user.id,
            lender_name=lender,
            rate_type=rate_type,
            deal_end_date=deal_end_date.isoformat(),
        )


def stage_advance_event(user, *, from_stage, to_stage, actor, when):
    write_event(
        "case_stage_advanced",
        when=when,
        user_id=actor.id,
        target_type="user",
        target_id=user.id,
        from_stage=from_stage,
        to_stage=to_stage,
        actor_role=actor.role,
    )


def seed():
    now = datetime.now(timezone.utc)
    print("Seeding demo data...")

    # 1. Adviser — registered earliest.
    adviser, _ = make_user(
        "demo_adviser@example.com",
        "adviser",
        case_stage=None,
        when=now - timedelta(days=15),
    )

    # 2. demo_client_new — just registered, no fact-find yet.
    make_user(
        "demo_client_new@example.com",
        "client",
        case_stage="fact_find_in_progress",
        when=now - timedelta(hours=2),
    )

    # 3. demo_client_docs — fact-find done, no documents yet.
    user, created = make_user(
        "demo_client_docs@example.com",
        "client",
        case_stage="documents_pending",
        when=now - timedelta(days=3),
    )
    if created:
        make_fact_find(user, now - timedelta(days=3) + timedelta(minutes=30))
        stage_advance_event(
            user,
            from_stage="fact_find_in_progress",
            to_stage="documents_pending",
            actor=user,
            when=now - timedelta(days=3) + timedelta(minutes=31),
        )

    # 4. demo_client_esign — fact-find + all 4 documents, awaiting e-sign.
    user, created = make_user(
        "demo_client_esign@example.com",
        "client",
        case_stage="awaiting_esign",
        when=now - timedelta(days=5),
    )
    if created:
        ff_when = now - timedelta(days=5) + timedelta(minutes=30)
        make_fact_find(user, ff_when)
        stage_advance_event(
            user,
            from_stage="fact_find_in_progress",
            to_stage="documents_pending",
            actor=user,
            when=ff_when + timedelta(seconds=1),
        )
        docs_when = now - timedelta(days=5) + timedelta(hours=2)
        make_documents(user, docs_when)
        write_event(
            "documents_complete",
            when=docs_when + timedelta(minutes=10),
            user_id=user.id,
            target_type="user",
            target_id=user.id,
        )
        stage_advance_event(
            user,
            from_stage="documents_pending",
            to_stage="awaiting_esign",
            actor=user,
            when=docs_when + timedelta(minutes=11),
        )

    # 5. demo_client_review — full journey + adviser advanced to recommendation_issued.
    user, created = make_user(
        "demo_client_review@example.com",
        "client",
        case_stage="recommendation_issued",
        when=now - timedelta(days=10),
    )
    if created:
        user.esigned = True

        ff_when = now - timedelta(days=10) + timedelta(minutes=30)
        make_fact_find(user, ff_when)
        stage_advance_event(
            user,
            from_stage="fact_find_in_progress",
            to_stage="documents_pending",
            actor=user,
            when=ff_when + timedelta(seconds=1),
        )

        docs_when = now - timedelta(days=10) + timedelta(hours=2)
        make_documents(user, docs_when)
        write_event(
            "documents_complete",
            when=docs_when + timedelta(minutes=10),
            user_id=user.id,
            target_type="user",
            target_id=user.id,
        )
        stage_advance_event(
            user,
            from_stage="documents_pending",
            to_stage="awaiting_esign",
            actor=user,
            when=docs_when + timedelta(minutes=11),
        )

        esign_when = now - timedelta(days=9)
        write_event(
            "client_esigned",
            when=esign_when,
            user_id=user.id,
            target_type="user",
            target_id=user.id,
            typed_name="Demo Client Review",
            documents=["Terms of Business", "Privacy Notice", "Fee Agreement"],
        )
        stage_advance_event(
            user,
            from_stage="awaiting_esign",
            to_stage="under_review",
            actor=user,
            when=esign_when + timedelta(seconds=1),
        )

        adviser_view_when = now - timedelta(days=8)
        write_event(
            "adviser_viewed_case",
            when=adviser_view_when,
            user_id=adviser.id,
            target_type="user",
            target_id=user.id,
        )
        rec_when = now - timedelta(days=8) + timedelta(minutes=20)
        stage_advance_event(
            user,
            from_stage="under_review",
            to_stage="recommendation_issued",
            actor=adviser,
            when=rec_when,
        )
        user.stage_updated_at = rec_when

        make_mortgages(user, now - timedelta(days=7))

    db.session.commit()
    print("Seeding complete.")


def main():
    parser = argparse.ArgumentParser(
        description="Seed demo data for screenshots, supervisor demos, and SUS user-study sessions."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete all existing demo_*@example.com users and their data before seeding.",
    )
    args = parser.parse_args()

    with app.app_context():
        if args.reset:
            reset()
        seed()


if __name__ == "__main__":
    main()
