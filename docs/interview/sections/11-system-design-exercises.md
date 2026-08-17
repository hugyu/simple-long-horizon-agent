下面继续以“项目负责人 / 核心开发者”的第一人称回答。这一部分属于现场系统设计题，我会先说明目标和边界，再给出架构、关键流程、故障处理和当前项目的实现程度。

# 1. 设计一个可以连续运行数小时的软件工程 Agent

我的核心目标不是让模型无限循环，而是让任务能够在有限预算下持续推进、接受外部验证、保留完整证据，并在故障后明确恢复或失败。

整体架构：

```text
Task API
  → Isolated Workspace
  → Agent Runtime
      → Provider Adapter
      → Bash / Read / Edit / Task
      → Context Policy
      → Goal Loop
  → Event / Checkpoint Store
  → Trace / Artifact Store
  → External Verifier
```

一次任务的主流程是：

1. 创建独立容器或 Worktree，记录代码版本、任务和接受标准。
2. 初始化 State，将原始任务作为受保护消息。
3. Agent 持续执行“模型决策—工具调用—观察结果”。
4. 上下文接近阈值时执行 Tool Compact、摘要或 Handoff。
5. 模型输出 final 后，外部 Goal Check 执行测试、静态检查或验证命令。
6. 验证失败时，把具体失败证据追加到同一 State，通过 `resume()` 继续。
7. 验证通过、预算耗尽、外部中止或确认阻塞时结束。

长运行必须设置多维预算：

```text
max turns
input/output Token
美元成本
wall-clock time
单工具超时
连续失败次数
子 Agent 总预算
```

一个符合项目设计的模拟故障是：

Agent 已运行两小时，模型认为修复完成，但测试仍有一个失败。Goal Loop 不把 final 当成任务完成，而是执行测试，将失败日志追加回 State。上下文已接近上限时，系统先生成 Handoff，保留当前任务、已完成修改、失败测试和下一步，再在新窗口中继续。

当前项目已经具备显式 Runtime、工具、上下文压缩、Recall、Handoff、Goal Loop、Trace 和容器 Eval；跨进程 Checkpoint、幂等副作用和通用持久化调度仍属于生产化缺口。

# 2. 设计 Agent 的上下文压缩与信息恢复系统

我会把“发生过什么”和“本轮模型看到什么”彻底分开，不能通过删除历史来控制上下文。

三层数据模型是：

```text
完整历史
  → 所有 Message 和 Event，追加保存

活跃上下文
  → active_context_indices 指向当前仍有效的消息

单轮模型输入
  → 活跃上下文再经过 Visibility 和 Provider Bridge
```

预算计算需要预留输出和误差：

```text
可用输入预算
= 模型上下文窗口
- 输出预留
- safety buffer
```

压缩策略采用分层方案：

1. 优先消费 Agent 主动提交的 Compact。
2. 规则压缩旧工具输出。
3. 调用独立 Compressor 总结旧历史。
4. 必要时生成 Handoff，在新窗口继续。

必须保护：

- 原始任务和接受标准。
- System 和 Runtime 规则。
- 已有摘要和关键 Context。
- 最近工作。
- 完整的 ToolCall/ToolResult 配对。

压缩只追加 Summary Message 和 Compression Event，不删除原消息：

```text
完整历史：[task, call, result, analysis, summary]
活跃上下文：[task, summary, recent]
```

信息恢复由 Recall 完成。摘要可以引用稳定消息索引，Agent 再按索引读取原始消息。Recall 必须限制索引数量、单条长度和总字符数，避免一次恢复重新撑爆窗口。

模拟故障：

摘要把错误码 `EACCES` 写成普通权限问题，但后续排查需要原始路径。Agent 使用 Recall 恢复对应 ToolResult，重新取得原始命令、路径和错误码。摘要错误不会覆盖原始证据。

当前项目已经实现完整历史、活跃索引、Tool Compact、模型摘要、Agent Compact、Tiered Strategy 和 Recall。生产化还需要摘要质量评测、Checkpoint 恢复和更精确的跨模型 Token 预算。

# 3. 设计支持 OpenAI 和 Anthropic 的统一模型接口

我不会让 Runtime 直接处理 OpenAI 或 Anthropic SDK 对象，而是增加项目拥有的中间协议。

整体分层：

```text
Runtime Message
  → Bridge
  → LLMMessage / LLMRequest
  → Adapter Registry
      ├─ OpenAI Chat
      ├─ OpenAI Responses
      └─ Anthropic Messages
  → StreamEvent / LLMResponse
  → AssistantMessage
```

