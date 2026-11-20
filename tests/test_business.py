import io
import uuid
import jwt
import pytest
from starlette.requests import Request
from starlette.datastructures import UploadFile
from services.upload.app import main as upload
from services.download.app import main as download
from services.auth.app import main as auth
from services.auth.app.common import now
from starlette.requests import ClientDisconnect

class MemoryFile(io.BytesIO):
    _rolled = False

@pytest.fixture(autouse=True)
def inline_hash_dispatch(monkeypatch):
    # Unit tests isolate business transactions from the thread-pool adapter.
    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(auth, "run_in_threadpool", inline)

class DB:
    def __init__(self,item=None):self.item=item;self.added=[];self.deleted=[]
    async def __aenter__(self):return self
    async def __aexit__(self,*args):return False
    async def get(self,model,key,**kwargs):return self.item
    async def scalar(self,query):return self.item
    def add(self,row):self.added.append(row)
    async def delete(self,row):self.deleted.append(row)

class Sessions:
    def __init__(self,db):self.db=db
    def begin(self):return self.db
    def __call__(self):return self.db

def request():
    result=Request({'type':'http','headers':[]})
    result.state.request_id=str(uuid.uuid4())
    return result

async def test_auth_register_stores_hash_and_outbox_together(monkeypatch):
    db=DB();monkeypatch.setattr(auth,'Session',Sessions(db))
    result=await auth.register(auth.Register(login='Alice',password='A secure password',display_name='Alice'),request())
    user,event=db.added
    assert user.password_hash.startswith('$argon2id$') and user.password_hash!='A secure password'
    assert result['login']=='alice' and 'access_token' not in result
    assert event.body['event_type']=='user.registered'
    assert set(event.body['payload'])=={'user_id','display_name'}

async def test_login_issues_one_hour_rs256_token(monkeypatch,keys,tmp_path):
    user=auth.User(id=str(uuid.uuid4()),login='alice',password_hash=auth.hasher.hash('Correct password'),display_name='Alice')
    path=tmp_path/'private.pem';path.write_bytes(keys);monkeypatch.setenv('JWT_PRIVATE_KEY_FILE',str(path))
    monkeypatch.setattr(auth,'Session',Sessions(DB(user)))
    result=await auth.login(auth.Login(login='alice',password='Correct password'))
    claims=jwt.decode(result['access_token'],options={'verify_signature':False})
    assert claims['exp']-claims['iat']==3600 and claims['sub']==user.id
    assert jwt.get_unverified_header(result['access_token'])['alg']=='RS256'
    with pytest.raises(auth.Problem) as error:
        await auth.login(auth.Login(login='alice',password='wrong password'))
    assert error.value.code=='INVALID_CREDENTIALS'

@pytest.mark.parametrize('size',[0,1048576])
async def test_upload_accepted_boundary(monkeypatch,size):
    db=DB(upload.UploadIntent(id=str(uuid.uuid4()),created_at=now()));user=str(uuid.uuid4());calls=[]
    monkeypatch.setattr(upload,'Session',Sessions(db));monkeypatch.setattr(upload,'identity',lambda request:user)
    async def operation(name,**kwargs):calls.append((name,kwargs))
    monkeypatch.setattr(upload,'operation',operation)
    result=await upload.upload(request(),UploadFile(MemoryFile(b'x'*size),filename='../report.txt'))
    assert result['size_bytes']==size and result['original_name']=='report.txt'
    assert calls[0][1]['Body']==b'x'*size
    assert db.added[-1].body['event_type']=='file.created'
    assert 'storage_key' not in result

async def test_upload_rejects_limit_before_put(monkeypatch):
    monkeypatch.setattr(upload,'identity',lambda request:str(uuid.uuid4()))
    with pytest.raises(upload.Problem) as error:
        await upload.upload(request(),UploadFile(MemoryFile(b'x'*1048577),filename='large.txt'))
    assert error.value.status==413

async def test_repeated_delete_and_ownership(monkeypatch):
    owner=str(uuid.uuid4());file=str(uuid.uuid4())
    item=upload.File(id=file,owner_id=owner,status='ACTIVE',version=1)
    db=DB(item);monkeypatch.setattr(upload,'Session',Sessions(db));monkeypatch.setattr(upload,'identity',lambda request:owner)
    first=await upload.delete_file(uuid.UUID(file),request())
    second=await upload.delete_file(uuid.UUID(file),request())
    assert first==second and item.status=='DELETING' and len(db.added)==1
    assert db.added[0].body['event_type']=='file.deletion.requested'
    monkeypatch.setattr(upload,'identity',lambda request:str(uuid.uuid4()))
    with pytest.raises(upload.Problem) as error:await upload.delete_file(uuid.UUID(file),request())
    assert error.value.status==403

async def test_only_current_ready_allows_physical_deletion():
    operation=str(uuid.uuid4());item=upload.File(id=str(uuid.uuid4()),status='DELETING',deletion_request_id=operation,ready=False)
    db=DB(item)
    body={'event_type':'file.deletion.ready','aggregate_id':item.id,'payload':{'deletion_request_id':str(uuid.uuid4())}}
    await upload.apply_event(db,body)
    assert not item.ready
    body['payload']['deletion_request_id']=operation
    await upload.apply_event(db,body)
    assert item.ready

@pytest.mark.parametrize('broken',[False,True])
async def test_stream_only_completed_final_send_counts(monkeypatch,broken):
    finished=[]
    async def finish(session_id,success):finished.append(success)
    monkeypatch.setattr(download,'finish',finish)
    async def chunks():yield b'hello'
    async def send(message):
        if broken and message['type']=='http.response.body':raise OSError('Disconnected')
    async def receive():return {'type':'http.disconnect'}
    body=io.BytesIO(b'hello')
    response=download.DownloadResponse(chunks(),session_id='test',storage_body=body)
    try:
        await response({'type':'http','asgi':{'spec_version':'2.4'}},receive,send)
    except (OSError, ClientDisconnect):pass
    assert finished==[not broken] and body.closed
