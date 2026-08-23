from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime
import sqlite3

app = FastAPI()

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
    return [dict(row) for row in rows]

@app.patch("/api/leads/{lead_id}")
def update_status(lead_id: int, update: StatusUpdate):
    conn = get_db()
    conn.execute("UPDATE leads SET status = ? WHERE id = ?", (update.status, lead_id))
    conn.commit()
    conn.close()
    return {"id": lead_id, "status": update.status}