统一请求至少包含：

```text
Provider
Messages
Tools
System Prompt
Temperature
Max Tokens
Reasoning
Timeout
Namespaced Extra
```

统一内容块包括 Text、Image、Thinking、ToolCall 和 ToolResult。工具模型侧只包含名称、描述和 JSON Schema，本地 `execute` 永远不会跨 Provider 边界。

Adapter 负责处理：

- System Prompt 的 Wire 位置。
- OpenAI Tool Call 与 Anthropic Tool Use 的映射。
- 多个工具结果的消息排列。
- Stop Reason 规范化。
- Token 和缓存使用量。
- Thinking 和推理连续性。
- 实际服务模型名称。
- Raw 请求和响应快照。

统一接口不能退化成最低公共能力。供应商独有特性先放在命名空间中，例如：

```text
anthropic.cache_breakpoint
openai_responses.reasoning_items
```

其他 Adapter 可以忽略不属于自己的命名空间，但不能改变通用消息正文。

切换 Provider 前需要 Capability Matrix，检查上下文窗口、工具调用、多模态和 Reasoning 能力。Adapter 能解决格式兼容，不能保证模型行为等价。

验证方式包括 Stub SDK Wire Test、统一 ToolCall Fixture、错误映射测试，以及同一个 Fake Agent Loop 的端到端测试。

# 4. 设计可中止、可恢复、可审计的工具调用系统

工具调用不能只是一次函数调用，而应是有身份、有授权、有持久状态的操作。

状态机可以设计为：

```text
proposed
  → authorized
  → intent_persisted
  → running
  → succeeded / failed / cancelled / in_doubt
```

每次调用保存：

```text
run_id
turn_id
tool_call_id
tool_name
normalized arguments
permission decision
idempotency key
start/end time
updates
result/error
side-effect reference
```

中止分成两层：

- 协作式 Abort：Runtime、MCP 和子 Agent 周期检查。
- 强制中止：宿主终止进程组、容器或远程任务。

超时必须产生结构化 ToolResult，不能让 ToolCall 没有对应结果。正在执行的线程无法可靠强杀时，应隔离到独立进程或容器。

恢复依靠写前 Intent 和幂等账本：

```text
持久化 Intent
  → 执行副作用
  → 持久化 Result
```

模拟故障：

Edit 已经写入文件，但进程在记录 ToolResult 前崩溃。恢复时发现操作状态为 pending，系统读取文件当前 Hash，并根据 expected version 判断修改是否已经完成。如果已完成，就补记原结果；如果状态无法确定，则标记 in_doubt，不能直接再次编辑。

审计从 ToolExecution Event、Hook Decision 和 Result 派生。敏感参数应脱敏，但调用身份、策略版本和审批结果必须保留。

当前项目已支持 Call ID、超时、Abort、Hook、结构化结果和 Trace；跨进程幂等账本、强制取消和 durable approval 仍未实现。

# 5. 设计支持主 Agent 和子 Agent 的任务委派系统

动态委派适合使用 Task Tool，因为从父模型视角看，委派与调用其他工具具有相同决策形状。

调用协议：

```text
subagent_type
task
optional context
optional budget
```

主流程：

```text
Parent State
  → Task Tool
  → 受控 Registry 选择 Child Agent
  → 创建独立 Child State
  → 运行同一 Agent Runtime
  → Child Result + Child Events
  → Parent ToolResult
```

父子 Agent 必须使用独立 State，避免消息、压缩索引和生命周期事件互相污染。父 Agent 只看到有界的子任务结果；子事件进入 `details`，由 Trace 合并为父 Tool Span 下的子运行。

委派系统需要限制：

- 最大递归深度。
- 单父任务最大子 Agent 数。
- 子 Agent 回合、Token、时间和成本。
- 最大并发。
- 可用工具和凭据。
- 可选择的 `subagent_type` 注册表。

Abort 从父任务传给子任务。父任务取消时，不再接受新的委派，并中止未完成子运行。

子 Agent 失败时，ToolResult 应区分：

```text
completed
max_turns
aborted
blocked
provider_error
tool_error
```

父 Agent 可以根据失败类型继续处理，但不能把子 Agent 最后一段文本误认为成功。

固定阶段流程仍应使用 Workflow，而不是强迫模型每次通过 Task Tool 决定。例如 Planner 一定先于 Executor 时，代码编排更可控。

