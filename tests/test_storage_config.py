"""MySQL 配置校验不连接数据库，也不读取真实密钥。"""

import unittest
import os
from pathlib import Path
import subprocess
import sys

from storage.config import database_config, storage_backend, mysql_error_hint


class StorageConfigTests(unittest.TestCase):
    def test_file_backend_default_and_explicit_mysql(self):
        self.assertEqual(storage_backend({}), "file")
        self.assertEqual(storage_backend({"STORAGE_BACKEND": "mysql"}), "mysql")
        with self.assertRaises(ValueError):
            storage_backend({"STORAGE_BACKEND": "redis"})

    def test_config_preserves_password_without_exposing_it(self):
        config = database_config({"MYSQL_HOST": "localhost", "MYSQL_DATABASE": "agent_test",
                                  "MYSQL_USER": "agent", "MYSQL_PASSWORD": "a@:/private"})
        self.assertEqual(config.password, "a@:/private")
        self.assertNotIn("a@:/private", repr(config))
        self.assertEqual(config.port, 3306)

    def test_missing_database_and_invalid_port_are_rejected(self):
        for environment in ({}, {"MYSQL_DATABASE": "bad/name", "MYSQL_USER": "u"},
                            {"MYSQL_DATABASE": "mysql", "MYSQL_USER": "root"},
                            {"MYSQL_DATABASE": "agent", "MYSQL_USER": "u", "MYSQL_PORT": "invalid"}):
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                database_config(environment)

    def test_connection_errors_are_actionable_without_driver_message(self):
        self.assertIn("尚未创建", mysql_error_hint(1049))
        self.assertIn("拒绝登录", mysql_error_hint(1045))
        self.assertIn("服务是否启动", mysql_error_hint(2003))

    def test_file_backend_does_not_require_mysql_dependencies(self):
        script = '''
import importlib.abc, sys, tempfile
from pathlib import Path
class BlockDatabase(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('sqlalchemy', 'alembic', 'pymysql'):
            raise ImportError('database dependencies deliberately absent')
sys.meta_path.insert(0, BlockDatabase())
from storage.factory import task_store, memory_store, desktop_stores
from context.compactor import ContextCompactor
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    assert task_store(root).list() == []
    assert memory_store(root).list_records() == []
    desktop_stores(root / 'ui')
    ContextCompactor(None, 'mock', root / '.transcripts', root / '.task_outputs').save_output('id', 'text')
'''
        result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "STORAGE_BACKEND": "file"}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
