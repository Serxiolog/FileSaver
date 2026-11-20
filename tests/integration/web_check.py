import sys,re,uuid,json
from pathlib import Path
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parent))
from stack_check import api,create_user,wait,check,checks,ROOT

def main():
 token,login,password=create_user('Проверка браузера')
 client=httpx.Client(base_url='http://127.0.0.1:8080',timeout=20)
 page=client.get('/app/login')
 csrf=re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)
 result=client.post('/app/login',data={'login':login,'password':password,'csrf':csrf})
 check(result.status_code==302 and 'access_token=' in result.headers.get('set-cookie',''),'browser login sets JWT cookie')
 check('HttpOnly' in result.headers['set-cookie'],'JWT cookie HttpOnly')
 check(client.post('/api/files',files={'file':('csrf.txt',b'no csrf')}).status_code==400,'cookie upload requires CSRF')
 result=client.post('/app/upload',data={'csrf':csrf},files={'file':('browser.txt',b'browser upload')})
 check(result.status_code==302,'browser multipart upload')
 def find():
  items=api('GET','/api/files',params={'name':'browser.txt','owner':'Проверка браузера'}).json()['items']
  return items[0] if items else None
 item=wait(find,'browser upload projection')
 check(client.get('/app/').status_code==200,'authenticated catalogue HTML')
 check(client.get('/app/files/'+item['id']+'/card').status_code==200,'HTML file card')
 check(client.get('/app/files/'+item['id']+'/stats').status_code==200,'HTML owner statistics')
 result=client.post('/app/files/'+item['id']+'/delete',data={'csrf':csrf})
 check(result.status_code==202,'HTML owner deletion')
 wait(lambda:api('GET','/api/files/'+item['id']+'/download').status_code==410,'browser deletion completed')
 result=client.post('/app/logout',data={'csrf':csrf})
 check(result.status_code==302 and client.cookies.get('access_token') is None,'browser logout clears cookie')
 (ROOT/'docs/WEB_RESULTS.json').write_text(json.dumps({'checks':len(checks),'passed':checks},indent=2,ensure_ascii=False)+'\n')
 print('ALL '+str(len(checks))+' WEB CHECKS PASSED',flush=True)
if __name__=='__main__':main()
