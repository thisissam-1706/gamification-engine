"""FastAPI app for learner home and optional writable demo endpoints.

Run from the repo root:  uvicorn webapp.app:app --port 8000   (page at http://localhost:8000/)
Seed first:              python -m webapp.seed
"""

from datetime import datetime
import os
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfoNotFoundError

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from webapp import views
from webapp.seed import AS_OF

DEMO_MODE = os.environ.get("DEMO_MODE") == "1"
DEMO_SANDBOX_PATH = os.environ.get("DEMO_SANDBOX_PATH")

if DEMO_MODE and not DEMO_SANDBOX_PATH:
    raise RuntimeError("DEMO_SANDBOX_PATH must be set when DEMO_MODE=1")

demo_sandbox = None
if DEMO_MODE:
    from webapp.demo_backend import Sandbox, create_sandbox

    sandbox_path = Path(DEMO_SANDBOX_PATH)
    if not (sandbox_path / "live" / "ui_profile.json").exists():
        demo_sandbox = create_sandbox(sandbox_path)
    else:
        demo_sandbox = Sandbox(sandbox_path)

app = FastAPI(title="Learner home API")

# CORS for localhost origins only, any port.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["GET", "POST"] if DEMO_MODE else ["GET"],
    allow_headers=["*"],
)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message})


@app.get("/api/learners")
def get_learners():
    return views.list_learners(demo_sandbox.live_dir if demo_sandbox else views.DATA_DIR)


@app.get("/api/learners/{learner_id}/home")
def get_home(learner_id: str, as_of: str | None = Query(default=None)):
    try:
        default_as_of = (
            demo_sandbox.get_clock(learner_id).isoformat()
            if demo_sandbox and as_of is None
            else (as_of or AS_OF)
        )
        when = datetime.fromisoformat(default_as_of.replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError("no timezone")
    except ValueError:
        return _error(422, "invalid_as_of", "as_of must be an ISO-8601 timestamp with a timezone")
    try:
        UUID(learner_id)
        return views.build_home(
            learner_id,
            when,
            data_dir=demo_sandbox.live_dir if demo_sandbox else views.DATA_DIR,
        )
    except ZoneInfoNotFoundError as exc:
        return _error(422, "invalid_timezone", f"Unknown timezone in learner profile: {exc}")
    except (ValueError, views.UnknownLearner):
        return _error(404, "learner_not_found", f"No learner with id {learner_id!r}")


if DEMO_MODE:
    from webapp.demo_routes import create_demo_router

    app.include_router(create_demo_router(demo_sandbox))
else:
    @app.post("/api/demo/{path:path}")
    def demo_routes_disabled(path: str):
        return _error(404, "demo_mode_disabled", "Demo routes require DEMO_MODE=1")


# Static home page at "/". Mounted last so the /api routes above take precedence.
app.mount(
    "/", StaticFiles(directory=Path(__file__).resolve().parent / "static", html=True), name="static"
)
