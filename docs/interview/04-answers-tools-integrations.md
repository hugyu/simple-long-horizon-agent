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

我会先按故障发生的位置和结果是否确定来分类。连接建立或工具发现失败，说明这个服务还没有成功
注册，当前会话应清理已启动资源，并根据它是不是必需能力决定启动失败还是降级运行。执行期间，
服务端明确返回的业务错误直接作为错误工具结果交给模型；单次超时或传输中断则要进一步判断连接
状态，以及这次操作是否可能已经产生副作用。

当前代码已经做到：每次 MCP 调用都会形成 `ToolResult`；连接仍然存活时，普通错误和超时不会终止
整个 Agent；连接死亡时会设置 `terminate=True`，避免继续调用失效服务。如果进一步完善，我会给
每个服务维护连接状态和熔断状态：连续传输失败后暂停新调用，后台按退避策略重连并重新发现工具；
恢复成功后再从下一轮模型请求开始重新暴露。

重试不能只看错误类型，还要看调用语义。只读或带幂等键的调用可以在重连后有限重试；已经发出但
结果未知的写操作不能直接重放，要先查询外部状态或进入人工确认。备用服务也应由会话或 Workflow
按“能力”预先配置，并确认参数和结果语义兼容，不能让 Runtime 看到连接失败就随意换一个同名工具。
无论最终选择重试、降级还是停止，每个原始 Tool Call 都必须得到明确结果，保持工具协议完整。

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

我会把权限控制分成三道关。第一道在会话组装时，只给 Agent 暴露当前任务允许使用的服务和工具；
第二道在每次执行前，根据 Agent 身份、工具参数、目标资源和运行环境重新授权，高风险写操作还要
经过用户审批；第三道由 MCP Server 使用真实身份凭据校验资源权限。前两道控制 Agent 能请求什么，
最后一道才是真正的数据安全边界。

当前项目已经具备部分基础：`AgentSession` 决定绑定哪些工具，`PRE_TOOL_USE` Hook 可以在执行前
拒绝调用，MCP Server 仍负责最终授权。但当前配置中的凭据直接绑定连接，也没有统一的资源级策略
和审批状态。

如果继续完善，我会让会话签发一份有时效的能力授权，明确允许的服务、工具、操作类型和资源范围；
Runtime 在调用前使用同一份授权检查规范化后的参数。凭据由连接层保管并尽量使用最小权限，不能
进入模型上下文。拒绝、审批和放行都记录事件，便于审计。即使本地策略配置错误，服务端仍必须再次
校验，不能信任来自 Agent 的工具参数。

### 追问问题与回答

**追问：权限策略应该由 MCP Server、Client、Runtime 还是上层 Session 管理？**

会话负责签发本次任务的最小权限，连接层负责安全持有凭据，Runtime 负责逐调用检查和审批，MCP
Server 负责对真实资源做最终授权。四层职责不同，不能只依赖其中一层。

### 技术追问补充

- `MCPServerConfig` 只保存 stdio 进程参数或 HTTP URL/headers、初始化超时和调用超时，不实现业务
  授权决策。
- `AgentSession` 只收集调用者明确传入的 Toolset 和静态工具，因此工具暴露范围属于组装边界。
- `PRE_TOOL_USE` Hook 可以根据 Agent、State 和 Tool Call 阻止调用，并生成模型可见错误结果。
- 可以增加结构化能力授权，至少包含 Agent、服务、工具、允许操作、资源范围、运行环境、过期时间
  和是否需要审批；默认拒绝未明确授权的组合。
- 授权检查必须基于规范化后的参数，例如把相对文件路径解析到工作区真实路径，再判断是否越过允许
  根目录，避免只按工具名授权。
- 高风险调用在 `PRE_TOOL_USE` 阶段进入待审批状态；批准后还要绑定本次调用编号和参数摘要，防止
  模型修改参数后复用旧批准。
- 服务凭据不应作为 Tool 参数或 Message 进入模型；连接层使用短期、最小权限凭据，并负责更新和
  吊销。
- Runtime 的拒绝是本地防线，不替代 MCP Server 对租户、用户和具体资源的最终校验。
- 当前没有统一的角色权限、用户审批、资源级策略或凭据代理；扩展可以复用 Session 组装、
  `PRE_TOOL_USE` 和 `HookFiredEvent`，新增授权对象、审批状态和审计事件。

## 45. Agent 可以访问已连接 MCP Server 的全部工具吗？

### 口述主回答

按当前默认实现，可以。`MCPToolset` 会把连接时发现的全部工具包装后交给 `AgentSession`，因此
只要连接了这个服务，模型默认就能看到它的全部工具。执行前 Hook 虽然可以拒绝调用，但不能消除
模型看到无权使用工具所带来的误导和攻击面。

更合理的设计是把“服务端发现结果”和“Agent 可见工具集”分开。服务连接可以发现全部工具，但
会话根据 Agent 身份、任务类型、运行环境和能力授权生成一份最小工具快照，只把允许的工具声明
发送给模型。例如代码审查 Agent 只能看到读取和搜索工具，发布 Agent 才能看到部署工具，并且只在
生产审批通过后可调用。

