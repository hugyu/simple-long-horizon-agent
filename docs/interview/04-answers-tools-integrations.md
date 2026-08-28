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

当前核心的 `AgentTool.timeout_seconds` 是单工具超时，没有独立的 Turn Timeout；Goal Loop 可以
通过 wall-clock deadline 组合 `abort`，但它也不是核心 Turn 的统一截止时间。单个工具超时后会
返回错误结果，其他并行工具继续，整批结果收齐后再一起反馈给模型。

如果进一步完善，我会设计成三级 Deadline：Run Deadline 限制整次任务，Turn Deadline 限制一次
“模型调用加工具批次”，Tool Deadline 限制单次调用。每个工具实际拿到的截止时间是自身配置、
Turn 剩余时间和 Run 剩余时间中的最小值。单个 Tool 到期只取消该调用；Turn 到期则取消本批所有
未完成调用，并为每个调用补齐超时 Tool Result，保证 Tool Call/Result 配对；Run 到期则在记录
已有结果和停止原因后终止 Agent。

这里的关键边界是 Deadline 只能决定“不再等待和接收结果”，真正停止工作必须由执行器配合：
Bash 要终止子进程组，异步或 MCP 调用要取消任务并尽量向远端传播，普通 Python 线程无法安全强杀，
只能隔离它并禁止晚到结果再写回 State。

### 追问问题与回答

**追问：某个 Tool timeout 后，其他 Tool 是否继续执行？**

单工具 Deadline 到期时，其他调用继续；只有 Turn 或 Run Deadline 到期，才取消整批未完成调用。

**追问：部分成功和部分失败如何反馈给模型？**

每个调用都必须形成 Tool Result：已完成调用保留真实结果，超时或取消的调用返回
`is_error=True` 和明确原因，最终仍按原 Tool Call 顺序组成同一结果包。

**追问：Tool timeout 后，如何真正终止仍在运行的 Tool，而不只是停止等待结果？**

Runtime 向工具传递取消信号，但真正终止由执行器负责：子进程终止进程组，异步任务执行
`cancel()`，远端工具调用取消协议。普通线程无法安全强杀，只能停止接收结果，并阻止超时后的
晚到结果修改 State。

**追问：用户取消整个 Agent Run 时，cancellation 如何从 Runtime 向 Tool 和外部进程传播？**

Run 级取消先触发共享 cancellation signal，Turn 停止提交新工具，所有未完成调用再把信号传播到
各自执行器。Runtime 等待一个有界 grace period，之后记录取消结果并结束；外部操作若无法确认
是否停止，应标记为状态不确定，不能直接当作“未执行”重试。

### 技术追问补充

- `AgentTool.timeout_seconds` 是单调用配置；`_execute_one()` 使用
  `future.result(timeout=...)`，超时后返回 `ToolResult(is_error=True)` 并关闭线程池时不等待。
- 当前核心没有独立 per-turn timeout；Goal Loop 的 `wall_clock_seconds` 通过单调时钟组成
  `abort`，可以限制更外层运行，但工具是否及时响应仍取决于具体实现。
- Bash 内部使用 `subprocess.run(..., timeout=...)`，超时会形成 `timed_out=True` 的结构化结果；
  abort 只在命令前后检查，不会在运行中轮询。
- `MCPConnection.call()` 每 0.1 秒检查 abort 和剩余时间，触发后取消提交到后台事件循环的 Future；
  服务端是否真正停止仍不由本地 Runtime 保证。
- 扩展时应使用绝对单调 Deadline，按
  `effective_tool_deadline = min(tool_deadline, turn_deadline, run_deadline)` 计算剩余时间，避免
  多层相对 timeout 累加后突破总预算。
- Turn 到期时仍要为每个未完成 Tool Call 生成结构完整的错误结果，再按原调用顺序写入结果包；
  若 Run 已同时到期，则记录这些结果后直接结束，不再发起下一次模型调用。
- 取消信号需要做到每个 Tool Call 独立，避免单工具 timeout 错误地取消 sibling；Run/Turn 取消
  才广播到整批调用。
