# GitHub MCP 接入

通过 GitHub 官方托管的远程 MCP 服务，向主、子 Agent 提供仓库、Issue 和 PR 查询工具。使用官方 Python SDK `mcp==2.3.0` 的 Streamable HTTP 传输，不需要 Docker 或本地 GitHub MCP 服务。

首版仅支持 GitHub 的只读工具，不创建 Issue、提交 PR、合并分支或推送代码。GitHub MCP 访问远程 GitHub 数据，本地 Git 操作仍通过原有命令工具执行。

## 本地配置

1. 在 Agent 虚拟环境中安装 `requirements-mcp.txt`。桌面依赖仍使用 `requirements-desktop.txt`；MCP 为可选依赖，未启用时核心 Agent 不加载 SDK 或连接服务。
2. 在 GitHub 创建个人访问令牌。优先使用 fine-grained token，限制到需要访问的仓库，并根据查询需求授予 Contents、Issues、Pull requests 的读取权限；组织仓库可能还需要管理员批准。具体权限和限制以 [GitHub 令牌文档](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) 为准。
3. 在 Agent 根目录的本地 `.env` 或环境变量中配置下面两个字段，然后重启 Agent。不要覆盖原有模型配置，也不要把令牌提交到仓库、发送到聊天或填进模型提示词。

```dotenv
GITHUB_MCP_ENABLED=true
GITHUB_MCP_TOKEN=你的GitHub个人访问令牌
```

默认 `GITHUB_MCP_ENABLED=false`，配置模板见 [../.env.example](../.env.example)。GitHub 令牌与 `ANTHROPIC_API_KEY` 是两种独立凭据。

可以缩小加载范围，例如只查询 Issue 和 PR：

```dotenv
GITHUB_MCP_TOOLS=list_issues,issue_read,list_pull_requests,pull_request_read
```

省略此项时请求下表的七个工具，实际可用集合还取决于服务端及令牌权限。配置不接受写入工具，也不支持通过环境变量修改服务地址。令牌只发给固定的 `https://api.githubcopilot.com/mcp/readonly`，同时发送只读请求头和工具范围；连接、认证或依赖失败时显示中文提示，本轮保留本地工具。

## 工具

| 模型中的工具名 | 用途 |
| --- | --- |
| `mcp__github__search_repositories` | 搜索远程仓库 |
| `mcp__github__get_file_contents` | 读取仓库文件或目录信息 |
| `mcp__github__search_code` | 搜索远程代码 |
| `mcp__github__list_issues` | 列出 Issue |
| `mcp__github__issue_read` | 读取 Issue 详情或评论 |
| `mcp__github__list_pull_requests` | 列出 PR |
| `mcp__github__pull_request_read` | 读取 PR 详情、差异或评论 |

参数通过 MCP 的 `tools/list` 动态取得，转换为模型的 `input_schema`，不在本地复制各工具的全部参数定义。调用时去掉 `mcp__github__` 前缀，把原工具名和参数交给 MCP 的 `tools/call`。

## 执行流程

1. 每轮主 Agent 开始时检查启用开关和令牌；未启用时不产生 MCP 网络请求。
2. 通过 SDK 完成初始化握手和工具发现，只保留本地只读白名单中的已配置工具。发现最多处理十页，防止无限分页。
3. 把发现的工具追加到本轮工具列表和处理函数映射，不修改全局内置工具注册表。未启用时仍是 15 个主工具、8 个共用工具；启用后各自最多增加 7 个只读工具。
4. 模型选中 MCP 工具后，继续经过现有执行器、权限检查及执行前后钩子；未连接、未注册和白名单外的 MCP 调用直接拒绝。
5. 本地根据工具 Schema 校验参数，再通过 SDK 调用远程工具。Schema 的外部引用被拒绝，避免参数验证时访问任意 URL 或本机文件。
6. 把文本、嵌入的文本资源或结构化结果转换为参考文本，过滤令牌并限制约 20,000 字符。图片和二进制内容省略，资源链接只展示而不自动下载；大结果需要缩小范围或使用工具提供的分页参数。
7. 结果按原 `tool_use_id` 回传模型。远程 `is_error` 或调用异常会作为模型工具错误返回，桌面工具卡片显示“执行失败”，与“权限被拒绝”区分。
8. 本轮结束、模型请求出错或中断时释放 MCP 会话。主 Agent 委派子 Agent 时复用当前连接；单独运行子 Agent 时建立并释放自己的连接。

Agent Loop 保持同步实现，通过 AnyIO 的 BlockingPortal 管理一个专用异步事件循环和 SDK 会话。工具发现与调用设有 30 秒超时，HTTP 连接超时为 5 秒；MCP 连接状态不持久化，恢复任务时重新建立连接和工具列表。SDK 会话独立关闭，不把 Agent 原有的模型请求异常或停止信号传入其异步任务组，避免被包装成 `ExceptionGroup` 后失去原始错误分类。

## 在桌面和命令行中验证

配置成功后发送：

> 使用 GitHub MCP 列出 zyx18272565772-sketch/Mini-Coding-Agent 最近的 Issue，并给出编号、标题和链接；不要修改远程内容。

也可以询问某个 PR 的详情，或要求读取远程仓库的 README。若所选工具未加载，应检查 `GITHUB_MCP_TOOLS`、令牌权限和服务端返回的工具列表。

桌面会显示“GitHub MCP 已连接，加载 N 个只读工具”，执行记录默认折叠；展开后可看到“GitHub MCP · 列出 Issue”等中文名称、仓库、参数和结果。命令行通过已有工具日志观察调用。

工具描述和参数定义会增加模型输入上下文，读取到的内容也会进入后续模型请求；MCP 不自动降低 Token 消耗。MCP 的 HTTP 请求本身不计入模型请求次数，模型处理这些结果的请求照常统计。

## 验证范围与限制

自动化测试使用模拟会话、真实官方 SDK 与内存 HTTP 传输，覆盖握手、认证头、分页发现、只读过滤、Schema 校验、超时、连接关闭、主/子循环和错误结果。不需要真实 GitHub 令牌，也不调用付费模型。真实账号是否可访问指定仓库，需要本地配置后再验证，不能用模拟测试替代。

当前不支持 OAuth 登录、MCP 写入工具、多服务配置、stdio 传输、资源主动下载或服务端请求调用本地模型。外部仓库、Issue 和评论作为参考数据处理；只读 MCP 不替代本地 Shell 权限控制，也不是系统沙箱。

## 官方参考

- [GitHub 远程 MCP 服务](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md)
- [GitHub 工具过滤与只读配置](https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md)
- [官方 Python SDK 2.3.0](https://pypi.org/project/mcp/2.3.0/)
- [AnyIO 同步/异步桥接](https://anyio.readthedocs.io/en/stable/threads.html#using-asynchronous-context-managers)
