# 08. Production Agent Platform

本文对应 [`question-checklist.md`](question-checklist.md) 中第八部分的问题。本篇是基于当前
Runtime 边界给出的生产化设计方案，不代表仓库已经实现这些平台能力。

## 92. 如何设计支持 10 万个同时运行任务的 Agent 平台？

### 口述主回答

我会先把“同时运行”拆成 10 万个处于生命周期中的 Task，而不是 10 万个持续占用线程的进程。
大部分任务实际在等待模型、工具、审批或重试时间，只有进入 runnable 状态的下一步才进入持久
队列。Control Plane 负责鉴权、配置、提交和查询；Scheduler 负责租户公平、优先级、能力路由和
预算；Worker 领取带租约的步骤，加载状态后推进一个可提交边界。

核心数据流是：Control Plane 创建 Run 并持久化，Scheduler 根据租户和资源令牌派发，Worker 加载
Checkpoint 与后续 Event，执行一次模型或工具步骤，再原子写回事件、状态和下一次调度意图。
Postgres 保存关键状态机和租约，对象存储保存大 Checkpoint、Trace 和 Artifact，Redis 只做可重建
的队列加速、缓存和限流。模型、工具、租户和平台预算分别设置并发与速率上限，队列等待时间超过
阈值时延迟或拒绝新任务，不能只靠无限扩容消化积压。

当前项目实际只有单个 `State` 的同步 Runtime、线程池式 Eval 批量执行，以及 Backend、Store、
RunSpec 这些执行边界，没有生产 Control Plane、持久 Scheduler 或 10 万并发验证。因此这部分是
沿现有边界向生产系统演进的设计，不是我会声称已经完成的项目能力。

### 追问问题与回答

**追问：Control Plane、Scheduler、Worker、State Store、Tool Execution 和 Observability 分别如何划分？**

Control Plane 管理租户、身份、配置、Run 生命周期和查询；Scheduler 只决定哪个可运行步骤可以
获得哪类容量；Worker 推进 Runtime；State Store 保存可恢复事实；Tool Execution 在独立权限边界
执行副作用；Observability 异步消费事件，生成 Trace、指标、成本和告警，不反向成为状态真相。

**追问：如何处理吞吐、背压、限流、多租户隔离和全局预算？**

我会使用持久队列和分层公平调度，根据模型、工具、Worker 和租户配额发放容量令牌；积压超过
SLO 时延迟、降级或拒绝新任务。状态命名空间、Artifact、凭据和工具沙箱按租户隔离，并在单次
Run、租户和平台三个层次限制并发、Token、成本与墙钟时间。

### 技术追问补充

- 当前 `core.run()` 只负责单个 Agent State 的同步循环，没有租户、队列、调度或跨任务配额概念。
- Eval 的 `run_dataset()` 使用本地线程池并发调用单实例 Runner，可验证批量控制形状，但不是持久
  Scheduler，也没有背压、公平调度和自动扩缩容。
- `ContainerBackend`、`ArtifactStore` 和 `RunSpec` 已把执行位置、产物传输与任务语义分开，可作为
  生产 Worker、Store 和执行请求边界的原型。
- 多机文档中的 Worker Pool 和 Kubernetes Backend 只是设计草图，仓库没有可运行的 10 万并发
  Control Plane。
- 生产状态机至少要区分 `waiting`、`runnable`、`leased`、`running`、`blocked` 和终态；只有
  `runnable` 步骤占用调度容量，等待模型、工具、审批或退避的任务不绑定 Worker。
- Run 创建、状态变更和入队意图需要事务性衔接，例如数据库事务加 Outbox，避免“状态已提交但
  没有入队”或“消息已入队但 Run 不存在”。
- 背压信号不只看 CPU，还要看 runnable 队列深度、排队年龄、模型 RPM/TPM、工具并发、租户额度
  和成本预算；Observability 从事件异步派生，不能把指标系统当作运行事实源。

