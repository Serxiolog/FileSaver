"""Frozen initial schema; independent service database."""
from alembic import op
import sqlalchemy as sa
revision='0001'
down_revision=None

def upgrade():
    op.execute(sa.text('\nCREATE TABLE files (\n\tid VARCHAR(36) NOT NULL, \n\towner_id VARCHAR(36) NOT NULL, \n\toriginal_name VARCHAR(255) NOT NULL, \n\tstorage_key VARCHAR(100) NOT NULL, \n\tsize_bytes INTEGER NOT NULL, \n\tcontent_type VARCHAR(255) NOT NULL, \n\tchecksum_sha256 VARCHAR(64) NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tdeletion_request_id VARCHAR(36), \n\tdeletion_requested_at TIMESTAMP WITH TIME ZONE, \n\tdeleted_at TIMESTAMP WITH TIME ZONE, \n\tready BOOLEAN NOT NULL, \n\tcorrelation_id VARCHAR(36), \n\tPRIMARY KEY (id), \n\tUNIQUE (storage_key)\n)\n\n'))
    op.execute(sa.text('CREATE INDEX ix_files_owner_id ON files (owner_id)'))
    op.execute(sa.text('\nCREATE TABLE outbox_events (\n\tid VARCHAR(36) NOT NULL, \n\tbody JSONB NOT NULL, \n\tsent_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id)\n)\n\n'))
    op.execute(sa.text('\nCREATE TABLE processed_events (\n\tid VARCHAR(36) NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n'))
    op.execute(sa.text('\nCREATE TABLE upload_intents (\n\tid VARCHAR(36) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n'))

def downgrade():
    op.drop_table('upload_intents')
    op.drop_table('processed_events')
    op.drop_table('outbox_events')
    op.drop_table('files')
