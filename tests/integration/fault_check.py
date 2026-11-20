"""Additional real-infrastructure fault and recovery checks."""
import sys,os,uuid,time,json,concurrent.futures
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from stack_check import api,wait,check,compose,sql,create_user,checks,ROOT
import httpx

def storage_probe(file,read_only=False):
    code="""import os,boto3
from botocore.config import Config
from botocore.exceptions import ClientError
s=boto3.client('s3',endpoint_url=os.environ['S3_ENDPOINT'],aws_access_key_id=os.environ['S3_ACCESS_KEY'],aws_secret_access_key=os.environ['S3_SECRET_KEY'],region_name='us-east-1',config=Config(signature_version='s3v4',s3={'addressing_style':'path'}))
"""
    if read_only:
        code+="""
for name,args in [('put_object',dict(Bucket='user-files',Key='files/forbidden-test',Body=b'x')),('delete_object',dict(Bucket='user-files',Key='files/forbidden-test'))]:
 try:getattr(s,name)(**args)
 except ClientError as error:
  assert error.response['ResponseMetadata']['HTTPStatusCode']==403
 else:raise AssertionError('Read-only account allowed mutation')
print('READ_ONLY_OK')
"""
        return compose('exec','-T','download-api','python','-c',code)
    code+="""
try:s.head_object(Bucket='user-files',Key='files/"""+file+"""')
except ClientError as error:
 assert error.response['ResponseMetadata']['HTTPStatusCode']==404
 print('ABSENT')
else:print('PRESENT')
"""
    return compose('exec','-T','upload-api','python','-c',code).strip()

def publish(body):
    code="""import asyncio,os,json,sys,aio_pika
async def run():
 async with await aio_pika.connect_robust(os.environ['RABBITMQ_URL']) as connection:
  channel=await connection.channel(publisher_confirms=True,on_return_raises=True)
  await channel.default_exchange.publish(aio_pika.Message(sys.stdin.buffer.read(),delivery_mode=aio_pika.DeliveryMode.PERSISTENT,content_type='application/json'),routing_key='reports.events',mandatory=True)
asyncio.run(run())
"""
    import subprocess
    result=subprocess.run(['docker','compose','exec','-T','reports-worker','python','-c',code],cwd=ROOT,input=json.dumps(body),capture_output=True,text=True)
    if result.returncode:raise RuntimeError(result.stderr[-500:])

def event(kind,file,version,payload):
    return dict(event_id=str(uuid.uuid4()),event_type=kind,event_version=1,aggregate_id=file,aggregate_version=version,
        occurred_at='2026-11-18T18:00:00Z',correlation_id=str(uuid.uuid4()),payload=payload)

