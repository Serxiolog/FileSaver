import asyncio
import logging
import os
import json
from pathlib import Path
import aio_pika
from sqlalchemy import select, text
from .common import Session, Outbox, Inbox, now
from . import main

BINDINGS = {'upload.events': ['file.deletion.ready'],
 'download.events': ['file.created', 'file.deletion.requested', 'file.deleted'],
 'reports.events': ['user.registered', 'user.renamed', 'file.created', 'file.deletion.requested', 'file.deleted', 'file.downloaded']}

async def topology(channel):
    exchange = await channel.declare_exchange('domain.events', aio_pika.ExchangeType.TOPIC, durable=True)
    selected = BINDINGS if os.environ.get('BOOTSTRAP_TOPOLOGY') == 'true' else {k:v for k,v in BINDINGS.items() if k == os.environ.get('EVENT_QUEUE')}
    for name, bindings in selected.items():
        queue = await channel.declare_queue(name, durable=True)
        for binding in bindings:
            await queue.bind(exchange, binding)
        await channel.declare_queue(name + '.dlq', durable=True)
        await channel.declare_queue(name + '.retry', durable=True, arguments={
            'x-message-ttl': 5000, 'x-dead-letter-exchange': '', 'x-dead-letter-routing-key': name})
    return exchange

async def publish(channel, exchange):
    async with Session.begin() as db:
        rows = (await db.scalars(select(Outbox).where(Outbox.sent_at.is_(None)).limit(30).with_for_update(skip_locked=True))).all()
        for row in rows:
            await exchange.publish(aio_pika.Message(json.dumps(row.body).encode(), content_type='application/json',
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT, message_id=row.id), routing_key=row.body['event_type'], mandatory=True)
            row.sent_at = now()

async def consume(channel, queue_name):
    queue = await channel.get_queue(queue_name)
    async with queue.iterator() as messages:
        async for message in messages:
            try:
                body = json.loads(message.body)
                main.validate_event(body)
                async with Session.begin() as db:
                    # Serializes callbacks in this service including duplicate deliveries.
                    await db.execute(text("SELECT pg_advisory_xact_lock(731109)"))
                    if await db.get(Inbox, body['event_id']) is None:
                        await main.apply_event(db, body)
                        db.add(Inbox(id=body['event_id']))
                await message.ack()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                attempts = int((message.headers or {}).get('attempt', 0))
                invalid = isinstance(error, (ValueError, KeyError, TypeError))
                target = queue_name + ('.dlq' if invalid or attempts >= 5 else '.retry')
                logging.warning('Event handling failed; target=%s type=%s', target, type(error).__name__)
                await channel.default_exchange.publish(aio_pika.Message(message.body,
                    content_type='application/json', delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    headers={'attempt': attempts + 1}), routing_key=target, mandatory=True)
                await message.ack()

async def maintenance(channel, exchange):
    while True:
        await publish(channel, exchange)
        if hasattr(main, 'recover'):
            await main.recover()
        Path('/tmp/worker-ready').touch()
        await asyncio.sleep(1)

async def run():
    while True:
        try:
            async with await aio_pika.connect_robust(os.environ['RABBITMQ_URL']) as connection:
                channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
                await channel.set_qos(prefetch_count=10)
                exchange = await topology(channel)
                async with asyncio.TaskGroup() as group:
                    group.create_task(maintenance(channel, exchange))
                    queue = os.environ.get('EVENT_QUEUE')
                    if queue:
                        group.create_task(consume(channel, queue))
        except Exception as error:
            Path('/tmp/worker-ready').unlink(missing_ok=True)
            logging.warning('Worker reconnecting after %s', type(error).__name__)
            await asyncio.sleep(3)

if __name__ == '__main__':
    asyncio.run(run())
