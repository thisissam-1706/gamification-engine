"""FastAPI app: read-only JSON endpoints for the learner home page.

Run from the repo root:  uvicorn webapp.app:app --port 8000   (page at http://localhost:8000/)
Seed first:              python -m webapp.seed
"""

from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from webapp import views
from webapp.seed import AS_OF

app = FastAPI(title="Learner home API (read-only)")

# CORS for localhost origins only, any port.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message})


@app.get("/api/learners")
def get_learners():
    return views.list_learners()


@app.get("/api/learners/{learner_id}/home")
def get_home(learner_id: str, as_of: str | None = Query(default=None)):
    try:
        when = datetime.fromisoformat((as_of or AS_OF).replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError("no timezone")
    except ValueError:
        return _error(422, "invalid_as_of", "as_of must be an ISO-8601 timestamp with a timezone")
    try:
        UUID(learner_id)
        return views.build_home(learner_id, when)
    except (ValueError, views.UnknownLearner):
        return _error(404, "learner_not_found", f"No learner with id {learner_id!r}")


# Static home page at "/". Mounted last so the /api routes above take precedence.
app.mount(
    "/", StaticFiles(directory=Path(__file__).resolve().parent / "static", html=True), name="static"
)