- 对不能协作取消的线程任务，Runtime 应丢弃晚到结果，并防止其通过回调继续写 State；有副作用且
  结果未知的外部调用还需要幂等键或 Reconcile，而不能盲目重试。

## 38. 两个工具同时修改同一个文件或共享资源时怎么办？

### 口述主回答

当前 Runtime 只有粗粒度保护：工具可以声明 `sequential`，本轮只要出现一个顺序工具，整批调用
就串行执行。这样能保护 Edit，但并不知道两个 Bash 命令是否会写同一个文件，也会把本来互不冲突
的操作一起串行化。

如果进一步完善，我会让工具在执行前根据参数声明资源访问，例如
`read:/repo/a.py`、`write:/repo/a.py` 或 `write:db:orders/42`。Runtime 对资源键做规范化后构建
冲突关系：读读可以并行，只要同一资源上出现写写或读写就按模型调用顺序串行；不相交的调用仍然
并发。执行时按稳定顺序获取资源锁，写入前再校验文件版本或内容哈希，并使用临时文件加原子替换，
避免锁外修改造成 lost update。

对于无法提前判断资源的 Bash 或外部副作用工具，我会保守地把它声明为 workspace 级写操作，或者
放进隔离环境。多个 Agent 修改仓库时继续使用独立 worktree，最后显式比较和合并，而不是让多个
Agent 共享锁直接修改同一工作区。这样 Runtime 负责单次工具批次的一致性，Workflow 负责跨 Agent
隔离和合并。

### 追问问题与回答

**追问：Runtime 如何避免竞态条件和不可预测的副作用？**

让工具声明规范化的读写资源，Runtime 根据冲突图分批调度并按固定顺序加锁；写入前再做版本检查。
无法声明资源的副作用工具保守串行或隔离执行。

### 技术追问补充

- `AgentTool.execution_mode` 只有 `"parallel"` 和 `"sequential"` 两种值，默认是 parallel。
- 本批只要有一个有效工具声明 sequential，`dispatch_tool_calls()` 就把整批线程数设为 1。
- Edit 工具默认声明 sequential，Read 和 Bash 默认可并行；调用者可以在构造工具时覆盖模式。
- 可以为 `AgentTool` 增加根据参数计算访问集合的函数，返回规范化 `ResourceAccess(key, mode)`；
  文件键应解析为工作区内的绝对真实路径，防止相对路径和符号链接绕过冲突判断。
- 调度规则是同一 key 上 read/read 不冲突，read/write 和 write/write 冲突；Runtime 可按原始
  Tool Call 顺序构建依赖批次，不冲突节点仍受现有 `max_concurrency` 限制并行执行。
- 多资源调用必须按排序后的资源键统一获取锁，避免两个调用以相反顺序拿锁形成死锁；取消或异常时
  在 `finally` 中释放。
- 文件写入还应携带读取时的版本或哈希，提交前做 compare-and-swap 检查，并通过同目录临时文件
  原子替换；冲突时返回结构化错误让模型重新读取，而不是覆盖新内容。
- Bash 很难可靠静态解析命令副作用，默认可声明为 `write:workspace`，只有显式只读或运行在隔离
  worktree/container 中时才放宽并发。
- 当前 PDR 类 Workflow 已支持为并行 Agent 准备独立 git worktree；资源锁适合一个 Runtime 内的
  工具批次，跨 Agent 修改仍优先采用工作区隔离和显式合并。
- 当前没有资源键、冲突图或锁管理器；可直接复用现有 Tool 注册信息、原调用顺序、线程池和稳定
  Tool Result 配对，新增资源声明、调度与提交校验即可。

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

#### MCP 连接生命周期

当前设计是“一次 `AgentSession` 持有一条长期 MCP 连接”，不是每次 Tool Call 都重新连接。

连接建立和工具发现的顺序是：