## 93. Agent Run 如何持久化，Runtime 应该有状态还是无状态？

### 口述主回答

我的判断是 Runtime 在语义上一定是有状态的，但承载它的 Worker 应尽量做成可替换的。Worker
执行一个步骤时可以把 `State` 放在内存里，但持久事实必须在进程外：领取租约后加载最近
Checkpoint，再重放其后的 Event，执行到模型响应、工具结果或 Workflow Step 结束这类安全边界后
持久化。Worker 丢失时，其他实例才能从最后一次已确认位置继续。

存储上我不会三选一。Postgres 保存 Run 状态机、Event 序号与索引、租约、预算和幂等记录；Redis
保存可重建的 ready queue、热点缓存和限流计数，不作为最终事实源；对象存储保存较大的
Checkpoint、工作区快照、Artifact 和原始 Trace。小 Event 可以直接追加到数据库，大 Trace 则从
Event 异步投影到分析存储或对象存储。

当前项目的 `State` 已经把追加式 `events` 和派生 `snapshot` 分开，`rebuild_snapshot()` 可以重建
消息与 Active Context；但 `resume()` 仍要求调用者持有同一进程内的 `State`，也没有序列化协议、
租约、未决副作用账本和跨进程 Checkpoint，所以现有实现只能证明状态模型，不等于 durable
execution。

### 追问问题与回答

**追问：Agent 状态应该存 Redis、Postgres 还是 Object Storage？**

关键状态机、租约、Event 索引和幂等记录放 Postgres；Redis 放丢失后可以重建的队列、缓存和限流
数据；大型 Checkpoint、工作区、Trace 与 Artifact 放对象存储，并由数据库保存版本和引用。

**追问：Event、Checkpoint、Artifact 和 Trace 分别适合存在哪里？**

Event 是恢复与审计需要的事实，放可追加且可事务确认的事件表或日志；Checkpoint 是快速恢复用的
派生快照，大型内容放对象存储；Artifact 是任务产品，也适合对象存储；Trace 是观察投影，进入
分析存储或对象存储，不应反过来充当可继续执行的 State。

### 技术追问补充

- 当前 `State` 包含 `task`、追加式 `events`、派生 `snapshot` 和扩展 `data`；`rebuild_snapshot()`
  可从 Event 重建消息和活跃索引。
- `Agent.resume()` 只支持调用者持有同一个进程内 State 后继续运行，没有通用 State 序列化、
  Checkpoint Manager 或跨进程恢复协议。
- Trace v5 将 Header 和 Event 写入 JSONL，大型 Provider raw 外置到相邻文件；Eval Store 保存
  `result.json`、trajectory 和其他 Artifact。
- Eval 的 `batch.json` 只持久化容器 RunHandle，用于重新轮询批次，不是 Agent 对话 State 的
  Checkpoint。
- 当前 `State.data` 是未类型化扩展字典，工具连接、进程句柄和闭包也不属于 State；生产
  Checkpoint 必须定义版本化 Schema，并把外部资源重建信息显式化，不能直接序列化整个 Python
  对象。
- 恢复流程应先校验 Run 版本和租约，再加载 Checkpoint、重放 `last_event_index` 之后的 Event，
  最后根据状态机判断是继续执行、等待外部结果还是进入 Reconcile。
- Checkpoint 至少要覆盖消息投影、Active Context、预算消耗、当前步骤、配置版本和未决操作引用；
  Event 仍是事实，Checkpoint 可以丢弃后重建，两者不能混成一份可覆盖写的状态文件。
- Postgres 与对象存储之间只能通过版本号、内容哈希和提交状态建立可验证引用；先上传对象、再用
  数据库事务发布引用，未被引用的对象由后台回收。

## 94. Scheduler 如何设计，Worker 如何水平扩容？

### 口述主回答

