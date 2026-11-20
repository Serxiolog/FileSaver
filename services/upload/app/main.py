import hashlib
from .runtime import runtime_lock
from fastapi import UploadFile, File as MultipartFile
from sqlalchemy import Boolean
from .common import *
from .events import validate_event
from .storage import operation

class File(Base):
    __tablename__ = 'files'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(100), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str] = mapped_column(String(255))
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deletion_request_id: Mapped[str | None] = mapped_column(String(36))
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready: Mapped[bool] = mapped_column(Boolean, default=False)
    correlation_id: Mapped[str | None] = mapped_column(String(36))

class UploadIntent(Base):
    __tablename__ = 'upload_intents'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

def safe_name(name):
    result = (name or 'file').replace('\\','/').rsplit('/',1)[-1]
    result = ''.join(c for c in result if ord(c) >= 32 and ord(c) != 127).strip()[:255]
    return result or 'file'

@app.post('/api/files', status_code=201)
async def upload(request: Request, file: UploadFile = MultipartFile(...)):
    owner = identity(request)
    data = bytearray()
    while chunk := await file.read(65536):
        data.extend(chunk)
        if len(data) > 1048576:
            raise Problem(413, 'FILE_TOO_LARGE', 'Maximum file size is 1048576 bytes')
    file_id = uid()
    with runtime_lock(file_id):
        # Durable intent before the external side effect enables orphan cleanup.
        async with Session.begin() as db:
            db.add(UploadIntent(id=file_id, created_at=now()))
        name = safe_name(file.filename)
        content_type = file.content_type or 'application/octet-stream'
        if any(ord(c) < 32 or ord(c) == 127 for c in content_type) or len(content_type) > 255:
            content_type = 'application/octet-stream'
        await operation('put_object', Key='files/' + file_id, Body=bytes(data), ContentType=content_type)
        item = File(id=file_id, owner_id=owner, original_name=name, storage_key='files/'+file_id,
            size_bytes=len(data), content_type=content_type, checksum_sha256=hashlib.sha256(data).hexdigest(),
            status='ACTIVE', version=1, created_at=now(), ready=False)
        async with Session.begin() as db:
            # Intent lock prevents concurrent recovery from deleting a committed object.
            intent = await db.get(UploadIntent, file_id, with_for_update=True)
            if intent is None:
                raise Problem(503, 'SERVICE_UNAVAILABLE', 'Upload expired; retry upload')
            db.add(item)
            await db.delete(intent)
            emit(db, 'file.created', file_id, 1, dict(file_id=file_id, owner_id=owner, original_name=name,
                size_bytes=len(data), content_type=content_type, storage_key=item.storage_key, created_at=iso(item.created_at)), request.state.request_id)
        return dict(id=file_id, owner_id=owner, original_name=name, size_bytes=len(data), content_type=content_type,
            status='ACTIVE', created_at=iso(item.created_at))

@app.delete('/api/files/{file_id}', status_code=202)
async def delete_file(file_id: uuid.UUID, request: Request):
    owner = identity(request)
    async with Session.begin() as db:
        item = await db.get(File, str(file_id), with_for_update=True)
        if not item:
            raise Problem(404, 'FILE_NOT_FOUND', 'File not found')
        if item.owner_id != owner:
            raise Problem(403, 'FILE_ACCESS_DENIED', 'Only the owner can delete this file')
        if item.status == 'DELETED':
            raise Problem(410, 'FILE_DELETED', 'File has been deleted')
        if item.status == 'ACTIVE':
            item.status = 'DELETING'
            item.version += 1
            item.deletion_request_id = uid()
            item.deletion_requested_at = now()
            item.correlation_id = request.state.request_id
            emit(db, 'file.deletion.requested', item.id, item.version,
                 dict(file_id=item.id, deletion_request_id=item.deletion_request_id, requested_at=iso(item.deletion_requested_at)), item.correlation_id)
        return dict(file_id=item.id, deletion_request_id=item.deletion_request_id, status='DELETING', message='File deletion has been scheduled')

async def apply_event(db, event):
    if event['event_type'] != 'file.deletion.ready':
        raise ValueError('Unexpected event')
    item = await db.get(File, event['aggregate_id'], with_for_update=True)
    if item and item.status == 'DELETING' and item.deletion_request_id == event['payload']['deletion_request_id']:
        item.ready = True

async def recover():
    # Readiness is committed before DeleteObject; a crash safely repeats DeleteObject.
    async with Session.begin() as db:
        items = (await db.scalars(select(File).where(File.status=='DELETING', File.ready.is_(True)).with_for_update(skip_locked=True).limit(10))).all()
        for item in items:
            await operation('delete_object', Key=item.storage_key)
            item.status, item.deleted_at = 'DELETED', now()
            item.version += 1
            emit(db, 'file.deleted', item.id, item.version, dict(file_id=item.id,
                deletion_request_id=item.deletion_request_id, deleted_at=iso(item.deleted_at)), item.correlation_id)
    # The kernel lock is held through PutObject and metadata commit. Acquiring
    # it proves that no request/process can still be using this intent.
    async with Session.begin() as db:
        intents=(await db.scalars(select(UploadIntent).with_for_update(skip_locked=True).limit(20))).all()
        for intent in intents:
            try:
                with runtime_lock(intent.id):
                    if await db.get(File,intent.id) is None:
                        await operation('delete_object',Key='files/'+intent.id)
                    await db.delete(intent)
            except BlockingIOError:
                continue
