from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import sqlite3

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class LeadCreate(BaseModel):
    name: str
    email: str
    phone: str | None = None
    service: str | None = None
    message: str | None = None

class StatusUpdate(BaseModel):
    status: str

def get_db():
    conn = sqlite3.connect("leads.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            service TEXT,
            message TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS automation_enrollments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            step INTEGER NOT NULL DEFAULT 0,
            next_action_at TEXT NOT NULL,
            stopped INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leads (id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS automation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            step INTEGER NOT NULL,
            action TEXT NOT NULL,
            executed_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

# Sequence definition: (wait time before this step, description of what happens)
# Step 0 = immediate confirmation (already handled at creation)
SEQUENCE_STEPS = [
    {"step": 1, "wait_minutes": 2, "action": "Follow-up #1: Checking in on your request"},
    {"step": 2, "wait_minutes": 5, "action": "Follow-up #2: Still interested? Let's talk"},
    {"step": 3, "wait_minutes": 10, "action": "Final follow-up: Last check-in"},
]
# NOTE: wait_minutes is set very short (2/5/10 min) for demo purposes so you
# can watch it work in real time. In a real deployment these would be
# wait_minutes = 2 days, 3 days, 5 days, etc.

def enroll_lead_in_automation(lead_id: int):
    conn = get_db()
    first_step = SEQUENCE_STEPS[0]
    next_action = datetime.utcnow() + timedelta(minutes=first_step["wait_minutes"])
    conn.execute(
        "INSERT INTO automation_enrollments (lead_id, step, next_action_at, stopped, created_at) VALUES (?, 0, ?, 0, ?)",
        (lead_id, next_action.isoformat(), datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()

def run_due_automations():
    """Called by the scheduler every minute. Checks for due follow-ups and executes them."""
    conn = get_db()
    now = datetime.utcnow().isoformat()

    due = conn.execute(
        "SELECT * FROM automation_enrollments WHERE stopped = 0 AND next_action_at <= ?",
        (now,)
    ).fetchall()

    for enrollment in due:
        lead_id = enrollment["lead_id"]
        current_step = enrollment["step"]

        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if lead is None:
            continue

        # Stop condition: lead already contacted/closed manually
        if lead["status"] != "new":
            conn.execute("UPDATE automation_enrollments SET stopped = 1 WHERE id = ?", (enrollment["id"],))
            conn.commit()
            continue

        if current_step >= len(SEQUENCE_STEPS):
            conn.execute("UPDATE automation_enrollments SET stopped = 1 WHERE id = ?", (enrollment["id"],))
            conn.commit()
            continue

        step_info = SEQUENCE_STEPS[current_step]

        # "Send" the follow-up (demo mode: log it, same pattern as your email logging)
        print(f"[AUTOMATION] Lead {lead_id} ({lead['name']}): {step_info['action']}")

        conn.execute(
            "INSERT INTO automation_log (lead_id, step, action, executed_at) VALUES (?, ?, ?, ?)",
            (lead_id, step_info["step"], step_info["action"], datetime.utcnow().isoformat())
        )

        next_step = current_step + 1
        if next_step < len(SEQUENCE_STEPS):
            next_wait = SEQUENCE_STEPS[next_step]["wait_minutes"]
            next_action_at = datetime.utcnow() + timedelta(minutes=next_wait)
            conn.execute(
                "UPDATE automation_enrollments SET step = ?, next_action_at = ? WHERE id = ?",
                (next_step, next_action_at.isoformat(), enrollment["id"])
            )
        else:
            conn.execute("UPDATE automation_enrollments SET stopped = 1 WHERE id = ?", (enrollment["id"],))

        conn.commit()

    conn.close()

scheduler = BackgroundScheduler()

@app.on_event("startup")
def on_startup():
    init_db()
    scheduler.add_job(run_due_automations, "interval", minutes=1)
    scheduler.start()

@app.on_event("shutdown")
def on_shutdown():
    scheduler.shutdown()

def calculate_lead_score(lead: dict) -> dict:
    reasons = []
    score = 0

    if lead.get("phone"):
        score += 15
        reasons.append({"points": 15, "reason": "Provided phone number"})

    if lead.get("message") and len(lead["message"]) > 20:
        score += 15
        reasons.append({"points": 15, "reason": "Included detailed message"})

    high_value_services = ["Remodeling", "Landscaping"]
    if lead.get("service") in high_value_services:
        score += 20
        reasons.append({"points": 20, "reason": f"Requested {lead['service']} (high-value service)"})
    elif lead.get("service"):
        score += 10
        reasons.append({"points": 10, "reason": f"Requested {lead['service']}"})

    created = datetime.fromisoformat(lead["created_at"])
    hours_old = (datetime.utcnow() - created).total_seconds() / 3600
    if hours_old < 24:
        score += 15
        reasons.append({"points": 15, "reason": "Submitted within the last 24 hours"})

    if score >= 50:
        temperature = "Hot"
    elif score >= 25:
        temperature = "Warm"
    else:
        temperature = "Cold"

    return {"score": score, "temperature": temperature, "reasons": reasons}

@app.get("/")
def read_root():
    return {"message": "Ridge & Co API is running"}

@app.post("/api/leads")
def create_lead(lead: LeadCreate):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    cursor = conn.execute(
        "INSERT INTO leads (name, email, phone, service, message, status, created_at) VALUES (?, ?, ?, ?, ?, 'new', ?)",
        (lead.name, lead.email, lead.phone, lead.service, lead.message, now)
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()

    enroll_lead_in_automation(new_id)

    return {"id": new_id, "name": lead.name, "email": lead.email, "status": "new"}

@app.get("/api/leads")
def list_leads():
    conn = get_db()
    rows = conn.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()
    conn.close()
    leads = []
    for row in rows:
        lead = dict(row)
        lead["scoring"] = calculate_lead_score(lead)
        leads.append(lead)
    return leads

@app.get("/api/leads/missed")
def missed_leads(hours_threshold: int = 48):
    conn = get_db()
    rows = conn.execute("SELECT * FROM leads WHERE status = 'new' ORDER BY created_at ASC").fetchall()
    conn.close()

    missed = []
    now = datetime.utcnow()

    for row in rows:
        lead = dict(row)
        created = datetime.fromisoformat(lead["created_at"])
        hours_waiting = (now - created).total_seconds() / 3600

        if hours_waiting >= hours_threshold:
            lead["scoring"] = calculate_lead_score(lead)
            lead["hours_waiting"] = round(hours_waiting, 1)
            lead["reason"] = f"No contact for {round(hours_waiting)} hours since submission"
            missed.append(lead)

    estimated_opportunity = sum(
        500 if l["scoring"]["temperature"] == "Hot" else
        250 if l["scoring"]["temperature"] == "Warm" else 100
        for l in missed
    )

    return {
        "count": len(missed),
        "estimated_opportunity": estimated_opportunity,
        "leads": missed
    }

@app.get("/api/leads/{lead_id}/automation")
def get_lead_automation(lead_id: int):
    conn = get_db()
    enrollment = conn.execute(
        "SELECT * FROM automation_enrollments WHERE lead_id = ? ORDER BY id DESC LIMIT 1", (lead_id,)
    ).fetchone()
    log = conn.execute(
        "SELECT * FROM automation_log WHERE lead_id = ? ORDER BY executed_at ASC", (lead_id,)
    ).fetchall()
    conn.close()

    if enrollment is None:
        return {"enrolled": False}

    return {
        "enrolled": True,
        "current_step": enrollment["step"],
        "next_action_at": enrollment["next_action_at"],
        "stopped": bool(enrollment["stopped"]),
        "history": [dict(l) for l in log]
    }

@app.get("/api/leads/{lead_id}")
def get_lead(lead_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    conn.close()
    if row is None:
        return {"error": "Lead not found"}
    lead = dict(row)
    lead["scoring"] = calculate_lead_score(lead)
    return lead

@app.patch("/api/leads/{lead_id}")
def update_status(lead_id: int, update: StatusUpdate):
    conn = get_db()
    conn.execute("UPDATE leads SET status = ? WHERE id = ?", (update.status, lead_id))
    conn.commit()
    conn.close()
    return {"id": lead_id, "status": update.status}