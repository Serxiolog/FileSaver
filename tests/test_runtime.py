import os
import subprocess
import sys
from services.upload.app.runtime import runtime_lock


def test_process_lock_blocks_another_owner_and_releases(tmp_path,monkeypatch):
    monkeypatch.setenv('RUNTIME_DIR',str(tmp_path))
    script="""import fcntl,sys
with open(sys.argv[1],'a') as handle:
 try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:sys.exit(7)
"""
    path=str(tmp_path/'active.lock')
    with runtime_lock('active'):
        assert subprocess.run([sys.executable,'-c',script,path]).returncode==7
    assert subprocess.run([sys.executable,'-c',script,path]).returncode==0

async def test_storage_client_closes_without_context_manager(monkeypatch):
    from services.upload.app import storage
    class Client:
        closed=False
        def put_object(self,**kwargs):return {'ok':True}
        def close(self):self.closed=True
    client=Client()
    monkeypatch.setattr(storage,'client',lambda:client)
    async def inline(function):return function()
    monkeypatch.setattr(storage,'run_in_threadpool',inline)
    result=await storage.operation('put_object',Key='files/test',Body=b'test')
    assert result=={'ok':True} and client.closed