隐藏工具和执行授权必须使用同一份策略：未授权工具既不出现在下一次模型请求中，即使模型伪造
工具名，Runtime 也会在执行前拒绝。不同 Agent 还应使用独立会话、凭据和工作区，避免通过共享连接
间接扩大权限。

### 追问问题与回答

**追问：如何按 Agent、任务或运行环境限制 Tool 的发现范围和调用权限？**

先根据 Agent、任务和环境从服务端工具快照中过滤出最小可见集合，再用同一授权策略在执行前复查
工具名和资源参数。不同权限范围使用独立会话和凭据。

### 技术追问补充

- `MCPConnection.agent_tools()` 和 `MCPToolset.tools()` 默认调用 `make_mcp_tools()`，包装连接时
  发现的全部工具。
- 当前 `MCPToolset` 没有 allowlist/denylist 参数，也不会按任务动态重新执行 `list_tools()`。
- 可以为 `MCPToolset` 增加工具选择函数，在连接发现完成后、包装成 `AgentTool` 前，根据当前能力
  授权筛选原始工具；筛选结果形成不可变的会话工具快照。
- 过滤条件至少可以读取 Agent、任务类别、部署环境、服务名、工具名和工具风险标签；资源范围仍需
  在调用参数确定后由执行前策略检查。
- 工具快照既用于构造下一次模型请求中的工具声明，也用于 Runtime 的分发表；不能只隐藏模型声明，
  却把未授权工具保留在可执行字典中。
- 权限变化时生成新快照并只对下一次模型请求生效；已经发出的调用仍按原快照和执行前授权处理，
  防止一个 Turn 中途出现工具集合不一致。
- 对多个 Agent，应分别建立 `AgentSession`，使用不同的工具快照、连接凭据和工作区；共享底层连接
  时也必须保留独立的授权上下文。
- 当前 Hook 只能在调用时阻止，不能从已发送给模型的工具声明中隐藏工具；因此最小权限应先在会话
  组装时完成，再由 `PRE_TOOL_USE` 做第二次校验。

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

- `default_skill_roots()` 始终加入包内 Bundled Library，并从当前目录向上扫描
  `.agents/skills` 和 `.simple_long_horizon_agent/skills`，直到 Git Root；最后加入 Home 下的
  同名目录。
- `discover_skills()` 使用 `realpath` 对相同文件去重，再按
  `repo > user > bundled` 处理同名 Skill，最终按名称排序，保证菜单稳定。
- 发现实现会打开整个 `SKILL.md` 解析 Frontmatter，但只保存名称、描述、绝对路径、Base Directory
  和 Scope，不把正文常驻内存或注入 Context。因此它是上下文渐进加载，不是严格的文件 I/O 懒读。
- 缺少 `description` 的 Skill 会被跳过；描述最长保留 1024 个字符。默认扫描深度上限为 6，
  Skill 目录被视为叶子目录，不继续把其中的 `references/` 或 `scripts/` 误识别成独立 Skill。
- `init_state_with_skills()` 先解析 `/skill-name` 与 `/no-skills`，再记录菜单和
  Mention/Preload 命中的正文，最后写入 Task Message。正文以 `<skill>` Context Message 注入，
  菜单以 System-kind Runtime Message 注入。
- `skill_body_messages()` 注入正文时只枚举最多 50 个、最多两层深度的附带文件路径，不读取这些
  文件内容；后续内容加载仍通过普通 Read Tool，脚本执行通过 Bash Tool。
- `SkillConfig.skills` 可以直接传入预发现或过滤后的列表，并优先于 Roots；这就是当前按 Agent
  限定 Skill 集合的方式，没有独立的 per-agent Skill 目录。
- `make_skill_agent()` 和 `AgentSession(skills=...)` 通过 `Agent.init_state` 接入 Skills；启用
  Skills 会同时提供 Read Tool，因为模型需要读取 `SKILL.md` 和附带资源。
- 默认初始化路径每次 Run 都会重新发现文件；显式传入固定的 `SkillMetadata` 列表时则复用该列表。
  当前没有文件 Watcher、注册表版本、Skill 锁文件或运行中的原子热切换。
- `/no-skills` 在创建初始 State 前生效，`SkillConfig(enabled=False)` 在构建 Agent 时不安装
  Skills 初始化器；二者都是运行前禁用，不是对已注入 Skill 的卸载。
- Skill 不持有连接、线程或子进程等生命周期资源，因此卸载不涉及资源关闭。需要处理的是后续可见
  Skill 集合和 Active Context，而不是像 MCP Toolset 一样调用 `close()`。
- 若增加运行中卸载，应记录一个显式的 Skill 禁用状态或事件；下一次模型请求重新生成菜单，并让
  Context View 排除对应的菜单和正文消息。原始 Event 和 Message 仍保留，避免为了卸载破坏审计
  历史。
- `bash_skills` 会在构建 Agent 时把菜单写入 System Prompt；这条路径若要卸载，当前只能重新构建
  不包含该 Skill 的 Agent，不能在原 System Prompt 上原地撤回。

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
