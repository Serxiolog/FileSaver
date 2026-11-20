from sqlalchemy import func, or_, UniqueConstraint
from fastapi import Query
from .common import *
from .events import validate_event

class UserProfile(Base):
    __tablename__ = 'user_profiles'
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[int] = mapped_column(Integer)

class Catalog(Base):
    __tablename__ = 'file_catalog'
    file_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(String(36), index=True)
    original_name: Mapped[str | None] = mapped_column(String(255), index=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), index=True)
    version: Mapped[int] = mapped_column(Integer)

class Download(Base):
    __tablename__ = 'file_downloads'
    download_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

async def apply_event(db, event):
    kind, payload, version = event['event_type'], event['payload'], event['aggregate_version']
    if kind in {'user.registered','user.renamed'}:
        user = await db.get(UserProfile, payload['user_id'])
        if not user:
            db.add(UserProfile(user_id=payload['user_id'], display_name=payload['display_name'], version=version))
        elif version > user.version:
            user.display_name, user.version = payload['display_name'], version
    elif kind == 'file.downloaded':
        if not await db.get(Download, payload['download_id']):
            db.add(Download(download_id=payload['download_id'], file_id=payload['file_id'], user_id=payload['user_id'],
                downloaded_at=datetime.fromisoformat(payload['completed_at'].replace('Z','+00:00'))))
    elif kind in {'file.created','file.deletion.requested','file.deleted'}:
        item = await db.get(Catalog, payload['file_id'])
        if item and item.version >= version:
            # Fill immutable metadata when creation arrives after a tombstone.
            if kind == 'file.created' and item.owner_id is None:
                item.owner_id, item.original_name = payload['owner_id'], payload['original_name']
                item.size_bytes = payload['size_bytes']
                item.created_at = datetime.fromisoformat(payload['created_at'].replace('Z','+00:00'))
            return
        if not item:
            item = Catalog(file_id=payload['file_id'], version=0, status='DELETING')
            db.add(item)
        item.version = version
        item.status = {'file.created':'ACTIVE','file.deletion.requested':'DELETING','file.deleted':'DELETED'}[kind]
        if kind == 'file.created':
            item.owner_id, item.original_name, item.size_bytes = payload['owner_id'], payload['original_name'], payload['size_bytes']
            item.created_at = datetime.fromisoformat(payload['created_at'].replace('Z','+00:00'))
    else:
        raise ValueError('Unexpected event')

def counts():
    return select(Download.file_id, func.count().label('downloads')).group_by(Download.file_id).subquery()

def serialize(item, name, count):
    return dict(id=item.file_id, original_name=item.original_name, owner=dict(id=item.owner_id,
        display_name=name or 'Имя ещё не получено'), size_bytes=item.size_bytes,
        created_at=iso(item.created_at), download_count=count or 0, download_url=f'/api/files/{item.file_id}/download')

@app.get('/api/files')
async def catalog(name: str = '', owner: str = '', sort: str = Query('name', pattern='^(name|owner|downloads)$'),
                  order: str = Query('asc', pattern='^(asc|desc)$'), page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100)):
    count = counts()
    query = select(Catalog, UserProfile.display_name, func.coalesce(count.c.downloads,0)).outerjoin(
        UserProfile, UserProfile.user_id==Catalog.owner_id).outerjoin(count, count.c.file_id==Catalog.file_id).where(Catalog.status=='ACTIVE')
    def escaped(value):
        return value.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
    if name:
        query = query.where(Catalog.original_name.ilike('%'+escaped(name)+'%', escape='\\'))
    if owner:
        query = query.where(UserProfile.display_name.ilike('%'+escaped(owner)+'%', escape='\\'))
    key = {'name':Catalog.original_name, 'owner':UserProfile.display_name, 'downloads':func.coalesce(count.c.downloads,0)}[sort]
    async with Session() as db:
        total = await db.scalar(select(func.count()).select_from(query.subquery()))
        rows = (await db.execute(query.order_by(key.desc() if order=='desc' else key.asc(), Catalog.file_id.asc()).offset((page-1)*size).limit(size))).all()
        return dict(items=[serialize(*row) for row in rows], page=page, size=size, total=total, pages=(total+size-1)//size)

@app.get('/api/files/{file_id}')
async def card(file_id: uuid.UUID):
    count = counts()
    async with Session() as db:
        row = (await db.execute(select(Catalog, UserProfile.display_name, func.coalesce(count.c.downloads,0)).outerjoin(
            UserProfile, UserProfile.user_id==Catalog.owner_id).outerjoin(count, count.c.file_id==Catalog.file_id).where(
            Catalog.file_id==str(file_id), Catalog.status=='ACTIVE'))).first()
        if not row:
            raise Problem(404, 'FILE_NOT_FOUND', 'File not found')
        return serialize(*row)

@app.get('/api/files/{file_id}/stats')
async def stats(file_id: uuid.UUID, request: Request):
    user = identity(request)
    async with Session() as db:
        item = await db.get(Catalog, str(file_id))
        if not item or not item.owner_id:
            raise Problem(404, 'FILE_NOT_FOUND', 'File not found')
        if item.owner_id != user:
            raise Problem(403, 'FILE_ACCESS_DENIED', 'Only the owner can view statistics')
        count = await db.scalar(select(func.count()).select_from(Download).where(Download.file_id==item.file_id))
        users = (await db.execute(select(Download.user_id, UserProfile.display_name).outerjoin(
            UserProfile, UserProfile.user_id==Download.user_id).where(Download.file_id==item.file_id,
                Download.user_id.is_not(None)).distinct().order_by(Download.user_id))).all()
        return dict(file_id=item.file_id, original_name=item.original_name, download_count=count,
            downloaded_by=[dict(user_id=uid, display_name=name or 'Имя ещё не получено') for uid,name in users])
