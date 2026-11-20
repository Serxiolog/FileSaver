import httpx
import pytest
from gateway.app import create_app

@pytest.mark.parametrize('path,upstream',[
('/api/files','reports-api'),('/api/files/11111111-1111-4111-8111-111111111111','reports-api'),
('/api/files/11111111-1111-4111-8111-111111111111/download','download-api')])
def test_public_routes_do_not_call_auth(keys,monkeypatch,path,upstream):
    seen=[]
    def send(self,request,**kwargs):
        seen.append(request.url.host)
        response = httpx.Response(200,stream=httpx.ByteStream(b'hello'),headers={'Content-Type':'application/octet-stream'},request=request)
        if not kwargs.get('stream'):
            response.read()
        return response
    monkeypatch.setattr(httpx.Client,'send',send)
    response=create_app().test_client().get(path,headers={'X-User-ID':'forged'})
    assert response.status_code==200 and response.data==b'hello'
    assert seen==[upstream]

def test_upstream_failure_is_503(keys,monkeypatch):
    def send(*args,**kwargs):raise httpx.ConnectError('unavailable')
    monkeypatch.setattr(httpx.Client,'send',send)
    result=create_app().test_client().get('/api/files')
    assert result.status_code==503 and result.json['error']['code']=='SERVICE_UNAVAILABLE'

def test_login_csrf_and_cookie_auth_csrf(keys):
    client=create_app().test_client()
    assert client.get('/app/login').status_code==200
    assert client.post('/app/login',data={'login':'x','password':'y'}).status_code==400
    client.set_cookie('access_token','invalid')
    assert client.delete('/api/files/11111111-1111-4111-8111-111111111111').status_code==400

def test_upload_uses_auth_and_rejects_large_request(keys):
    client=create_app().test_client()
    assert client.post('/api/files').status_code==401
    result=client.post('/api/auth/register',data=b'x'*1200000,content_type='application/json')
    assert result.status_code==413 and result.json['error']['code']=='FILE_TOO_LARGE'

def test_expired_cookie_does_not_block_reauthentication(keys):
    client=create_app().test_client()
    client.set_cookie('access_token','expired-token')
    result=client.get('/app/login')
    assert result.status_code==200
    assert 'access_token=;' in result.headers.get('Set-Cookie','')