Scheduler 调度的单位应该是“可恢复的下一步”，不是整个长任务。任务进入 runnable 后，先按租户
做公平队列，再在租户内应用优先级和等待时间；同时根据模型、工具、镜像、区域和安全等级等能力
标签路由。Worker 使用 pull 模式领取任务，通过条件更新获得 Lease 和 Fencing Token，只在自己
具备能力且还有本地槽位时拉取。

Worker 执行期间心跳续租，到达安全边界后提交 Event、Checkpoint 引用和下一状态。基础设施异常、
尚未产生副作用的模型调用可以按策略退避重试；状态不确定的有副作用工具不能直接重放。Worker
水平扩容应根据分区队列深度、最老等待时间、每类能力的空闲槽位和下游模型或工具配额，而不是只看
CPU。下游令牌耗尽时 Scheduler 必须停止派发并向提交端施加背压。

当前项目的 `run_dataset()` 只有固定大小线程池，`max_attempts` 也只重试抛出的异常；
`submit_dataset()` 和 `reconcile_dataset()` 能持久化 RunHandle 后跨进程轮询容器，但没有
Scheduler Lease、心跳、优先级或多租户公平。因此它们提供的是调度边界的局部原型，不是生产
Scheduler。

### 追问问题与回答

**追问：如何进行任务分片、优先级调度、重试、负载均衡和容量控制？**

按租户或 Run 做稳定分片，分片内使用租户公平加优先级和 aging；Worker 按能力标签拉取带租约
任务。重试先区分基础设施、模型暂时失败和副作用不确定三类，容量则同时受 Worker 槽位与下游配额
约束。

### 技术追问补充

- 当前 `run_dataset()` 使用固定大小 `ThreadPoolExecutor`，`concurrency` 只是单进程并发上限，没有
  优先级、租户公平或能力感知调度。
- Dataset Driver 的 `max_attempts` 只重试抛出的基础设施异常；已完成但非零退出或评分失败不会
  自动重跑。
- `RemoteDockerBackend` 针对一个远程 Docker Daemon 执行 RunSpec；多机文档建议用带容量槽位的
  Worker Pool 包装多个 Backend，但该 Pool 尚未实现。
- `submit_dataset()` 每启动一个容器就持久化 RunHandle，`reconcile_dataset()` 可在新进程轮询；
  这提供长批次重入，不提供 Lease、心跳或 Worker 抢占协议。
- 调度记录至少需要 `run_id`、`step_id`、`tenant_id`、priority、capabilities、`available_at`、
  attempt、lease owner、lease expiry 和 fencing token；领取通过原子条件更新完成。
- 多个 Scheduler 实例可以按分区竞争 runnable 记录，但同一步只有持有最新 Fencing Token 的
  Worker 可以提交；普通分布式锁不足以阻止旧 Worker 在网络恢复后回写。
- 优先级队列必须配合租户并发上限、加权公平和 aging，否则高优任务或大租户会让普通任务长期
  饥饿。
- 自动扩容按能力池分别计算，例如 LLM Worker 看排队年龄和 Provider 配额，代码执行 Worker 看
  CPU、内存和沙箱启动时间；扩容上限仍受全局预算和下游服务容量约束。

## 95. Tool Execution 和 LLM Runtime 是否应该分离？

### 口述主回答

逻辑边界一定要分离，物理部署是否分离取决于工具风险。LLM Runtime 主要拥有 Run 状态、模型访问
和调度权限；Tool Worker 可能执行不可信代码、访问文件或内网、控制浏览器，甚至调用发送邮件这类
有副作用接口。两者需要的权限、资源、网络策略和故障处理不同，生产环境中的高风险工具通常应该
放到独立进程、容器或专用服务。

