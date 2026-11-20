from pathlib import Path
import subprocess,os,time
root=Path(__file__).resolve().parents[1];os.chdir(root)
values=dict(line.split('=',1) for line in (root/'.env').read_text().splitlines() if line and not line.startswith('#'))
def run(args,label):
    result=subprocess.run(args,text=True,capture_output=True)
    if result.returncode:
        message=result.stderr+result.stdout
        for key,value in values.items():
            if ('PASSWORD' in key or 'SECRET' in key or 'ACCESS_KEY' in key or key=='RUSTFS_ROOT_USER') and value:
                message=message.replace(value,'[redacted]')
        raise RuntimeError(label+': '+message[-2000:])
    print(label+': OK',flush=True)
    return result.stdout
def ensure_cli():
    import urllib.request,tarfile,tempfile
    folder=root/'.runtime/tools'
    folder.mkdir(parents=True,exist_ok=True)
    binary=folder/'rc'
    if not binary.exists():
        url='https://github.com/rustfs/cli/releases/download/v0.1.36/rustfs-cli-linux-amd64-v0.1.36.tar.gz'
        with tempfile.TemporaryDirectory() as directory:
            archive=Path(directory)/'rc.tar.gz'
            urllib.request.urlretrieve(url,archive)
            with tarfile.open(archive) as tar:
                member=tar.getmember('rc')
                binary.write_bytes(tar.extractfile(member).read())
        binary.chmod(0o755)
    return binary

container=subprocess.check_output(['docker','compose','ps','-q','rustfs'],text=True).strip()
if not container:raise SystemExit('RustFS container is not running')
run(['docker','cp',str(ensure_cli()),container+':/tmp/rc'],'Install official rc client in storage container')
cli=['docker','compose','exec','-T','-e','HOME=/tmp','-e','XDG_CONFIG_HOME=/tmp/ampis-cli','-e','RC_HOST_local=http://'+values['RUSTFS_ROOT_USER']+':'+values['RUSTFS_ROOT_PASSWORD']+'@localhost:9000','rustfs','/tmp/rc']
run(cli+['bucket','create','local/user-files','--ignore-existing'],'Create private user-files bucket')
for service in ['upload','download']:
    key=service.upper()
    run(cli+['admin','user','add','local',values[key+'_ACCESS_KEY'],values[key+'_SECRET_KEY']],'Create '+service+' service account')
    policy='/tmp/ampis-'+service+'-policy.json'
    run(['docker','cp',str(root/'infrastructure/iam'/f'{service}.json'),container+':'+policy],'Copy '+service+' policy')
    run(cli+['admin','policy','create','local','ampis-'+service,policy],'Create '+service+' policy')
    run(cli+['admin','policy','attach','local','ampis-'+service,'--user',values[key+'_ACCESS_KEY']],'Attach '+service+' policy')
