"""Kernel locks shared by API/worker containers on this single Docker host.
A timer never establishes that a stream or PutObject has stopped.
"""
import fcntl
import os
from pathlib import Path
from contextlib import contextmanager

@contextmanager
def runtime_lock(key, blocking=False):
    root=Path(os.environ.get('RUNTIME_DIR','/tmp/ampis-runtime'))
    root.mkdir(parents=True,exist_ok=True)
    with (root/(key+'.lock')).open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(handle,fcntl.LOCK_UN)