具体流程是 Runtime 先持久化工具执行意图，再提交包含 `run_id`、`step_id`、`tool_call_id`、
幂等键、参数引用和短期能力令牌的请求。Tool Worker 校验策略，在受限文件挂载、网络和凭据下执行，
把结构化结果或 Artifact 引用写回；Runtime 只有确认结果后才推进下一轮。这样 Tool Worker 可以按
CPU、内存、GPU 或浏览器会话扩容，LLM Runtime 则按模型并发和状态推进负载扩容，工具故障也不会
直接拖垮状态服务。

当前项目中 `AgentTool` 已经把模型可见 Schema、执行函数、Timeout 和执行模式分开，但工具仍由
`core.dispatch_tool_calls()` 在同一 Runtime 进程的线程池中调用；Bash 再启动本地子进程，MCP 通过
长连接访问外部 Server。它已经有清晰的逻辑接口，还没有远程 Tool Queue、能力令牌和 durable
result protocol。纯函数或低风险本地工具可以继续同进程运行，没有必要为了架构形式全部远程化。

### 追问问题与回答

**追问：两者在权限、资源隔离、网络访问、扩缩容和故障域上有什么差异？**

LLM Runtime 主要需要模型 API 和受控状态读写权限；Tool Worker 按工具获得文件、内网或业务接口
的最小权限，并使用更强沙箱和短期凭据。两者按不同资源指标扩容，使用独立队列、超时、熔断和故障
域，避免工具耗尽资源或被攻击后影响 Run 状态与其他租户。

### 技术追问补充

- 当前 `AgentTool.execute()` 与 Agent Runtime 位于同一进程边界，普通工具在线程池中执行；Bash
  再启动本地子进程，MCP 则通过长连接调用外部服务。
- `AgentTool` 已把模型可见声明与本地 execute、超时和执行模式分开，可作为远程 Tool Worker
  协议拆分的基础，但当前没有远程工具 RPC。
- Eval 的 LaunchSpec 支持 network_mode、cap_add、security_opt 和资源限制，可为工具沙箱提供
  环境配置，但项目明确不是完整安全沙箱。
- Runtime 目前仍直接等待工具结果后继续 Turn，没有独立工具队列、能力令牌、熔断器或工具服务
  自动扩缩容。
- 当前 `ToolExecutionStartEvent`、`ToolExecutionEndEvent` 和 `ToolResultBlock` 都通过
  `tool_call_id` 关联调用，但这个 ID 目前只用于进程内配对和 Trace，不等于跨服务幂等协议。
- 远程执行协议需要区分 `accepted`、`running`、`succeeded`、`failed` 和 `unknown`；网络超时只
  能说明 Runtime 没收到结果，不能直接证明工具没有执行。
- 能产生外部副作用的工具需要稳定幂等键、执行意图记录和 Reconcile；只读工具或确定性纯函数可以
  采用更简单的至少一次重试策略。
- 物理拆分会增加序列化、排队和网络延迟，所以低风险、低耗时、无外部权限的工具保留进程内执行
  更简单；真正的拆分依据应是权限、资源与故障域，而不是所有工具一刀切。

## 96. 长达两小时的 Agent Task 如何容错？

### 口述主回答

核心是不要把两小时任务的正确性绑定到某个 Worker 进程。Run 的状态、Event、Checkpoint 和外部
操作编号都要持久化，Worker 只持有带过期时间的执行租约。每完成一次模型响应、工具结果或
Workflow Step，就形成一个可恢复的提交边界；进程崩溃或 Worker 丢失后，新 Worker 获得更高
Fencing Token，从最近 Checkpoint 加后续 Event 恢复。

不同故障不能统一重试。模型请求在没有提交响应时通常可以有限重试，但要记录重复请求和成本；
只读、幂等工具可以退避重试。对于已经发出但结果未知的副作用工具，超时或断连不能证明它没有
执行，必须先按幂等键或外部操作 ID 做 Reconcile，确认未执行后才能重试，无法确认时进入 blocked
或人工处理。外部服务持续不可用时，任务应释放 Worker，保存等待原因和下一次重试时间，而不是
占用线程空等两小时。

