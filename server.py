"""
Egypt Wealth Tracker - local server
Flask + SQLite. Serves finance.html and a small JSON API.

Run:  python server.py   (then open http://127.0.0.1:8765)
"""
import calendar
import csv
import io
import json
import os
import sqlite3
import threading
import urllib.request
import webbrowser
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory, Response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "finance.db")
HTML_FILE = "finance.html"
PORT = 8765

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS loans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    amount     REAL    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS debts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    amount     REAL    NOT NULL,
    due_date   TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS gold_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    weight     REAL    NOT NULL,
    karat      INTEGER NOT NULL CHECK (karat IN (18, 21, 24)),
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    amount      REAL NOT NULL,
    category    TEXT NOT NULL DEFAULT 'Other',
    spent_on    TEXT NOT NULL DEFAULT (date('now', 'localtime')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    total_loans REAL NOT NULL,
    total_gold  REAL NOT NULL,
    total_usd   REAL NOT NULL,
    total_egp   REAL NOT NULL,
    total_debts REAL NOT NULL DEFAULT 0,
    grand_total REAL NOT NULL,
    usd_rate    REAL
);

CREATE TABLE IF NOT EXISTS budgets (
    category      TEXT PRIMARY KEY,
    monthly_limit REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS goals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    target_amount REAL NOT NULL,
    saved_amount  REAL NOT NULL DEFAULT 0,
    deadline      TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS incomes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    amount      REAL NOT NULL,
    source      TEXT NOT NULL DEFAULT 'Other',
    received_on TEXT NOT NULL DEFAULT (date('now', 'localtime')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS recurring (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    description  TEXT NOT NULL,
    amount       REAL NOT NULL,
    category     TEXT NOT NULL DEFAULT 'Other',
    day_of_month INTEGER NOT NULL DEFAULT 1,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS rate_history (
    date     TEXT PRIMARY KEY,
    rate18k  REAL,
    rate21k  REAL,
    rate24k  REAL,
    usd_rate REAL
);

CREATE TABLE IF NOT EXISTS gameyat (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    monthly_amount REAL NOT NULL,
    total_members  INTEGER NOT NULL,
    my_position    INTEGER NOT NULL,
    rounds_done    INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS recurring_skips (
    recurring_id INTEGER NOT NULL,
    ym           TEXT NOT NULL,
    PRIMARY KEY (recurring_id, ym)
);
"""


def get_db():
    """Open a fresh connection (Flask tears it down per request)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@app.teardown_appcontext
def close_db(exc):
    db = app.config.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    try:
        db.executescript(SCHEMA)
        # CREATE TABLE IF NOT EXISTS never extends an existing table
        for stmt in (
            "ALTER TABLE expenses ADD COLUMN recurring_id INTEGER REFERENCES recurring(id) ON DELETE SET NULL",
            "ALTER TABLE snapshots ADD COLUMN usd_rate REAL",
            "ALTER TABLE snapshots ADD COLUMN total_debts REAL NOT NULL DEFAULT 0",
        ):
            try:
                db.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def body():
    return request.get_json(silent=True) or {}


def as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_settings(db):
    return {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings")}


def compute_totals(db):
    """Single source of truth for all wealth math (also used for snapshots)."""
    settings = get_settings(db)

    def setting_num(key):
        return as_float(settings.get(key))

    total_loans = db.execute("SELECT COALESCE(SUM(amount), 0) AS s FROM loans").fetchone()["s"]
    total_debts = db.execute("SELECT COALESCE(SUM(amount), 0) AS s FROM debts").fetchone()["s"]

    rates = {"18": setting_num("rate18k"), "21": setting_num("rate21k"), "24": setting_num("rate24k")}
    total_gold = 0.0
    for row in db.execute("SELECT weight, karat FROM gold_items"):
        total_gold += row["weight"] * rates.get(str(row["karat"]), 0.0)

    total_usd = setting_num("usd_amount") * setting_num("usd_rate")
    total_egp = setting_num("egp_amount")
    return {
        "total_loans": round(total_loans, 2),
        "total_gold": round(total_gold, 2),
        "total_usd": round(total_usd, 2),
        "total_egp": round(total_egp, 2),
        "total_debts": round(total_debts, 2),
        # debts are money owed TO others, so they reduce net wealth
        "grand_total": round(total_loans + total_gold + total_usd + total_egp - total_debts, 2),
    }


def row_to_dict(row):
    return dict(row)


def materialize_recurring(db):
    """Turn active recurring rules into expenses up to today (idempotent).

    For each rule, walk months from its creation to the current one and insert
    the expense on min(day_of_month, last day of month) unless one already
    exists for that rule+month. Future dates are left for when they arrive —
    the UI surfaces those as "due soon".
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    for rule in db.execute("SELECT * FROM recurring WHERE active = 1").fetchall():
        y, m = int(rule["created_at"][:4]), int(rule["created_at"][5:7])
        while (y, m) <= (now.year, now.month):
            ym = f"{y:04d}-{m:02d}"
            day = min(rule["day_of_month"], calendar.monthrange(y, m)[1])
            due = f"{ym}-{day:02d}"
            # the month-walk starts at the rule's creation month, so this can
            # never backdate past the month the user added the rule in
            if due <= today:
                exists = db.execute(
                    "SELECT 1 FROM expenses WHERE recurring_id = ? AND substr(spent_on, 1, 7) = ?",
                    (rule["id"], ym),
                ).fetchone()
                skipped = db.execute(
                    "SELECT 1 FROM recurring_skips WHERE recurring_id = ? AND ym = ?",
                    (rule["id"], ym),
                ).fetchone()
                if exists is None and skipped is None:
                    db.execute(
                        "INSERT INTO expenses (description, amount, category, spent_on, recurring_id) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (rule["description"], rule["amount"], rule["category"], due, rule["id"]),
                    )
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    db.commit()


def monthly_report(db, months=6):
    """[{ym, expenses, income}] for the last N months, oldest first."""
    now = datetime.now()
    out = []
    for i in range(months - 1, -1, -1):
        tm = now.year * 12 + (now.month - 1) - i
        y, m = divmod(tm, 12)
        ym = f"{y:04d}-{m + 1:02d}"
        exp = db.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM expenses WHERE substr(spent_on, 1, 7) = ?", (ym,)
        ).fetchone()["s"]
        inc = db.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM incomes WHERE substr(received_on, 1, 7) = ?", (ym,)
        ).fetchone()["s"]
        out.append({"ym": ym, "expenses": round(exp, 2), "income": round(inc, 2)})
    return out


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, HTML_FILE)


# ---------------------------------------------------------------------------
# Full state (one call hydrates the whole page)
# ---------------------------------------------------------------------------
@app.route("/api/state")
def api_state():
    db = get_db()
    try:
        materialize_recurring(db)
        return jsonify(
            loans=[row_to_dict(r) for r in db.execute("SELECT * FROM loans ORDER BY id")],
            debts=[row_to_dict(r) for r in db.execute("SELECT * FROM debts ORDER BY id")],
            gold=[row_to_dict(r) for r in db.execute("SELECT * FROM gold_items ORDER BY id")],
            settings=get_settings(db),
            expenses=[row_to_dict(r) for r in db.execute("SELECT * FROM expenses ORDER BY spent_on DESC, id DESC")],
            snapshots=[row_to_dict(r) for r in db.execute("SELECT * FROM snapshots ORDER BY taken_at")],
            totals=compute_totals(db),
            budgets=[row_to_dict(r) for r in db.execute("SELECT * FROM budgets ORDER BY category")],
            goals=[row_to_dict(r) for r in db.execute("SELECT * FROM goals ORDER BY id")],
            incomes=[row_to_dict(r) for r in db.execute("SELECT * FROM incomes ORDER BY received_on DESC, id DESC")],
            recurring=[row_to_dict(r) for r in db.execute("SELECT * FROM recurring ORDER BY day_of_month, id")],
            gameyat=[row_to_dict(r) for r in db.execute("SELECT * FROM gameyat ORDER BY id")],
            rate_history=[row_to_dict(r) for r in db.execute("SELECT * FROM rate_history ORDER BY date")],
            monthly=monthly_report(db, 6),
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Loans
# ---------------------------------------------------------------------------
@app.route("/api/loans", methods=["POST"])
def add_loan():
    data = body()
    name = (data.get("name") or "").strip()
    amount = as_float(data.get("amount"), default=None)
    if not name or amount is None or amount <= 0:
        return jsonify(error="Please provide a name and a positive amount"), 400
    db = get_db()
    try:
        cur = db.execute("INSERT INTO loans (name, amount) VALUES (?, ?)", (name, amount))
        db.commit()
        row = db.execute("SELECT * FROM loans WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify(row_to_dict(row)), 201
    finally:
        db.close()


@app.route("/api/loans/<int:loan_id>", methods=["PUT"])
def update_loan(loan_id):
    data = body()
    db = get_db()
    try:
        row = db.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        if row is None:
            return jsonify(error="Loan not found"), 404
        name = (data.get("name") or row["name"]).strip()
        amount = as_float(data.get("amount"), row["amount"])
        if not name or amount <= 0:
            return jsonify(error="Please provide a name and a positive amount"), 400
        db.execute("UPDATE loans SET name = ?, amount = ? WHERE id = ?", (name, amount, loan_id))
        db.commit()
        return jsonify(row_to_dict(db.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()))
    finally:
        db.close()


@app.route("/api/loans/<int:loan_id>", methods=["DELETE"])
def delete_loan(loan_id):
    db = get_db()
    try:
        db.execute("DELETE FROM loans WHERE id = ?", (loan_id,))
        db.commit()
        return jsonify(ok=True)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Debts (money other people lent the user — a liability, reduces net wealth)
# ---------------------------------------------------------------------------
def _clean_due_date(value):
    """Return 'YYYY-MM-DD', None when absent, or raises ValueError."""
    due = (value or "").strip()
    if not due:
        return None
    datetime.strptime(due[:10], "%Y-%m-%d")
    return due[:10]


@app.route("/api/debts", methods=["POST"])
def add_debt():
    data = body()
    name = (data.get("name") or "").strip()
    amount = as_float(data.get("amount"), default=None)
    if not name or amount is None or amount <= 0:
        return jsonify(error="Please provide a name and a positive amount"), 400
    try:
        due = _clean_due_date(data.get("due_date"))
    except ValueError:
        return jsonify(error="Due date must be YYYY-MM-DD"), 400
    db = get_db()
    try:
        cur = db.execute("INSERT INTO debts (name, amount, due_date) VALUES (?, ?, ?)", (name, amount, due))
        db.commit()
        row = db.execute("SELECT * FROM debts WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify(row_to_dict(row)), 201
    finally:
        db.close()


@app.route("/api/debts/<int:debt_id>", methods=["PUT"])
def update_debt(debt_id):
    data = body()
    db = get_db()
    try:
        row = db.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone()
        if row is None:
            return jsonify(error="Debt not found"), 404
        name = (data.get("name") or row["name"]).strip()
        amount = as_float(data.get("amount"), row["amount"])
        if not name or amount <= 0:
            return jsonify(error="Please provide a name and a positive amount"), 400
        try:
            due = _clean_due_date(data.get("due_date") if "due_date" in data else row["due_date"])
        except ValueError:
            return jsonify(error="Due date must be YYYY-MM-DD"), 400
        db.execute("UPDATE debts SET name = ?, amount = ?, due_date = ? WHERE id = ?", (name, amount, due, debt_id))
        db.commit()
        return jsonify(row_to_dict(db.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone()))
    finally:
        db.close()


@app.route("/api/debts/<int:debt_id>", methods=["DELETE"])
def delete_debt(debt_id):
    db = get_db()
    try:
        db.execute("DELETE FROM debts WHERE id = ?", (debt_id,))
        db.commit()
        return jsonify(ok=True)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Gold
# ---------------------------------------------------------------------------
@app.route("/api/gold", methods=["POST"])
def add_gold():
    data = body()
    name = (data.get("name") or "").strip()
    weight = as_float(data.get("weight"), default=None)
    karat = data.get("karat")
    if not name or weight is None or weight <= 0:
        return jsonify(error="Please provide an item name and a positive weight"), 400
    if str(karat) not in ("18", "21", "24"):
        return jsonify(error="Karat must be 18, 21 or 24"), 400
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO gold_items (name, weight, karat) VALUES (?, ?, ?)",
            (name, weight, int(karat)),
        )
        db.commit()
        row = db.execute("SELECT * FROM gold_items WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify(row_to_dict(row)), 201
    finally:
        db.close()


@app.route("/api/gold/<int:item_id>", methods=["PUT"])
def update_gold(item_id):
    data = body()
    db = get_db()
    try:
        row = db.execute("SELECT * FROM gold_items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            return jsonify(error="Gold item not found"), 404
        name = (data.get("name") or row["name"]).strip()
        weight = as_float(data.get("weight"), row["weight"])
        karat = str(data.get("karat", row["karat"]))
        if not name or weight <= 0:
            return jsonify(error="Please provide an item name and a positive weight"), 400
        if karat not in ("18", "21", "24"):
            return jsonify(error="Karat must be 18, 21 or 24"), 400
        db.execute(
            "UPDATE gold_items SET name = ?, weight = ?, karat = ? WHERE id = ?",
            (name, weight, int(karat), item_id),
        )
        db.commit()
        return jsonify(row_to_dict(db.execute("SELECT * FROM gold_items WHERE id = ?", (item_id,)).fetchone()))
    finally:
        db.close()


@app.route("/api/gold/<int:item_id>", methods=["DELETE"])
def delete_gold(item_id):
    db = get_db()
    try:
        db.execute("DELETE FROM gold_items WHERE id = ?", (item_id,))
        db.commit()
        return jsonify(ok=True)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Settings (rates + USD/EGP amounts)
# ---------------------------------------------------------------------------
SETTING_KEYS = ("rate18k", "rate21k", "rate24k", "usd_rate", "usd_amount", "egp_amount")


@app.route("/api/settings", methods=["PUT"])
def update_settings():
    data = body()
    updates = {}
    for key in SETTING_KEYS:
        if key in data:
            value = data[key]
            if value is None or value == "":
                updates[key] = ""
            else:
                num = as_float(value, default=None)
                if num is None or num < 0:
                    return jsonify(error=f"Invalid value for {key}"), 400
                updates[key] = str(num)
    db = get_db()
    try:
        for key, value in updates.items():
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        db.commit()
        return jsonify(ok=True)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Expenses (monthly cost calculator)
# ---------------------------------------------------------------------------
@app.route("/api/expenses", methods=["POST"])
def add_expense():
    data = body()
    description = (data.get("description") or "").strip()
    amount = as_float(data.get("amount"), default=None)
    if not description or amount is None or amount <= 0:
        return jsonify(error="Please provide a description and a positive amount"), 400
    category = (data.get("category") or "Other").strip() or "Other"
    spent_on = (data.get("spent_on") or "").strip()
    if spent_on:
        try:
            datetime.strptime(spent_on, "%Y-%m-%d")
        except ValueError:
            return jsonify(error="Date must be YYYY-MM-DD"), 400
    else:
        spent_on = datetime.now().strftime("%Y-%m-%d")
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO expenses (description, amount, category, spent_on) VALUES (?, ?, ?, ?)",
            (description, amount, category, spent_on),
        )
        db.commit()
        row = db.execute("SELECT * FROM expenses WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify(row_to_dict(row)), 201
    finally:
        db.close()


@app.route("/api/expenses/<int:expense_id>", methods=["PUT"])
def update_expense(expense_id):
    data = body()
    db = get_db()
    try:
        row = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        if row is None:
            return jsonify(error="Expense not found"), 404
        description = (data.get("description") or row["description"]).strip()
        amount = as_float(data.get("amount"), row["amount"])
        category = (data.get("category") or row["category"]).strip() or "Other"
        spent_on = (data.get("spent_on") or row["spent_on"]).strip()
        if not description or amount <= 0:
            return jsonify(error="Please provide a description and a positive amount"), 400
        try:
            datetime.strptime(spent_on, "%Y-%m-%d")
        except ValueError:
            return jsonify(error="Date must be YYYY-MM-DD"), 400
        db.execute(
            "UPDATE expenses SET description = ?, amount = ?, category = ?, spent_on = ? WHERE id = ?",
            (description, amount, category, spent_on, expense_id),
        )
        db.commit()
        return jsonify(row_to_dict(db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()))
    finally:
        db.close()


@app.route("/api/expenses/<int:expense_id>", methods=["DELETE"])
def delete_expense(expense_id):
    db = get_db()
    try:
        row = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        if row is not None and row["recurring_id"]:
            # tombstone so materialize_recurring doesn't re-insert this month's copy
            db.execute(
                "INSERT OR IGNORE INTO recurring_skips (recurring_id, ym) VALUES (?, ?)",
                (row["recurring_id"], row["spent_on"][:7]),
            )
        db.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        db.commit()
        return jsonify(ok=True)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Snapshots (wealth history)
# ---------------------------------------------------------------------------
@app.route("/api/snapshots", methods=["POST"])
def add_snapshot():
    db = get_db()
    try:
        totals = compute_totals(db)
        usd_rate = as_float(get_settings(db).get("usd_rate")) or None
        cur = db.execute(
            "INSERT INTO snapshots (total_loans, total_gold, total_usd, total_egp, total_debts, grand_total, usd_rate) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                totals["total_loans"],
                totals["total_gold"],
                totals["total_usd"],
                totals["total_egp"],
                totals["total_debts"],
                totals["grand_total"],
                usd_rate,
            ),
        )
        db.commit()
        row = db.execute("SELECT * FROM snapshots WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify(row_to_dict(row)), 201
    finally:
        db.close()


@app.route("/api/snapshots/<int:snapshot_id>", methods=["DELETE"])
def delete_snapshot(snapshot_id):
    db = get_db()
    try:
        db.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
        db.commit()
        return jsonify(ok=True)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Budgets (monthly limit per category; limit 0 removes the budget)
# ---------------------------------------------------------------------------
@app.route("/api/budgets", methods=["POST"])
def set_budget():
    data = body()
    category = (data.get("category") or "").strip()
    monthly_limit = as_float(data.get("monthly_limit"), default=None)
    if not category or monthly_limit is None or monthly_limit < 0:
        return jsonify(error="Please provide a category and a non-negative monthly limit"), 400
    db = get_db()
    try:
        if monthly_limit == 0:
            db.execute("DELETE FROM budgets WHERE category = ?", (category,))
            db.commit()
            return jsonify(ok=True, removed=True)
        db.execute(
            "INSERT INTO budgets (category, monthly_limit) VALUES (?, ?) "
            "ON CONFLICT(category) DO UPDATE SET monthly_limit = excluded.monthly_limit",
            (category, monthly_limit),
        )
        db.commit()
        return jsonify(ok=True)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Goals (savings targets)
# ---------------------------------------------------------------------------
@app.route("/api/goals", methods=["POST"])
def add_goal():
    data = body()
    name = (data.get("name") or "").strip()
    target = as_float(data.get("target_amount"), default=None)
    saved = as_float(data.get("saved_amount"), 0.0)
    deadline = (data.get("deadline") or "").strip()
    if not name or target is None or target <= 0:
        return jsonify(error="Please provide a name and a positive target amount"), 400
    if saved < 0:
        return jsonify(error="Saved amount cannot be negative"), 400
    if deadline:
        try:
            datetime.strptime(deadline, "%Y-%m-%d")
        except ValueError:
            return jsonify(error="Deadline must be YYYY-MM-DD"), 400
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO goals (name, target_amount, saved_amount, deadline) VALUES (?, ?, ?, ?)",
            (name, target, saved, deadline or None),
        )
        db.commit()
        row = db.execute("SELECT * FROM goals WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify(row_to_dict(row)), 201
    finally:
        db.close()


@app.route("/api/goals/<int:goal_id>", methods=["PUT"])
def update_goal(goal_id):
    data = body()
    db = get_db()
    try:
        row = db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if row is None:
            return jsonify(error="Goal not found"), 404
        name = (data.get("name") or row["name"]).strip()
        target = as_float(data.get("target_amount"), row["target_amount"])
        saved = as_float(data.get("saved_amount"), row["saved_amount"])
        deadline = (data.get("deadline", row["deadline"] or "") or "").strip()
        if not name or target <= 0:
            return jsonify(error="Please provide a name and a positive target amount"), 400
        if saved < 0:
            return jsonify(error="Saved amount cannot be negative"), 400
        if deadline:
            try:
                datetime.strptime(deadline, "%Y-%m-%d")
            except ValueError:
                return jsonify(error="Deadline must be YYYY-MM-DD"), 400
        db.execute(
            "UPDATE goals SET name = ?, target_amount = ?, saved_amount = ?, deadline = ? WHERE id = ?",
            (name, target, saved, deadline or None, goal_id),
        )
        db.commit()
        return jsonify(row_to_dict(db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()))
    finally:
        db.close()


@app.route("/api/goals/<int:goal_id>", methods=["DELETE"])
def delete_goal(goal_id):
    db = get_db()
    try:
        db.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
        db.commit()
        return jsonify(ok=True)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Incomes (mirror of expenses)
# ---------------------------------------------------------------------------
@app.route("/api/incomes", methods=["POST"])
def add_income():
    data = body()
    description = (data.get("description") or "").strip()
    amount = as_float(data.get("amount"), default=None)
    if not description or amount is None or amount <= 0:
        return jsonify(error="Please provide a description and a positive amount"), 400
    source = (data.get("source") or "Other").strip() or "Other"
    received_on = (data.get("received_on") or "").strip()
    if received_on:
        try:
            datetime.strptime(received_on, "%Y-%m-%d")
        except ValueError:
            return jsonify(error="Date must be YYYY-MM-DD"), 400
    else:
        received_on = datetime.now().strftime("%Y-%m-%d")
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO incomes (description, amount, source, received_on) VALUES (?, ?, ?, ?)",
            (description, amount, source, received_on),
        )
        db.commit()
        row = db.execute("SELECT * FROM incomes WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify(row_to_dict(row)), 201
    finally:
        db.close()


@app.route("/api/incomes/<int:income_id>", methods=["DELETE"])
def delete_income(income_id):
    db = get_db()
    try:
        db.execute("DELETE FROM incomes WHERE id = ?", (income_id,))
        db.commit()
        return jsonify(ok=True)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Recurring bills (auto-inserted as expenses when their day arrives)
# ---------------------------------------------------------------------------
@app.route("/api/recurring", methods=["POST"])
def add_recurring():
    data = body()
    description = (data.get("description") or "").strip()
    amount = as_float(data.get("amount"), default=None)
    day = int(as_float(data.get("day_of_month"), 1))
    if not description or amount is None or amount <= 0:
        return jsonify(error="Please provide a description and a positive amount"), 400
    if not 1 <= day <= 31:
        return jsonify(error="Day of month must be between 1 and 31"), 400
    category = (data.get("category") or "Other").strip() or "Other"
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO recurring (description, amount, category, day_of_month) VALUES (?, ?, ?, ?)",
            (description, amount, category, day),
        )
        db.commit()
        materialize_recurring(db)
        row = db.execute("SELECT * FROM recurring WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify(row_to_dict(row)), 201
    finally:
        db.close()


@app.route("/api/recurring/<int:rule_id>", methods=["PUT"])
def update_recurring(rule_id):
    """Any subset of fields; `active` toggles pause/resume."""
    data = body()
    db = get_db()
    try:
        row = db.execute("SELECT * FROM recurring WHERE id = ?", (rule_id,)).fetchone()
        if row is None:
            return jsonify(error="Recurring rule not found"), 404
        description = (data.get("description") or row["description"]).strip()
        amount = as_float(data.get("amount"), row["amount"])
        category = (data.get("category") or row["category"]).strip() or "Other"
        day = int(as_float(data.get("day_of_month"), row["day_of_month"]))
        active = int(data.get("active", row["active"]))
        if not description or amount <= 0:
            return jsonify(error="Please provide a description and a positive amount"), 400
        if not 1 <= day <= 31:
            return jsonify(error="Day of month must be between 1 and 31"), 400
        if active not in (0, 1):
            return jsonify(error="active must be 0 or 1"), 400
        db.execute(
            "UPDATE recurring SET description = ?, amount = ?, category = ?, day_of_month = ?, active = ? WHERE id = ?",
            (description, amount, category, day, active, rule_id),
        )
        db.commit()
        if active:
            materialize_recurring(db)
        return jsonify(row_to_dict(db.execute("SELECT * FROM recurring WHERE id = ?", (rule_id,)).fetchone()))
    finally:
        db.close()


@app.route("/api/recurring/<int:rule_id>", methods=["DELETE"])
def delete_recurring(rule_id):
    db = get_db()
    try:
        db.execute("DELETE FROM recurring WHERE id = ?", (rule_id,))
        db.commit()
        return jsonify(ok=True)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Game'yat (rotating savings)
# ---------------------------------------------------------------------------
@app.route("/api/gameyat", methods=["POST"])
def add_gameyat():
    data = body()
    name = (data.get("name") or "").strip()
    monthly = as_float(data.get("monthly_amount"), default=None)
    members = int(as_float(data.get("total_members"), 0))
    position = int(as_float(data.get("my_position"), 0))
    if not name or monthly is None or monthly <= 0:
        return jsonify(error="Please provide a name and a positive monthly amount"), 400
    if members < 2 or position < 1 or position > members:
        return jsonify(error="Members must be ≥ 2 and your position between 1 and members"), 400
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO gameyat (name, monthly_amount, total_members, my_position) VALUES (?, ?, ?, ?)",
            (name, monthly, members, position),
        )
        db.commit()
        row = db.execute("SELECT * FROM gameyat WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify(row_to_dict(row)), 201
    finally:
        db.close()


@app.route("/api/gameyat/<int:g_id>", methods=["DELETE"])
def delete_gameyat(g_id):
    db = get_db()
    try:
        db.execute("DELETE FROM gameyat WHERE id = ?", (g_id,))
        db.commit()
        return jsonify(ok=True)
    finally:
        db.close()


@app.route("/api/gameyat/<int:g_id>/advance", methods=["POST"])
def advance_gameyat(g_id):
    """One more round paid out — increments rounds_done up to total_members."""
    db = get_db()
    try:
        row = db.execute("SELECT * FROM gameyat WHERE id = ?", (g_id,)).fetchone()
        if row is None:
            return jsonify(error="Game'yat not found"), 404
        if row["rounds_done"] >= row["total_members"]:
            return jsonify(error="All rounds already completed"), 400
        db.execute("UPDATE gameyat SET rounds_done = rounds_done + 1 WHERE id = ?", (g_id,))
        db.commit()
        return jsonify(row_to_dict(db.execute("SELECT * FROM gameyat WHERE id = ?", (g_id,)).fetchone()))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Live rates (gold spot + USD/EGP)
# ---------------------------------------------------------------------------
GOLD_API = "https://api.gold-api.com/price/XAU"
FX_API = "https://open.er-api.com/v6/latest/USD"
OZ_TO_GRAM = 31.1034768


def _http_json(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


@app.route("/api/rates/fetch", methods=["POST"])
def fetch_rates():
    """Fetch live gold spot (USD/oz) and USD→EGP, derive per-gram EGP prices,
    save them to settings + rate_history, and return them."""
    try:
        spot = _http_json(GOLD_API)
        fx = _http_json(FX_API)
        xau_usd = as_float(spot.get("price"), default=None)
        usd_egp = as_float((fx.get("rates") or {}).get("EGP"), default=None)
        if not xau_usd or not usd_egp:
            return jsonify(error="Live price services returned unexpected data"), 502
        rate24 = round(xau_usd / OZ_TO_GRAM * usd_egp)
        rate21 = round(rate24 * 0.875)
        rate18 = round(rate24 * 0.75)
        usd_rate = round(usd_egp, 2)
    except Exception as exc:
        return jsonify(error=f"Could not fetch live prices (offline?): {exc}"), 502

    db = get_db()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        for key, value in (("rate18k", rate18), ("rate21k", rate21), ("rate24k", rate24), ("usd_rate", usd_rate)):
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
        db.execute(
            "INSERT INTO rate_history (date, rate18k, rate21k, rate24k, usd_rate) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET rate18k = excluded.rate18k, rate21k = excluded.rate21k, "
            "rate24k = excluded.rate24k, usd_rate = excluded.usd_rate",
            (today, rate18, rate21, rate24, usd_rate),
        )
        db.commit()
        return jsonify(ok=True, spot_usd_oz=round(xau_usd, 2), usd_egp=usd_rate,
                       rate18k=rate18, rate21k=rate21, rate24k=rate24)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# CSV export / import
# ---------------------------------------------------------------------------
CSV_COLUMNS = [
    "type", "name", "amount", "weight", "karat", "category", "date",
    "total_loans", "total_debts", "total_gold", "total_usd", "total_egp", "grand_total",
    "saved_amount", "day_of_month", "active", "members", "position", "rounds", "usd_rate",
]


@app.route("/api/export.csv")
def export_csv():
    db = get_db()
    try:
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, restval="")
        writer.writeheader()

        for r in db.execute("SELECT * FROM loans ORDER BY id"):
            writer.writerow({"type": "loan", "name": r["name"], "amount": r["amount"], "date": r["created_at"]})
        for r in db.execute("SELECT * FROM debts ORDER BY id"):
            writer.writerow({"type": "debt", "name": r["name"], "amount": r["amount"], "date": r["due_date"] or ""})
        for r in db.execute("SELECT * FROM gold_items ORDER BY id"):
            writer.writerow({"type": "gold", "name": r["name"], "weight": r["weight"], "karat": r["karat"], "date": r["created_at"]})
        for r in db.execute("SELECT * FROM expenses ORDER BY id"):
            writer.writerow({"type": "expense", "name": r["description"], "amount": r["amount"], "category": r["category"], "date": r["spent_on"]})
        for k, v in get_settings(db).items():
            writer.writerow({"type": "setting", "name": k, "amount": v})
        for r in db.execute("SELECT * FROM snapshots ORDER BY id"):
            writer.writerow({
                "type": "snapshot", "date": r["taken_at"],
                "total_loans": r["total_loans"], "total_gold": r["total_gold"],
                "total_debts": r["total_debts"] if "total_debts" in r.keys() else 0,
                "total_usd": r["total_usd"], "total_egp": r["total_egp"],
                "grand_total": r["grand_total"], "usd_rate": r["usd_rate"] if "usd_rate" in r.keys() else "",
            })
        for r in db.execute("SELECT * FROM budgets ORDER BY category"):
            writer.writerow({"type": "budget", "name": r["category"], "amount": r["monthly_limit"]})
        for r in db.execute("SELECT * FROM goals ORDER BY id"):
            writer.writerow({"type": "goal", "name": r["name"], "amount": r["target_amount"],
                             "saved_amount": r["saved_amount"], "date": r["deadline"] or ""})
        for r in db.execute("SELECT * FROM incomes ORDER BY id"):
            writer.writerow({"type": "income", "name": r["description"], "amount": r["amount"],
                             "category": r["source"], "date": r["received_on"]})
        for r in db.execute("SELECT * FROM recurring ORDER BY id"):
            writer.writerow({"type": "recurring", "name": r["description"], "amount": r["amount"],
                             "category": r["category"], "day_of_month": r["day_of_month"],
                             "active": r["active"], "date": r["created_at"]})
        for r in db.execute("SELECT * FROM gameyat ORDER BY id"):
            writer.writerow({"type": "gameyat", "name": r["name"], "amount": r["monthly_amount"],
                             "members": r["total_members"], "position": r["my_position"],
                             "rounds": r["rounds_done"]})

        filename = f"wealth_export_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        return Response(
            out.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    finally:
        db.close()


@app.route("/api/import", methods=["POST"])
def import_csv():
    """Replace-all restore from a CSV produced by /api/export.csv."""
    if "file" not in request.files:
        return jsonify(error="No file uploaded"), 400
    text = request.files["file"].read().decode("utf-8-sig", errors="replace")

    loans, gold, expenses, settings, snapshots = [], [], [], {}, []
    budgets, goals, incomes, recurring, gameyat, debts = [], [], [], [], [], []
    try:
        for row in csv.DictReader(io.StringIO(text)):
            rtype = (row.get("type") or "").strip().lower()
            if rtype == "loan":
                loans.append(((row["name"] or "").strip(), as_float(row.get("amount"))))
            elif rtype == "debt":
                due = (row.get("date") or "").strip()[:10]
                debts.append((
                    (row["name"] or "").strip(), as_float(row.get("amount")),
                    due if due else None,
                ))
            elif rtype == "gold":
                gold.append(((row["name"] or "").strip(), as_float(row.get("weight")), int(row.get("karat") or 0)))
            elif rtype == "expense":
                expenses.append((
                    (row["name"] or "").strip(), as_float(row.get("amount")),
                    (row.get("category") or "Other").strip() or "Other",
                    (row.get("date") or datetime.now().strftime("%Y-%m-%d"))[:10],
                ))
            elif rtype == "setting":
                key = (row["name"] or "").strip()
                if key in SETTING_KEYS:
                    settings[key] = row.get("amount") or ""
            elif rtype == "snapshot":
                snapshots.append((
                    row.get("date") or datetime.now().strftime("%Y-%m-%d %H:%M"),
                    as_float(row.get("total_loans")), as_float(row.get("total_gold")),
                    as_float(row.get("total_usd")), as_float(row.get("total_egp")),
                    as_float(row.get("total_debts")),  # absent in pre-debt CSVs -> 0
                    as_float(row.get("grand_total")),
                    as_float(row.get("usd_rate")) or None,
                ))
            elif rtype == "budget":
                budgets.append(((row["name"] or "").strip(), as_float(row.get("amount"))))
            elif rtype == "goal":
                goals.append((
                    (row["name"] or "").strip(), as_float(row.get("amount")),
                    as_float(row.get("saved_amount")), (row.get("date") or "").strip() or None,
                ))
            elif rtype == "income":
                incomes.append((
                    (row["name"] or "").strip(), as_float(row.get("amount")),
                    (row.get("category") or "Other").strip() or "Other",
                    (row.get("date") or datetime.now().strftime("%Y-%m-%d"))[:10],
                ))
            elif rtype == "recurring":
                recurring.append((
                    (row["name"] or "").strip(), as_float(row.get("amount")),
                    (row.get("category") or "Other").strip() or "Other",
                    int(as_float(row.get("day_of_month"), 1)),
                    int(as_float(row.get("active"), 1)),
                ))
            elif rtype == "gameyat":
                gameyat.append((
                    (row["name"] or "").strip(), as_float(row.get("amount")),
                    int(as_float(row.get("members"), 0)), int(as_float(row.get("position"), 0)),
                    int(as_float(row.get("rounds"), 0)),
                ))
    except (KeyError, ValueError) as exc:
        return jsonify(error=f"Could not parse CSV: {exc}"), 400

    for name, amount in loans:
        if not name or amount <= 0:
            return jsonify(error="CSV has a loan row with empty name or bad amount"), 400
    for name, weight, karat in gold:
        if not name or weight <= 0 or karat not in (18, 21, 24):
            return jsonify(error=f"CSV has an invalid gold row: {name!r}"), 400
    for desc, amount, _, spent_on in expenses:
        if not desc or amount <= 0:
            return jsonify(error=f"CSV has an invalid expense row: {desc!r}"), 400
        try:
            datetime.strptime(spent_on, "%Y-%m-%d")
        except ValueError:
            return jsonify(error=f"CSV expense {desc!r} has a bad date"), 400
    for category, limit in budgets:
        if not category or limit < 0:
            return jsonify(error=f"CSV has an invalid budget row: {category!r}"), 400
    for name, target, saved, deadline in goals:
        if not name or target <= 0 or saved < 0:
            return jsonify(error=f"CSV has an invalid goal row: {name!r}"), 400
        if deadline:
            try:
                datetime.strptime(deadline, "%Y-%m-%d")
            except ValueError:
                return jsonify(error=f"CSV goal {name!r} has a bad deadline"), 400
    for desc, amount, _, received_on in incomes:
        if not desc or amount <= 0:
            return jsonify(error=f"CSV has an invalid income row: {desc!r}"), 400
        try:
            datetime.strptime(received_on, "%Y-%m-%d")
        except ValueError:
            return jsonify(error=f"CSV income {desc!r} has a bad date"), 400
    for desc, amount, _, day, active in recurring:
        if not desc or amount <= 0 or not 1 <= day <= 31 or active not in (0, 1):
            return jsonify(error=f"CSV has an invalid recurring row: {desc!r}"), 400
    for name, monthly, members, position, rounds in gameyat:
        if not name or monthly <= 0 or members < 2 or not 1 <= position <= members or not 0 <= rounds <= members:
            return jsonify(error=f"CSV has an invalid gameyat row: {name!r}"), 400

    db = get_db()
    try:
        db.execute("BEGIN")
        for table in ("loans", "debts", "gold_items", "expenses", "snapshots", "budgets", "goals", "incomes", "recurring", "gameyat", "recurring_skips"):
            db.execute(f"DELETE FROM {table}")
        db.executemany("INSERT INTO loans (name, amount) VALUES (?, ?)", loans)
        db.executemany("INSERT INTO debts (name, amount, due_date) VALUES (?, ?, ?)", debts)
        db.executemany("INSERT INTO gold_items (name, weight, karat) VALUES (?, ?, ?)", gold)
        db.executemany("INSERT INTO expenses (description, amount, category, spent_on) VALUES (?, ?, ?, ?)", expenses)
        db.executemany(
            "INSERT INTO snapshots (taken_at, total_loans, total_gold, total_usd, total_egp, total_debts, grand_total, usd_rate) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            snapshots,
        )
        db.executemany("INSERT INTO budgets (category, monthly_limit) VALUES (?, ?)", budgets)
        db.executemany("INSERT INTO goals (name, target_amount, saved_amount, deadline) VALUES (?, ?, ?, ?)", goals)
        db.executemany("INSERT INTO incomes (description, amount, source, received_on) VALUES (?, ?, ?, ?)", incomes)
        db.executemany("INSERT INTO recurring (description, amount, category, day_of_month, active) VALUES (?, ?, ?, ?, ?)", recurring)
        db.executemany("INSERT INTO gameyat (name, monthly_amount, total_members, my_position, rounds_done) VALUES (?, ?, ?, ?, ?)", gameyat)
        for key, value in settings.items():
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        # Relink expenses to their recurring rules (the CSV format doesn't carry
        # recurring_id) so materialize_recurring doesn't duplicate them.
        for rule_id, desc in db.execute("SELECT id, description FROM recurring").fetchall():
            db.execute(
                "UPDATE expenses SET recurring_id = ? WHERE recurring_id IS NULL AND description = ?",
                (rule_id, desc),
            )
        materialize_recurring(db)
        db.commit()
        return jsonify(
            ok=True,
            imported={"loans": len(loans), "debts": len(debts), "gold": len(gold), "expenses": len(expenses), "snapshots": len(snapshots),
                      "budgets": len(budgets), "goals": len(goals), "incomes": len(incomes),
                      "recurring": len(recurring), "gameyat": len(gameyat)},
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    url = f"http://127.0.0.1:{PORT}"
    print(f"Wealth Tracker running at {url}  (Ctrl+C to stop)")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
