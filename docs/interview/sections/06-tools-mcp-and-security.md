下面继续以“项目负责人 / 核心开发者”的第一人称回答。这里会明确区分当前已有的受控工具边界和完整安全沙箱，并如实说明路径隔离、进程取消及并发写入方面的现有缺口。

## 1. 工具的统一协议是什么？

Runtime 使用 `AgentTool` 作为统一工具协议，包含：

```text
name
description
parameters
execute
timeout_seconds
execution_mode
```

`name` 是模型和 Runtime 的调度键；`description` 和 JSON Schema 帮助模型选择并生成参数；`execute` 是本地实现；`timeout_seconds` 和 `execution_mode` 属于 Runtime 控制。

统一执行签名是：

```python
execute(
    call_id: str,
    args: dict[str, Any],
    abort: Callable[[], bool],
    on_update: Callable[[ToolResult], None] | None,
) -> ToolResult
```

投影到模型侧的 LLMTool 时，只保留名称、描述和参数 Schema，本地执行函数不会发送给 Provider。

## 2. 工具参数如何定义和验证？

工具参数使用 JSON Schema 声明，包括字段类型、必填项、取值范围、enum 和 `additionalProperties`。

当前 JSON Schema 主要是模型生成合同，Runtime 没有对所有 AgentTool 做统一的完整 Schema Validation。真正执行前，每个内置工具仍会手动校验关键字段，例如路径是否为空、整数是否有效、超时是否为正数、Edit 的 old/new string 是否存在。

MCP 工具 Schema 来自 Server 的 `inputSchema`，缺少基础字段时会补成 object/properties，但具体业务合法性仍主要由 MCP Server 检查。

生产化可以在 Runtime 调度前增加统一 JSON Schema Validator，但工具内部仍必须做业务和权限校验。

## 3. 模型生成非法参数时怎么处理？

需要区分结构错误和业务错误。

未知工具名或无法解析的 JSON 参数，会在 LLM 边界触发有限纠正请求，最多进行三次模型调用。系统会告诉模型错误原因和可用工具。

缺少字段、类型错误、路径不存在、Edit 匹配失败等业务错误，由工具返回 `is_error=true` 的 ToolResult。Runtime 将错误作为正常工具结果写入下一轮上下文，让模型修正。

非法参数通常不会直接中止整个 Agent，因为模型仍有机会恢复。只有协议无法建立、资源永久失效或外部明确中止时，才停止运行。

## 4. 工具执行结果如何结构化返回？

ToolResult 包含四部分：

```python
ToolResult(
    content=(TextBlock(...) | ImageBlock(...),),
    details=...,
    is_error=False,
    terminate=False,
)
```

`content` 是下一轮模型可见内容；`details` 是本地结构化信息，不自动进入模型请求；`is_error` 表示失败但仍可由模型处理；`terminate` 请求 Runtime 在记录结果后停止。

同一轮多个结果会组成一个 UserMessage，每个 ToolResultBlock 通过 call ID 对应原调用。`details` 也按照 Tool Call ID 分组，避免同名工具或并发完成顺序造成错配。

## 5. Bash 工具如何限制危险命令？

当前 Bash 的限制主要是执行边界，而不是命令级安全策略。

已有措施包括：固定工作目录、默认及最大超时、输出截断、非交互环境变量、阻止明显的长时间前置 sleep，以及可通过 `exec_prefix` 在 `unshare`、容器或其他宿主包装器中运行。

但当前 Bash 没有 `rm`、`sudo`、网络访问或目录逃逸黑名单，也不会分析 Shell AST。模型仍可以执行工作区之外的命令。

因此真正的安全边界必须来自最小权限用户、只读挂载、容器、网络隔离、资源限制和 PRE_TOOL_USE 审批 Hook。项目本身不宣称 Bash 是完整沙箱。

## 6. Read/Edit 如何防止访问工作区之外的文件？

当前 Read 和 Edit 会把相对路径解析到配置的 cwd，但也允许绝对路径，并且没有在 `resolve()` 后强制检查结果仍位于 workspace root 内。

所以当前实现不能作为严格路径沙箱。`cwd` 是默认解析基准，不是不可逃逸的安全边界。

严格实现应当：

1. 将路径规范化并解析符号链接。
2. 检查解析结果满足 `path.is_relative_to(workspace_root)`。
3. 拒绝绝对路径或 root 之外的 `..`。
4. 对创建文件检查最近存在的父目录及符号链接。
5. 配合只读挂载和操作系统权限。

这是生产化前必须补齐的安全项。

## 7. Edit 如何处理并发修改和版本冲突？

当前 Edit 默认声明为 `sequential`，因此同一轮只要包含 Edit，整组有效工具调用都会用单 Worker 顺序执行。

Edit 采用精确字符串替换，默认要求 old string 唯一。如果第一个 Edit 已经修改了第二个 Edit 依赖的文本，第二次通常会因为找不到 old string 而返回错误，而不是盲目写入。

