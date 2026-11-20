import pytest
import httpx
from html.parser import HTMLParser
from gateway.app import create_app

class Inputs(HTMLParser):
    def __init__(self,html):
        super().__init__();self.fields={};self.feed(html)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='input' and 'name' in attrs:self.fields[attrs['name']]=attrs

@pytest.mark.parametrize('code,status,message,expected',[
 ('VALIDATION_ERROR',422,'Пароль: не менее 10 символов.','Пароль: не менее 10 символов.'),
 ('LOGIN_ALREADY_EXISTS',409,'Login exists','Этот логин уже занят'),
 ('INVALID_CREDENTIALS',401,'Wrong password','Неверный логин или пароль'),
 ('SERVICE_UNAVAILABLE',503,'Internal details','Сервис временно недоступен'),
])
def test_form_explains_failure_and_does_not_echo_password(keys,monkeypatch,code,status,message,expected):
    def send(self,request,**kwargs):
        return httpx.Response(status,json={'error':{'code':code,'message':message,'request_id':'test'}},request=request)
    monkeypatch.setattr(httpx.Client,'send',send)
    client=create_app().test_client();client.get('/app/register')
    with client.session_transaction() as session:csrf=session['csrf']
    password='UNIQUE_PRIVATE_PASSWORD'
    result=client.post('/app/register',data={'csrf':csrf,'login':'valid_login','password':password,'display_name':'Имя'})
    html=result.get_data(as_text=True)
    assert result.status_code==status and expected in html
    assert password not in html and 'Internal details' not in html
    fields=Inputs(html).fields
    assert fields['login']['value']=='valid_login' and fields['display_name']['value']=='Имя'
    assert 'value' not in fields['password']

def test_registration_hints_and_constraints(keys):
    html=create_app().test_client().get('/app/register').get_data(as_text=True)
    fields=Inputs(html).fields
    assert fields['login']['minlength']=='3' and fields['login']['maxlength']=='64'
    assert fields['password']['minlength']=='10' and fields['password']['maxlength']=='256'
    assert 'латинские буквы' in html and 'от 10 до 256 символов' in html

@pytest.mark.parametrize('change,expected',[
 ({'login':'кириллица'},'Логин: только латинские буквы'),
 ({'password':'hide123'},'Пароль: не менее 10 символов'),
 ({'display_name':'   '},'Отображаемое имя не может состоять только из пробелов'),
])
async def test_registration_validation_identifies_field_without_input_leak(change,expected):
    from services.auth.app.main import app
    body=dict(login='valid_login',password='SAFE_PRIVATE_PASSWORD_123',display_name='Имя')
    body.update(change)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        result=await client.post('/api/auth/register',json=body)
    assert result.status_code==422 and result.json()['error']['code']=='VALIDATION_ERROR'
    assert expected in result.json()['error']['message']
    assert body['password'] not in result.text
    assert 'кириллица' not in result.text