当前项目实际有 `max_turns`、墙钟 `abort`、工具超时和模型层有限退避重试；Eval 还支持持久化
RunHandle 后由新进程 `reconcile_dataset()`。但 Agent `State` 仍在内存中，工具超时也不能保证底层
线程已经停止，因此项目尚不具备上述 Agent 级故障恢复能力。

### 追问问题与回答

**追问：Process Crash、Worker 丢失、模型超时和外部 Tool 不可用时如何恢复？**

Process Crash 和 Worker 丢失通过 Lease 过期发现，新 Worker 获取更高 Fencing Token 后从
Checkpoint 与 Event 恢复；模型超时若没有响应提交，可以按运行策略有限重试并记录重复成本；
外部 Tool 不可用则退避等待，若调用结果不确定，先 Reconcile 而不是直接重放。

**追问：哪些阶段可以自动重试，哪些阶段必须 Reconcile 或人工介入？**

尚未开始的步骤、明确失败的模型请求、只读工具和带幂等保证的工具可以自动重试；已经发出且可能
产生副作用、但没有确认结果的操作必须 Reconcile，外部系统既不支持幂等也不能查询时只能人工
决策。

### 技术追问补充

- 当前 `core.run()` 使用同步生成器推进，调用者停止消费或进程退出后，没有后台 Runtime 自动
  接管；`AgentEndReason` 只覆盖 done、max_turns、tool_terminate 和 abort。
- `run_goal_loop()` 可以组合墙钟截止时间、回合和 Token 预算，并在同一进程内对同一个 `State`
  调用 `resume()`；它不是跨进程 durable workflow。
- 模型层 `complete_with_retry()` 只对 429、限流和部分 500 类错误做有限指数退避。模型请求重试
  虽通常没有业务副作用，但可能重复计费并产生不同响应，因此要记录 attempt 和最终采用的响应。
- 当前 `is_retryable_llm_error()` 没有把一般 Timeout 明确列为可重试错误；生产策略需要按
  Provider 契约、请求阶段和错误类型单独判断，不能把所有超时自动重试。
- `_execute_one()` 的工具超时返回 `is_error=True`，但线程池使用 `shutdown(wait=False)`，底层线程
  可能继续运行；生产恢复不能把“Runtime 已超时”当成“外部操作一定停止”。
- Eval 的 `submit_dataset()` 每启动一个容器就持久化 RunHandle，Host 崩溃后新进程可
  `reconcile_dataset()`；这只恢复批次轮询，不能恢复容器内部 Agent 到某个 Turn。
- 恢复所需工作区、文件和大产物必须放持久卷或对象存储；只恢复 Message/Event 而丢失实际工作区，
  Agent 仍可能在错误环境上继续。
- 生产状态至少应保留 `last_committed_event`、Checkpoint 版本、预算、lease/fencing、等待原因、
  `available_at` 和未决外部操作；恢复协调器据此选择 resume、retry、reconcile、blocked 或终止。

## 97. 如何实现 Checkpoint 和 Resume？

### 口述主回答

我会把 Event Log、Checkpoint 和 Workflow 状态分成三层。Event Log 追加保存已经发生的事实；
Checkpoint 保存从某个 Event high-water mark 派生出的可快速加载状态；Workflow Engine 保存当前
节点、等待条件、重试策略和下一步控制决策。恢复时先加载最新有效 Checkpoint，再重放它之后的
Event，最后由 Workflow 状态机判断下一步，而不是简单从最后一行 Trace 继续执行。

Checkpoint 至少要保存 Run 和 Schema 版本、最后已提交 Event 位置、消息与 Active Context 投影、
预算消耗、当前 Workflow Step、Agent 与工具配置版本、工作区或 Artifact 引用，以及未决外部操作
编号。保存频率以安全边界为主：模型响应落地后、工具结果确认后、Workflow Step 完成后必须保存；
长计算可以周期保存，副作用工具则要在执行前持久化意图、执行后持久化结果。

