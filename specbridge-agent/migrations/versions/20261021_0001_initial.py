"""Create the initial SpecBridge persistence model.

Revision ID: 20261021_0001
Revises: None
"""

from alembic import op

from app.infrastructure.models import Base

revision = "20261021_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=connection)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