但它没有文件版本号、内容 Hash、跨进程锁或 Compare-And-Swap。读取和写入之间仍有竞争窗口，多个 Agent 或进程修改同一文件时不能保证冲突一定被发现。

当前的 sequential 只是单个 Runtime、单轮工具组内的防护，不是完整并发控制。

## 8. 工具调用超时后，底层进程是否真的被终止？

不能统一保证。

通用 AgentTool 超时由 Future 等待包装。超时后 Runtime 返回错误结果，但 Python 线程本身不能被强制终止，工具代码可能继续在后台运行。

Bash 内部使用 `subprocess.run(timeout=...)`，超时后会终止直接启动的 Shell 进程，但不保证任意孙进程或脱离进程组的后台进程全部被清理。

MCP 超时会取消客户端 Future，但远端 Server 是否停止实际操作取决于 Server 实现。

生产级实现需要进程组、容器级 Kill、Cgroup、远端取消协议和副作用对账，不能只依赖线程 Future Timeout。

## 9. 用户中止任务时，正在运行的工具如何取消？

Runtime 在每轮开始时检查 Abort Flag，并把同一个函数传给工具。

MCP 每约 0.1 秒轮询 Abort，发现中止后取消异步 Future，能够较快返回。Task Tool 会把 Abort 继续传给子 Agent。

Read 和 Edit 操作较短，主要在开始前检查。当前 Bash 在开始前和完成后检查 Abort，但执行中的 `subprocess.run` 不会持续轮询，因此需要等待命令完成或超时。

所以当前取消是协作式取消，不是所有工具的强制抢占。真正可取消的工具必须主动检查 Abort，或由宿主终止其进程和资源。

## 10. 并行工具调用由模型决定还是 Runtime 决定？

模型决定一轮输出多少个 ToolCall，以及每个调用使用什么参数。

Runtime 决定这些调用如何执行。它检查本地 AgentTool 的 `execution_mode`、最大并发数和 Hook 结果，再选择线程池大小。

因此“模型并行调用”和“工具并行执行”是两个不同层次。模型可以一次提出多个调用，但 Runtime 仍可以把它们串行执行。

## 11. 哪些工具可以并行，哪些必须串行？

原则上，只读、无共享可变资源且彼此独立的工具可以并行；会修改共享状态或依赖同一会话顺序的工具应串行。

当前默认配置中：

- Read、Recall、MCP 和 Task 通常是 parallel。
- Bash 默认也是 parallel，但 Bash 可能执行写操作，因此调用者需要根据使用场景调整。
- Edit 默认是 sequential。

只要同一组有效调用中有一个工具声明为 sequential，Runtime 就会把整组调用设为单 Worker。

当前没有根据文件路径或 Shell 命令内容自动推断资源冲突。

## 12. 两个工具同时修改同一个文件怎么办？

如果两者都是默认 Edit，同一轮会顺序执行。第二个 Edit 会重新读取文件并做精确匹配，因此很多旧内容冲突会转换成“字符串不存在”的错误。

但如果使用 Bash 修改文件、工具错误地声明为 parallel，或者来自不同 Agent 和进程，当前没有全局文件锁或资源调度器，仍可能发生覆盖。

更完整的方案是让每个工具声明读写资源，例如 `write:file:/path`，Runtime 按资源键串行化；同时在文件层使用版本 Hash 和原子替换。

## 13. 并行结果按照完成顺序还是调用顺序写入上下文？

工具生命周期 Event 可以按照实际完成顺序写入，这样 Trace 能反映真实并发时间。

但最终给模型看的 ToolResultBlock 一定按照 AssistantMessage 中原始 ToolCall 顺序组装。

这保证模型输入、测试和重放不依赖线程调度时序。Tool Call ID 负责结果因果配对，列表顺序负责稳定展示。

## 14. Hook 能拦截哪些阶段？

当前有四个 HookPoint：

```text
SESSION_START
PRE_TOOL_USE
POST_TOOL_USE
SESSION_END
```

SESSION_START 可以注入运行说明；PRE_TOOL_USE 可以检查并阻止工具；POST_TOOL_USE 可以在结果包记录后添加提醒或上下文；SESSION_END 可以观察结束并做外围沉淀。

PRE_TOOL_USE 不能插入 Message，因为插在 Assistant ToolCall 和 User ToolResult 之间会破坏 Provider 配对。它只能阻止，阻止原因会转换成错误 ToolResult。

Hook 不能修改已有历史，也不能任意跳转 Runtime 控制流。

## 15. Hook 与硬编码权限判断相比有什么优势？

Hook 可以按 Agent、运行环境和工具调用动态组合策略，不需要在 `core.run()` 中增加 Provider、业务或权限分支。

每次 Hook 触发都会记录 HookFiredEvent，包括是否阻止和添加了多少消息，因此权限决策可以审计和测试。

不同部署可以安装不同 Hook，例如只读模式、高风险命令审批或租户策略，而核心循环保持不变。

但 Hook 不是不可绕过的安全边界。如果调用者忘记安装 Hook，策略就不存在。路径隔离、系统权限和危险参数校验等硬性不变量仍应在工具或宿主环境中强制执行。

