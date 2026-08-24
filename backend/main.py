from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
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
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

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