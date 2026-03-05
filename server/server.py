"""
AI Factory Dashboard — FastAPI server.
Serves Kanban board UI and API endpoints.
"""

import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Project root
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.task_card import TaskCard
from core.state_machine import StateMachine

app = FastAPI(title="AI Factory Dashboard", version="1.0.0")

PIPELINE_ROOT = BASE_DIR / "pipeline"
REGISTRY_DIR = BASE_DIR / "registry"

# Serve static files
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the Kanban board."""
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>AI Factory</h1><p>Dashboard not found. Place index.html in server/static/</p>"


@app.get("/api/pipeline")
async def get_pipeline():
    """Get all tasks grouped by pipeline stage."""
    sm = StateMachine(PIPELINE_ROOT)
    stages = [
        "0_inbox", "1_planning", "2_ready", "3_in_progress",
        "4_review", "5_rework", "6_completed", "7_archived",
    ]

    result = {}
    for stage in stages:
        tasks = sm.get_tasks(stage)
        result[stage] = [t.model_dump() for t in tasks]

    return JSONResponse(result)


@app.get("/api/summary")
async def get_summary():
    """Get pipeline summary counts + costs."""
    sm = StateMachine(PIPELINE_ROOT)
    summary = sm.get_pipeline_summary()

    # Load dashboard data
    dashboard_path = REGISTRY_DIR / "dashboard.json"
    dashboard = {}
    if dashboard_path.exists():
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

    return JSONResponse({
        "pipeline": summary,
        "costs": dashboard.get("costs", {}),
        "agents": dashboard.get("agents", {}),
        "agent_costs": dashboard.get("agent_costs", {}),
        "updated_at": dashboard.get("updated_at", ""),
    })


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """Get a specific task card."""
    stages = [
        "0_inbox", "1_planning", "2_ready", "3_in_progress",
        "4_review", "5_rework", "6_completed", "7_archived",
    ]
    for stage in stages:
        task_path = PIPELINE_ROOT / stage / f"{task_id}.json"
        if task_path.exists():
            task = TaskCard.load(task_path)
            return JSONResponse(task.model_dump())

    return JSONResponse({"error": f"Task {task_id} not found"}, status_code=404)


@app.get("/api/agents")
async def get_agents():
    """Get all registered agents."""
    agents_path = REGISTRY_DIR / "agents.json"
    if agents_path.exists():
        data = json.loads(agents_path.read_text(encoding="utf-8"))
        return JSONResponse(data)
    return JSONResponse({"agents": []})


@app.post("/api/submit")
async def submit_task(body: dict):
    """Submit a new task via API."""
    task = TaskCard(
        title=body.get("title", body.get("description", "")[:80]),
        description=body.get("description", ""),
        priority=body.get("priority", 5),
        tags=body.get("tags", []),
    )
    inbox = PIPELINE_ROOT / "0_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    task.save(inbox)
    return JSONResponse({"id": task.id, "title": task.title, "status": "created"})


def start_server(host: str = "127.0.0.1", port: int = 5050):
    """Start the server (called from start.py)."""
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="warning")
