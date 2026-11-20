import asyncio
from .runtime import runtime_lock
from sqlalchemy import update
from urllib.parse import quote
from sqlalchemy import Boolean, func
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from .common import *
from .events import validate_event
from .storage import operation

class AvailableFile(Base):
    __tablename__ = 'available_files'
    file_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    storage_key: Mapped[str | None] = mapped_column(String(100))
    original_name: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    file_version: Mapped[int] = mapped_column(Integer)
    deletion_request_id: Mapped[str | None] = mapped_column(String(36))
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    ready_sent: Mapped[bool] = mapped_column(Boolean, default=False)

class DownloadSession(Base):
    __tablename__ = 'download_sessions'
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[str] = mapped_column(String(36))

class DownloadHistory(Base):
    __tablename__ = 'download_history'
    download_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

async def ready_if_drained(db, item):
    active = await db.scalar(select(func.count()).select_from(DownloadSession).where(
        DownloadSession.file_id == item.file_id, DownloadSession.status == 'ACTIVE'))
    if item.status == 'DRAINING' and not active and not item.ready_sent:
        emit(db, 'file.deletion.ready', item.file_id, None,
            dict(file_id=item.file_id, deletion_request_id=item.deletion_request_id), item.correlation_id)
        item.ready_sent = True

async def apply_event(db, event):
    kind, payload = event['event_type'], event['payload']
    if kind not in {'file.created','file.deletion.requested','file.deleted'}:
        raise ValueError('Unexpected event')
    item = await db.get(AvailableFile, event['aggregate_id'], with_for_update=True)
    if item and item.file_version >= event['aggregate_version']:
        return
    if not item:
        item = AvailableFile(file_id=event['aggregate_id'], status='DRAINING', file_version=0, ready_sent=False)
        db.add(item)
    item.file_version = event['aggregate_version']
    if kind == 'file.created':
        item.storage_key, item.original_name = payload['storage_key'], payload['original_name']
        item.content_type, item.size_bytes = payload['content_type'], payload['size_bytes']
        item.status = 'AVAILABLE'
    elif kind == 'file.deletion.requested':
        item.status = 'DRAINING'
        item.deletion_request_id = payload['deletion_request_id']
        item.correlation_id = event['correlation_id']
        await db.flush()
        await ready_if_drained(db, item)
    else:
        item.status = 'DELETED'

async def finish(session_id, success):
    async with Session.begin() as db:
        session = await db.get(DownloadSession, session_id)
        item = await db.get(AvailableFile, session.file_id, with_for_update=True)
        if session.status != 'ACTIVE':
            return
        session.status = 'COMPLETED' if success else 'FAILED'
        session.finished_at = now()
        if success:
            db.add(DownloadHistory(download_id=session_id, file_id=session.file_id, user_id=session.user_id, completed_at=session.finished_at))
            emit(db, 'file.downloaded', session.file_id, None, dict(download_id=session_id, file_id=session.file_id,
                user_id=session.user_id, completed_at=iso(session.finished_at)), session.correlation_id)
        await db.flush()
        await ready_if_drained(db, item)

class DownloadResponse(StreamingResponse):
    # ASGI final send must succeed before committing a successful history entry.
    def __init__(self, *args, session_id, storage_body, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_id, self.storage_body = session_id, storage_body

    async def __call__(self, scope, receive, send):
        completed = False
        async def tracked_send(message):
            nonlocal completed
            await send(message)
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                completed = True
        try:
            await super().__call__(scope, receive, tracked_send)
        finally:
            self.storage_body.close()
            task = asyncio.create_task(finish(self.session_id, completed))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

@app.get('/api/files/{file_id}/download')
async def download(file_id: uuid.UUID, request: Request):
    user = identity(request, required=False)
    session_id = uid()
    async with Session.begin() as db:
        # Same row lock as DRAINING transition; no long transaction during streaming.
        # Advisory lock covers the absent-row case while processing initial events.
        await db.execute(text('SELECT pg_advisory_xact_lock(731109)'))
        item = await db.get(AvailableFile, str(file_id), with_for_update=True)
        if not item:
            raise Problem(404, 'FILE_NOT_FOUND', 'File not found')
        if item.status == 'DELETED':
            raise Problem(410, 'FILE_DELETED', 'File has been deleted')
        if item.status != 'AVAILABLE':
            raise Problem(409, 'FILE_NOT_AVAILABLE', 'File is being deleted')
        db.add(DownloadSession(session_id=session_id, file_id=item.file_id, user_id=user, status='ACTIVE',
            started_at=now(), correlation_id=request.state.request_id))
    try:
        obj = await operation('get_object', Key=item.storage_key)
    except Exception:
        await finish(session_id, False)
        raise
    async def chunks():
        remaining = item.size_bytes
        while remaining:
            chunk = await run_in_threadpool(obj['Body'].read, min(65536, remaining))
            if not chunk:
                raise IOError('Truncated object')
            remaining -= len(chunk)
            yield chunk
    name = ''.join(c for c in (item.original_name or 'file') if ord(c) >= 32 and ord(c) != 127)
    return DownloadResponse(chunks(), session_id=session_id, storage_body=obj['Body'], media_type=item.content_type,
        headers={'Content-Disposition': "attachment; filename=file; filename*=UTF-8''" + quote(name, safe=''),
                 'Content-Length': str(item.size_bytes)})

async def recover():
    # Fail closed: ACTIVE sessions never expire merely because a clock elapsed.
    # Operator-confirmed API shutdown is required to reconcile crashed streams.
    async with Session.begin() as db:
        items = (await db.scalars(select(AvailableFile).where(AvailableFile.status=='DRAINING').with_for_update(skip_locked=True))).all()
        for item in items:
            await ready_if_drained(db, item)


@asynccontextmanager
async def download_lifespan(application):
    # One API process on one Docker host. The shared kernel lock cannot expire
    # while a previous process still exists, including during a DB partition.
    with runtime_lock('download-api'):
        async with Session.begin() as db:
            await db.execute(update(DownloadSession).where(DownloadSession.status=='ACTIVE').values(
                status='FAILED',finished_at=now()))
        async with lifespan(application):
            yield

app.router.lifespan_context=download_lifespan