当前项目的 `State.events` 和 `StateSnapshot` 已经体现“事实与派生视图分离”，
`rebuild_snapshot()` 能重建消息和 Active Context，`resume()` 能在同一个内存 State 上继续。
但是 Trajectory 是观察数据，Eval 的 `batch.json` 只是 RunHandle manifest，项目没有版本化
Checkpoint Schema、跨进程 State Loader 或未决操作恢复协议。

### 追问问题与回答

**追问：Checkpoint 应该保存哪些状态，多久保存一次？**

保存可决定下一步的最小完整状态，包括 Event 位置、上下文投影、预算、Workflow Step、配置版本、
工作区引用和未决操作。模型响应、工具结果和步骤结束后强制保存，长步骤按恢复成本周期保存，关键
副作用前后额外落盘。

**追问：Event Sourcing、Snapshot 和 Workflow Engine 分别承担什么职责？**

Event Sourcing 保存不可覆盖的运行事实，Snapshot 或 Checkpoint 缩短加载和重放时间，Workflow
Engine 根据状态机、计时器、重试和审批决定接下来执行什么；三者分别回答“发生了什么”“怎样快速
恢复”和“下一步是什么”。

### 技术追问补充

- 当前 `State.record_event()` 为 Event 分配递增 `index`、相对 `elapsed` 和 UUID，再更新
  `StateSnapshot`；`rebuild_snapshot()` 顺序重放 Event，但 Snapshot 只投影 Message 和
  ContextCompression，不覆盖预算、Workflow 节点和未决 I/O。
- `Agent.resume(state, followup)` 会在原 State 追加新的 task Message，再运行同一个循环；
  `State.task` 仍保留初始任务，恢复时 Agent name 必须一致。
- Trajectory v5 的 Header 和逐行 Event 支持审计、Viewer 和分析恢复，但 Reader 得到的是只读观察
  状态；继续执行还需要 Agent 配置、工具注册、ContextPolicy、工作区和凭据。
- Checkpoint 需要记录 `schema_version`、`run_id`、`checkpoint_id`、`last_event_index`、
  `last_event_uuid`、配置版本、内容哈希和创建者的 Fencing Token，加载时校验引用与事件连续性。
- Checkpoint 发布应是原子的：先写对象内容，再通过数据库条件更新发布引用和 high-water mark；
  不能让数据库指向半写入对象，也不能覆盖比自己更新的 Checkpoint。
- `ToolExecutionStartEvent` 只能证明 Runtime 开始处理调用，不能证明外部系统未执行或已完成；
  Checkpoint 必须单独保存 pending/unknown 操作，不能把缺少 End Event 自动解释为可安全重试。
- Checkpoint 太稀会增加恢复重放时间，太密会增加存储和事务压力；频率应按最大可接受恢复时间
  决定，而不是固定“每 N 秒”覆盖所有步骤。

## 98. 两个 Worker 同时尝试恢复同一个 Agent Run 时，如何避免重复执行？

### 口述主回答

我不会只用一个会过期的分布式锁，因为旧 Worker 可能在网络恢复后继续运行。Worker 领取 Run 时
通过数据库条件更新获得 Lease 和单调递增的 Fencing Token；之后续租、写 Event、发布 Checkpoint
和提交工具结果都必须同时校验 Run 当前状态、Lease owner 和 Token。即使旧 Worker 还活着，它的
过期 Token 也不能覆盖新 Worker 的结果。

这几个机制职责不同：Lease 表示临时所有权，Lock 只用于降低并发竞争，Fencing Token 拒绝旧
持有者写入，状态机和 CAS 限制合法迁移，Idempotency Key 则保护数据库之外的外部副作用。工具
调用使用由 `run_id + step_id + operation_id` 派生的稳定幂等键，Worker 重试时不能生成新键。

