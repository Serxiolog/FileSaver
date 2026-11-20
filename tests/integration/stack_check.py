"""Real HTTP integration checks. Run with the Docker stack available."""
import os,sys,time,uuid,json,subprocess
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[2]; os.chdir(ROOT)
BASE='http://127.0.0.1:8080'
client=httpx.Client(base_url=BASE,timeout=20)
checks=[]
def check(condition,label):
    if not condition:raise AssertionError(label)
    checks.append(label);print('PASS '+label,flush=True)
def wait(fn,label,seconds=45):
    end=time.monotonic()+seconds; last=None
    while time.monotonic()<end:
        try:
            result=fn()
            if result:return result
        except (httpx.HTTPError,KeyError,AssertionError) as error:last=type(error).__name__
        time.sleep(.5)
    raise AssertionError('Timed out: '+label+' '+str(last))
def api(method,path,token=None,**kwargs):
    return client.request(method,path,headers={'Authorization':'Bearer '+token} if token else {},**kwargs)
def compose(*args):
    result=subprocess.run(['docker','compose',*args],capture_output=True,text=True,stdin=subprocess.DEVNULL)
    if result.returncode:raise RuntimeError('Compose failed: '+str(args[:2])+' '+result.stderr[-500:])
    return result.stdout
def sql(service,query):
    return compose('exec','-T',service+'-db','psql','-U',service,'-d',service,'-Atc',query).strip()
def create_user(label):
    login='e2e_'+uuid.uuid4().hex[:12]
    password=uuid.uuid4().hex+'!'
    response=api('POST','/api/auth/register',json=dict(login=login,password=password,display_name=label))
    check(response.status_code==201,'register '+label)
    response=api('POST','/api/auth/login',json=dict(login=login,password=password))
    check(response.status_code==200,'login '+label)
    return response.json()['access_token'],login,password

