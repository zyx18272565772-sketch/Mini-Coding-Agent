# MySQL 存储

MySQL 8.0 及以上可作为桌面和命令行共用的存储。运行中的 `messages` 仍是 Python 列表；数据库在用户输入、完整工具批次、最终回复及运行状态变化等保存点记录有效上下文。数据库不会代替模型调用或工具执行。

## 1. 填写连接配置

打开项目根目录的 `.env`，保留现有模型和 GitHub 配置，追加以下内容。暂时不要启动 Agent，先完成下面的初始化与导入。

```dotenv
STORAGE_BACKEND=mysql
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=mini_coding_agent
MYSQL_USER=mini_agent
MYSQL_PASSWORD="你为这个MySQL账号设置的密码"
```

| 字段 | 填什么 |
| --- | --- |
| `STORAGE_BACKEND` | `mysql` 启用数据库；`file` 使用原来的文件存储 |
| `MYSQL_HOST` | MySQL 在这台电脑就填 `127.0.0.1`，在另一台机器则填其地址 |
| `MYSQL_PORT` | 安装 MySQL 时选择的端口，通常为 `3306` |
| `MYSQL_DATABASE` | 专门给本项目创建的数据库名，建议 `mini_coding_agent` |
| `MYSQL_USER` | MySQL 登录账号，下面示例创建的是 `mini_agent`；本机临时验证也可使用已有账号 |
| `MYSQL_PASSWORD` | 上面这个 MySQL 账号的登录密码，不是 DeepSeek 或 GitHub 密钥 |

密码含空格或 `#` 等字符时使用引号。配置仅由 Python 后端读取，不返回界面、不发送给模型、不提交 Git。不要把实际密码粘贴到聊天中。代码使用 URL 对象传递密码，不需要手动做 URL 编码。

## 2. 创建数据库和账号

在 MySQL Workbench、Navicat 或 MySQL 命令行中，以有建库权限的账号执行。**先将示例密码改成自己的密码**。如果已有专用账号，使用已有账号并调整 `.env` 即可。

```sql
CREATE DATABASE IF NOT EXISTS mini_coding_agent
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'mini_agent'@'localhost' IDENTIFIED BY '这里换成你自己的密码';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES
  ON mini_coding_agent.* TO 'mini_agent'@'localhost';
```

这些权限仅作用于专用数据库，不授予服务器管理权限。当前应用面向个人本地使用，不支持多个桌面实例同时操作同一会话。

## 3. 安装依赖并初始化表结构