```text
进入 AgentSession
    ↓
MCPToolset.__enter__()
    ↓
MCPConnection.open()
    ↓
建立传输并完成 initialize 握手
    ↓
调用 list_tools()
    ↓
保存工具清单并包装成 AgentTool
    ↓
开始运行 Agent
```

连接成功后会立即获取工具。`list_tools()` 只在初始化阶段调用一次，结果保存在
`MCPConnection.tools`；后续多个模型回合和多个 Tool Call 都复用同一条连接，不会为每次调用
重新连接或重新发现工具。当前实现没有运行中的动态工具刷新。

#### 正常关闭

连接通常持续到 `AgentSession` 退出：

```python
with agent_session(..., mcp_servers=[config]) as session:
    state, events = session.run(task)
    for event in events:
        pass
# 退出 with 后关闭 MCP 连接
```

退出时，`AgentSession` 通过 `ExitStack` 调用 `MCPToolset.__exit__()`，再调用
`MCPConnection.close()`，关闭 `ClientSession`、后台事件循环和传输资源。对于 stdio MCP Server，
还会结束由连接启动的服务进程。调用方也可以显式执行 `connection.close()`。

#### 连接失败和异常断开

- **建立失败**：握手、`list_tools()` 或初始化超时会使 `open()` 清理已经启动的线程和传输资源，
  然后抛出 `MCPError`。
- **运行中断开**：服务进程退出或传输失效后，`is_connected` 变为 `False`；后续调用返回错误
  `ToolResult` 并设置 `terminate=True`，避免继续调用失效服务。
- **自动恢复**：当前实现不会自动重连；连接失效后的恢复由上层重新建立 Session 或连接处理。

## 42. MCP Server 如何发现和注册工具？

### 口述主回答

项目里的链路分成工具发现、格式转换、注册和调用路由四步。MCP 连接完成握手后，
`MCPConnection.open()` 调用 `list_tools()` 获取服务端工具列表，再把每个工具的名称、描述和输入
参数定义转换成统一的 `AgentTool`。模型看到的工具名默认加服务名前缀，真正调用服务端时仍使用
原始名称，因此多个服务的同名工具不会混淆。

注册前会检查参数结构和工具名称冲突，再由当前会话把 MCP 工具与本地工具一起绑定给 Agent。注册
完成后，核心 Runtime 会像执行普通工具一样处理调用、超时、结果和运行记录，不需要针对 MCP
维护另一套 Agent Loop。

当前实现只在建立连接时发现一次工具。如果要支持服务端动态更新，我会先在旁路获取并校验一份
新的工具快照，确认名称、参数结构、权限和模型接口都兼容后，再一次性替换注册表。后续模型请求
使用新版本，已经发出的工具调用继续使用原版本；刷新失败则保留最后一个可用版本，不能让一次
更新中的半成品影响正在执行的任务。

### 追问问题与回答

**追问：MCP Tool Schema 如何转换成统一 Tool Protocol 和模型 Tool Calling Schema？**

服务端的 `name`、`description` 和 `inputSchema` 先转换为 `AgentTool`；模型桥接层再把统一参数
定义转换成不同模型接口要求的工具声明。带前缀的名称用于本地分发，原始名称用于调用 MCP Server。

**追问：Schema 不兼容或动态变化时如何处理？**

当前只补齐最小对象结构，复杂的不兼容可能在模型请求或工具调用阶段暴露。扩展后应先校验新的
工具快照，不兼容就拒绝更新并保留旧版本；通过校验的新版本只对后续模型请求生效。

### 技术追问补充

- `MCPConnection.open()` 在握手后调用一次 `session.list_tools()`，把返回值保存为不可变工具元组。
- `_normalize_parameters()` 保留服务端字典，并补充 `type="object"` 和空 `properties`；空或非字典
  Schema 会降级成最小对象 Schema。
- 默认模型可见名称是 `<server>_<tool>`，调用服务端时仍使用原始工具名；重复模型可见名称直接
  抛出 `MCPError`。
- `mcp_tool_to_agent_tool()` 返回绑定具体 `MCPConnection` 和原始工具名的执行闭包，并配置 MCP
  调用超时及 Runtime 后备超时；工具结果继续转换为统一内容块和 `ToolResult`。
