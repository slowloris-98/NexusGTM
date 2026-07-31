"""Control plane API. Reads the store; the only write is kicking off a run.

Answers business questions -- did it work, was it worth it -- backed by the
per-call data underneath.
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from typing import Annotated, Iterator

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core import registry
from core.orchestrator import Orchestrator
from store import db, queries

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db().close()  # create the schema once, then connect per request
    yield


app = FastAPI(title="NexusGTM Control Plane", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_conn() -> Iterator[sqlite3.Connection]:
    """One connection per request.

    FastAPI runs sync endpoints in a threadpool and SQLite connections cannot
    cross threads, so a shared module-level connection is not an option. WAL is
    what makes the concurrent readers cheap.
    """
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


class LaunchRequest(BaseModel):
    crm_reference_id: str
    lead: dict


@app.get("/orchestrations")
def list_orchestrations(conn: Conn, limit: int = 100):
    return queries.list_orchestrations(conn, limit)


@app.get("/orchestrations/{orchestration_id}")
def get_orchestration(orchestration_id: str, conn: Conn):
    found = queries.get_orchestration(conn, orchestration_id)
    if found is None:
        raise HTTPException(404, "orchestration not found")
    return found


@app.post("/orchestrations")
async def launch(request: LaunchRequest, conn: Conn):
    orchestrator = Orchestrator(conn)
    return await orchestrator.run(request.crm_reference_id, request.lead)


@app.get("/costs")
def costs(conn: Conn):
    return {
        "by_agent": queries.cost_by_agent(conn),
        "by_department": queries.cost_by_department(conn),
        "per_outcome": queries.cost_per_outcome(conn),
        "trend": queries.cost_trend(conn),
    }


@app.get("/departments")
async def departments():
    """Registry-derived taxonomy. New departments appear here automatically."""
    discovery = await registry.discover()
    grouped: dict[str, dict] = {}
    for spec in discovery.agents:
        entry = grouped.setdefault(
            spec.department,
            {
                "id": spec.department,
                "name": spec.department_name,
                "endpoint": spec.endpoint,
                "agents": [],
            },
        )
        entry["agents"].append({"name": spec.agent, "description": spec.description})
    return {
        "departments": list(grouped.values()),
        "unreachable": discovery.unreachable,
    }


@app.get("/search")
def search(q: str, conn: Conn, limit: int = 50):
    """Plain-text search across past runs -- the knowledge base."""
    if not q.strip():
        return []
    return queries.search_runs(conn, q.strip(), limit)
