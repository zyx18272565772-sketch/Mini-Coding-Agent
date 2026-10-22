"""显式初始化、验证和迁移入口，不在启动 Agent 时自动建表。"""
import argparse
from pathlib import Path


def upgrade(database):
    from alembic import command
    from alembic.config import Config
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with database.transaction() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("upgrade", "check", "import-files"))
    parser.add_argument("--source", type=Path, default=Path(".agent_data/sessions"))
    args = parser.parse_args()
    try:
        from storage.database import get_database, StorageError
    except ImportError:
        parser.exit(1, "缺少 MySQL 依赖，请先安装 requirements-mysql.txt\n")
    try:
        database = get_database()
        if args.action == "upgrade":
            upgrade(database)
            print("MySQL 表结构已就绪。")
        else:
            database.verify()
            if args.action == "check":
                print("MySQL 连接与表结构验证通过。")
            else:
                from storage.migrate import import_files
                print(import_files(database, args.source))
    except (StorageError, ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
