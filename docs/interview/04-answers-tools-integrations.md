# 04. Tools 与集成

本文对应 [`question-checklist.md`](question-checklist.md) 中第四部分的问题。每道题包含可直接
口述的主回答；存在清单子问题时，保留追问原文和简短回答；最后记录整组问题对应的仓库实现。

## 36. 模型一次返回多个工具调用时，运行时如何并发执行？

### 口述主回答

运行时会先为这一批调用记录开始事件，再经过工具执行前 Hook 检查。允许并行的调用进入线程池，
默认最多 8 个并发；如果本轮有任意工具声明必须顺序执行，整批调用就只使用一个工作线程。

运行时会等待本轮所有工具结束后再进入下一次模型调用。执行完成顺序可以不同，但最终返回给模型
的结果会按模型原始调用顺序重新组装，保证每次运行的上下文顺序稳定。

### 追问问题与回答

**追问：一个 Tool 执行 3 秒、另一个执行 30 秒时，整轮如何等待和记录进度？**

快工具完成后可以先记录结束事件，但当前 Turn 仍要等待慢工具完成；全部结果写回后才进入下一轮。

**追问：当前实现使用什么并发模型？**

当前是同步 Agent Loop 加线程池，工具调用在线程中并发执行，不是完整异步 Runtime。

### 技术追问补充

- `dispatch_tool_calls()` 先按模型原始顺序记录全部 `ToolExecutionStartEvent`，再执行
  `PRE_TOOL_USE` Hook。
- 如果本批有效调用中存在 `execution_mode="sequential"`，线程数为 1；否则使用
  `min(max_concurrency, 调用数)`，默认最大并发为 8。
- Runtime 使用 `ThreadPoolExecutor` 和 `as_completed()` 获取完成结果，因此快工具可以先产生
  Update/End Event，但线程池退出和 Turn 结束仍等待全部调用。
- `on_update` 目前先写入每个调用的内存缓冲，通常在该工具完成后才转成
  `ToolExecutionUpdateEvent`，并非网络式实时流。

## 37. Timeout 是单工具还是整个 Turn？

### 口述主回答

当前核心超时是单工具级别。某个工具超时会形成错误结果，其他并行工具继续执行；等整批调用结束
后，成功和失败结果一起返回模型，由模型决定下一步。

需要说明的是，通用线程超时只能停止等待，不能强制杀死仍在运行的 Python 线程。真正的终止能力
必须由具体工具实现，例如 Bash 使用子进程超时，MCP 会取消提交到异步会话的调用。

### 追问问题与回答

**追问：某个 Tool timeout 后，其他 Tool 是否继续执行？**

会。单个工具超时只生成该调用的错误结果，其他并行调用继续运行。

**追问：部分成功和部分失败如何反馈给模型？**

每个 Tool Result 独立携带 `is_error`，成功和失败结果会一起写入同一批结果消息。

**追问：Tool timeout 后，如何真正终止仍在运行的 Tool，而不只是停止等待结果？**

通用线程工具无法被核心强制终止，必须由工具自身支持取消。Bash 使用子进程超时，MCP 会取消后台
协程，但远端工作是否停止仍取决于外部实现。

**追问：用户取消整个 Agent Run 时，cancellation 如何从 Runtime 向 Tool 和外部进程传播？**

Runtime 把 abort 函数传给每个工具；工具需要主动检查并向底层操作传播。忽略 abort 的阻塞工具
不能被核心强制停止。

### 技术追问补充

- `AgentTool.timeout_seconds` 是单调用配置；`_execute_one()` 使用
  `future.result(timeout=...)`，超时后返回 `ToolResult(is_error=True)` 并关闭线程池时不等待。
- 通用线程任务超时后可能仍在后台运行，Python Runtime 没有安全的强杀线程机制；当前核心也没有
  独立 per-turn timeout。
- Bash 内部使用 `subprocess.run(..., timeout=...)`，超时会形成 `timed_out=True` 的结构化结果；
  abort 只在命令前后检查，不会在运行中轮询。
- `MCPConnection.call()` 每 0.1 秒检查 abort 和剩余时间，触发后取消提交到后台事件循环的 Future；
  服务端是否真正停止仍不由本地 Runtime 保证。

