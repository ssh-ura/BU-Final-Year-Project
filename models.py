from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="client")  # client or adviser
    esigned = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class FactFind(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # Step 1
    first_name = db.Column(db.String(80))
    last_name = db.Column(db.String(80))
    date_of_birth = db.Column(db.Date)
    phone_number = db.Column(db.String(30))
    current_address = db.Column(db.String(255))

    # Step 2
    employment_status = db.Column(db.String(20))
    employer_name = db.Column(db.String(120))
    job_title = db.Column(db.String(120))
    start_date = db.Column(db.Date)
    contract_type = db.Column(db.String(30))

    # Step 3
    annual_salary = db.Column(db.Float)
    additional_income = db.Column(db.Float)
    monthly_outgoings = db.Column(db.Float)

    # Step 4
    property_type = db.Column(db.String(20))
    purchase_price = db.Column(db.Float)
    deposit_amount = db.Column(db.Float)
    mortgage_type = db.Column(db.String(20))

    current_step = db.Column(db.Integer, default=1)
    is_complete = db.Column(db.Boolean, default=False)
    submitted_at = db.Column(db.DateTime)


class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
