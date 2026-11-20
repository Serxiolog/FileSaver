"""Build and start the local 15-container system. Run from the host."""
import subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
def run(*args):
    subprocess.run(args,cwd=root,check=True,stdin=subprocess.DEVNULL)
run(sys.executable,'infrastructure/prepare.py')
run('docker','compose','build')
run('docker','compose','stop','auth-worker','upload-worker','download-worker','reports-worker')
run('docker','compose','up','-d','--wait','auth-db','upload-db','download-db','reports-db','rabbitmq','rustfs')
for script in ['bootstrap-rabbitmq.py','bootstrap-topology.py','bootstrap-storage.py']:
    run(sys.executable,'infrastructure/'+script)
run('docker','compose','up','-d','--wait','--wait-timeout','120')
print('AMPIS is ready at http://localhost:8080/app/')
