"""Frozen initial schema; independent service database."""
from alembic import op
import sqlalchemy as sa
revision='0001'
down_revision=None

def upgrade():
    op.execute(sa.text('\nCREATE TABLE outbox_events (\n\tid VARCHAR(36) NOT NULL, \n\tbody JSONB NOT NULL, \n\tsent_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id)\n)\n\n'))
    op.execute(sa.text('\nCREATE TABLE processed_events (\n\tid VARCHAR(36) NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n'))
    op.execute(sa.text('\nCREATE TABLE users (\n\tid VARCHAR(36) NOT NULL, \n\tlogin VARCHAR(64) NOT NULL, \n\tpassword_hash TEXT NOT NULL, \n\tdisplay_name VARCHAR(120) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tversion INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (login)\n)\n\n'))

def downgrade():
    op.drop_table('users')
    op.drop_table('processed_events')
    op.drop_table('outbox_events')
