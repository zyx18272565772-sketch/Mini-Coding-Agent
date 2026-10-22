"""归档正文入库，保留已有文件记录和原编号。"""
from pathlib import Path
from alembic import op

revision = "0002_artifact_content"
down_revision = "0001_mysql_storage"
branch_labels = None
depends_on = None


def upgrade():
    source = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(source)


def downgrade():
    raise RuntimeError("归档正文不自动删除，请从备份恢复数据库。")