- 动态刷新可以维护递增的注册表版本和不可变工具快照。一次模型请求、模型返回的工具调用及对应
  结果必须固定使用同一版本，不能在一个 Turn 中途切换。
- 候选快照应校验模型可见名称、JSON Schema 基本结构、模型服务支持的参数范围和当前会话的工具
  暴露策略；任一工具不合法时整批拒绝，继续使用最后一个可用版本。
- 新增或修改工具只在下一次模型请求中重新声明；删除工具后不再向模型暴露，但旧版本已经产生的
  在途调用仍由原快照完成，或者返回明确的不可用错误。
- 无论刷新由服务端通知、定期轮询还是显式管理操作触发，都复用“发现、校验、比较、原子替换”
  流程，并记录注册表变更事件。
- 当前 MCP 集成只包装 Tools；Resources、Prompts 和 Sampling 没有接入 Agent Runtime。

## 43. MCP Server 不可用、连接中断或执行失败时怎么办？

### 口述主回答

遇到 MCP Server 不可用、连接中断或工具执行失败，我会先区分连接阶段和调用阶段。连接阶段由
`MCPConnection` 完成握手和工具发现；如果初始化或 `list_tools()` 失败，就在 `MCPToolset` 进入
会话时直接报错，并由 `AgentSession` 清理已经打开的资源，避免把未完成注册的工具交给模型。

调用阶段，服务端明确返回的 `isError=true` 属于业务错误，包装层把它转换成
`ToolResult(is_error=True, terminate=False)` 返回模型，让模型根据错误信息修正参数或调整计划。
调用超时或被取消时，客户端取消本地等待并返回错误结果，但不会把“没有收到响应”当成“远端一定
没有执行”。工具捕获异常后会检查连接状态：连接仍然存活时，错误结果不会终止整个 Run；连接已经
死亡时，结果设置 `terminate=True`，停止继续调用这个失效服务。

Runtime 不会因为连接失败就静默替换成另一个同名工具；备用能力必须在 Session 或 Workflow 组装
时明确注册，并保持参数、结果和错误语义一致。这样既保证模型能看到可修正的工具错误，也避免在
连接失效或写操作结果未知时产生重复副作用，同时每个 Tool Call 都能得到明确的 `ToolResult`。

### 追问问题与回答

**追问：MCP Tool timeout 如何处理？**

当前客户端会轮询截止时间，超时后取消本地等待并返回错误结果。扩展后只有只读或幂等调用可以有限
重试；副作用调用超时后应先确认外部状态，不能把“没有收到结果”理解成“没有执行”。

**追问：错误应该返回模型、中断 Run，还是触发 fallback？**

参数或业务错误返回模型修正；暂时性连接错误进入熔断和重连；必需服务长期不可用时停止任务；可选
能力可以降级。备用服务只在上层预先声明能力兼容时切换。

### 技术追问补充

- `MCPConnection.call()` 每 0.1 秒检查 abort 和剩余时间；触发后调用 `future.cancel()` 并抛出
  `MCPError`。
- MCP 包装工具捕获错误并返回 `ToolResult(is_error=True)`；`terminate` 的值取决于
  `connection.is_connected`。
- 服务端返回 `isError=true` 时，其内容仍转换成模型可见块，连接正常时不会终止 Run。
- `AgentTool` 还配置 `call_timeout + 5` 秒的 Runtime 单工具超时，作为 MCP 调用截止时间之外的
  后备保护。
- 扩展时可以把服务状态表示为可用、熔断、重连中和关闭。只有传输或连接故障累计到阈值才打开
  熔断；服务端明确返回的业务错误不应计入连接健康度。
- 熔断期间停止提交新调用，并为已经发出的调用补齐明确错误结果；后台重连成功后重新执行工具发现
  和快照校验，新工具列表只对下一次模型请求生效。
- 重试策略应读取工具的只读、幂等或有副作用声明。只读调用可以退避重试；写操作必须携带幂等键，
  或在超时后先执行状态核对。
