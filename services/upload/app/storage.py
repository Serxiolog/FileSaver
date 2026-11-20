from contextlib import closing
import os
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from starlette.concurrency import run_in_threadpool
from .common import Problem

def client():
    return boto3.client('s3', endpoint_url=os.environ['S3_ENDPOINT'],
        aws_access_key_id=os.environ['S3_ACCESS_KEY'], aws_secret_access_key=os.environ['S3_SECRET_KEY'],
        region_name='us-east-1', config=Config(signature_version='s3v4', s3={'addressing_style':'path'},
            connect_timeout=5, read_timeout=15, retries={'max_attempts':2}))

async def operation(name, **kwargs):
    try:
        def call():
            with closing(client()) as s3:
                return getattr(s3, name)(Bucket='user-files', **kwargs)
        return await run_in_threadpool(call)
    except (BotoCoreError, ClientError):
        raise Problem(503, 'SERVICE_UNAVAILABLE', 'Object storage unavailable')
