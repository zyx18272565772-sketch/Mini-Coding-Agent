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

默认 `GITHUB_MCP_ENABLED=false`，配置模板见 [../.env.example](../.env.example)。`true` 表示允许按需使用，不会在每次提问时预连接；令牌、依赖和工具配置在首次加载时才验证。GitHub 令牌与 `ANTHROPIC_API_KEY` 是两种独立凭据。

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

1. 每个用户任务开始时只检查启用开关；关闭时使用原有 15 个主工具、8 个共用工具，启用时先额外提供一个 `load_mcp` 入口。此时不导入可选 MCP SDK、不创建异步线程、不握手或请求工具列表，也不显示连接提示。
2. 模型在正常 Agent 请求中判断是否需要查询 GitHub 远程数据，再调用 `load_mcp(server="github")`。普通聊天、本地代码任务和概念解释不应调用；会话中“继续查看刚才那个仓库”的追问可结合上下文决定。程序不额外请求一次模型来分类，也不使用简单关键词匹配自动连接。
3. 加载工具同样经过执行前钩子；若被拒绝，不连接。真正执行时才验证配置和令牌，通过 SDK 完成初始化握手与工具发现，只保留已配置的只读白名单工具，最多处理十页。
4. 成功后 Loop 更新工具定义、处理函数映射及摘要使用的工具快照，后续模型请求可以使用最多 7 个远程只读工具。加载入口从模型列表移除，重复调用仍可直接返回已有工具列表，不重复连接。全局内置工具注册表不变。
5. 远程调用继续经过原有执行器、权限检查和执行前后钩子；未连接、未注册和白名单外的调用直接拒绝。工具参数按 Schema 本地验证，外部 Schema 引用被拒绝。
6. 返回文本、嵌入文本资源或结构化结果时过滤令牌，限制约 20,000 字符。图片和二进制内容省略，资源链接只展示而不自动下载；需要更多内容时缩小范围或分页。
7. 加载结果与远程结果各自使用原 `tool_use_id` 回传。加载失败会返回工具错误、撤下入口，并在本任务内记住失败，不反复自动连接；本地工具继续可用。远程 `is_error` 或调用异常继续作为工具错误返回。
8. 主 Agent 与本轮委派的子 Agent 共享同一个惰性对象，谁先需要就由谁首次加载；子 Agent 结束不会提前关闭父 Agent 的连接。整个用户任务结束、出错或中断时统一释放资源。下一个用户任务重新从未加载状态开始。

Agent Loop 保持同步实现，首次实际加载时通过 AnyIO 的 BlockingPortal 管理专用异步事件循环和 SDK 会话。工具发现与调用设有 30 秒超时，HTTP 连接超时为 5 秒；连接状态不持久化，恢复任务时也不会仅因历史中出现 GitHub 工具而自动连接，需要再次调用加载入口。SDK 会话独立关闭，不把 Agent 原有异常或停止信号传入异步任务组，避免被包装成 `ExceptionGroup`。

## 在桌面和命令行中验证

配置成功后发送：

> 使用 GitHub MCP 列出 zyx18272565772-sketch/Mini-Coding-Agent 最近的 Issue，并给出编号、标题和链接；不要修改远程内容。

也可以询问某个 PR 的详情，或要求读取远程仓库的 README。若所选工具未加载，应检查 `GITHUB_MCP_TOOLS`、令牌权限和服务端返回的工具列表。

仅在模型调用 `load_mcp` 时，桌面显示连接中及“GitHub MCP 已连接，加载 N 个只读工具”。执行记录默认折叠；展开后可看到加载工具调用、“GitHub MCP · 列出 Issue”等名称、参数和结果。命令行通过已有工具日志观察调用。

未加载时只有轻量入口说明进入模型工具列表；加载后的具体工具定义和远程结果会增加上下文。首次 GitHub 查询通常多一个模型调用加载工具的轮次，但普通问题省去连接、握手、发现与完整 Schema 的开销。HTTP 请求本身不计入模型请求次数，模型处理加载结果和远程数据的请求照常统计。

## 验证范围与限制

自动化测试使用模拟模型、模拟会话、真实 SDK 与内存 HTTP 传输，覆盖普通问题零连接、首次加载、动态 Schema、主子复用、同批工具刷新、失败不重连、握手、认证头、参数校验和资源关闭。不使用真实 GitHub 令牌或付费模型。测试验证加载调用与网络动作的关系；模型对任意自然语言是否选择正确工具，不属于模拟响应测试能证明的范围。真实账号能否访问指定仓库也需要另行验证。

当前不支持 OAuth 登录、MCP 写入工具、多服务配置、stdio 传输、资源主动下载或服务端请求调用本地模型。外部仓库、Issue 和评论作为参考数据处理；只读 MCP 不替代本地 Shell 权限控制，也不是系统沙箱。

## 官方参考

- [GitHub 远程 MCP 服务](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md)
- [GitHub 工具过滤与只读配置](https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md)
- [官方 Python SDK 2.3.0](https://pypi.org/project/mcp/2.3.0/)
- [AnyIO 同步/异步桥接](https://anyio.readthedocs.io/en/stable/threads.html#using-asynchronous-context-managers)