- 备用服务应按逻辑能力注册适配器，明确参数转换、结果转换和错误语义；切换决定属于 Session 或
  Workflow，核心 Runtime 只执行已经选定的具体工具。
- 需要记录连接失败、熔断、重连、重试和备用服务选择事件，便于区分模型参数错误、服务业务错误、
  基础设施故障和状态不确定的外部操作。

## 44. MCP 工具的权限如何控制？

### 口述主回答

我会把 MCP 的访问分成四层：服务端发现、Agent 可见、Runtime 执行和资源授权。`MCPConnection`
连接服务后通过 `list_tools()` 获取工具；`MCPToolset` 把发现到的工具包装成 `AgentTool`；
`AgentSession` 再把显式配置的 Toolset 和本地工具一起注册给 Agent。因此按默认路径，连接一个
MCP Server 后，模型会看到这个服务发现到的工具，工具名前缀用于避免不同服务之间的名称冲突。

权限管理的入口在 Session 的工具组装，而不是让模型自己决定。Session 根据 Agent、任务和环境确定
可见工具集合，这份集合同时用于生成模型的工具声明和 Runtime 的分发表，避免工具虽然对模型隐藏，
却仍然可以被后台调用。进入执行阶段后，`PRE_TOOL_USE` 再根据 Agent、工具名、参数和目标资源做
一次检查，拒绝就返回错误 `ToolResult`。Runtime 的允许也不等于最终拥有资源权限，MCP Server 仍然
使用真实凭据校验用户、租户和具体资源。不同 Agent 按任务隔离 Session、凭据和工作区，不能因为
共享一个连接就共享全部能力。这样 Session 管工具可见性，Hook 和 Runtime 管逐调用授权，MCP Server
管最终资源权限。

我会按这几个依据筛选：
1. Agent 角色或 Workflow 节点：例如代码审查节点只声明 read、search，发布节点才声明 deploy。
2. 任务类型：把用户任务先归类为需要读取、修改、查询外部系统还是发布等能力，再映射到对应工具。
3. 运行环境和授权：开发环境可以使用测试工具，生产环境还要叠加用户权限、审批状态和资源范围。
4. 工具清单和参数 Schema：从 MCP 的 list_tools() 得到候选工具，再按名称、参数能力和风险标签过滤。

### 追问问题与回答

**追问：权限策略应该由 MCP Server、Client、Runtime 还是上层 Session 管理？**

会话负责确定本次 Run 的最小权限，Runtime 负责逐调用检查，连接层负责安全传递凭据，MCP Server
负责对真实资源做最终授权。四层职责不同，不能只依赖其中一层。

### 技术追问补充

#### 设计示例：发布 GitHub Release

假设 Agent 的任务是“把 `org/app` 仓库发布为 `v1.2.0`”，一次调用会经过四层控制：

1. **Session / Workflow 决定本次 Run 的能力范围**

   创建 Agent 时，根据任务类型配置允许使用的工具，例如 `get_release` 和 `create_release`；
   资源范围限定为 `org/app`，`create_release` 需要审批。模型只会收到经过筛选的工具声明。

2. **Runtime 检查每一次具体调用**

   模型提出调用：

   ```json
   {
     "name": "github_create_release",
     "arguments": {
       "repo": "org/app",
       "tag": "v1.2.0"
     }
   }
   ```

   Runtime 在执行前检查工具名、仓库、Tag 和审批状态。如果模型把仓库改成 `org/other-app`，
   Runtime 直接拒绝，并返回错误 `ToolResult`。

3. **MCP Client / 连接层负责携带凭据**

   连接层取得本次会话的短期 Token，并把它放进 MCP HTTP 请求的认证 Header。它负责连接和传输，
   不根据工具名称推断 Agent 是否有业务权限。

4. **MCP Server 对真实资源做最终授权**

   GitHub MCP Server 收到请求后，根据 Token 对应的身份、组织、仓库和操作权限再次校验。即使
   Runtime 判断允许，如果 Token 没有 `org/app` 的发布权限，Server 仍然拒绝请求。

