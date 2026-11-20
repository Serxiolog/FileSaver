"""Audit the twelve PDF functions and its three explicit failure scenarios.
Database/storage boundaries follow the agreed Markdown architecture.
"""
import sys,json,uuid,time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parent))
from stack_check import api,create_user,wait,check,checks,compose,sql,ROOT


def wait_api(service):
    wait(lambda:'healthy' in compose('ps','--format','{{.Health}}',service+'-api'),'ready '+service,seconds=60)


def main():
    prefix='pdf'+uuid.uuid4().hex[:8]
    people=[create_user(prefix+'-Owner-'+letter)[0] for letter in ['A','M','Z']]
    files=[]
    content=b'\x00\x01\xff\xfeBinary content\n'
    def upload(token,name,body=content):
        result=api('POST','/api/files',token,files={'file':(name,body,'application/octet-stream')})
        assert result.status_code==201,('upload status',result.status_code)
        item=result.json();files.append((item['id'],token))
        return item
    def path(file):return '/api/files/'+file['id']
    def listing(**params):
        return api('GET','/api/files',params={'name':prefix,**params}).json()
    def remove(file,token):
        result=api('DELETE','/api/files/'+file,token)
        assert result.status_code in [202,410],result.status_code
        wait(lambda:api('GET','/api/files/'+file+'/download').status_code==410,'physical deletion')
    try:
        # Filename order differs from owner order, and counts differ from both.
        records=[upload(people[0],prefix+'-C.bin'),upload(people[1],prefix+'-A.bin'),upload(people[2],prefix+'-B.bin')]
        wait(lambda:listing().get('total')==3,'three catalogue rows')
        check(api('GET','/api/files').status_code==200,'F1/F4: guest can view catalogue')
        check(api('GET','/api/users/me',people[0]).status_code==200,'F1: authenticated profile')
        check(api('POST','/api/files',files={'file':('guest.bin',content)}).status_code==401,'F2: guest cannot upload')
        check(all(record['status']=='ACTIVE' for record in records),'F2: registered users upload arbitrary binary files')
        row=listing()['items'][0]
        check(all(key in row for key in ['id','original_name','owner','download_url','download_count']),'F5: catalogue contains every required attribute')
        check(row['download_url']=='/api/files/'+row['id']+'/download' and row['owner']['display_name'].startswith(prefix),'F5: link uses gateway and owner is named')
        for record,downloads in [(records[0],[None,people[1],people[1]]),(records[1],[None]),(records[2],[people[0],people[0]])]:
            for token in downloads:
                result=api('GET',path(record)+'/download',token)
                check(result.status_code==200 and result.content==content,'F6: guest or registered download preserves arbitrary bytes')
            wait(lambda:api('GET',path(record)).json().get('download_count')==len(downloads),'download counter')
        check([api('GET',path(record)).json()['download_count'] for record in records]==[3,1,2],'F6: every completed download increments exactly once')
        report=api('GET',path(records[0])+'/stats',people[0]).json()
        check(report['download_count']==3 and len(report['downloaded_by'])==1,'F9: owner sees all downloads and unique registered downloaders')
        check(report['downloaded_by'][0]['display_name']==prefix+'-Owner-M','F9: report shows downloader name')
        check(api('GET',path(records[0])+'/stats',people[2]).status_code==403,'F9: another user cannot view private report')
        check(api('DELETE',path(records[0]),people[2]).status_code==403,'F7: another user cannot delete owner file')
        check(sql('download',"SELECT count(*) FROM download_history WHERE file_id='"+records[0]['id']+"' AND user_id IS NOT NULL AND completed_at IS NOT NULL")=='2','S3: history persists who downloaded and when')
        names=[record['original_name'] for record in records]
        ids=[record['id'] for record in records]
        owners=[prefix+'-Owner-'+letter for letter in ['A','M','Z']]
        counts=[3,1,2]
        for field,values in [('name',names),('owner',owners),('downloads',counts)]:
            for direction in ['asc','desc']:
                expected=[ids[index] for index in sorted(range(3),key=lambda index:values[index],reverse=direction=='desc')]
                actual=[item['id'] for item in listing(sort=field,order=direction)['items']]
                check(actual==expected,'F11: '+field+' sort '+direction)
        check([item['id'] for item in listing(name=prefix+'-a.BIN')['items']]==[records[1]['id']],'F10: filename substring search is case insensitive')
        check([item['id'] for item in listing(owner=prefix+'-Owner-Z')['items']]==[records[2]['id']],'F10: owner display-name search')
        check(listing(owner=prefix+'-Owner-A',name=prefix+'-A')['total']==0,'F10: name and owner filters combine')
        check(listing(name=prefix+'%')['total']==0,'F10: percent in search is treated literally')
        all_ids=[]
        for number in [1,2,3]:
            page=listing(sort='name',page=number,size=1)
            check(page['page']==number and page['size']==1 and page['total']==3 and page['pages']==3 and len(page['items'])==1,'F12: page '+str(number)+' and totals')
            all_ids.extend(item['id'] for item in page['items'])
        check(len(set(all_ids))==3,'F12: pages have no duplicate records')
        check(listing(page=4,size=1)['items']==[],'F12: past last page is empty')
        check(listing(name=prefix+'-no-match')['pages']==0,'F12: empty search has zero pages')
        for query in [{'page':0},{'size':101},{'sort':'invalid'}]:
            check(api('GET','/api/files',params=query).status_code==422,'F11/F12: invalid parameters rejected')
        renamed=prefix+'-Owner-Renamed'
        check(api('PATCH','/api/users/me',people[0],json={'display_name':renamed}).status_code==200,'F8: registered user changes own name')
        wait(lambda:api('GET',path(records[0])).json()['owner']['display_name']==renamed,'owner rename reflected')
        check(listing(owner=renamed)['items'][0]['id']==records[0]['id'],'F8/F10: owner rename updates catalogue and owner search')
        boundary=upload(people[0],prefix+'-limit.bin',b'x'*1048576)
        check(boundary['size_bytes']==1048576,'F3: agreed 1 MiB boundary accepted')
        check(api('POST','/api/files',people[0],files={'file':('too-large.bin',b'x'*1048577)}).status_code==413,'F3: oversize file rejected')
        remove(boundary['id'],people[0]);files.remove((boundary['id'],people[0]))
        check(api('GET',path(boundary)).status_code==404,'F7: owner deletion removes file from catalogue')
        # Scenario 1: stop the complete business service, exercise all other APIs.
        for service in ['upload','download','reports']:
            compose('stop',service+'-api',service+'-worker')
            additions=[]
            try:
                outsider=create_user(prefix+'-During-'+service)[0]
                check(api('GET','/api/users/me',outsider).status_code==200,'S1: auth works while S'+{'upload':'2','download':'3','reports':'4'}[service]+' is stopped')
                if service!='reports':
                    check(api('GET','/api/files').status_code==200 and api('GET',path(records[0])+'/stats',people[0]).status_code==200,'S1: reports work while '+service+' is stopped')
                if service!='download':
                    result=api('GET',path(records[0])+'/download')
                    check(result.status_code==200 and result.content==content,'S1: anonymous download works while '+service+' is stopped')
                if service!='upload':
                    addition=upload(outsider,prefix+'-during-'+service+'.bin')
                    additions.append((addition['id'],outsider))
                    check(True,'S1: upload works while '+service+' is stopped')
                    check(api('DELETE',path(addition),outsider).status_code==202,'S1: delete request accepted while '+service+' is stopped')
            finally:
                compose('start',service+'-api',service+'-worker')
            wait_api(service)
            for file,token in additions:
                wait(lambda:api('GET','/api/files/'+file+'/download').status_code==410,'delayed deletion after service restart')
                files.remove((file,token))
        # Scenario 2 explicitly uses no token, unlike stronger JWT independence checks.
        compose('stop','auth-api','auth-worker')
        try:
            check(api('GET','/api/files').status_code==200,'S2: public S4 list without registration while S1 stopped')
            result=api('GET',path(records[0])+'/download')
            check(result.status_code==200 and result.content==content,'S2: public S3 download without registration while S1 stopped')
        finally:compose('start','auth-api','auth-worker')
        wait_api('auth')
        # Scenario 3 disables only consumption D1S4; API and other consumers stay up.
        wait(lambda:listing(name=prefix+'-C.bin')['total']==1,'baseline projection')
        old_name=api('GET',path(records[0])).json()['owner']['display_name']
        compose('stop','reports-worker')
        try:
            paused=upload(people[0],prefix+'-paused.bin')
            final_name=prefix+'-Owner-Final'
            check(api('PATCH','/api/users/me',people[0],json={'display_name':final_name}).status_code==200,'S3: user update accepted while D1S4 disabled')
            check(api('GET',path(records[0])).json()['owner']['display_name']==old_name,'S3: saved owner name remains visible while D1S4 disabled')
            check(api('GET','/api/files').status_code==200 and api('GET',path(paused)).status_code==404,'S3: old catalogue available and new file not prematurely visible')
            result=api('GET',path(records[0])+'/download')
            check(result.status_code==200,'S3: other consumers and downloads keep working')
            lines=compose('exec','-T','rabbitmq','rabbitmqctl','list_queues','-p','ampis','name','messages_ready')
            check(any(line.startswith('reports.events\t') and int(line.split('\t')[1])>0 for line in lines.splitlines()),'S3: D1S4 events accumulate in durable queue')
        finally:compose('start','reports-worker')
        wait(lambda:api('GET',path(paused)).status_code==200 and api('GET',path(records[0])).json()['owner']['display_name']==final_name,'automatic catalogue catch-up')
        check(True,'S3: file catalogue and owner profiles automatically catch up after D1S4 recovery')
        # Single history source is local B3; Reports still use their own B4 only.
        raw=compose('ps','--format','json')
        items=[json.loads(line) for line in raw.splitlines() if line.strip()]
        check(len(items)==15 and all(item.get('State')=='running' for item in items),'architecture: all fifteen containers are running')
        result={'source':'Задание ПИ (1).pdf','database_authority':'Previously agreed Markdown documents',
                'timestamp':datetime.now(ZoneInfo('Europe/Ulyanovsk')).isoformat(),'checks':len(checks),'passed':checks,
                'functional_requirements':12,'explicit_failure_scenarios':3}
        (ROOT/'docs/ASSIGNMENT_RESULTS.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
        print('ALL '+str(len(checks))+' ASSIGNMENT CHECKS PASSED',flush=True)
    finally:
        for file,token in files:
            remove(file,token)

if __name__=='__main__':main()