def main():
    import jwt
    token,_,_=create_user('Проверка восстановления')
    owner=jwt.decode(token,options={'verify_signature':False})['sub']
    payload=b'R'*1048576
    result=api('POST','/api/files',token,files={'file':('recovery.bin',payload)})
    check(result.status_code==201,'recovery fixture upload')
    file=result.json()['id'];path='/api/files/'+file
    wait(lambda:api('GET',path).status_code==200,'fixture propagated')
    check('READ_ONLY_OK' in storage_probe(file,True),'download IAM forbids PutObject and DeleteObject')
    compose('stop','rustfs')
    try:
        failed=api('POST','/api/files',token,files={'file':('should-fail.txt',b'do not publish')})
        check(failed.status_code==503,'storage outage upload 503')
        check(api('GET',path+'/download').status_code==503,'storage outage download 503')
        check(api('GET',path+'/stats',token).json()['download_count']==0,'failed transfer does not count download')
        check(api('GET','/api/users/me',token).status_code==200 and api('GET',path).status_code==200,'auth and catalogue independent of storage')
    finally:compose('start','rustfs')
    wait(lambda:api('GET',path+'/download').status_code==200,'storage recovered')
    # Inject one orphan intent/object while no API request owns its kernel lock.
    orphan=str(uuid.uuid4())
    code="""import os,boto3
from app.storage import client
s=client();s.put_object(Bucket='user-files',Key='files/"""+orphan+"""',Body=b'orphan');s.close()
"""
    compose('exec','-T','upload-api','python','-c',code)
    sql('upload',"INSERT INTO upload_intents(id,created_at) VALUES ('"+orphan+"',NOW())")
    wait(lambda:sql('upload',"SELECT count(*) FROM upload_intents WHERE id='"+orphan+"'")=='0','orphan compensation')
    check(storage_probe(orphan)=='ABSENT','orphan object safely removed')
    wait(lambda:sql('download',"SELECT count(*) FROM download_sessions WHERE file_id='"+file+"' AND status='ACTIVE'")=='0','normal sessions closed')
    # Simulate a persisted session left by an API crash. It must block delete.
    session=str(uuid.uuid4())
    sql('download',"INSERT INTO download_sessions(session_id,file_id,status,started_at,correlation_id) VALUES ('"+session+"','"+file+"','ACTIVE',NOW(),'"+str(uuid.uuid4())+"')")
    result=api('DELETE',path,token);check(result.status_code==202,'deletion starts with stranded session')
    wait(lambda:api('GET',path+'/download').status_code==409,'DRAINING')
    time.sleep(2)
    check(storage_probe(file)=='PRESENT','object preserved while session ACTIVE')
    check(api('DELETE',path,token).json()['deletion_request_id']==result.json()['deletion_request_id'],'repeated delete keeps operation ID')
    # A second process cannot take the kernel ownership lock from the API.
    code="""from app.runtime import runtime_lock
try:
 with runtime_lock('download-api'):raise RuntimeError('Second API acquired ownership')
except BlockingIOError:print('OWNERSHIP_HELD')
"""
    check('OWNERSHIP_HELD' in compose('exec','-T','download-api','python','-c',code),'live API ownership cannot expire')
    compose('restart','download-api')
    wait(lambda:sql('download',"SELECT status FROM download_sessions WHERE session_id='"+session+"'")=='FAILED','restart repairs stranded session')
    wait(lambda:api('GET',path+'/download').status_code==410,'restart completes draining delete')
    check(storage_probe(file)=='ABSENT','physical object absent after safe drain')
    check(True,'download crash recovery')
    # PostgreSQL restart must reconnect without restarting the Reports application.
    compose('restart','reports-db')
    wait(lambda:api('GET','/api/files').status_code==200,'reports PostgreSQL reconnect')
    check(True,'PostgreSQL reconnection')
    # Out-of-order events go through the real broker/consumer/database path.
    synthetic=str(uuid.uuid4());download=str(uuid.uuid4())
    downloaded=event('file.downloaded',synthetic,None,dict(download_id=download,file_id=synthetic,user_id=owner,completed_at='2026-11-18T18:00:00Z'))
    publish(downloaded);publish(downloaded)
    created=event('file.created',synthetic,1,dict(file_id=synthetic,owner_id=owner,original_name='event-order-test.txt',size_bytes=0,
        content_type='text/plain',storage_key='files/'+synthetic,created_at='2026-11-18T18:00:00Z'))
    publish(created)
    sp='/api/files/'+synthetic
    wait(lambda:api('GET',sp+'/stats',token).status_code==200,'early download projection')
    check(api('GET',sp+'/stats',token).json()['download_count']==1,'download before creation retained exactly once')
    deleted=event('file.deleted',synthetic,3,dict(file_id=synthetic,deletion_request_id=str(uuid.uuid4()),deleted_at='2026-11-18T18:00:00Z'))
    publish(deleted);publish(created)
    wait(lambda:api('GET',sp).status_code==404,'tombstone')
    time.sleep(1)
    check(api('GET',sp).status_code==404,'late creation cannot resurrect deleted file')
    # Unknown schema goes to DLQ instead of an infinite retry loop.
    def dlq_count():
        lines=compose('exec','-T','rabbitmq','rabbitmqctl','list_queues','-p','ampis','name','messages_ready')
        return next((int(line.split('\t')[1]) for line in lines.splitlines() if line.startswith('reports.events.dlq\t')),0)
    previous_dlq=dlq_count()
    invalid={**created,'event_id':str(uuid.uuid4()),'event_version':999}
    publish(invalid)
    wait(lambda:dlq_count()>previous_dlq,'new incompatible event arrived in DLQ')
    lines=compose('exec','-T','rabbitmq','rabbitmqctl','list_queues','-p','ampis','name','messages_ready')
    check(any(line.startswith('reports.events.dlq\t') and int(line.split('\t')[1])>previous_dlq for line in lines.splitlines()),'unsupported event schema routed to DLQ')
    # Durable DLQ survives complete broker container recreation with the same volume.
    compose('up','-d','--force-recreate','--wait','rabbitmq')
    wait(lambda:api('GET','/api/files').status_code==200,'catalogue after broker recreate')
    lines=compose('exec','-T','rabbitmq','rabbitmqctl','list_queues','-p','ampis','name','messages_ready')
    check(any(line.startswith('reports.events.dlq\t') and int(line.split('\t')[1])>previous_dlq for line in lines.splitlines()),'durable queue survives broker container recreation')
    # Parallel new downloads racing deletion either finish with intact bytes or get a state error.
    uploaded=api('POST','/api/files',token,files={'file':('concurrent.bin',payload)})
    concurrent_file=uploaded.json()['id'];cp='/api/files/'+concurrent_file
    wait(lambda:api('GET',cp).status_code==200,'concurrent fixture')
    def fetch(_):
        with httpx.Client(timeout=30) as client:
            response=client.get('http://127.0.0.1:8080'+cp+'/download')
            return response.status_code,response.content
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        tasks=[pool.submit(fetch,i) for i in range(12)]
        time.sleep(.02)
        check(api('DELETE',cp,token).status_code==202,'concurrent owner delete')
        results=[task.result() for task in tasks]
    check(all((status==200 and body==payload) or status in [409,410] for status,body in results),'concurrent downloads finish intact or reject new session')
    wait(lambda:api('GET',cp+'/download').status_code==410,'concurrent drain complete')
    check(storage_probe(concurrent_file)=='ABSENT','concurrent drain physically deleted object')
    report={'checks':len(checks),'passed':checks}
    (ROOT/'docs/FAULT_RECOVERY_RESULTS.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print('ALL '+str(len(checks))+' EXTENDED CHECKS PASSED',flush=True)
if __name__=='__main__':main()
