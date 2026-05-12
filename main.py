import os
import re
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, redirect, url_for, request, flash, abort, send_from_directory
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv
from models import db, User, FactFind, Document, DOCUMENT_CATEGORIES, DOCUMENT_CATEGORY_KEYS
import audit
import storage

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

    return render_template("client-portal.html", step=step)


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
    documents_by_category = {key: [] for key, _, _ in DOCUMENT_CATEGORIES}
    for doc in documents:
        documents_by_category.setdefault(doc.category, []).append(doc)
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
        fee_agreed = request.form.get("fee_agreed") == "on"
        binding_ack = request.form.get("legally_binding") == "on"
        signature = request.form.get("signature", "").strip()

        if not (tob_agreed and fee_agreed and binding_ack and signature):
            flash("Please tick every confirmation and type your full name.", "error")
            return render_template(
                "e-sign.html",
                signature=signature,
                tob_agreed=tob_agreed,
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
            documents=["Terms of Business", "Fee Agreement"],
        )
        db.session.commit()
        flash("Documents signed. Your case is now under adviser review.", "success")
        return redirect(url_for("portal"))

    return render_template("e-sign.html")


@app.route("/forms")
@login_required
@adviser_required
def forms():
    return render_template("adviser-forms.html")


@app.route("/workflow")
@login_required
@adviser_required
def workflow():
    return render_template("automated-workflow.html")


@app.route("/audit-log")
@login_required
@adviser_required
def compliance():
    return render_template("audit-log.html")


@app.route("/renewals")
@login_required
@adviser_required
def renewals():
    return render_template("renewals.html")


@app.route("/dashboard")
@login_required
@adviser_required
def dashboard():
    return render_template("adviser-dashboard.html")


if __name__ == "__main__":
    app.run(debug=True)