# 6. 设计 Agent Trace 与成本统计平台

平台应以 Event Stream 为唯一运行事实，Trace、Span、ModelTurn 和 Cost 都是派生视图。

数据流：

```text
State Event Stream
  → Incremental Writer
  → Trajectory JSONL
      ├─ Span Builder
      ├─ ModelTurn Builder
      ├─ Cost Aggregator
      ├─ Trace Viewer
      └─ Eval / Training Export
```

JSONL 第一行保存 Schema 和运行身份，后续一行一条 Event。这样可以实时追加、部分恢复，并在进程崩溃时保留已经完整写入的前缀。

核心 Span 包括：

- Agent Run。
- Turn。
- Model Call。
- Tool Call。
- Compression。
- Child Agent。
- Workflow Step。

ModelTurn 必须保存当时真正发送给模型的输入，而不是从最终 Transcript 反推，因为历史可能已经被压缩。

成本根据每次 ModelResponseEvent 的实际模型和 TokenUsage 计算：

```text
input tokens
output tokens
cache read
cache write
reasoning usage
estimated USD
```

未知模型价格或缺失 Usage 必须标记为 unknown，不能显示成零成本。子 Agent 成本需要归入父 Run，同时保留按 Agent、模型和步骤拆分。

Provider Raw 数据单独放入受限 Sidecar，避免主 Trace 平方增长。生产环境还要增加脱敏、访问控制、保留期限和 Schema Migration。

模拟故障：

Trace 后台 Writer 写盘失败，但 Agent 仍在运行。Writer 通过错误回调和 Metrics 告警，不把观察层故障伪装成 Agent 行为失败。生产环境可以先写本地 WAL，再异步上传中央存储。

# 7. 设计可复现的容器化 Agent Benchmark 平台

我会把任务语义、执行位置和产物传输设计成三个正交协议：Suite、Backend 和 ArtifactStore。

```text
Suite
  → 任务、镜像、工作目录、公开输入和私有评分数据

Backend
  → LocalProcess / LocalDocker / RemoteDocker

ArtifactStore
  → LocalDir / HostHttp / 未来对象存储
```

Suite 分为两半：

- Host Half：加载数据集、决定镜像、隔离私有数据、运行官方评分器。
- Container Half：构造 Agent 任务、运行 Agent、从工作区提取结果。

单实例流程：

```text
Host 写 instance.json 和 eval.json
  → Backend 启动环境
  → Container build_task
  → Agent run
  → extract_result
  → 写 result.json 和 trajectory.jsonl
  → Host 使用官方 Harness 评分
```

可复现 Manifest 至少记录：

```text
代码 Commit
数据集和 Split
Instance ID
镜像和 Workdir
模型和 Reasoning
Prompt和 Agent Flavor
工具和预算
Provider API Kind
评分器版本
重试和异常
```

Gold、隐藏测试和官方答案必须走私有通道，不能进入 Agent Context。Oracle 只能用于验证布线，不能与模型结果混合。

Dataset 并发时每个实例拥有独立目录和容器。只重试基础设施异常；已完成但未通过的任务不能自动重跑。

收集预测时必须保留全部 expected IDs。失败实例写空结果并留在分母，重复或意外 ID 直接拒绝。

本地开发先使用 LocalProcess + Fake Provider，正式评测再切换 Docker。长运行使用 Submit/Reconcile，并在每次成功启动后更新 Batch Manifest，避免 Host 崩溃留下未知任务。

# 8. 设计多 Agent 并行执行和结果合并机制

首先判断是 Ensemble 还是 Map。Ensemble 是多个 Agent 解决同一问题；Map 是不同 Agent 处理不同子任务。

架构：

```text
Orchestrator
  → Worker 1 → State 1
  → Worker 2 → State 2
  → Worker 3 → State 3
  → Aggregator / External Verifier
  → Final Result
```

每个 Worker 必须拥有：

- 独立 State。
- 独立预算。
- 独立 Trace。
- 独立可写工作区或 Worktree。

共享同一个可写目录会产生文件冲突，即使 State 已经隔离。代码任务可以让每个 Worker 产生 Patch，再由 Aggregator 比较和合并。

结果顺序按照 Worker 声明顺序保存，而不是线程完成顺序，保证测试和审计稳定。

Aggregator 接收：

```text
原始任务
Worker 身份
每个输出
可选证据和评分
```