## 38. 两个工具同时修改同一个文件或共享资源时怎么办？

### 口述主回答

当前 Runtime 不会自动分析两个调用是否修改同一资源。不能安全并发的工具应声明为顺序执行，
这样只要本轮出现这类工具，整批调用就会串行运行。

更细的文件锁、数据库锁或工作区隔离由工具实现或 Workflow 负责。对于多个 Agent 修改仓库的
场景，更稳妥的是使用独立工作区或显式合并，而不是依赖线程调度碰运气。

### 追问问题与回答

**追问：Runtime 如何避免竞态条件和不可预测的副作用？**

当前只提供顺序执行这一粗粒度保护，不会自动分析文件路径或共享资源；更细的锁和隔离由工具或
Workflow 实现。

### 技术追问补充

- `AgentTool.execution_mode` 只有 `"parallel"` 和 `"sequential"` 两种值，默认是 parallel。
- 本批只要有一个有效工具声明 sequential，`dispatch_tool_calls()` 就把整批线程数设为 1。
- Edit 工具默认声明 sequential，Read 和 Bash 默认可并行；调用者可以在构造工具时覆盖模式。
- 当前没有资源键、文件路径冲突分析或锁管理器；事务、幂等、文件锁和独立 worktree 属于工具或
  Workflow 边界。

## 39. 如何判断两个工具调用可以安全并行？

### 口述主回答

模型可以一次提出多个调用，但它不拥有最终并发决定权。项目由工具注册时的执行模式声明是否允许
并行，Runtime 读取这个属性执行调度。

工具作者最了解副作用和资源约束，因此负责声明安全性；Runtime 负责落实并发上限和顺序模式；
Workflow 负责更高层的任务隔离和依赖关系。

### 追问问题与回答

**追问：并行意图由模型表达，还是由 Runtime 根据 Tool 属性决定？**

模型只能一次提出多个调用，最终是否并行由 Runtime 读取工具注册时的执行模式决定。

**追问：Tool、Runtime 和 Workflow 分别承担什么责任？**

Tool 作者声明本地并发安全性，Runtime 执行调度规则，Workflow 负责跨步骤依赖、工作区和资源隔离。

### 技术追问补充

- 模型响应只提供 `ToolCallBlock` 的 ID、名称和参数，不包含并发策略字段。
- Runtime 根据工具名从本轮固定的 `tool_by_name` 表查找 `AgentTool.execution_mode`。
- 默认模式是 parallel；当前采用“任一 sequential，整批串行”的保守规则。
- Runtime 不读取参数推断读写集合，也不理解 Workflow 依赖图，因此无法动态判定共享资源冲突。

## 40. 多个工具结果返回模型时，如何保证顺序稳定？

### 口述主回答

最终结果使用模型原始工具调用顺序，而不是线程完成顺序。运行时先用调用编号保存每个执行结果，
全部结束后再遍历原始调用列表，组装成一条工具结果消息。

这样慢工具和快工具的调度差异不会改变模型下一轮看到的结果排列，同时每个结果仍通过调用编号
与原请求准确配对。

### 追问问题与回答

**追问：使用提交顺序、完成顺序，还是原始 Tool Call 顺序？**

模型可见结果使用原始 Tool Call 顺序；完成事件可以保留真实完成顺序。

### 技术追问补充

- Runtime 用 `results[tool_call.id]` 保存每个完成结果，不依赖 Future 返回位置。
- 所有 Future 完成后，结果包遍历原始 `tool_calls` 列表构造 `ToolResultBlock`，因此模型可见顺序
  固定。
- 每个 `ToolResultBlock.tool_call_id` 和 `sidecar["details"][tool_call.id]` 使用同一个调用编号，
  确保内容与本地详情配对。
- ToolExecutionEnd Event 按 `as_completed()` 的实际完成顺序记录，Trace 因而仍能反映运行过程。

## 41. MCP 在架构中位于哪一层？

### 口述主回答

MCP 位于外部能力接入层。MCP Client 负责连接服务和调用远端工具，再把发现到的工具包装成普通
`AgentTool`；核心 Runtime 看不到 MCP 特殊类型，只按统一工具协议调度。

