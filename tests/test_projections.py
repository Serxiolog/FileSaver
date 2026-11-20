import uuid
from datetime import datetime,timezone
from services.reports.app import main as reports
from services.auth.app.events import validate_event
import pytest

class MemoryDB:
    def __init__(self):self.rows={}
    async def get(self,model,key,**kwargs):return self.rows.get((model,key))
    def add(self,row):
        key=next(getattr(row,field) for field in ['download_id','file_id','user_id'] if hasattr(row,field))
        self.rows[type(row),key]=row

def event(kind,aggregate,version,payload):
    return dict(event_id=str(uuid.uuid4()),event_type=kind,event_version=1,aggregate_id=aggregate,
        aggregate_version=version,occurred_at='2026-11-18T12:00:00Z',correlation_id=str(uuid.uuid4()),payload=payload)

async def test_rename_before_registration_does_not_revert_name():
    db=MemoryDB(); user=str(uuid.uuid4())
    await reports.apply_event(db,event('user.renamed',user,2,dict(user_id=user,display_name='New')))
    await reports.apply_event(db,event('user.registered',user,1,dict(user_id=user,display_name='Old')))
    assert (await db.get(reports.UserProfile,user)).display_name=='New'

async def test_creation_after_deleted_preserves_tombstone_and_recovers_owner():
    db=MemoryDB(); file,user,operation=[str(uuid.uuid4()) for _ in range(3)]
    await reports.apply_event(db,event('file.deleted',file,3,dict(file_id=file,deletion_request_id=operation,deleted_at='2026-11-18T12:00:00Z')))
    await reports.apply_event(db,event('file.created',file,1,dict(file_id=file,owner_id=user,original_name='test.txt',
        size_bytes=1,content_type='text/plain',storage_key='files/'+file,created_at='2026-11-18T11:00:00Z')))
    item=await db.get(reports.Catalog,file)
    assert item.status=='DELETED' and item.version==3 and item.owner_id==user

async def test_download_before_creation_and_duplicate_download_id():
    db=MemoryDB(); file,download=[str(uuid.uuid4()) for _ in range(2)]
    body=event('file.downloaded',file,None,dict(download_id=download,file_id=file,user_id=None,completed_at='2026-11-18T11:00:00Z'))
    await reports.apply_event(db,body)
    body['event_id']=str(uuid.uuid4())
    await reports.apply_event(db,body)
    assert len(db.rows)==1
    assert (await db.get(reports.Download,download)).user_id is None

@pytest.mark.parametrize('kind',['user.registered','user.renamed'])
def test_valid_event_and_extra_payload_rejected(kind):
    user=str(uuid.uuid4());body=event(kind,user,1,dict(user_id=user,display_name='Name'))
    validate_event(body)
    body['payload']['version']=1
    with pytest.raises(ValueError):validate_event(body)

def test_fact_cannot_carry_aggregate_version():
    file,download=[str(uuid.uuid4()) for _ in range(2)]
    body=event('file.downloaded',file,5,dict(download_id=download,file_id=file,user_id=None,completed_at='2026-11-18T11:00:00Z'))
    with pytest.raises(ValueError):validate_event(body)
