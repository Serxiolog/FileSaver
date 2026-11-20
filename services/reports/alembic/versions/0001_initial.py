"""Frozen initial schema; independent service database."""
from alembic import op
import sqlalchemy as sa
revision='0001'
down_revision=None

def upgrade():
    op.execute(sa.text('\nCREATE TABLE file_catalog (\n\tfile_id VARCHAR(36) NOT NULL, \n\towner_id VARCHAR(36), \n\toriginal_name VARCHAR(255), \n\tsize_bytes INTEGER, \n\tcreated_at TIMESTAMP WITH TIME ZONE, \n\tstatus VARCHAR(20) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tPRIMARY KEY (file_id)\n)\n\n'))
    op.execute(sa.text('CREATE INDEX ix_file_catalog_owner_id ON file_catalog (owner_id)'))
    op.execute(sa.text('CREATE INDEX ix_file_catalog_original_name ON file_catalog (original_name)'))
    op.execute(sa.text('CREATE INDEX ix_file_catalog_status ON file_catalog (status)'))
    op.execute(sa.text('\nCREATE TABLE file_downloads (\n\tdownload_id VARCHAR(36) NOT NULL, \n\tfile_id VARCHAR(36) NOT NULL, \n\tuser_id VARCHAR(36), \n\tdownloaded_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (download_id)\n)\n\n'))
    op.execute(sa.text('CREATE INDEX ix_file_downloads_user_id ON file_downloads (user_id)'))
    op.execute(sa.text('CREATE INDEX ix_file_downloads_file_id ON file_downloads (file_id)'))
    op.execute(sa.text('\nCREATE TABLE outbox_events (\n\tid VARCHAR(36) NOT NULL, \n\tbody JSONB NOT NULL, \n\tsent_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id)\n)\n\n'))
    op.execute(sa.text('\nCREATE TABLE processed_events (\n\tid VARCHAR(36) NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n'))
    op.execute(sa.text('\nCREATE TABLE user_profiles (\n\tuser_id VARCHAR(36) NOT NULL, \n\tdisplay_name VARCHAR(120) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tPRIMARY KEY (user_id)\n)\n\n'))
    op.execute(sa.text('CREATE INDEX ix_user_profiles_display_name ON user_profiles (display_name)'))

def downgrade():
    op.drop_table('user_profiles')
    op.drop_table('processed_events')
    op.drop_table('outbox_events')
    op.drop_table('file_downloads')
    op.drop_table('file_catalog')