连接生命周期由 `AgentSession` 和 `MCPToolset` 管理。Session 进入时建立连接并发现工具，运行
期间保持长会话，退出时关闭连接和本地服务进程，核心 Agent Loop 不负责资源创建和销毁。

### 追问问题与回答

**追问：MCP Client、Agent Runtime、Tool Protocol 和资源生命周期之间是什么关系？**

MCP Client 连接并调用服务，发现的能力被转换成统一 AgentTool；Runtime 只按普通工具协议调度；
Session 和 Toolset 负责在完整运行期间打开和关闭连接。

### 技术追问补充

- `MCPConnection` 在专属后台线程中运行 asyncio event loop，并长期持有一个已初始化
  `ClientSession`；同步 `AgentTool.execute()` 通过 `run_coroutine_threadsafe()` 提交调用。
- `MCPToolset.__enter__()` 调用 `connect_mcp()`，`tools()` 只在连接打开期间有效，
  `__exit__()` 负责关闭连接。
- `AgentSession` 使用 `ExitStack` 统一打开 Toolset、收集 AgentTool 并构建 Agent；退出时按所有权
  关闭全部资源。
- 核心 `core.run()` 不导入 MCP 模块，也不拥有 MCP 连接生命周期。

## 42. MCP Server 如何发现和注册工具？

### 口述主回答

连接建立后，客户端调用一次工具发现接口，读取服务端返回的名称、描述和输入 Schema，再把每个
工具包装成统一的 `AgentTool`。默认会在模型可见名称前加服务名，避免多个服务出现同名工具。

当前工具列表是在连接初始化时确定的，不会运行中自动刷新。服务端 Schema 动态变化时需要重新
连接；项目只做基础结构归一化，没有实现完整的 Schema 兼容性协商。

### 追问问题与回答

**追问：MCP Tool Schema 如何转换成统一 Tool Protocol 和模型 Tool Calling Schema？**

服务端名称、描述和 `inputSchema` 被转换成 AgentTool 的名称、描述和 JSON Schema；之后再通过
现有模型桥接转换成供应商工具声明。

**追问：Schema 不兼容或动态变化时如何处理？**

缺少基本对象字段时会补成最小 Schema；更复杂的不兼容由模型服务或调用阶段报错。工具列表变化
需要重新连接，当前不会自动热刷新。

### 技术追问补充

- `MCPConnection.open()` 在握手后调用一次 `session.list_tools()`，把返回值保存为不可变工具元组。
- `_normalize_parameters()` 保留服务端字典，并补充 `type="object"` 和空 `properties`；空或非字典
  Schema 会降级成最小对象 Schema。
- 默认模型可见名称是 `<server>_<tool>`，调用服务端时仍使用原始工具名；重复模型可见名称直接
  抛出 `MCPError`。
- 当前 MCP 集成只包装 Tools；Resources、Prompts 和 Sampling 没有接入 Agent Runtime。

## 43. MCP Server 不可用、连接中断或执行失败时怎么办？

### 口述主回答

连接或工具发现失败会让 Session 初始化失败，并清理已经启动的线程、连接和本地子进程。运行中
单次工具失败或超时会转换成错误工具结果，让模型有机会修正参数或选择其他能力。

如果只是单次超时且连接仍然存活，运行不会终止；如果连接已经死亡，工具结果会要求 Runtime
停止，避免模型在失效连接上反复重试。项目没有自动选择备用 MCP Server，fallback 需要由模型
或上层 Workflow 明确设计。

### 追问问题与回答

**追问：MCP Tool timeout 如何处理？**

Client 轮询调用截止时间，超时后取消本地 Future 并返回错误结果；连接仍存活时允许模型后续重试。

**追问：错误应该返回模型、中断 Run，还是触发 fallback？**

普通服务端错误和单次超时返回模型；连接已经死亡时中断 Run；fallback 由模型或 Workflow 显式
选择，Runtime 不自动切换服务。

### 技术追问补充

- `MCPConnection.call()` 每 0.1 秒检查 abort 和剩余时间；触发后调用 `future.cancel()` 并抛出
  `MCPError`。
- MCP 包装工具捕获错误并返回 `ToolResult(is_error=True)`；`terminate` 的值取决于
  `connection.is_connected`。
