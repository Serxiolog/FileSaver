EVENT_FIELDS = {
 'user.registered': {'user_id', 'display_name'}, 'user.renamed': {'user_id', 'display_name'},
 'file.created': {'file_id','owner_id','original_name','size_bytes','content_type','storage_key','created_at'},
 'file.deletion.requested': {'file_id','deletion_request_id','requested_at'},
 'file.deletion.ready': {'file_id','deletion_request_id'},
 'file.deleted': {'file_id','deletion_request_id','deleted_at'},
 'file.downloaded': {'download_id','file_id','user_id','completed_at'}}

def validate_event(event):
    import uuid
    from datetime import datetime
    required = {'event_id','event_type','event_version','aggregate_id','aggregate_version','occurred_at','correlation_id','payload'}
    if set(event) != required or type(event['event_version']) is not int or event['event_version'] != 1:
        raise ValueError('Unsupported envelope')
    kind = event['event_type']
    if kind not in EVENT_FIELDS or set(event['payload']) != EVENT_FIELDS[kind]:
        raise ValueError('Unsupported payload')
    payload = event['payload']
    for key, value in payload.items():
        if value is None:
            if not (kind == 'file.downloaded' and key == 'user_id'):
                raise ValueError('Null is not allowed')
        elif key != 'size_bytes' and not isinstance(value, str):
            raise ValueError('String field required')
        if key.endswith('_at'):
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.utcoffset() is None:
                raise ValueError('Timezone required')
    for key in ['event_id','aggregate_id','correlation_id']:
        uuid.UUID(event[key])
    for key, value in payload.items():
        if key.endswith('_id') and value is not None:
            uuid.UUID(value)
    if payload.get('file_id', payload.get('user_id')) != event['aggregate_id']:
        raise ValueError('Aggregate mismatch')
    fact = kind in {'file.downloaded','file.deletion.ready'}
    version = event['aggregate_version']
    if (fact and version is not None) or (not fact and (type(version) is not int or version < 1)):
        raise ValueError('Invalid version')
    for key in ['occurred_at']:
        if datetime.fromisoformat(event[key].replace('Z', '+00:00')).utcoffset() is None:
            raise ValueError('Timezone required')
    if kind == 'file.created':
        if type(payload['size_bytes']) is not int or not 0 <= payload['size_bytes'] <= 1048576:
            raise ValueError('Invalid size')
        if payload['storage_key'] != 'files/' + payload['file_id']:
            raise ValueError('Invalid storage key')