在项目根目录的 PowerShell 执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-mysql.txt
.\.venv\Scripts\python.exe -m storage.manage upgrade
.\.venv\Scripts\python.exe -m storage.manage check
```

`upgrade` 使用 Alembic 创建业务表和 `alembic_version` 版本表；不会创建数据库本身，也不提供自动删表降级。首次初始化应使用专用空数据库。MySQL 的 DDL 不保证整体回滚，如果首次初始化中断并留下部分表，请检查错误，在新的专用空数据库上重试，不要对已有业务库执行删表清理。成功后重复 `upgrade` 不会重建表。

已使用第一版数据库的项目也执行同一个 `upgrade` 命令。迁移 `0002_artifact_content` 为 `artifacts` 增加正文和行数字段，允许新归档不设置文件路径；保留已有数据和编号。建表和升级 SQL 均可在 `storage/migrations/versions/` 查看。

未设置连接参数、版本未初始化、认证失败或断开连接都会明确报错。选择 MySQL 后不会悄悄写回 JSON，避免产生两份数据。

## 4. 导入旧文件（如需保留原历史）

先正常停止所有任务，关闭旧的桌面和命令行程序；不要在导入过程中运行 Agent。请在第一次使用 MySQL 启动 Agent **之前**导入，以保留原项目和会话 ID。

```powershell
.\.venv\Scripts\python.exe -m storage.manage import-files --source ".agent_data/sessions"
```

导入器读取 `workspace.json` 和会话 JSON，并按其工作目录读取各项目 `.tasks/`、`.memory/` 及归档文件。源文件不会改写、重命名或删除，仍可作为原始备份；建议在切换前另行备份整个数据目录和项目级数据。

- 保留会话 ID、项目 ID、事件顺序、消息内容和用量。已有同 ID 数据不会被旧文件覆盖，重复执行可跳过已导入会话、任务和记忆。
- 缺失依赖、循环任务图、损坏文件、目录冲突等使本次数据写入整体回滚；不会导入一半。导入必须在源程序退出后执行，导入器不负责协调正在写入源文件的旧进程。
- 若源会话仍标记运行中，请先用原文件存储模式正常打开、停止并退出旧程序。不要手动删除原 JSON。
- 界面外观从旧配置读取后保存到本机 `appearance.json`，不会修改作为迁移来源的 `workspace.json`。
- 大型归档保持本地，只登记路径、大小和 SHA-256；旧文件若无法可靠判断会话归属，数据库中的会话和运行关联为空。
- 已在数据库为同一路径创建了不同项目 ID 时会拒绝导入，不会擅自合并或覆盖项目。

完成后按原方式运行 `desktop.py` 或 `main.py`。命令行的每次启动创建一个新会话，其记录也能在桌面的同项目下查看；当前没有命令行会话选择/恢复参数。

## 数据如何划分

| 表 | 内容与写入时机 |
| --- | --- |
| `projects` | 项目 ID、名称、工作目录、路径去重键和隐藏状态 |
| `sessions` | 会话标题、状态、所属项目、当前运行、最新检查点和并发版本 |
| `chat_messages` | 用户原文与主/子 Agent 回复；流式片段合并到同一消息，保留中断标记 |
| `agent_runs` | 一条用户指令的原始请求、模型、目录、状态、失败、重试次数与起止时间 |
| `context_checkpoints` | 当前有效的模型协议 `messages` JSON，保留最近三个快照 |
| `session_events` | 按会话顺序保存工具、权限、回复、用量和状态事件，供界面重放 |
| `run_usage` | 按运行和 main/subagent/memory/compact 保存计数，使用最新快照覆盖而非重复累加 |
| `project_tasks` | 同项目共享的任务、状态与领取者 |
| `task_dependencies` | 项目内的任务依赖边 |
| `memories` | 结构化长期记忆、正文、版本及可确定的来源 |
| `artifacts` | 新压缩归档及大型工具输出的正文、行数、归属、大小与校验值；兼容旧文件元数据 |

聊天原文和执行事件不随模型上下文压缩删除。检查点仅在内容变化时写入，流式片段先供界面轮询，在完整回复、检查点或运行结束时批量持久化；进程崩溃前尚未保存的片段可能丢失。

任务图更新和记忆更新以项目行锁串行处理，防止重复领取和并发整理覆盖。模型推理和 Shell 执行期间不持有这些事务。父子进程只传递身份和消息，连接池在各进程分别创建。重试沿用原运行 ID，用量继续累计，不追加第二条用户指令。

删除会话会删除其原文、事件、检查点和运行用量，释放它领取但未完成的任务；项目任务和记忆仍保留。删除项目会隐藏项目并删除其会话，保留源文件及项目级知识；重新添加同一目录会恢复原项目 ID。归档文件不会自动删除。

新工具输出和压缩归档保存在 `artifacts.content`，不再生成本地归档目录。上下文中使用 `artifact://编号` 引用，模型仍调用 `read_file`，通过 `start_line`、`end_line` 或 `limit` 分页读取；查询在数据库端定位行并截取有界片段，返回长度沿用文件工具限制。每次读取按当前项目过滤，不能读取其他项目的归档；归档编号不包含连接凭据，也不能用于 `write_file` 或 `edit_file`。

`session_events` 保留执行当时的工具结果，`artifacts` 保存压缩时需要独立取回的归档正文，因此目前仍可能保存相同文本的多个副本。归档使用不可变的新编号，避免主、子 Agent 或不同运行中同名工具调用覆盖历史正文。

外观设置、本地源码和技能文件仍在磁盘中。旧 `.task_outputs`、`.transcripts` 中的已有文件不自动转入数据库，旧会话可以继续按原路径读取；备份旧会话时应保留这些被引用的文件。Redis 未接入，也不参与会话恢复。

## 测试

普通测试不依赖 MySQL。真实集成测试必须使用**单独测试数据库**，禁止填写实际业务库。

```powershell
$env:STORAGE_BACKEND='file'
$env:GITHUB_MCP_ENABLED='false'
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

`tests/test_mysql_storage.py` 使用 `MYSQL_TEST_CONFIG` 指向包含测试连接参数的本地 JSON，未设置时跳过。测试会创建和删除专用项目数据；应仅在隔离实例执行。`tests/mysql_sandbox.py` 可在安装了标准路径 MySQL Server 8.0 的 Windows 上创建全新测试实例，日志和凭据仅放在被忽略的 `.agent_data/`，不修改系统 MySQL 服务。测试后应通过该测试实例的 SQL `SHUTDOWN` 停止它。

实现参考 [MySQL 行锁](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)、[SQLAlchemy MySQL 方言](https://docs.sqlalchemy.org/en/20/dialects/mysql.html)、[进程与连接池](https://docs.sqlalchemy.org/en/20/core/pooling.html#using-connection-pools-with-multiprocessing-or-os-fork) 和 [Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html)。