长时间 Tool Call 期间要由独立心跳续租。续租失败后旧 Worker 必须停止推进和提交，但已经发出的
外部调用未必能取消；新 Worker 接管后应把该操作视为 unknown，先 Reconcile 再决定采用结果还是
重试。当前项目没有 Lease、Fencing 或持久幂等账本，`tool_call_id` 只用于进程内结果配对和 Trace。

### 追问问题与回答

**追问：Lease、Lock、Fencing Token、Idempotency Key 和状态机如何配合？**

Lease 选出当前执行者，Lock 可减少抢占冲突，Fencing Token 阻止旧执行者回写，状态机加 CAS 保证
迁移合法，Idempotency Key 让外部系统识别同一次业务操作；任何一个机制都不能单独解决全部重复
执行问题。

**追问：Lease 过期与长时间 Tool Call 之间如何处理？**

Tool Call 期间持续续租；一旦续租失败，旧 Worker 停止后续写入。由于外部调用可能仍在执行，新
Worker 不能立即重放，而应先根据幂等键或操作 ID 查询状态，必要时延长等待或人工介入。

### 技术追问补充

- 领取 Run 可以使用类似 `UPDATE ... WHERE status='runnable' AND lease_expired RETURNING ...` 的
  条件写，将 `lease_owner`、`lease_expires_at` 和递增 `fencing_token` 一次提交。
- 后续状态更新使用 `WHERE run_id=? AND lease_owner=? AND fencing_token=? AND version=?`；
  version/CAS 防止同一 Token 下的并发写，Fencing 防止过期 Worker 写。
- Lease 时长必须大于正常心跳抖动，但不能覆盖整个两小时任务。续租由独立控制线程或进程负责，
  不能依赖可能阻塞在模型或 Tool Call 中的主执行线程。
- 外部 Tool Executor 也必须校验 Fencing Token 或 Idempotency Key；如果只有数据库拒绝旧 Worker，
  旧 Worker 仍可能在外部系统重复发送邮件或修改资源。
- 当前 `State.record_event()` 依赖进程内 `len(events)` 分配 index，且不是线程安全的分布式追加；
  两个 Worker 不能共享同一个持久 Event 序号而不增加数据库序列或条件提交协议。
- Eval `reconcile_dataset()` 可以在新进程读取同一 manifest 并以 `result.json` 作为完成事实，但
  没有单一 Reconciler Lease；它也只轮询已启动容器，不会解决 Agent Step 的重复领取。
- 状态机应显式包含 `leased/running/waiting_external/unknown/succeeded/failed` 等状态，使接管者
  能区分“从未执行”“正在执行”和“结果不确定”。

## 99. `send_email()` 成功后服务崩溃，如何避免重复发送？

### 口述主回答

这类问题的关键是 Outbox 只能保证发送意图不丢，不能单独保证邮件不重复。我会先在数据库事务中
写入业务状态、发送意图和稳定 Idempotency Key；Outbox Dispatcher 领取记录后调用邮件供应商，
始终携带同一个幂等键，成功后保存供应商 Message ID、响应摘要和 confirmed 状态。

最危险的窗口是供应商已经接受邮件，但 Worker 在保存成功结果前崩溃。恢复时这条操作必须标记为
unknown，先按 Idempotency Key、Message ID 或业务查询接口向供应商 Reconcile。如果供应商支持
幂等请求，用同一个 Key 重试应返回同一结果；如果只能查询，就确认未发送后再重试；如果既不支持
幂等也无法查询，就不可能严格保证 exactly once，只能选择人工确认、允许偶发重复，或者采用
“至多发送一次”并接受可能漏发。

当前项目会在工具执行前记录 `ToolExecutionStartEvent`，完成后记录 End Event 和 ToolResult，但
两者之间没有事务，`tool_call_id` 也没有传递为外部幂等键。Runtime 默认不会自动重试普通工具，
这减少了无条件重复，却仍无法解决崩溃恢复时的 unknown 状态，因此项目当前没有邮件恰好一次保证。

