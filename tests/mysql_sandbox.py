"""手动集成测试辅助：仅启动本任务拥有的全新 MySQL 实例，不触碰系统服务。"""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from uuid import uuid4


def start():
    import pymysql
    root = Path(__file__).resolve().parents[1]
    directory = root / ".agent_data" / ("mysql-test-" + uuid4().hex)
    directory.mkdir(parents=True)
    binary = Path(r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe")
    options = [str(binary), "--no-defaults", f"--basedir={binary.parent.parent}",
               f"--datadir={directory / 'data'}", "--console"]
    log = (directory / "server.log").open("ab")
    result = subprocess.run(options + ["--initialize-insecure"], stdout=log, stderr=log,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=90)
    if result.returncode:
        raise RuntimeError(f"测试实例初始化失败，日志：{directory / 'server.log'}")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(options + [f"--port={port}", "--bind-address=127.0.0.1", "--mysqlx=0",
        "--innodb-buffer-pool-size=32M"], stdout=log, stderr=log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        deadline = time.monotonic() + 45
        while True:
            try:
                connection = pymysql.connect(host="127.0.0.1", port=port, user="root", connect_timeout=1)
                break
            except pymysql.Error:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f"测试实例启动失败，日志：{directory / 'server.log'}") from None
                time.sleep(.2)
        password = uuid4().hex
        with connection:
            with connection.cursor() as cursor:
                cursor.execute("CREATE DATABASE mini_agent_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                cursor.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (password,))
        config = dict(MYSQL_HOST="127.0.0.1", MYSQL_PORT=str(port), MYSQL_USER="root",
                      MYSQL_PASSWORD=password, MYSQL_DATABASE="mini_agent_test", STORAGE_BACKEND="mysql")
        config_path = directory / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        (directory / "pid").write_text(str(process.pid))
        print(config_path)
    except BaseException:
        process.terminate()
        process.wait(timeout=15)
        raise
    finally:
        log.close()


if __name__ == "__main__":
    start()
