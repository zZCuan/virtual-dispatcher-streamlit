"""Independent API service for province, city and county dispatch agents.

Run separately with:
    uvicorn agent_service:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
DB_PATH = Path(os.getenv("AGENT_SERVICE_DB_PATH", "/tmp/dispatch_agent_service.db"))
AGENT_SERVICE_TOKEN = os.getenv("AGENT_SERVICE_TOKEN", "")
app = FastAPI(title="龙江电网三级调度智能体服务", version="1.0.0")


class HeartbeatRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    level: str = Field(pattern="^(province|city|county)$")


class TicketCreateRequest(BaseModel):
    target_county: str
    title: str
    steps: str
    sender: str = "黑龙江省调度中心"
    receiver: str
    request_key: str | None = None


class ExecuteRequest(BaseModel):
    executed_by: str


def authorize(x_agent_token: str | None = Header(default=None)) -> None:
    if AGENT_SERVICE_TOKEN and x_agent_token != AGENT_SERVICE_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid agent service token")


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS dispatch_messages (
            id TEXT PRIMARY KEY, created_at REAL NOT NULL, sender TEXT NOT NULL,
            receiver TEXT NOT NULL, title TEXT NOT NULL, ticket_no TEXT NOT NULL,
            target_county TEXT NOT NULL, content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '已送达', acknowledged_at REAL,
            executed_at REAL, executed_by TEXT, request_key TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_presence (
            agent_id TEXT PRIMARY KEY, level TEXT NOT NULL, last_seen REAL NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_request_key
        ON dispatch_messages(request_key) WHERE request_key IS NOT NULL
        """
    )
    connection.commit()
    return connection


def serialize(row: sqlite3.Row) -> dict:
    return dict(row)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "three-level-dispatch-agents"}


@app.post("/v1/agents/heartbeat", dependencies=[Depends(authorize)])
def heartbeat(request: HeartbeatRequest) -> dict:
    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO agent_presence(agent_id, level, last_seen) VALUES (?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                level=excluded.level, last_seen=excluded.last_seen
            """,
            (request.agent_id, request.level, time.time()),
        )
        connection.commit()
    return {"ok": True}


@app.get("/v1/agents/online", dependencies=[Depends(authorize)])
def online_agents(max_age_seconds: int = Query(default=30, ge=5, le=3600)) -> dict:
    with connect_db() as connection:
        rows = connection.execute(
            "SELECT agent_id FROM agent_presence WHERE last_seen>=?",
            (time.time() - max_age_seconds,),
        ).fetchall()
    return {"agents": [row["agent_id"] for row in rows]}


@app.post("/v1/tickets", dependencies=[Depends(authorize)])
def create_ticket(request: TicketCreateRequest) -> dict:
    ticket_no = f"HLJ-{datetime.now(BEIJING_TZ).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    content = (
        f"{request.receiver}调度员，请执行以下操作票任务。{request.title}。"
        f"{request.steps.replace(chr(10), '；')}。操作完成后立即回令。"
    )
    with connect_db() as connection:
        if request.request_key:
            existing = connection.execute(
                "SELECT ticket_no FROM dispatch_messages WHERE request_key=?",
                (request.request_key,),
            ).fetchone()
            if existing:
                return {"ticket_no": existing["ticket_no"], "created": False}
        connection.execute(
            """
            INSERT INTO dispatch_messages
            (id, created_at, sender, receiver, title, ticket_no, target_county,
             content, status, request_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, '已送达', ?)
            """,
            (
                uuid.uuid4().hex, time.time(), request.sender, request.receiver,
                request.title, ticket_no, request.target_county, content,
                request.request_key,
            ),
        )
        connection.commit()
    return {"ticket_no": ticket_no, "created": True}


@app.get("/v1/tickets", dependencies=[Depends(authorize)])
def list_tickets(receiver: str | None = None, party: str | None = None) -> dict:
    sql = "SELECT * FROM dispatch_messages"
    parameters: tuple = ()
    if receiver:
        sql += " WHERE receiver=?"
        parameters = (receiver,)
    elif party:
        sql += " WHERE receiver=? OR sender=?"
        parameters = (party, party)
    sql += " ORDER BY created_at DESC"
    with connect_db() as connection:
        rows = connection.execute(sql, parameters).fetchall()
    return {"tickets": [serialize(row) for row in rows]}


def require_ticket(connection: sqlite3.Connection, message_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM dispatch_messages WHERE id=?", (message_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return row


@app.post("/v1/tickets/{message_id}/ack", dependencies=[Depends(authorize)])
def acknowledge(message_id: str) -> dict:
    with connect_db() as connection:
        require_ticket(connection, message_id)
        connection.execute(
            "UPDATE dispatch_messages SET status='已签收', acknowledged_at=? WHERE id=?",
            (time.time(), message_id),
        )
        connection.commit()
    return {"ok": True}


@app.post("/v1/tickets/{message_id}/execute", dependencies=[Depends(authorize)])
def execute(message_id: str, request: ExecuteRequest) -> dict:
    with connect_db() as connection:
        row = require_ticket(connection, message_id)
        now = time.time()
        connection.execute(
            """
            UPDATE dispatch_messages SET status='已执行', acknowledged_at=?,
                executed_at=?, executed_by=? WHERE ticket_no=?
            """,
            (now, now, request.executed_by, row["ticket_no"]),
        )
        connection.commit()
    return {"ok": True}


@app.post("/v1/tickets/{message_id}/forward", dependencies=[Depends(authorize)])
def forward(message_id: str) -> dict:
    with connect_db() as connection:
        row = require_ticket(connection, message_id)
        county = row["target_county"]
        city_center = row["receiver"]
        city_name = city_center.removesuffix("调度中心")
        county_agent = (
            f"{city_name}{county}调度智能体"
            if county == "向阳区"
            else f"{county}调度智能体"
        )
        content = row["content"].replace(
            f"{city_center}调度员", f"{county}调度员", 1
        )
        duplicate = connection.execute(
            """
            SELECT 1 FROM dispatch_messages
            WHERE ticket_no=? AND sender=? AND target_county=?
            """,
            (row["ticket_no"], city_center, county),
        ).fetchone()
        if duplicate is None:
            connection.execute(
                """
                INSERT INTO dispatch_messages
                (id, created_at, sender, receiver, title, ticket_no,
                 target_county, content, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, '已送达')
                """,
                (
                    uuid.uuid4().hex, time.time(), city_center,
                    county_agent, row["title"], row["ticket_no"],
                    county, content,
                ),
            )
        connection.execute(
            "UPDATE dispatch_messages SET status='已转发', acknowledged_at=? WHERE id=?",
            (time.time(), message_id),
        )
        connection.commit()
    return {"ok": True}