模型 Aggregator 只能做语义综合，不能替代事实验证。代码、计算和环境任务应优先使用测试、官方评分器或确定性 Check。

部分失败时不能丢弃失败 Worker。WorkflowResult 应同时保存成功、失败、超时和中止步骤。策略可以要求 quorum、至少一个成功，或所有分片都成功。

预算应统一计算所有 Worker 和 Aggregator 的 Token。外部 Abort 传递给所有 Worker，但取消属于协作式取消，生产环境还需要进程或容器级终止。

当前实现使用 ThreadPoolExecutor 和独立 State，适合单机实验；分布式场景需要 Durable Queue、Worker Lease 和远程 Result Store。

# 9. 设计 Agent 长期 Memory 系统

长期 Memory 不应保存全部历史，而应保存少量高价值、可验证、跨任务仍有用的经验。

接口保持简单：

```text
initial(ctx) → 启动时注入 Message
tools(ctx)   → 提供 Memory 工具
finish(ctx)  → 结束时沉淀经验
```

当前文件型布局是：

```text
memory-root/
  namespace/
    MEMORY.md
    memory_summary.md
    INDEX.md
    runs/
      run-id/
        summary.md
        evidence
        artifacts/
```

运行开始时只注入 Memory 路径、摘要和使用策略，不把全部经验塞入 Prompt。模型通过普通 Read/Bash 工具主动读取。

运行结束时保存有界证据，由 Distiller 同时读取旧 `MEMORY.md` 和新证据，返回完整新版手册。提交采用跨进程单写者锁和临时文件原子替换。

写入规则：

- 不保存密钥和隐私数据。
- 不保存大段原始日志。
- 不保存当前任务临时状态。
- 用户纠正和工具证据优先于模型自述。
- 文件路径、配置和仓库状态等旧事实必须重新验证。
- “没有值得保存的新经验”是合法结果。

Memory 需要容量限制、运行数量限制、Namespace 限制和最旧证据裁剪。多租户环境必须完全隔离 Namespace 和加密密钥。

模拟故障：

Distiller 返回空内容或试图删除所有既有经验。系统拒绝这次重写，保留原 `MEMORY.md`，写入错误标记，但不让 Memory 失败影响已经完成的主任务。

如果未来需要向量检索，应实现另一种 Memory 类型。不能悄悄把 FilesystemMemory 改成每轮自动检索，否则信息出现的原因和 Trace 会变得不可解释。

# 10. 设计带权限审批的 MCP 工具平台

MCP Server 同时控制工具声明、参数 Schema 和返回内容，因此必须把它看成外部不可信能力，而不是普通可信函数。

整体架构：

```text
MCP Registry
  → Server Identity / Trust Level
  → Connection Session
  → Tool Discovery
  → Namespaced AgentTool
  → Policy Engine
  → Approval Service
  → MCP Invocation
  → Result Validation
  → Audit Event
```

工具发现后使用 Server 前缀生成模型可见名称，例如：

```text
github_create_issue
filesystem_read_file
```

调用前先规范化参数并评估策略：

```text
tenant
run
server
tool
arguments
data classification
risk level
current capability
```

权限分级可以是：

| 风险 | 示例 | 策略 |
| --- | --- | --- |
| 低 | 读取公开数据 | 自动允许 |
| 中 | 读取租户内部数据 | RBAC + Scope |
| 高 | 修改、发送、发布 | 人工审批 |
| 禁止 | 导出秘密、越权访问 | 强制拒绝 |

高风险操作进入持久化 Approval Request。审批通过后生成短期 Capability Token，绑定具体 Server、Tool、参数摘要、租户、Run 和有效期。修改参数后必须重新审批。

MCP Server 应运行在隔离容器中，只获得最小环境变量、文件挂载和网络权限。凭据由 Credential Broker 按调用注入，不能让 Server 继承主进程全部 Secrets。

MCP 返回内容同样不可信，需要：

- 大小和内容类型限制。
- 明确来源标签。
- 二进制和不支持类型的显式降级。
- Prompt Injection 风险提示。
- 敏感信息检测和脱敏。
- 不允许返回文本自动提升权限。

超时或 Abort 后不能自动重放有副作用的 MCP 调用。连接死亡时应停止继续调用；重新连接由 Session 层负责。

当前项目已经支持 stdio/HTTP MCP、长连接、名称前缀、超时、Abort、Hook 阻止和审计事件，但还没有集中 Registry、持久化审批、Capability Token、Credential Broker 和完整 MCP 沙箱。
