"""merge heads

Revision ID: 3062e02f9f78
Revises: 205bb6c05117, 6f2b3a8c9d10
Create Date: 2026-02-21 22:26:06.649282

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3062e02f9f78'
down_revision: Union[str, Sequence[str], None] = ('205bb6c05117', '6f2b3a8c9d10')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
