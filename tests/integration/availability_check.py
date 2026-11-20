"""Whole-service API+worker outage checks against the real stack."""
import sys,uuid,time,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from stack_check import api,create_user,wait,check,checks,compose,ROOT

def main():
    token,_,_=create_user('Проверка изоляции сервисов')
    content=b'independent services'
    response=api('POST','/api/files',token,files={'file':('independence.txt',content)})
    check(response.status_code==201,'availability fixture upload')
    path='/api/files/'+response.json()['id']
    wait(lambda:api('GET',path+'/download').status_code==200,'availability fixture propagation')
    for service in ['auth','upload']:
        compose('stop',service+'-api',service+'-worker')
        try:
            check(api('GET','/api/files').status_code==200,'public catalogue with whole '+service+' service stopped')
            result=api('GET',path+'/download',token)
            check(result.status_code==200 and result.content==content,'authenticated download with whole '+service+' service stopped')
        finally:compose('start',service+'-api',service+'-worker')
        wait(lambda:api('GET','/api/users/me',token).status_code==200,'auth recovery')
    compose('stop','download-api','download-worker')
    try:
        response=api('POST','/api/files',token,files={'file':('while-s3-off.txt',content)})
        check(response.status_code==201,'upload with whole download service stopped')
        later='/api/files/'+response.json()['id']
        wait(lambda:api('GET',later).status_code==200,'catalogue without S3')
        lines=compose('exec','-T','rabbitmq','rabbitmqctl','list_queues','-p','ampis','name','messages_ready')
        check(any(line.startswith('download.events\t') and int(line.split('\t')[1])>=1 for line in lines.splitlines()),'download events retained while whole S3 stopped')
    finally:compose('start','download-api','download-worker')
    wait(lambda:api('GET',later+'/download').status_code==200,'download backlog recovered')
    check(True,'whole download service restart recovers backlog')
    compose('stop','reports-api','reports-worker')
    try:
        check(api('GET',path+'/download').content==content,'download with whole reports service stopped')
        response=api('POST','/api/files',token,files={'file':('while-s4-off.txt',content)})
        check(response.status_code==201,'upload with whole reports service stopped')
        report_later='/api/files/'+response.json()['id']
    finally:compose('start','reports-api','reports-worker')
    wait(lambda:api('GET',report_later).status_code==200,'full reports service catch-up')
    check(True,'whole reports service restart recovers backlog')
    # Storage bytes and service IAM accounts also survive container recreation.
    compose('up','-d','--force-recreate','--wait','rustfs')
    wait(lambda:api('GET',path+'/download').content==content,'storage recreation')
    check(True,'storage bytes and IAM survive container recreation')
    for item in [path,later,report_later]:
        wait(lambda:api('DELETE',item,token).status_code in [202,410],'delete after service recovery')
        wait(lambda:api('GET',item+'/download').status_code==410,'physical cleanup')
    (ROOT/'docs/AVAILABILITY_RESULTS.json').write_text(json.dumps({'checks':len(checks),'passed':checks},indent=2,ensure_ascii=False)+'\n')
    print('ALL '+str(len(checks))+' AVAILABILITY CHECKS PASSED',flush=True)
if __name__=='__main__':main()
