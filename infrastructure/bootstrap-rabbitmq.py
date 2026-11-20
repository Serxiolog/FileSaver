"""Run after RabbitMQ starts: python infrastructure/bootstrap-rabbitmq.py.
Uses rabbitmqctl via Docker; no management port is exposed.
"""
import subprocess
from pathlib import Path
root = Path(__file__).resolve().parents[1]
values = dict(line.split('=',1) for line in (root/'.env').read_text().splitlines() if line and not line.startswith('#'))
for service in ['auth','upload','download','reports']:
    prefix = ['docker','compose','exec','-T','rabbitmq','rabbitmqctl']
    listed = subprocess.check_output(prefix+['list_users','--silent'],cwd=root,text=True)
    if not any(line.split()[0]==service for line in listed.splitlines() if line.split()):
        subprocess.run(prefix+['add_user',service,values[service.upper()+'_MQ_PASSWORD']],cwd=root,check=True,stdout=subprocess.DEVNULL)
    else:
        subprocess.run(prefix+['change_password',service,values[service.upper()+'_MQ_PASSWORD']],cwd=root,check=True,stdout=subprocess.DEVNULL)
    own = service + r'\.events(?:\.(?:retry|dlq))?'
    configure = r'^domain\.events$' if service=='auth' else r'^(domain\.events|' + own + ')$'
    write = r'^domain\.events$' if service=='auth' else r'^(amq\.default|' + own + (r'|domain\.events' if service!='reports' else '') + ')$'
    read = '^$' if service=='auth' else r'^(domain\.events|' + own + ')$'
    subprocess.run(prefix+['set_permissions','-p','ampis',service,configure,write,read],cwd=root,check=True,stdin=subprocess.DEVNULL)
