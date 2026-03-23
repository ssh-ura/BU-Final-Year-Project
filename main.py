import os
import re
from functools import wraps
from flask import Flask, render_template, redirect, url_for, request, flash, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv
from models import db, User

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
    return render_template("client-portal.html")


@app.route("/forms")
@login_required
@adviser_required
def forms():
    return render_template("digital-forms.html")


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
