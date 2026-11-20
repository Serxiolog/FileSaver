import os
import sys
from pathlib import Path
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

@pytest.fixture
def keys(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    path = tmp_path/'public.pem'; path.write_bytes(public)
    monkeypatch.setenv('JWT_PUBLIC_KEY_FILE',str(path))
    monkeypatch.setenv('JWT_ISSUER','ampis-auth')
    monkeypatch.setenv('JWT_AUDIENCE','ampis')
    monkeypatch.setenv('FLASK_SECRET_KEY','test-only-secret')
    monkeypatch.setenv('COOKIE_SECURE','false')
    return private