## 16. MCP Server 不可信时有哪些风险？

MCP Server 同时控制工具名称、描述、参数 Schema 和返回内容，因此风险包括：

- 在描述或结果中注入恶意指令。
- 诱导模型发送敏感文件或凭据。
- Server 自身读取文件、执行命令或访问网络。
- 返回超大内容消耗上下文和内存。
- 伪造结构化结果或资源链接。
- 长时间阻塞、崩溃或产生不可撤销副作用。
- stdio Server 进程继承不应获得的环境变量和权限。

当前系统提供超时、Abort、错误转换、内容类型降级和显式连接生命周期，但不验证 Server 的业务可信度，也没有统一的 MCP 返回大小上限。

不可信 MCP 应在容器中运行，使用最小权限环境、允许列表、网络策略、凭据隔离和高风险操作审批。

## 17. MCP 工具名称冲突怎么处理？

默认情况下，每个 MCP 工具会加上 Server 名称前缀，例如：

```text
github_search
filesystem_read
```

调用 MCP Server 时仍使用原始工具名，前缀只用于模型可见名称和本地调度。

同一个 MCPConnection 中发现重复的模型可见名称时，会直接抛出 MCPError，要求配置不同前缀，不能静默覆盖。

但当前 Agent 最终组装所有静态工具和多个 Toolset 时，没有统一检查全局工具名唯一性；`core.run()` 建立字典时可能让后一个同名工具覆盖前一个。默认不同 Server 前缀降低了风险，但自定义前缀或静态工具仍可能冲突。生产化应在 Agent 构造阶段统一拒绝重复名称。

## 18. MCP 连接中断后是否重试？

当前没有自动重连和自动重放 MCP 调用。

单次调用超时但连接仍然存活时，会返回普通错误 ToolResult，`terminate=false`，模型可以决定重试。

如果后台 Session 已经死亡，工具结果会设置 `terminate=true`，避免 Agent 持续调用不可用资源并浪费预算。

MCPConnection 的 `close()` 是幂等的，关闭后可以再次 `open()`；初始化失败也会清理半启动资源，调用者可以重新建立连接。但重新连接属于 Session 或宿主层责任，Runtime 不会自动重放可能已经产生副作用的工具调用。

## 19. 如何防止 Prompt Injection 引导 Agent 调用危险工具？

不能只依赖提示词告诉模型“不要执行危险操作”。

防护需要分层：

1. 工具和文件系统采用最小权限。
2. Bash 和 MCP 运行在隔离环境中。
3. PRE_TOOL_USE Hook 对命令、路径、目标服务和参数做策略检查。
4. 高风险写操作要求宿主或人工审批。
5. 工具结果明确标记为外部不可信数据。
6. 秘密不进入模型上下文，凭据只在工具边界使用。
7. 对下载内容、仓库文本和 MCP 返回内容做来源与信任域记录。

当前项目能够通过 Hook 阻止调用并留下审计事件，但没有完整的污点追踪、自动 Prompt Injection 分类器或人工审批产品界面。

## 20. 工具返回内容是否也应被视为不可信输入？

应该。工具成功只表示调用完成，不表示返回文本可信，更不表示其中的指令应该执行。

文件、网页、日志、MCP Server 和子 Agent 都可能返回错误信息或恶意 Prompt。模型应把它们视为观察数据，并与用户指令、system policy 和当前任务目标区分。

当前 ToolResult 会保留工具名、Call ID 和错误状态，但不会自动给每段文本增加信任标签，也不会阻止模型服从返回内容中的指令。

更强实现需要在 Message 中保留来源、信任域和敏感级别，并让 Hook 根据后续工具调用与数据来源做策略判断。

## 21. 场景题：两个并行 Edit 中一个基于旧版本，如何避免静默覆盖？

按当前默认配置，Edit 是 sequential，因此两个 Edit 不会真正同时写入。第一个完成后，第二个会重新读取文件并做 old string 精确匹配。如果旧内容已经消失，第二个返回冲突错误，让模型重新读取。

但这还不能严格保证不会覆盖。第二个 old string 可能仍然存在，或者其他 Agent 在 Edit 的读取和写入之间修改文件；当前实现也没有版本 Hash 和跨进程锁。

要提供严格保证，我会把 Edit 改成 Compare-And-Swap：

```text
Read 文件
  → 返回 content + sha256/version

Edit(path, old_string, new_string, expected_version)
  → 按规范路径取得文件锁
  → 在锁内重新读取
  → 比较当前 Hash 与 expected_version
  → 不一致：返回 conflict ToolResult，不写文件
  → 一致：执行唯一匹配
  → 临时文件写入并原子 replace
  → 返回新版本 Hash
```

Runtime 还应按照规范化文件路径建立资源锁，使不同工具、Agent 和进程对同一文件的写操作串行化。

因此当前回答应该是：默认 sequential 和精确匹配降低了风险，但不能提供强一致性；要保证不静默覆盖，需要版本校验、文件锁和原子提交。