完整链路是：

```text
Session / Workflow
  决定本次 Run 能使用哪些能力
        ↓
Runtime
  检查每一次具体调用和参数
        ↓
MCP Client / 连接层
  携带凭据发送 MCP 请求
        ↓
MCP Server
  对真实资源执行最终 ACL 校验
```

“模型不能签发或扩大自己的权限”是指：模型可以提出调用，但不能通过修改参数、伪造工具名，
或在提示词中声明“已经获得批准”，获得原本没有的仓库、文件或发布权限。

#### Token 的获取

Session 不把用户名和密码交给 Agent，而是传递一个凭据引用，例如 `github-release-bot`。连接层
或 Credential Broker 根据这个引用，向 OAuth 服务申请短期 access token。具体方式取决于部署场景：

| 场景 | 获取方式 |
| --- | --- |
| 用户授权 | Authorization Code + PKCE |
| 后台服务 | Client Credentials |
| 云环境 | Workload Identity 或服务账号换取 Token |

Token 通常包含签发方、受众、主体、权限范围和过期时间：

```text
iss   = https://auth.example.com
aud   = github-mcp
sub   = release-bot
scope = repo:read repo:release
exp   = 过期时间
```

#### Token 如何进入 MCP 请求

连接层取得 Token 后，在 HTTP 请求中加入：

```http
Authorization: Bearer eyJ...
```

同一个 HTTP 客户端负责发送 `initialize`、`list_tools` 和 `tools/call` 请求。Token 不能进入模型的
Tool 参数、Prompt 或普通消息。

#### MCP Server 如何校验

Server 收到请求后，先验证 Token 本身：

- 签名是否正确，或通过 introspection 确认 Token 有效；
- `iss` 是否为信任的签发方；
- `aud` 是否确实面向当前 MCP Server；
- `exp` 是否尚未过期；
- `scope` 是否包含当前操作所需的权限。

以 `create_release` 为例，Server 还要结合请求参数做资源级校验：

```text
用户身份：release-bot
操作：    create_release
仓库：    org/app
所需范围：repo:release
```

最后再通过仓库 ACL 判断 `release-bot` 是否真的可以在 `org/app` 创建 Release。Token 校验通过，
不代表它自动拥有所有仓库和所有操作的权限。

## 45. Agent 可以访问已连接 MCP Server 的全部工具吗？

### 口述主回答

默认情况下，Agent 可以看到已连接 MCP Server 暴露的全部工具，但“模型能看到”不等于“调用一定
会被允许”。

`MCPConnection` 建立连接后通过 `list_tools()` 获取工具清单，`MCPToolset` 会把这些工具包装成
`AgentTool`，再由 `AgentSession` 和本地工具一起注册给 Agent，所以默认模型看到的是该服务的完整
工具集合，名称通过服务名前缀隔离。

权限控制不能只依赖执行前 Hook。Hook 可以在 `PRE_TOOL_USE` 阶段根据 Agent、任务、环境和调用参数
拒绝执行，但这时模型已经看到了工具声明。更严格的做法是在 Session 组装工具时，就从服务端发现
快照中过滤出最小可见集合，并用同一份集合构造模型声明和 Runtime 分发表；这样未授权工具既不会
暴露给模型，即使模型伪造调用，也会在执行前被拒绝。

不同 Agent 应使用独立的 Session、工具快照和授权上下文；MCP 连接、凭据和工作区也按任务边界隔离，
不能因为共享一个 Server 连接，就默认共享全部能力。

### 追问问题与回答

**追问：如何按 Agent、任务或运行环境限制 Tool 的发现范围和调用权限？**

先根据 Agent、任务和环境从服务端工具快照中过滤出最小可见集合，再用同一授权策略在执行前复查
工具名和资源参数。不同权限范围使用独立会话和凭据。

## 45A. Skills 是渐进式加载的吗？如何注入和管理？

### 口述主回答

