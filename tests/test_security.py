import importlib
import time
import uuid
import jwt
import pytest
from starlette.requests import Request

@pytest.mark.parametrize('service',['auth','upload','download','reports'])
def test_identity_verifies_signature_claims_and_uuid(service,keys):
    common=importlib.import_module(f'services.{service}.app.common')
    user=str(uuid.uuid4()); now=int(time.time())
    claims=dict(sub=user,iat=now,exp=now+3600,iss='ampis-auth',aud='ampis')
    def request(token):
        return Request({'type':'http','headers':[(b'authorization',('Bearer '+token).encode())]})
    assert common.identity(request(jwt.encode(claims,keys,algorithm='RS256')))==user
    for change in [{'exp':now-1},{'aud':'other'},{'sub':'not-a-uuid'}]:
        with pytest.raises(common.Problem) as error:
            common.identity(request(jwt.encode({**claims,**change},keys,algorithm='RS256')))
        assert error.value.status==401
    with pytest.raises(common.Problem):
        common.identity(request(jwt.encode(claims,'wrong-secret-that-is-at-least-32-bytes',algorithm='HS256')))
    forged=Request({'type':'http','headers':[(b'x-user-id',user.encode())]})
    with pytest.raises(common.Problem):common.identity(forged)
    assert common.identity(forged,required=False) is None

@pytest.mark.parametrize('service,path,method',[('auth','/api/users/me','GET'),('upload','/api/files/11111111-1111-4111-8111-111111111111','DELETE'),('reports','/api/files/11111111-1111-4111-8111-111111111111/stats','GET')])
async def test_unauthorized_is_contract_error(service,path,method):
    import httpx
    app=importlib.import_module(f'services.{service}.app.main').app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        result=await client.request(method,path,headers={'X-User-ID':str(uuid.uuid4())})
    assert result.status_code==401
    assert result.json()['error']['code']=='AUTH_REQUIRED'
    uuid.UUID(result.json()['error']['request_id'])

def test_argon2_uses_unique_salts():
    from services.auth.app.main import hasher
    a,b=hasher.hash('a long password'),hasher.hash('a long password')
    assert a!=b and a.startswith('$argon2id$')
    assert hasher.verify(a,'a long password')
