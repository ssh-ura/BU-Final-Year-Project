import json
import os
import re
from datetime import date, datetime, timezone
from functools import wraps
from flask import Flask, render_template, redirect, url_for, request, flash, abort, send_from_directory
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv
from models import (
    db,
    User,
    FactFind,
    Document,
    Mortgage,
    AuditEvent,
    DOCUMENT_CATEGORIES,
    DOCUMENT_CATEGORY_KEYS,
    CASE_STAGES,
    STAGE_LABELS,
    ADVISER_DRIVEN_STAGES,
    ALLOWED_TRANSITIONS,
    MORTGAGE_RATE_TYPES,
)
import audit
import renewals
import storage
import workflow

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///mortgage_hive.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "error"

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

with app.app_context():
    db.create_all()


# Roles

def adviser_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "adviser":
            abort(403)
        return f(*args, **kwargs)
    return decorated


def client_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "client":
            abort(403)
        return f(*args, **kwargs)
    return decorated


# Routes

@app.route("/")
def index():
    return redirect(url_for("login"))


def role_home():
    if current_user.role == "adviser":
        return url_for("dashboard")
    return url_for("portal")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(role_home())
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or role_home())
        flash("Invalid email or password.", "error")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(role_home())
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        invite_code = request.form.get("invite_code", "").strip()

        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            flash("Please enter a valid email address.", "error")
            return render_template("register.html")
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("register.html")

        adviser_code = os.getenv("ADVISER_CODE", "")
        role = "adviser" if adviser_code and invite_code == adviser_code else "client"
        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
        else:
            user = User(email=email, role=role)
            user.set_password(password)
            if role == "client":
                user.case_stage = "fact_find_in_progress"
                user.stage_updated_at = datetime.now(timezone.utc)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(role_home())
    return render_template("register.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


ADVISER_STAGE_ORDER = (
    "under_review",
    "recommendation_issued",
    "application_submitted",
    "completed",
)

CLIENT_STAGE_MESSAGES = {
    "under_review": "Your adviser is reviewing your case and will be in touch with their recommendation shortly.",
    "recommendation_issued": "Your adviser has issued a recommendation. They will contact you to discuss further.",
    "application_submitted": "Your application has been submitted to the lender. We're now waiting on their decision.",
    "completed": "Your case is complete. Thank you for choosing The Mortgage Hive.",
}


@app.route("/portal")
@login_required
@client_required
def portal():
    fact_find = FactFind.query.filter_by(user_id=current_user.id).first()
    fact_find_done = fact_find is not None and fact_find.is_complete
    uploaded_categories = set()
    for document in Document.query.filter_by(user_id=current_user.id).all():
        uploaded_categories.add(document.category)
    docs_done = DOCUMENT_CATEGORY_KEYS.issubset(uploaded_categories)
    esign_done = current_user.esigned

    if esign_done:
        step = 4
    elif docs_done:
        step = 3
    elif fact_find_done:
        step = 2
    else:
        step = 1

    current_stage = current_user.case_stage
    if current_stage in ADVISER_STAGE_ORDER:
        current_stage_index = ADVISER_STAGE_ORDER.index(current_stage)
    else:
        current_stage_index = -1

    adviser_stages = []
    for index, stage_key in enumerate(ADVISER_STAGE_ORDER):
        if index < current_stage_index:
            state = "done"
        elif index == current_stage_index:
            state = "current"
        else:
            state = "future"
        adviser_stages.append(
            {
                "key": stage_key,
                "label": STAGE_LABELS[stage_key],
                "state": state,
            }
        )

    if current_stage in CLIENT_STAGE_MESSAGES:
        current_stage_message = CLIENT_STAGE_MESSAGES[current_stage]
    else:
        current_stage_message = "Your case is awaiting adviser review."

    today = date.today()
    mortgage_rows = (
        Mortgage.query.filter_by(user_id=current_user.id)
        .order_by(Mortgage.deal_end_date.asc())
        .all()
    )
    mortgages = []
    for mortgage in mortgage_rows:
        mortgages.append(renewals.mortgage_summary(mortgage, today))

    return render_template(
        "client-portal.html",
        step=step,
        adviser_stages=adviser_stages,
        current_stage_message=current_stage_message,
        mortgages=mortgages,
    )


EMPLOYMENT_STATUSES = {"Employed", "Self-employed", "Contractor", "Retired"}
CONTRACT_TYPES = {"Permanent", "Fixed-term", "Temporary", "Zero-hours"}
PROPERTY_TYPES = {"House", "Flat", "Bungalow"}
MORTGAGE_TYPES = {"Repayment", "Interest only"}


def _parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_money(value):
    amount = float(value)
    if amount < 0:
        raise ValueError
    return amount


def _apply_step(fact_find, step, form):
    def get(field):
        return form.get(field, "").strip()

    if step == 1:
        first_name = get("first_name")
        last_name = get("last_name")
        dob_raw = get("date_of_birth")
        phone = get("phone_number")
        address = get("current_address")
        if not all([first_name, last_name, dob_raw, phone, address]):
            return "Please fill in every field before continuing."
        try:
            dob = _parse_date(dob_raw)
        except ValueError:
            return "Please enter a valid date of birth."
        fact_find.first_name = first_name
        fact_find.last_name = last_name
        fact_find.date_of_birth = dob
        fact_find.phone_number = phone
        fact_find.current_address = address
        return None

    if step == 2:
        status = get("employment_status")
        employer = get("employer_name")
        job_title = get("job_title")
        start_raw = get("start_date")
        contract = get("contract_type")
        if status not in EMPLOYMENT_STATUSES:
            return "Please select an employment status."
        if not all([employer, job_title, start_raw]):
            return "Please fill in every field before continuing."
        if contract not in CONTRACT_TYPES:
            return "Please choose a contract type."
        try:
            start = _parse_date(start_raw)
        except ValueError:
            return "Please enter a valid start date."
        fact_find.employment_status = status
        fact_find.employer_name = employer
        fact_find.job_title = job_title
        fact_find.start_date = start
        fact_find.contract_type = contract
        return None

    if step == 3:
        try:
            salary = _parse_money(get("annual_salary"))
            additional = _parse_money(get("additional_income") or "0")
            outgoings = _parse_money(get("monthly_outgoings"))
        except ValueError:
            return "Please enter valid, non-negative figures for income and outgoings."
        fact_find.annual_salary = salary
        fact_find.additional_income = additional
        fact_find.monthly_outgoings = outgoings
        return None

    if step == 4:
        prop = get("property_type")
        mortgage = get("mortgage_type")
        if prop not in PROPERTY_TYPES:
            return "Please choose a property type."
        if mortgage not in MORTGAGE_TYPES:
            return "Please choose a mortgage type."
        try:
            price = _parse_money(get("purchase_price"))
            deposit = _parse_money(get("deposit_amount"))
        except ValueError:
            return "Please enter valid, non-negative figures for price and deposit."
        if deposit > price:
            return "Deposit cannot exceed the purchase price."
        fact_find.property_type = prop
        fact_find.purchase_price = price
        fact_find.deposit_amount = deposit
        fact_find.mortgage_type = mortgage
        return None

    return "Invalid step"


@app.route("/fact-find", methods=["GET", "POST"])
@login_required
@client_required
def fact_find():
    fact_find = FactFind.query.filter_by(user_id=current_user.id).first()

    if request.method == "GET" and "step" not in request.args:
        resume = fact_find.current_step if fact_find and not fact_find.is_complete else 1
        return redirect(url_for("fact_find", step=resume))

    try:
        step = int(request.args.get("step", 1))
    except ValueError:
        step = 1
    step = max(1, min(4, step))

    if request.method == "POST":
        if fact_find is None:
            fact_find = FactFind(user_id=current_user.id, current_step=1)
            db.session.add(fact_find)

        error = _apply_step(fact_find=fact_find, step=step, form=request.form)
        if error:
            flash(error, "error")
            return render_template("digital-forms.html", step=step, fact_find=fact_find)

        if step == 4:
            fact_find.is_complete = True
            fact_find.current_step = 4
            fact_find.submitted_at = datetime.now(timezone.utc)
            db.session.flush()
            audit.log_event(
                "fact_find_submitted",
                user_id=current_user.id,
                target_type="fact_find",
                target_id=fact_find.id,
            )
            workflow.auto_advance_if_eligible(
                current_user, "documents_pending", actor=current_user
            )
            db.session.commit()
            return redirect(url_for("portal"))

        fact_find.current_step = max(fact_find.current_step or 1, step + 1)
        db.session.commit()
        return redirect(url_for("fact_find", step=step + 1))

    return render_template("digital-forms.html", step=step, fact_find=fact_find)


@app.route("/upload-documents")
@login_required
@client_required
def upload_documents():
    documents = Document.query.filter_by(user_id=current_user.id).order_by(Document.uploaded_at.desc()).all()
    documents_by_category = {}
    for category_key, _label, _help in DOCUMENT_CATEGORIES:
        documents_by_category[category_key] = []
    for doc in documents:
        if doc.category not in documents_by_category:
            documents_by_category[doc.category] = []
        documents_by_category[doc.category].append(doc)
    return render_template(
        "upload-documents.html",
        categories=DOCUMENT_CATEGORIES,
        documents_by_category=documents_by_category,
        is_locked=current_user.esigned,
        max_size_mb=storage.MAX_SIZE_MB,
        allowed_extensions=sorted(storage.ALLOWED_EXTENSIONS),
    )


@app.route("/upload-documents/<category>", methods=["POST"])
@login_required
@client_required
def upload_document(category):
    if category not in DOCUMENT_CATEGORY_KEYS:
        abort(404)
    if current_user.esigned:
        flash("Your case is under adviser review. Documents are read-only.", "error")
        return redirect(url_for("upload_documents"))

    file_storage = request.files.get("file")
    try:
        meta = storage.save_upload(file_storage, current_user.id)
    except storage.UploadError as exc:
        flash(str(exc), "error")
        return redirect(url_for("upload_documents"))

    document = Document(
        user_id=current_user.id,
        category=category,
        original_filename=meta["original_filename"],
        stored_filename=meta["stored_filename"],
        mime_type=meta["mime_type"],
        size_bytes=meta["size_bytes"],
        sha256=meta["sha256"],
    )
    db.session.add(document)
    db.session.flush()
    audit.log_event(
        "document_uploaded",
        user_id=current_user.id,
        target_type="document",
        target_id=document.id,
        category=category,
        original_filename=meta["original_filename"],
        sha256=meta["sha256"],
        size_bytes=meta["size_bytes"],
    )

    uploaded_categories = set()
    for document_row in Document.query.filter_by(user_id=current_user.id).all():
        uploaded_categories.add(document_row.category)
    if DOCUMENT_CATEGORY_KEYS.issubset(uploaded_categories):
        audit.log_event(
            "documents_complete",
            user_id=current_user.id,
            target_type="user",
            target_id=current_user.id,
        )
        workflow.auto_advance_if_eligible(
            current_user, "awaiting_esign", actor=current_user
        )

    db.session.commit()
    flash("Document uploaded.", "success")
    return redirect(url_for("upload_documents"))


@app.route("/upload-documents/<int:doc_id>/delete", methods=["POST"])
@login_required
@client_required
def delete_document(doc_id):
    document = db.session.get(Document, doc_id)
    if document is None or document.user_id != current_user.id:
        abort(404)
    if current_user.esigned:
        flash("Your case is under adviser review. Documents are read-only.", "error")
        return redirect(url_for("upload_documents"))

    storage.delete_upload(document.user_id, document.stored_filename)
    audit.log_event(
        "document_deleted",
        user_id=current_user.id,
        target_type="document",
        target_id=document.id,
        category=document.category,
        original_filename=document.original_filename,
        sha256=document.sha256,
    )
    db.session.delete(document)
    db.session.commit()
    flash("Document deleted.", "success")
    return redirect(url_for("upload_documents"))


@app.route("/upload-documents/<int:doc_id>/download")
@login_required
def download_document(doc_id):
    document = db.session.get(Document, doc_id)
    if document is None:
        abort(404)
    is_owner = current_user.id == document.user_id
    is_adviser = current_user.role == "adviser"
    if not (is_owner or is_adviser):
        abort(403)

    audit.log_event(
        "document_downloaded",
        user_id=current_user.id,
        target_type="document",
        target_id=document.id,
        category=document.category,
        downloader_role=current_user.role,
    )
    db.session.commit()

    full_path = storage.upload_path(document.user_id, document.stored_filename)
    return send_from_directory(
        full_path.parent,
        document.stored_filename,
        as_attachment=True,
        download_name=document.original_filename,
    )


@app.route("/e-sign", methods=["GET", "POST"])
@login_required
@client_required
def esign():
    if current_user.esigned:
        flash("You have already signed your documents.", "success")
        return redirect(url_for("portal"))

    if request.method == "POST":
        tob_agreed = request.form.get("tob_agreed") == "on"
        privacy_agreed = request.form.get("privacy_agreed") == "on"
        fee_agreed = request.form.get("fee_agreed") == "on"
        binding_ack = request.form.get("legally_binding") == "on"
        signature = request.form.get("signature", "").strip()

        if not (tob_agreed and privacy_agreed and fee_agreed and binding_ack and signature):
            flash("Please tick every confirmation and type your full name.", "error")
            return render_template(
                "e-sign.html",
                signature=signature,
                tob_agreed=tob_agreed,
                privacy_agreed=privacy_agreed,
                fee_agreed=fee_agreed,
                legally_binding=binding_ack,
            )

        current_user.esigned = True
        audit.log_event(
            "client_esigned",
            user_id=current_user.id,
            target_type="user",
            target_id=current_user.id,
            typed_name=signature,
            documents=["Terms of Business", "Privacy Notice", "Fee Agreement"],
        )
        workflow.auto_advance_if_eligible(
            current_user, "under_review", actor=current_user
        )
        db.session.commit()
        flash("Documents signed. Your case is now under adviser review.", "success")
        return redirect(url_for("portal"))

    return render_template("e-sign.html")


FACT_FIND_STEP_FIELDS = {
    1: ("first_name", "last_name", "date_of_birth", "phone_number", "current_address"),
    2: ("employment_status", "employer_name", "job_title", "start_date", "contract_type"),
    3: ("annual_salary", "monthly_outgoings"),
    4: ("property_type", "purchase_price", "deposit_amount", "mortgage_type"),
}


def _fact_find_percent(fact_find):
    if fact_find is None:
        return 0
    if fact_find.is_complete:
        return 100
    total = 0
    for fields in FACT_FIND_STEP_FIELDS.values():
        total += len(fields)
    filled = 0
    for fields in FACT_FIND_STEP_FIELDS.values():
        for field in fields:
            value = getattr(fact_find, field, None)
            if value is not None and value != "":
                filled += 1
    return int(round(filled / total * 100))


def _case_summary(user):
    fact_find = FactFind.query.filter_by(user_id=user.id).first()
    uploaded_categories = set()
    for document in Document.query.filter_by(user_id=user.id).all():
        uploaded_categories.add(document.category)

    next_stage = ALLOWED_TRANSITIONS.get(user.case_stage)
    if next_stage is None:
        next_stage_label = None
        next_stage_is_adviser_driven = False
        can_advance = False
    else:
        next_stage_label = STAGE_LABELS.get(next_stage, next_stage)
        next_stage_is_adviser_driven = next_stage in ADVISER_DRIVEN_STAGES
        can_advance = workflow.can_advance(user, next_stage)

    return {
        "user": user,
        "stage": user.case_stage,
        "stage_label": STAGE_LABELS.get(user.case_stage, "—"),
        "fact_find_percent": _fact_find_percent(fact_find),
        "docs_uploaded": len(uploaded_categories & DOCUMENT_CATEGORY_KEYS),
        "docs_required": len(DOCUMENT_CATEGORY_KEYS),
        "esigned": user.esigned,
        "stage_updated_at": user.stage_updated_at,
        "next_stage": next_stage,
        "next_stage_label": next_stage_label,
        "next_stage_is_adviser_driven": next_stage_is_adviser_driven,
        "can_advance": can_advance,
    }


@app.route("/forms")
@login_required
@adviser_required
def forms():
    return render_template("adviser-forms.html")


@app.route("/workflow")
@login_required
@adviser_required
def workflow_view():
    clients = (
        User.query.filter_by(role="client")
        .order_by(User.stage_updated_at.desc().nullslast())
        .all()
    )
    summaries = []
    for client in clients:
        summaries.append(_case_summary(client))

    columns = []
    for stage in CASE_STAGES:
        cases_in_stage = []
        for summary in summaries:
            if summary["stage"] == stage:
                cases_in_stage.append(summary)
        columns.append(
            {
                "key": stage,
                "label": STAGE_LABELS[stage],
                "is_adviser_driven": stage in ADVISER_DRIVEN_STAGES,
                "cases": cases_in_stage,
            }
        )
    return render_template("automated-workflow.html", columns=columns)


@app.route("/audit-log")
@login_required
@adviser_required
def compliance():
    filter_user_id = request.args.get("user_id", type=int)
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    per_page = 50

    query = AuditEvent.query
    if filter_user_id is not None:
        query = query.filter(
            (AuditEvent.user_id == filter_user_id)
            | ((AuditEvent.target_type == "user") & (AuditEvent.target_id == filter_user_id))
            | ((AuditEvent.target_type == "fact_find") & (AuditEvent.user_id == filter_user_id))
            | ((AuditEvent.target_type == "document") & (AuditEvent.user_id == filter_user_id))
        )
    total = query.count()
    events = (
        query.order_by(AuditEvent.created_at.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
        .all()
    )

    actor_ids = set()
    for event in events:
        if event.user_id:
            actor_ids.add(event.user_id)

    actors = {}
    if actor_ids:
        for actor in User.query.filter(User.id.in_(actor_ids)).all():
            actors[actor.id] = actor

    rows = []
    for event in events:
        if event.metadata_json:
            try:
                details = json.loads(event.metadata_json)
            except ValueError:
                details = {"raw": event.metadata_json}
        else:
            details = {}

        if event.user_id in actors:
            actor_email = actors[event.user_id].email
        else:
            actor_email = "—"

        if event.ip_address:
            ip_address = event.ip_address
        else:
            ip_address = "—"

        if event.user_agent:
            user_agent_short = event.user_agent[:60]
        else:
            user_agent_short = "—"

        rows.append(
            {
                "id": event.id,
                "created_at": event.created_at,
                "event_type": event.event_type,
                "actor_email": actor_email,
                "target_type": event.target_type,
                "target_id": event.target_id,
                "ip_address": ip_address,
                "user_agent": user_agent_short,
                "details": details,
            }
        )

    if filter_user_id is not None:
        filter_user = db.session.get(User, filter_user_id)
    else:
        filter_user = None
    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "audit-log.html",
        rows=rows,
        page=page,
        total_pages=total_pages,
        total=total,
        filter_user_id=filter_user_id,
        filter_user=filter_user,
    )


def _parse_mortgage_form(form):
    """Parse and validate mortgage form input.

    Returns (data_dict, None) on success, or (None, error_message) on failure.
    """
    def get(field):
        return form.get(field, "").strip()

    lender_name = get("lender_name")
    rate_type = get("rate_type")
    deal_end_raw = get("deal_end_date")

    if not lender_name:
        return None, "Lender name is required."
    if rate_type not in MORTGAGE_RATE_TYPES:
        return None, "Please choose a valid rate type."
    if not deal_end_raw:
        return None, "Deal end date is required."

    try:
        deal_end_date = _parse_date(deal_end_raw)
    except ValueError:
        return None, "Please enter a valid deal end date."

    product_name = get("product_name") or None
    notes = get("notes") or None

    rate_pct = None
    term_years = None
    monthly_payment = None
    balance_remaining = None

    rate_pct_raw = get("rate_pct")
    if rate_pct_raw:
        try:
            rate_pct = float(rate_pct_raw)
            if rate_pct < 0:
                raise ValueError
        except ValueError:
            return None, "Rate percentage must be a non-negative number."

    term_years_raw = get("term_years")
    if term_years_raw:
        try:
            term_years = int(term_years_raw)
            if term_years < 0:
                raise ValueError
        except ValueError:
            return None, "Term must be a non-negative whole number of years."

    monthly_payment_raw = get("monthly_payment")
    if monthly_payment_raw:
        try:
            monthly_payment = _parse_money(monthly_payment_raw)
        except ValueError:
            return None, "Monthly payment must be a non-negative number."

    balance_raw = get("balance_remaining")
    if balance_raw:
        try:
            balance_remaining = _parse_money(balance_raw)
        except ValueError:
            return None, "Balance remaining must be a non-negative number."

    data = {
        "lender_name": lender_name,
        "product_name": product_name,
        "rate_type": rate_type,
        "rate_pct": rate_pct,
        "term_years": term_years,
        "monthly_payment": monthly_payment,
        "balance_remaining": balance_remaining,
        "deal_end_date": deal_end_date,
        "notes": notes,
    }
    return data, None


@app.route("/renewals")
@login_required
@adviser_required
def renewals_view():
    band_filter = request.args.get("band")
    if band_filter not in renewals.ALERT_BANDS:
        band_filter = None

    today = date.today()
    mortgages = (
        Mortgage.query.order_by(Mortgage.deal_end_date.asc()).all()
    )

    client_ids = set()
    for mortgage in mortgages:
        client_ids.add(mortgage.user_id)
    clients = {}
    if client_ids:
        for client in User.query.filter(User.id.in_(client_ids)).all():
            clients[client.id] = client

    rows = []
    for mortgage in mortgages:
        summary = renewals.mortgage_summary(mortgage, today)
        if band_filter is not None and summary["band"] != band_filter:
            continue
        summary["client"] = clients.get(mortgage.user_id)
        rows.append(summary)

    band_options = []
    for band in renewals.ALERT_BANDS:
        band_options.append((band, renewals.BAND_LABELS[band]))

    return render_template(
        "renewals.html",
        rows=rows,
        band_filter=band_filter,
        band_options=band_options,
        today=today,
    )


@app.route("/case/<int:user_id>/mortgage/add", methods=["GET", "POST"])
@login_required
@adviser_required
def mortgage_add(user_id):
    client = db.session.get(User, user_id)
    if client is None or client.role != "client":
        abort(404)

    if request.method == "POST":
        data, error = _parse_mortgage_form(request.form)
        if error:
            flash(error, "error")
            return render_template(
                "mortgage-form.html",
                client=client,
                mortgage=None,
                rate_types=MORTGAGE_RATE_TYPES,
                form_action=url_for("mortgage_add", user_id=client.id),
                back_url=url_for("case_detail", user_id=client.id),
            )

        mortgage = Mortgage(user_id=client.id, **data)
        db.session.add(mortgage)
        db.session.flush()
        audit.log_event(
            "mortgage_added",
            user_id=current_user.id,
            target_type="mortgage",
            target_id=mortgage.id,
            client_user_id=client.id,
            lender_name=data["lender_name"],
            rate_type=data["rate_type"],
            deal_end_date=data["deal_end_date"].isoformat(),
        )
        db.session.commit()
        flash("Mortgage added.", "success")
        return redirect(url_for("case_detail", user_id=client.id))

    return render_template(
        "mortgage-form.html",
        client=client,
        mortgage=None,
        rate_types=MORTGAGE_RATE_TYPES,
        form_action=url_for("mortgage_add", user_id=client.id),
        back_url=url_for("case_detail", user_id=client.id),
    )


@app.route("/mortgage/<int:mortgage_id>/edit", methods=["GET", "POST"])
@login_required
@adviser_required
def mortgage_edit(mortgage_id):
    mortgage = db.session.get(Mortgage, mortgage_id)
    if mortgage is None:
        abort(404)
    client = db.session.get(User, mortgage.user_id)

    if request.method == "POST":
        data, error = _parse_mortgage_form(request.form)
        if error:
            flash(error, "error")
            return render_template(
                "mortgage-form.html",
                client=client,
                mortgage=mortgage,
                rate_types=MORTGAGE_RATE_TYPES,
                form_action=url_for("mortgage_edit", mortgage_id=mortgage.id),
                back_url=url_for("case_detail", user_id=client.id),
            )

        before = {
            "lender_name": mortgage.lender_name,
            "rate_type": mortgage.rate_type,
            "deal_end_date": mortgage.deal_end_date.isoformat(),
        }
        mortgage.lender_name = data["lender_name"]
        mortgage.product_name = data["product_name"]
        mortgage.rate_type = data["rate_type"]
        mortgage.rate_pct = data["rate_pct"]
        mortgage.term_years = data["term_years"]
        mortgage.monthly_payment = data["monthly_payment"]
        mortgage.balance_remaining = data["balance_remaining"]
        mortgage.deal_end_date = data["deal_end_date"]
        mortgage.notes = data["notes"]
        mortgage.updated_at = datetime.now(timezone.utc)

        audit.log_event(
            "mortgage_updated",
            user_id=current_user.id,
            target_type="mortgage",
            target_id=mortgage.id,
            client_user_id=client.id,
            before=before,
            after={
                "lender_name": data["lender_name"],
                "rate_type": data["rate_type"],
                "deal_end_date": data["deal_end_date"].isoformat(),
            },
        )
        db.session.commit()
        flash("Mortgage updated.", "success")
        return redirect(url_for("case_detail", user_id=client.id))

    return render_template(
        "mortgage-form.html",
        client=client,
        mortgage=mortgage,
        rate_types=MORTGAGE_RATE_TYPES,
        form_action=url_for("mortgage_edit", mortgage_id=mortgage.id),
        back_url=url_for("case_detail", user_id=client.id),
    )


@app.route("/mortgage/<int:mortgage_id>/delete", methods=["POST"])
@login_required
@adviser_required
def mortgage_delete(mortgage_id):
    mortgage = db.session.get(Mortgage, mortgage_id)
    if mortgage is None:
        abort(404)
    client_id = mortgage.user_id

    audit.log_event(
        "mortgage_deleted",
        user_id=current_user.id,
        target_type="mortgage",
        target_id=mortgage.id,
        client_user_id=client_id,
        lender_name=mortgage.lender_name,
        rate_type=mortgage.rate_type,
        deal_end_date=mortgage.deal_end_date.isoformat(),
    )
    db.session.delete(mortgage)
    db.session.commit()
    flash("Mortgage deleted.", "success")
    return redirect(url_for("case_detail", user_id=client_id))


@app.route("/dashboard")
@login_required
@adviser_required
def dashboard():
    stage_filter = request.args.get("stage")
    if stage_filter not in CASE_STAGES:
        stage_filter = None

    query = User.query.filter_by(role="client")
    if stage_filter:
        query = query.filter_by(case_stage=stage_filter)
    clients = query.order_by(User.stage_updated_at.desc().nullslast()).all()

    summaries = []
    for client in clients:
        summaries.append(_case_summary(client))

    stage_options = []
    for stage_key in CASE_STAGES:
        stage_options.append((stage_key, STAGE_LABELS[stage_key]))
    return render_template(
        "adviser-dashboard.html",
        summaries=summaries,
        stage_options=stage_options,
        stage_filter=stage_filter,
    )


@app.route("/case/<int:user_id>")
@login_required
@adviser_required
def case_detail(user_id):
    client = db.session.get(User, user_id)
    if client is None or client.role != "client":
        abort(404)

    audit.log_event(
        "adviser_viewed_case",
        user_id=current_user.id,
        target_type="user",
        target_id=client.id,
    )
    db.session.commit()

    fact_find = FactFind.query.filter_by(user_id=client.id).first()
    documents = (
        Document.query.filter_by(user_id=client.id)
        .order_by(Document.uploaded_at.desc())
        .all()
    )

    docs_by_category = {}
    for category_key, _label, _help in DOCUMENT_CATEGORIES:
        docs_by_category[category_key] = []
    for document_row in documents:
        if document_row.category not in docs_by_category:
            docs_by_category[document_row.category] = []
        docs_by_category[document_row.category].append(document_row)

    timeline_events = (
        AuditEvent.query.filter(
            (AuditEvent.user_id == client.id)
            | ((AuditEvent.target_type == "user") & (AuditEvent.target_id == client.id))
            | ((AuditEvent.target_type == "fact_find") & (AuditEvent.user_id == client.id))
            | ((AuditEvent.target_type == "document") & (AuditEvent.user_id == client.id))
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(50)
        .all()
    )
    timeline = []
    for event in timeline_events:
        if event.metadata_json:
            try:
                details = json.loads(event.metadata_json)
            except ValueError:
                details = {}
        else:
            details = {}
        timeline.append(
            {
                "created_at": event.created_at,
                "event_type": event.event_type,
                "details": details,
            }
        )

    summary = _case_summary(client)

    today = date.today()
    mortgage_rows = (
        Mortgage.query.filter_by(user_id=client.id)
        .order_by(Mortgage.deal_end_date.asc())
        .all()
    )
    mortgages = []
    for mortgage in mortgage_rows:
        mortgages.append(renewals.mortgage_summary(mortgage, today))

    return render_template(
        "case-detail.html",
        client=client,
        fact_find=fact_find,
        categories=DOCUMENT_CATEGORIES,
        docs_by_category=docs_by_category,
        timeline=timeline,
        summary=summary,
        mortgages=mortgages,
    )


@app.route("/case/<int:user_id>/advance", methods=["POST"])
@login_required
@adviser_required
def case_advance(user_id):
    client = db.session.get(User, user_id)
    if client is None or client.role != "client":
        abort(404)

    next_stage = request.form.get("next_stage", "")
    try:
        workflow.advance_stage(client, next_stage, actor=current_user)
    except workflow.TransitionError as exc:
        flash(str(exc), "error")
        return redirect(url_for("case_detail", user_id=client.id))

    db.session.commit()
    flash(f"Stage advanced to {STAGE_LABELS.get(next_stage, next_stage)}.", "success")
    return redirect(url_for("case_detail", user_id=client.id))


if __name__ == "__main__":
    app.run(debug=True)
