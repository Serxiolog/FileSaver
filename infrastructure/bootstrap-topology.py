from pathlib import Path
import subprocess
r=Path(__file__).resolve().parents[1];values=dict(line.split('=',1) for line in (r/'.env').read_text().splitlines() if line and not line.startswith('#'))
code='''import asyncio,os,aio_pika
from app.worker import topology
async def setup():
 async with await aio_pika.connect_robust(os.environ['RABBITMQ_URL']) as c:
  await topology(await c.channel())
asyncio.run(setup())
'''
result=subprocess.run(['docker','compose','run','--rm','--no-deps','-e','BOOTSTRAP_TOPOLOGY=true','-e','RABBITMQ_URL=amqp://bootstrap:'+values['AUTH_MQ_PASSWORD']+'@rabbitmq/ampis','auth-worker','python','-c',code],cwd=r,capture_output=True,text=True,stdin=subprocess.DEVNULL)
if result.returncode:
 message=result.stderr
 for value in values.values():
  if len(value)>15:message=message.replace(value,'[redacted]')
 raise RuntimeError(message)
print('All queues, retry queues, DLQs and exact bindings initialized.')