是，我把 Skills 做成了三层渐进式加载。第一层在任务开始时只给模型 Skill 的名称、描述和路径，
让模型先判断当前任务需要哪个 Skill；第二层在 Skill 被明确指定或模型选中后，再加载对应的
`SKILL.md`；第三层是其中引用的脚本、模板和参考资料，只在真正需要时继续读取。

注入时，我会先把可用 Skill 的菜单放进上下文。用户显式指定的 Skill 和配置为 Preload 的 Skill，
正文会在第一次模型调用前进入上下文；其他 Skill 由模型根据描述自行选择并读取。这样 Skill 数量
增加时，不需要把所有说明都塞进 Prompt。

管理上分为项目级、用户级和内置 Skill。同名时项目级优先，用户级其次，内置版本最后；不同 Agent
也可以只暴露经过筛选的 Skill 集合，并在任务启动时关闭本次运行的 Skills。这个设计主要解决的
是可复用能力不断增加后，如何同时控制 Context 成本和 Skill 的覆盖范围。

### 追问问题与回答

**追问：Skill 的元数据、正文和附带资源分别在什么时候加载？**

发现阶段解析 `name` 和 `description`，只把元数据和路径保存在 `SkillMetadata` 中；正文通常在
模型选中后由 Read Tool 读取。显式注入正文时只附带浅层文件清单，脚本、模板和 References 的内容
仍按任务需要单独读取。

**追问：显式提及、预加载和模型按需读取有什么区别？**

任务中的 `/skill-name` 和配置里的 `preload` 都会让正文在第一次模型调用前进入 Context；区别是
前者由用户任务触发，后者由 Agent 配置决定。没有显式加载时，模型根据菜单中的描述选择 Skill，
再用 Read Tool 按需读取，Context 成本最低。

**追问：Repo、User 和 Bundled Skill 如何发现、去重、覆盖和禁用？**

默认从项目目录、用户目录和包内目录扫描 `SKILL.md`，先按真实路径去重，再按名称执行
Repo、User、Bundled 的覆盖优先级。可以通过指定 Roots 或 Skills 列表限制可见范围，也可以用
`SkillConfig(enabled=False)` 或 `/no-skills` 禁用。

**追问：当前是否支持运行中卸载 Skill？如果需要，应该如何实现？**

当前没有运行中卸载。Skill 被加载后已经成为本次对话上下文的一部分，直接删除文件或注册信息不能
撤回模型已经看到的内容。现有做法是在运行前禁用或过滤；如果要支持运行中卸载，我会停止继续暴露
该 Skill，并从后续 Active Context 中排除它的菜单和正文，但完整 History 仍保留这次变化用于审计。

### 技术追问补充

目前代码实现了 Repo、User、Bundled 三层 Skill 的发现、去重和覆盖。

#### 1. 扫描哪些目录

`default_skill_roots(cwd)` 会生成三类扫描根目录：

- **Bundled**：包内固定的 `skills/library` 目录。
- **Repo**：从当前 `cwd` 开始向父目录逐级查找，直到 Git 根目录；每一级检查：
  - `.agents/skills`
  - `.simple_long_horizon_agent/skills`
- **User**：当前操作系统用户主目录下的：
  - `~/.agents/skills`
  - `~/.simple_long_horizon_agent/skills`

这里的 `~` 表示当前用户的 Home Directory，不是项目目录。在本机上当前用户是 `hgy`，所以：

```text
~                                   = /Users/hgy
~/.agents/skills                    = /Users/hgy/.agents/skills
~/.simple_long_horizon_agent/skills = /Users/hgy/.simple_long_horizon_agent/skills
```

项目目录则是另一层路径，例如：

```text
项目目录：     /Users/hgy/Desktop/simple-long-horizon-agent
项目级 Skill： /Users/hgy/Desktop/simple-long-horizon-agent/.agents/skills
用户级 Skill： /Users/hgy/.agents/skills
```

换一台机器或换一个操作系统用户，`~` 会自动解析成对应用户自己的 Home Directory；代码通过
`os.path.expanduser("~")` 获取它，不会把 `/Users/hgy` 写死。