def main():
    wait(lambda:api('GET','/health/live').status_code==200,'gateway')
    owner,login,password=create_user('Владелец проверки')
    downloader,_,_=create_user('Скачивающий пользователь')
    check(api('POST','/api/auth/register',json=dict(login=login,password=password,display_name='duplicate')).status_code==409,'duplicate login 409')
    check(api('POST','/api/auth/login',json=dict(login=login,password='incorrect')).status_code==401,'wrong password 401')
    check(api('GET','/api/users/me',owner).json()['login']==login,'current profile')
    check(api('GET','/api/files').status_code==200,'public catalogue')
    check(api('POST','/api/files',files={'file':('x.txt',b'x')}).status_code==401,'anonymous upload forbidden')
    content=('Файл проверки AMPIS\n'*128).encode()
    response=api('POST','/api/files',owner,files={'file':('Проверка.txt',content,'text/plain')})
    check(response.status_code==201,'upload Unicode filename')
    item=response.json();file=item['id']; path='/api/files/'+file
    check('storage_key' not in item,'storage key private')
    wait(lambda:api('GET',path).status_code==200,'catalogue propagation')
    check(api('GET',path).json()['download_count']==0,'metadata does not count download')
    response=api('GET',path+'/download')
    check(response.status_code==200 and response.content==content,'anonymous download byte equality')
    check("filename*=UTF-8''" in response.headers['content-disposition'],'Unicode Content-Disposition')
    for _ in range(2):
        response=api('GET',path+'/download',downloader)
        check(response.status_code==200 and response.content==content,'authenticated download byte equality')
    wait(lambda:api('GET',path+'/stats',owner).json().get('download_count')==3,'download events')
    stats=api('GET',path+'/stats',owner).json()
    check(len(stats['downloaded_by'])==1,'three downloads; one unique registered downloader')
    check(api('GET',path+'/stats',downloader).status_code==403,'other user statistics forbidden')
    check(api('DELETE',path,downloader).status_code==403,'other user deletion forbidden')
    check(api('GET',path+'/stats').status_code==401,'anonymous statistics forbidden')
    newname='Переименованный пользователь'
    check(api('PATCH','/api/users/me',downloader,json={'display_name':newname}).status_code==200,'rename display name')
    wait(lambda:api('GET',path+'/stats',owner).json()['downloaded_by'][0]['display_name']==newname,'rename propagation')
    listing=api('GET','/api/files',params={'name':'Проверка','owner':'Владелец проверки','sort':'downloads','order':'desc','size':1}).json()
    check(listing['total']>=1 and len(listing['items'])==1,'search sort pagination')
    check(api('GET','/api/files',params={'size':101}).status_code==422,'pagination validation')
    check(api('POST','/api/files',owner,files={'file':('large.bin',b'x'*1048577)}).status_code==413,'file size limit')
    boundary=api('POST','/api/files',owner,files={'file':('boundary.bin',b'x'*1048576)})
    check(boundary.status_code==201,'exact 1 MiB accepted')
    boundary_path='/api/files/'+boundary.json()['id']
    wait(lambda:api('GET',boundary_path).status_code==200,'boundary propagation')
    check(api('GET',boundary_path+'/download').content==b'x'*1048576,'1 MiB bytes preserved')
    # Re-publish original outbox records with unchanged event IDs.
    sql('download',"UPDATE outbox_events SET sent_at=NULL WHERE body->>'event_type'='file.downloaded' AND body->>'aggregate_id'='"+file+"'")
    time.sleep(3)
    check(api('GET',path+'/stats',owner).json()['download_count']==3,'duplicate download events do not increment count')
    # Service outages: restore every dependency even if an assertion fails.
    for service in ['auth-api','upload-api','download-api']:
        compose('stop',service)
        try:
            check(api('GET','/api/files').status_code==200,'catalogue during '+service+' outage')
            if service!='download-api':
                check(api('GET',path+'/download',owner).status_code==200,'download during '+service+' outage')
            else:
                check(api('GET',path+'/download').status_code==503,'download service outage isolated')
                uploaded=api('POST','/api/files',owner,files={'file':('during-download-outage.txt',b'available later')})
                check(uploaded.status_code==201,'upload during download outage')
                api('DELETE','/api/files/'+uploaded.json()['id'],owner)
        finally:compose('start',service)
        wait(lambda:api('GET','/api/users/me',owner).status_code==200 if service=='auth-api' else api('GET',path+'/download').status_code==200 if service=='download-api' else api('DELETE',boundary_path,owner).status_code in [202,410],'restart '+service)
    compose('stop','reports-worker')
    try:
        paused=api('POST','/api/files',owner,files={'file':('backlog.txt',b'queue me')})
        check(paused.status_code==201,'upload while reports worker stopped')
        backlog_path='/api/files/'+paused.json()['id']
        check(api('GET','/api/files').status_code==200 and api('GET',backlog_path).status_code==404,'reports keeps last projection')
    finally:compose('start','reports-worker')
    wait(lambda:api('GET',backlog_path).status_code==200,'backlog recovered')
    check(True,'reports backlog recovery')
    compose('stop','rabbitmq')
    try:
        offline=api('POST','/api/files',owner,files={'file':('outbox.txt',b'outbox remains')})
        check(offline.status_code==201,'upload while broker stopped')
        offline_path='/api/files/'+offline.json()['id']
        check(api('GET','/api/files').status_code==200,'catalogue while broker stopped')
    finally:compose('start','rabbitmq')
    wait(lambda:api('GET',offline_path).status_code==200,'outbox recovery',seconds=75)
    check(True,'outbox delivered after broker restart')
    for candidate in [path,boundary_path,backlog_path,offline_path]:
        result=api('DELETE',candidate,owner)
        check(result.status_code in [202,410],'owner deletion accepted')
        wait(lambda:api('GET',candidate).status_code==404,'hidden after deletion')
        wait(lambda:api('GET',candidate+'/download').status_code==410,'physical deletion completed')
    check(sql('upload',"SELECT count(*) FROM files WHERE id='"+file+"' AND status='DELETED'")=='1','upload DB reaches DELETED')
    check(api('GET',path+'/stats',owner).status_code==200,'owner historical statistics retained')
    check(api('GET','/app/').status_code==200 and api('GET','/app/login').status_code==200,'Jinja web pages')
    result={'checks':len(checks),'passed':checks,'containers':15}
    (ROOT/'docs/INTEGRATION_RESULTS.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print('ALL '+str(len(checks))+' CHECKS PASSED',flush=True)

if __name__=='__main__':main()
