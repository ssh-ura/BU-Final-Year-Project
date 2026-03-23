import os
from flask import Flask, render_template, redirect, url_for
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

# Routes

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login")
def login():
    return render_template("login.html")


@app.route("/register")
def register():
    return render_template("register.html")


@app.route("/portal")
def portal():
    return render_template("portal.html")


@app.route("/forms")
def forms():
    return render_template("forms.html")


@app.route("/workflow")
def workflow():
    return render_template("workflow.html")


@app.route("/compliance")
def compliance():
    return render_template("compliance.html")


@app.route("/renewals")
def renewals():
    return render_template("renewals.html")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")



if __name__ == "__main__":
    app.run(debug=True)