### 追问问题与回答

**追问：如何设计执行意图、幂等键、Outbox、结果确认和状态不确定时的 Reconcile？**

在同一事务中写业务状态、执行意图和 Outbox；为这次业务发送生成稳定幂等键；Dispatcher 使用该键
调用供应商并保存外部 Message ID；恢复发现结果未确认时先查询供应商，只有确认未执行才重试，
无法确认则进入人工处理或明确的交付策略。

### 技术追问补充

- 建议的操作记录包含 `operation_id`、`run_id`、`step_id`、tool、参数哈希、idempotency key、
  status、attempt、provider message ID、last error、created/confirmed timestamps 和 fencing token。
- 业务状态与 Outbox 必须在同一数据库事务提交，避免“Run 认为要发但消息没入队”或“邮件入队但
  Run 没记录意图”；Dispatcher 对 Outbox 使用带 Lease 的领取协议。
- 供应商响应成功后，应先把原始回执和 Message ID 写入操作账本，再推进 Agent State；Agent 最终
  看到的 ToolResult 是 confirmed 事实的投影。
- 供应商超时、连接断开或进程崩溃都可能处于 unknown，而不是普通 failed。unknown 不进入自动
  重试队列，先由 Reconciler 按稳定键和外部 ID 查询。
- 发送内容变化必须生成新的业务操作和幂等键；同一操作的重试必须复用旧键，不能把 attempt 编号
  拼进幂等键导致供应商将其视为新邮件。
- 即使内部数据库使用 exactly-once 状态迁移，跨越不受控外部系统仍需要对方支持幂等或查询；
  “恰好一次”是端到端协议属性，不是单靠 Outbox 或消息队列可以声明的能力。
- 当前项目没有事务 Outbox、外部操作账本、远程 Tool Worker 或 Reconcile 状态机；Trace Replay
  只能观察历史，不能把缺少 Tool End Event 的调用自动补执行。

## 核对依据

- [`state.py`](../../src/simple_long_horizon_agent/state.py)
- [`core.py`](../../src/simple_long_horizon_agent/core.py)
- [`tools/__init__.py`](../../src/simple_long_horizon_agent/tools/__init__.py)
- [`mcp/client.py`](../../src/simple_long_horizon_agent/mcp/client.py)
- [`llm/retry.py`](../../src/simple_long_horizon_agent/llm/retry.py)
- [`trace/run_trace.py`](../../src/simple_long_horizon_agent/trace/run_trace.py)
- [`workflow/goal_loop.py`](../../src/simple_long_horizon_agent/workflow/goal_loop.py)
- [`src/simple_long_horizon_agent/evals/dataset.py`](../../src/simple_long_horizon_agent/evals/dataset.py)
- [`src/simple_long_horizon_agent/evals/batch.py`](../../src/simple_long_horizon_agent/evals/batch.py)
- [`src/simple_long_horizon_agent/evals/runner.py`](../../src/simple_long_horizon_agent/evals/runner.py)
- [`src/simple_long_horizon_agent/evals/protocols.py`](../../src/simple_long_horizon_agent/evals/protocols.py)
- [`src/simple_long_horizon_agent/evals/backends/remote_docker.py`](../../src/simple_long_horizon_agent/evals/backends/remote_docker.py)
- [`04-agent-runtime.md`](../design/04-agent-runtime.md)
- [`06-tools-and-integrations.md`](../design/06-tools-and-integrations.md)
- [`09-trace-and-observability.md`](../design/09-trace-and-observability.md)
- [`01-product-goals-and-scope.md`](../design/01-product-goals-and-scope.md)
- [`02-system-architecture.md`](../design/02-system-architecture.md)
- [`10-evaluation-and-operations.md`](../design/10-evaluation-and-operations.md)
- [`multi-machine-eval.md`](../multi-machine-eval.md)
