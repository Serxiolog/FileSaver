import os
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager
import jwt
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import String, DateTime, Integer, Text, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.exc import SQLAlchemyError

class Base(DeclarativeBase):
    pass

class Outbox(Base):
    __tablename__ = 'outbox_events'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    body: Mapped[dict] = mapped_column(JSONB)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class Inbox(Base):
    __tablename__ = 'processed_events'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)

engine = create_async_engine(os.environ.get('DATABASE_URL', 'postgresql+psycopg://ampis:ampis@localhost/ampis'), pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False)

def now():
    return datetime.now(timezone.utc)

def uid():
    return str(uuid.uuid4())

def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

class Problem(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message

def identity(request, required=True):
    authorization = request.headers.get('authorization')
    if not authorization and not required:
        return None
    try:
        kind, token = authorization.split(' ', 1)
        if kind.lower() != 'bearer':
            raise ValueError()
        claims = jwt.decode(token, Path(os.environ['JWT_PUBLIC_KEY_FILE']).read_text(), algorithms=['RS256'],
                            issuer=os.environ['JWT_ISSUER'], audience=os.environ['JWT_AUDIENCE'],
                            options={'require': ['sub', 'iat', 'exp', 'iss', 'aud']})
        return str(uuid.UUID(claims['sub']))
    except (ValueError, TypeError, AttributeError, KeyError, jwt.PyJWTError):
        raise Problem(401, 'AUTH_REQUIRED', 'A valid access token is required')

def emit(db, kind, aggregate, version, payload, correlation=None):
    event_id = uid()
    db.add(Outbox(id=event_id, body=dict(event_id=event_id, event_type=kind, event_version=1,
        aggregate_id=aggregate, aggregate_version=version, occurred_at=iso(now()),
        correlation_id=correlation or uid(), payload=payload)))

@asynccontextmanager
async def lifespan(app):
    yield
    await engine.dispose()

app = FastAPI(lifespan=lifespan)

@app.middleware('http')
async def request_context(request, call_next):
    try:
        request.state.request_id = str(uuid.UUID(request.headers.get('x-request-id', '')))
    except ValueError:
        request.state.request_id = uid()
    response = await call_next(request)
    response.headers['X-Request-ID'] = request.state.request_id
    return response

@app.exception_handler(Problem)
async def problem_handler(request, error):
    return JSONResponse(status_code=error.status, content={'error': {'code': error.code, 'message': error.message,
        'request_id': request.state.request_id}})

@app.exception_handler(RequestValidationError)
async def validation_handler(request, error):
    return await problem_handler(request, Problem(422, 'VALIDATION_ERROR', 'Invalid request fields'))

@app.exception_handler(SQLAlchemyError)
async def database_handler(request, error):
    return await problem_handler(request, Problem(503, 'SERVICE_UNAVAILABLE', 'Database unavailable'))

@app.get('/health/live')
async def live():
    return {'status': 'ok'}

@app.get('/health/ready')
async def ready():
    async with Session() as db:
        await db.execute(text('SELECT 1'))
    return {'status': 'ready'}

from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
async def http_handler(request, error):
    return await problem_handler(request, Problem(error.status_code, 'INVALID_REQUEST', str(error.detail)))
