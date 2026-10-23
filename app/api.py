"""HTTP layer. All lifecycle rules live in app.domain."""
import secrets
import uuid
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictInt, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain import EventType, InvalidTransition, Parcel, ParcelState, apply_event

PROBLEM = "application/problem+json"


class ProblemError(Exception):
    def __init__(self, status: int, title: str, detail: str | None = None):
        self.status = status
        self.title = title
        self.detail = detail


class ParcelCreate(BaseModel):
    merchant_ref: StrictStr = Field(min_length=1, max_length=64)
    cod_amount_piastres: StrictInt = Field(ge=0, le=5_000_000)
    address_text: StrictStr = Field(min_length=1, max_length=500)


class ParcelOut(BaseModel):
    id: str
    merchant_ref: str
    cod_amount_piastres: int
    address_text: str
    state: ParcelState
    attempts: int


class ParcelPage(BaseModel):
    items: list[ParcelOut]
    next_cursor: str | None


class EventIn(BaseModel):
    type: EventType


class ProblemOut(BaseModel):
    type: str
    title: str
    status: int
    detail: str | None = None


class ValidationProblemOut(ProblemOut):
    errors: list[dict[str, Any]]


def _problem(status: int, title: str, detail: str | None = None, **extra: Any) -> JSONResponse:
    body: dict[str, Any] = {"type": "about:blank", "title": title, "status": status}
    if detail:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM)


def _resp(status: int, description: str, model: type[BaseModel] = ProblemOut) -> dict:
    return {"description": description, "model": model}


def create_app() -> FastAPI:
    app = FastAPI(title="Tawseel parcel API", version="1.0.0")

    parcels: dict[str, Parcel] = {}
    order: list[str] = []  # creation order; index = position
    refs: dict[str, str] = {}  # merchant_ref -> parcel id
    cursors: dict[str, int] = {}  # issued cursor -> index of last item on its page

    @app.exception_handler(ProblemError)
    async def _on_problem(request: Request, exc: ProblemError):
        return _problem(exc.status, exc.title, exc.detail)

    @app.exception_handler(StarletteHTTPException)
    async def _on_http(request: Request, exc: StarletteHTTPException):
        return _problem(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _on_validation(request: Request, exc: RequestValidationError):
        errors: list[dict[str, Any]] = []
        seen: set[tuple] = set()
        for err in exc.errors():
            loc = [p if isinstance(p, (str, int)) else str(p) for p in err["loc"]]
            # one entry per invalid field
            key = tuple(loc[:2])
            if key in seen:
                continue
            seen.add(key)
            errors.append({"loc": loc, "msg": err["msg"]})
        return _problem(422, "Validation error", "The request is invalid.", errors=errors)

    @app.post(
        "/v1/parcels",
        operation_id="createParcel",
        status_code=201,
        response_model=ParcelOut,
        responses={
            409: _resp(409, "A parcel with this merchant_ref already exists"),
            422: _resp(422, "Invalid request body", ValidationProblemOut),
        },
    )
    def create_parcel(body: ParcelCreate, response: Response):
        if body.merchant_ref in refs:
            raise ProblemError(409, "Duplicate merchant_ref", f"merchant_ref {body.merchant_ref!r} already exists.")
        parcel = Parcel(
            id=f"pcl_{uuid.uuid4().hex[:16]}",
            merchant_ref=body.merchant_ref,
            cod_amount_piastres=body.cod_amount_piastres,
            address_text=body.address_text,
        )
        parcels[parcel.id] = parcel
        order.append(parcel.id)
        refs[parcel.merchant_ref] = parcel.id
        response.headers["Location"] = f"/v1/parcels/{parcel.id}"
        return asdict(parcel)

    @app.get(
        "/v1/parcels/{parcel_id}",
        operation_id="getParcel",
        response_model=ParcelOut,
        responses={404: _resp(404, "Parcel not found")},
    )
    def get_parcel(parcel_id: str):
        parcel = parcels.get(parcel_id)
        if parcel is None:
            raise ProblemError(404, "Parcel not found", f"No parcel with id {parcel_id!r}.")
        return asdict(parcel)

    @app.get(
        "/v1/parcels",
        operation_id="listParcels",
        response_model=ParcelPage,
        responses={
            400: _resp(400, "Invalid cursor"),
            422: _resp(422, "Invalid state or limit", ValidationProblemOut),
        },
    )
    def list_parcels(
        state: ParcelState | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None),
    ):
        start = 0
        if cursor is not None:
            if cursor not in cursors:
                raise ProblemError(400, "Invalid cursor", "The cursor was not issued by this server.")
            start = cursors[cursor] + 1
        items: list[Parcel] = []
        last_index = -1
        has_more = False
        for i in range(start, len(order)):
            p = parcels[order[i]]
            if state is not None and p.state != state:
                continue
            if len(items) == limit:
                has_more = True
                break
            items.append(p)
            last_index = i
        next_cursor = None
        if has_more:
            next_cursor = "c_" + secrets.token_urlsafe(12)
            cursors[next_cursor] = last_index
        return {"items": [asdict(p) for p in items], "next_cursor": next_cursor}

    @app.post(
        "/v1/parcels/{parcel_id}/events",
        operation_id="recordParcelEvent",
        response_model=ParcelOut,
        responses={
            404: _resp(404, "Parcel not found"),
            409: _resp(409, "Event not allowed in the parcel's current state"),
            422: _resp(422, "Unknown or missing event type", ValidationProblemOut),
        },
    )
    def record_parcel_event(parcel_id: str, body: EventIn):
        parcel = parcels.get(parcel_id)
        if parcel is None:
            raise ProblemError(404, "Parcel not found", f"No parcel with id {parcel_id!r}.")
        try:
            updated = apply_event(parcel, body.type)
        except InvalidTransition as exc:
            raise ProblemError(409, "Invalid transition", str(exc))
        parcels[parcel_id] = updated
        return asdict(updated)

    return app


app = create_app()
