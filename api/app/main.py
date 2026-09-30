import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.middleware.base import RequestResponseEndpoint

from app.auth import router as auth_router
from app.categories import router as categories_router
from app.contacts import router as contacts_router
from app.dedup_review import router as review_router
from app.events import router as events_router
from app.feedback import router as feedback_router
from app.graph import graph_driver
from app.invites import router as invites_router
from app.similar_events import router as similar_events_router


class HealthResponse(BaseModel):
    status: Literal["ok"]


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    yield
    if graph_driver.cache_info().currsize:
        graph_driver().close()
        graph_driver.cache_clear()


app = FastAPI(title="Event Discovery API", lifespan=lifespan)


@app.middleware("http")
async def private_auth_responses(request: Request, call_next: RequestResponseEndpoint) -> Response:
    response = await call_next(request)
    if request.url.path == "/api/me" or request.url.path.startswith(
        ("/api/auth/", "/api/invites", "/api/attendance", "/api/contacts")
    ):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response

allowed_origins = [
    origin.strip() for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if origin.strip()
]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

app.include_router(events_router)
app.include_router(similar_events_router)
app.include_router(categories_router)
app.include_router(review_router)
app.include_router(auth_router)
app.include_router(invites_router)
app.include_router(feedback_router)
app.include_router(contacts_router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok")
