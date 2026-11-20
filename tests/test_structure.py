import json
from pathlib import Path
import importlib
import yaml
ROOT=Path(__file__).resolve().parents[1]

def test_compose_has_fifteen_containers_and_only_gateway_and_local_console_ports():
    compose=yaml.safe_load((ROOT/'docker-compose.yml').read_text())
    assert len(compose['services'])==15
    assert {name for name,config in compose['services'].items() if 'ports' in config}=={'gateway','rustfs'}
    for name in ['auth','upload','download','reports']:
        assert compose['services'][name+'-worker']['image']==f'ampis-{name}:local'
        assert name+'-db-net' in compose['services'][name+'-api']['networks']

def test_seven_event_schemas():
    assert len(list((ROOT/'contracts/events').glob('*.schema.json')))==7

def test_only_ten_business_endpoints():
    routes=[]
    for service in ['auth','upload','download','reports']:
        app=importlib.import_module(f'services.{service}.app.main').app
        routes += [(route.path,method) for route in app.routes if route.path.startswith('/api/') for method in route.methods]
    assert len(routes)==10

def test_iam_download_is_read_only():
    policy=json.loads((ROOT/'infrastructure/iam/download.json').read_text())
    assert policy['Statement'][0]['Action']==['s3:GetObject']
