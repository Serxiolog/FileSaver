"""Frozen initial schema; independent service database."""
from alembic import op
import sqlalchemy as sa
revision='0001'
down_revision=None

def upgrade():
    op.execute(sa.text('\nCREATE TABLE available_files (\n\tfile_id VARCHAR(36) NOT NULL, \n\tstorage_key VARCHAR(100), \n\toriginal_name VARCHAR(255), \n\tcontent_type VARCHAR(255), \n\tsize_bytes INTEGER, \n\tstatus VARCHAR(20) NOT NULL, \n\tfile_version INTEGER NOT NULL, \n\tdeletion_request_id VARCHAR(36), \n\tcorrelation_id VARCHAR(36), \n\tready_sent BOOLEAN NOT NULL, \n\tPRIMARY KEY (file_id)\n)\n\n'))
    op.execute(sa.text('\nCREATE TABLE download_history (\n\tdownload_id VARCHAR(36) NOT NULL, \n\tfile_id VARCHAR(36) NOT NULL, \n\tuser_id VARCHAR(36), \n\tcompleted_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (download_id)\n)\n\n'))
    op.execute(sa.text('CREATE INDEX ix_download_history_file_id ON download_history (file_id)'))
    op.execute(sa.text('\nCREATE TABLE download_sessions (\n\tsession_id VARCHAR(36) NOT NULL, \n\tfile_id VARCHAR(36) NOT NULL, \n\tuser_id VARCHAR(36), \n\tstatus VARCHAR(20) NOT NULL, \n\tstarted_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tfinished_at TIMESTAMP WITH TIME ZONE, \n\tlease_expires_at TIMESTAMP WITH TIME ZONE, \n\tcorrelation_id VARCHAR(36) NOT NULL, \n\tPRIMARY KEY (session_id)\n)\n\n'))
    op.execute(sa.text('CREATE INDEX ix_download_sessions_status ON download_sessions (status)'))
    op.execute(sa.text('CREATE INDEX ix_download_sessions_file_id ON download_sessions (file_id)'))
    op.execute(sa.text('\nCREATE TABLE outbox_events (\n\tid VARCHAR(36) NOT NULL, \n\tbody JSONB NOT NULL, \n\tsent_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id)\n)\n\n'))
    op.execute(sa.text('\nCREATE TABLE processed_events (\n\tid VARCHAR(36) NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n'))

def downgrade():
    op.drop_table('processed_events')
    op.drop_table('outbox_events')
    op.drop_table('download_sessions')
    op.drop_table('download_history')
    op.drop_table('available_files')
