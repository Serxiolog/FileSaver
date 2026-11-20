"""Create local secrets and environment. Never overwrites existing files."""
import os
import json
import secrets
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
root = Path(__file__).resolve().parents[1]
folder = root/'secrets'
folder.mkdir(mode=0o700, exist_ok=True)
private, public = folder/'jwt-private.pem', folder/'jwt-public.pem'
if not private.exists():
    if public.exists():
        raise SystemExit('Public key exists without private key; refusing to replace identity')
    key = rsa.generate_private_key(public_exponent=65537,key_size=3072)
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    private.chmod(0o600)
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
# Containers use the host UID/GID; the private key remains mode 0600.
values = {'FLASK_SECRET_KEY':secrets.token_hex(32),'RUSTFS_ROOT_USER':'admin-'+secrets.token_hex(6),
    'RUSTFS_ROOT_PASSWORD':secrets.token_urlsafe(32),'UPLOAD_ACCESS_KEY':'upload-'+secrets.token_hex(8),
    'UPLOAD_SECRET_KEY':secrets.token_urlsafe(32),'DOWNLOAD_ACCESS_KEY':'download-'+secrets.token_hex(8),
    'DOWNLOAD_SECRET_KEY':secrets.token_urlsafe(32),'COOKIE_SECURE':'false',
    'APP_UID':str(os.getuid()), 'APP_GID':str(os.getgid())}
values.update({key:value['digest'] for key,value in json.loads((root/'infrastructure/images.json').read_text()).items()})
for service in ['upload','download']:
    (root/'.runtime'/service).mkdir(parents=True,exist_ok=True)
for name in ['AUTH','UPLOAD','DOWNLOAD','REPORTS']:
    values[name+'_DB_PASSWORD'] = secrets.token_hex(24)
    values[name+'_MQ_PASSWORD'] = secrets.token_hex(24)
if not (root/'.env').exists():
    (root/'.env').write_text(''.join(f'{k}={v}\n' for k,v in values.items()))
    (root/'.env').chmod(0o600)
    print('Created local .env and keys; using tested images pinned by digest.')
else:
    print('.env exists; unchanged')