#### 2. 如何发现 Skill

每个扫描根目录会递归查找 `SKILL.md`。发现时解析文件 frontmatter 中的 `name` 和 `description`：

```yaml
---
name: pdf
description: Read and generate PDF files
---
```

随后构造 `SkillMetadata`，保存：

- `name`
- `description`
- `path_to_skill_md`
- `base_dir`
- `scope`

`SkillMetadata` 保存的是元数据和文件路径，不保存完整正文；正文需要通过 `read_body()` 读取。

#### 3. 同名 Skill 如何覆盖

代码使用 `SCOPE_RANK` 定义优先级：

```text
repo    = 0
user    = 1
bundled = 2
```

因此同名 Skill 的覆盖顺序是：

```text
Repo > User > Bundled
```

例如三层都存在名为 `pdf` 的 Skill，最终只保留 Repo 版本。这个选择由 scope 优先级决定，
不依赖扫描目录的先后顺序。

#### 4. 如何去重和排序

代码先用 `realpath` 对同一个实际文件去重，避免符号链接造成重复发现；同名文件再按 scope 优先级
选择版本。最终结果按 Skill 名称排序，保证注入模型的菜单顺序稳定。

#### 5. 正文和附属资源何时加载

任务开始时，`init_state_with_skills()` 会：

1. 发现 Skill 并生成包含名称、描述和路径的菜单。
2. 对用户在任务中显式提到的 Skill，或配置在 `preload` 中的 Skill，提前读取 `SKILL.md`。
3. 对其他 Skill 保留菜单信息，模型需要时再通过 Read Tool 读取正文。
4. 对脚本、模板和 `references` 只生成浅层文件清单，具体内容按任务需要再读取。

#### 6. 不同 Agent 如何限制 Skill 范围

`init_state_with_skills()` 和 `run_with_skills()` 支持直接传入筛选后的 `skills` 列表：

```python
run_with_skills(
    agent,
    task,
    skills=[pdf_skill, git_skill],
)
```

传入 `skills` 后，运行时优先使用这份列表，不再按默认目录重新发现。这样不同 Agent 可以使用
不同的 Skill 集合。

当前实现可以概括为：

```text
扫描 Repo / User / Bundled 目录
        ↓
读取 SKILL.md 元数据
        ↓
按真实路径去重
        ↓
按 Repo > User > Bundled 解决同名冲突
        ↓
按名称排序生成 Skill 菜单
        ↓
显式提及或 preload 的 Skill 加载正文
        ↓
其他 Skill 由模型按需通过 Read Tool 读取
```

## 核对依据

- [`core.py`](../../src/simple_long_horizon_agent/core.py)
- [`tools/__init__.py`](../../src/simple_long_horizon_agent/tools/__init__.py)
- [`tools/bash.py`](../../src/simple_long_horizon_agent/tools/bash.py)
- [`mcp/client.py`](../../src/simple_long_horizon_agent/mcp/client.py)
- [`mcp/config.py`](../../src/simple_long_horizon_agent/mcp/config.py)
- [`agents/toolsets.py`](../../src/simple_long_horizon_agent/agents/toolsets.py)
- [`agents/starter.py`](../../src/simple_long_horizon_agent/agents/starter.py)
- [`skills/directives.py`](../../src/simple_long_horizon_agent/skills/directives.py)
- [`skills/discovery.py`](../../src/simple_long_horizon_agent/skills/discovery.py)
- [`skills/prompt.py`](../../src/simple_long_horizon_agent/skills/prompt.py)
- [`skills/runtime.py`](../../src/simple_long_horizon_agent/skills/runtime.py)
- [`06-tools-and-integrations.md`](../design/06-tools-and-integrations.md)
- [`test_core.py`](../../tests/unit/test_core.py)
- [`test_mcp.py`](../../tests/unit/test_mcp.py)
- [`test_hooks.py`](../../tests/unit/test_hooks.py)
- [`test_skills.py`](../../tests/unit/test_skills.py)
