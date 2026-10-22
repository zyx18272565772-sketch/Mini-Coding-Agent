"""初始业务存储。升级只创建新表，不清空已有数据。"""
from alembic import op
from pathlib import Path

revision = "0001_mysql_storage"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # 使用固定版本的 DDL，后续修改 models 不会改变旧迁移的含义。
    source = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    for statement in source.split(";"):
        if statement.strip():
            op.get_bind().exec_driver_sql(statement)


def downgrade():
    raise RuntimeError("不提供自动删表降级；请从备份恢复数据库。")