- 服务端返回 `isError=true` 时，其内容仍转换成模型可见块，连接正常时不会终止 Run。
- AgentTool 还配置 `call_timeout + 5` 秒的 Runtime 单工具超时，作为内层 MCP 超时之外的后备。

## 44. MCP 工具的权限如何控制？

### 口述主回答

权限需要分层负责。MCP Server 应负责真实资源的认证和授权；Client 配置负责服务地址、凭据和
连接范围；上层 Session 决定给某个 Agent 绑定哪些服务和工具；Runtime 只提供通用的工具执行前
Hook，可以在调用前阻止高风险操作。

当前项目没有完整的 MCP 权限系统，也不是安全沙箱。仅有 JSON Schema 或工具描述不能替代服务端
授权、宿主审批和运行环境隔离。

### 追问问题与回答

**追问：权限策略应该由 MCP Server、Client、Runtime 还是上层 Session 管理？**

Server 负责真实资源认证授权，Client 负责连接凭据和目标，Session 决定向 Agent 暴露哪些工具，
Runtime 只执行通用调用前策略；任何一层都不能单独替代其他层。

### 技术追问补充

- `MCPServerConfig` 只保存 stdio 进程参数或 HTTP URL/headers、初始化超时和调用超时，不实现业务
  授权决策。
- `AgentSession` 只收集调用者明确传入的 Toolset 和静态工具，因此工具暴露范围属于组装边界。
- `PRE_TOOL_USE` Hook 可以根据 Agent、State 和 Tool Call 阻止调用，并生成模型可见错误结果。
- 当前没有统一 RBAC、用户审批、资源级策略或凭据代理；MCP Server 必须自行校验真实访问权限。

## 45. Agent 可以访问已连接 MCP Server 的全部工具吗？

### 口述主回答

按当前默认实现，可以。一个 `MCPToolset` 会把连接时发现的全部工具包装并加入 Agent 工具表。
因此访问范围应在 Session 组装阶段控制，而不是连接后默认认为所有工具都安全。

目前没有内建的工具白名单或按任务动态过滤。调用者可以只连接批准的服务、手动选择部分工具，
或者为不同 Agent 创建不同 Session；Hook 可以阻止调用，但不能让被阻止的工具从模型工具列表
中消失。

### 追问问题与回答

**追问：如何按 Agent、任务或运行环境限制 Tool 的发现范围和调用权限？**

在组装阶段只连接批准的 Server，并从发现结果中选择允许的工具后绑定给目标 Agent；不同任务和
环境使用独立 Session。执行阶段还可以用 Hook 做二次阻止。

### 技术追问补充

- `MCPConnection.agent_tools()` 和 `MCPToolset.tools()` 默认调用 `make_mcp_tools()`，包装连接时
  发现的全部工具。
- 当前 `MCPToolset` 没有 allowlist/denylist 参数，也不会按任务动态重新执行 `list_tools()`。
- 调用者可以过滤返回的 AgentTool 列表，或直接对选中的原始 MCP Tool 调用
  `mcp_tool_to_agent_tool()` 后再构建 Agent。
- Hook 只能在调用时阻止，不能从已发送给模型的工具声明中隐藏该工具；最小权限应优先在组装前
  完成。

## 核对依据

- [`core.py`](../../src/simple_long_horizon_agent/core.py)
- [`tools/__init__.py`](../../src/simple_long_horizon_agent/tools/__init__.py)
- [`tools/bash.py`](../../src/simple_long_horizon_agent/tools/bash.py)
- [`mcp/client.py`](../../src/simple_long_horizon_agent/mcp/client.py)
- [`mcp/config.py`](../../src/simple_long_horizon_agent/mcp/config.py)
- [`agents/toolsets.py`](../../src/simple_long_horizon_agent/agents/toolsets.py)
- [`agents/starter.py`](../../src/simple_long_horizon_agent/agents/starter.py)
- [`06-tools-and-integrations.md`](../design/06-tools-and-integrations.md)
- [`test_core.py`](../../tests/unit/test_core.py)
- [`test_mcp.py`](../../tests/unit/test_mcp.py)
- [`test_hooks.py`](../../tests/unit/test_hooks.py)
