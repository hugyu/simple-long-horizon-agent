# 08. Production Agent Platform

本文对应 [`question-checklist.md`](question-checklist.md) 中第八部分的问题。本篇是基于当前
Runtime 边界给出的生产化设计方案，不代表仓库已经实现这些平台能力。

## 92. 设计一个支持 10 万个同时运行 Agent Task 的 Production Agent Platform。

### 口述主回答

我会先确认“10 万同时运行”的口径。如果是 10 万个处于生命周期中的 Run，可以使用事件驱动架构；
如果要求 10 万个步骤同时占用模型连接、容器或浏览器，就必须同时具备对应的供应商配额、机器容量
和成本预算，不能仅靠平台架构解决。容量规划要看活跃 Run 数、可运行步骤到达率、各类步骤平均耗时
和排队时延目标，所需并发大致由“到达率乘平均服务时间”决定。

平台的核心是把每个 Run 做成持久状态机，而不是长期绑定一个进程。控制面完成身份校验、配置冻结
和 Run 创建；调度器只派发当前可运行的下一步；无状态 Worker 领取带租约和防陈旧令牌的步骤，加载
最近 Checkpoint 与后续 Event，推进到下一个安全提交边界。任务等待模型、工具、审批或退避时，
状态写回存储并释放 Worker；外部结果到达后再把 Run 唤醒。

一次状态推进必须形成可恢复事务：Worker 提交新增 Event、预算变化、Checkpoint 引用和下一状态，
同时写入后续派发意图；模型请求和工具执行分别经过受配额控制的执行层。工具有副作用时，执行前
保存意图和幂等键，结果不确定时进入核对状态，不能直接重放。只有持有最新租约令牌的 Worker 才能
提交，避免旧 Worker 恢复网络后覆盖新结果。

吞吐控制放在入口和调度两端：入口根据租户额度和预计成本决定接收、延迟或拒绝；调度器按租户公平、
优先级和等待时间分配模型、工具与沙箱容量令牌，下游配额耗尽就停止派发。状态、凭据、Artifact 和
执行环境按租户隔离；Trace、指标和成本由事件异步生成，不参与状态裁决。当前仓库只提供单 State
Runtime、批量 Eval 线程池和 Backend、Store、`RunSpec` 等边界，因此这是基于现有抽象的生产化
扩展方案，不是已经验证过的 10 万并发能力。

### 追问问题与回答

**追问：Control Plane、Scheduler、Worker、State Store、Tool Execution 和 Observability 分别如何划分？**

控制面管理身份、配置和 Run 生命周期；调度器决定哪个可运行步骤获得哪类容量；Worker 只推进一次
状态转换；状态存储保存 Event、Checkpoint、租约和预算；工具执行层隔离副作用；观测层异步生成
Trace、指标、成本和告警，不反向修改运行状态。

**追问：如何处理吞吐、背压、限流、多租户隔离和全局预算？**

入口先做准入控制，调度时再按租户公平、优先级和等待时间发放模型、工具和沙箱容量令牌。积压或
排队年龄超过目标时延迟、降级或拒绝新任务。并发、Token、成本和墙钟预算同时在 Run、租户和平台
三个层次扣减，状态、凭据和执行环境按租户隔离。

### 技术追问补充

- 当前 `core.run()` 只负责单个 Agent State 的同步循环，没有租户、队列、调度或跨任务配额概念。
- Eval 的 `run_dataset()` 使用本地线程池并发调用单实例 Runner，可验证批量控制形状，但不是持久
  Scheduler，也没有背压、公平调度和自动扩缩容。
- `ContainerBackend`、`ArtifactStore` 和 `RunSpec` 已把执行位置、产物传输与任务语义分开，可作为
  生产 Worker、Store 和执行请求边界的原型。
- 多机文档中的 Worker Pool 和 Kubernetes Backend 只是设计草图，仓库没有可运行的 10 万并发
  Control Plane。
- 容量模型至少记录活跃 Run、可运行步骤到达率、分类型服务时间、队列年龄和完成时延。模型调用、
  沙箱工具和人工审批分别建容量池，不能用一个全局 Worker 数量代表全部吞吐。
- 生产状态机应区分 runnable、leased、waiting_model、waiting_tool、waiting_approval、
  waiting_backoff、reconciling 和终态；只有短暂推进状态的步骤占用 Worker。
- 调度单位是一次可恢复状态转换。Worker 领取时通过原子条件更新获得 lease owner、过期时间和递增
  fencing token；提交时同时校验 Run 版本与令牌。
- Run 创建、状态提交和下一次入队意图需要数据库事务与 Outbox 衔接，避免状态已更新但没有再次
  调度，或队列中出现不存在的 Run。队列是可重建投影，不是最终事实源。
- 模型执行层按供应商、模型、区域和租户维护并发、RPM、TPM 和成本令牌；工具执行层按 CPU、内存、
  GPU、浏览器会话和安全等级分池。调度必须同时获得 Run 预算与下游容量。
- 多租户调度先保证租户并发上限和加权公平，再应用任务优先级与等待时间提升，避免单个大租户或高
  优任务长期饿死其他队列。
- 状态机和小 Event 适合事务数据库；大型 Checkpoint、Trace、工作区和 Artifact 放对象存储；
  缓存和限流数据可以放可重建的内存存储，但不能成为恢复依据。
- 高风险工具执行前记录意图、幂等键和参数哈希；超时或 Worker 丢失后若结果未知，进入
  reconciling，确认外部状态后才能采用结果或重试。
- 观测系统异步消费 Event，并对普通成功任务采样详细 Trace，但租约变更、副作用意图、预算和终态
  等审计事件不能采样丢失；指标系统不可反向驱动状态机事实。
- 验证 10 万规模需要分层压测：状态存储写入、调度领取、队列恢复、下游配额背压和 Worker 故障
  接管分别测试，再做长时间混合负载与故障注入；当前仓库没有这些结果。

## 93. Agent Run 如何持久化，Runtime 应该有状态还是无状态？

### 口述主回答

我的结论是：Agent Run 在语义上必须有持久状态，但执行它的 Worker 不应该有粘性。Run 的事实状态
保存在进程外，任何 Worker 都可以领取租约、恢复状态并推进下一步；Worker 内存中的 Python 对象只
是本次执行缓存，不能成为恢复依据。

持久化我会分成三层。Event Journal 是事实源，记录模型响应、工具结果、预算和状态转换；Run Record
保存当前状态、事件版本、租约、配置版本和未决操作；Checkpoint 是从 Event 派生的加速快照，保存
消息投影、Active Context、当前工作流位置和预算余额。Worker 恢复时先取得最新防陈旧令牌，再加载
Checkpoint、重放后续 Event，得到与崩溃前一致的可执行状态。

提交边界必须围绕外部操作设计。模型响应收到后要先持久化，再根据它启动工具；有副作用的工具在
执行前先保存意图和幂等键，执行后保存确认结果。恢复时如果只看到意图，没有确认结果，不能简单重跑，
而要根据操作类型选择等待、查询外部系统或进入状态核对。也就是说，持久化的不是 Python 调用栈，
而是足以确定下一步安全动作的状态机。

当前项目已经有追加式 `State.events`、派生 `snapshot` 和 `rebuild_snapshot()`，说明了 Event 与
投影的边界；但 `resume()` 仍依赖同进程 State，也没有版本化序列化、租约和未决操作账本。因此现有
实现是持久 Runtime 的数据模型基础，还不是跨进程可恢复执行。

### 追问问题与回答

**追问：Agent 状态应该存 Redis、Postgres 还是 Object Storage？**

需要事务和条件更新的 Run 状态、Event 序号、租约、预算和幂等记录放关系数据库；Redis 只放可
重建队列、缓存和限流；大型 Checkpoint、工作区和 Artifact 放对象存储，并由数据库保存内容哈希和
已提交引用。

**追问：Event、Checkpoint、Artifact 和 Trace 分别适合存在哪里？**

Event 放可追加且能与 Run 版本一起事务提交的事件表；Checkpoint 是可丢弃重建的派生快照，大对象
放对象存储；Artifact 是任务产品，也放对象存储；Trace 是面向观察的投影，进入分析或对象存储，
不能反过来作为恢复状态。

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
- Run Record 至少包含 `run_id`、状态、最新 Event 序号、Checkpoint 版本、配置和工具注册表版本、
  预算、租约、防陈旧令牌以及当前未决操作。
- 每次提交使用乐观版本和防陈旧令牌做条件写，并在同一事务中追加 Event、更新 Run Record 和写入
  下一次调度的 Outbox；旧 Worker 或重复提交必须失败。
- 恢复流程先领取新租约，再读取 Run Record，加载 `checkpoint_event_index` 对应快照，重放之后的
  Event，最后根据未决操作状态决定继续执行、等待结果、重试或核对外部状态。
- Checkpoint 至少覆盖消息投影、Active Context、当前 Agent 或 Workflow 位置、预算、计划版本、
  工具注册表版本和未决操作引用；连接、线程、文件句柄和密钥不进入快照，只保存重建所需引用。
- Checkpoint 必须带 Schema 版本、内容哈希和它覆盖的最后 Event 序号。恢复后可抽样从头重放并与
  Checkpoint 比较，发现投影代码或迁移错误。
- 对象存储提交采用“先上传临时对象，再在数据库事务中发布不可变引用”；没有被提交记录引用的对象
  由后台回收，数据库不能指向尚未完成上传的内容。
- Event 只追加，Checkpoint 可以覆盖更新，Trace 可以重新派生，Artifact 通常不可变；四者不同的
  所有权和生命周期不能合并成一个通用 JSON 状态文件。
- 模型调用也应有请求编号和未决状态。若崩溃发生在供应商已接受请求但响应未持久化之后，恢复时要
  利用供应商请求编号查询或按计费策略处理，不能默认这次调用没有发生。

## 94. Scheduler 如何设计，Worker 如何水平扩容？

### 口述主回答

调度器的单位应该是一个可恢复步骤，而不是整个长任务。每条可运行记录都带租户、优先级、最早执行
时间、截止时间、所需能力和资源预算；同一个 Run 同时只允许存在一个能够推进状态的步骤，避免两个
Worker 并发修改同一状态机。

我会做两级选择。第一层在租户之间使用加权公平调度，并受租户并发和成本额度约束，防止大租户占满
平台；第二层在选中的租户内部综合业务优先级、截止时间和等待时长，等待越久的普通任务逐步提升，
避免长期饥饿。选出步骤后，再根据模型类型、工具能力、镜像、区域和安全等级放入对应 Worker 池，
而不是让所有 Worker 竞争一个混合队列。

Worker 使用拉取模式，只在有空闲槽位时领取匹配步骤。领取通过数据库条件更新获得租约和递增的
防陈旧令牌；执行期间续租，提交时再次校验令牌和 Run 版本。租约过期后可以由其他 Worker 接管，
但旧 Worker 的迟到结果不能写回。基础设施或暂时性模型错误进入带退避和随机抖动的重试队列；
有副作用且结果未知的工具调用进入状态核对，不能直接重放。

水平扩容要按能力池分别计算。模型调用池主要看可运行步骤到达率、请求时延、队列最老等待时间和
供应商并发或 Token 配额；代码执行池看 CPU、内存、沙箱启动时间和空闲槽位；浏览器等昂贵资源还要
维护受限的预热池。扩容不能超过下游配额和全局预算，容量不足时调度器停止领取，入口执行背压。
当前项目只有本地线程池和可跨进程轮询的容器句柄，还没有上述调度与租约机制。

### 追问问题与回答

**追问：如何进行任务分片、优先级调度、重试、负载均衡和容量控制？**

先按能力池和 Run 做稳定分片，再用租户加权公平选择份额，租户内部按优先级、截止时间和等待时长
排序。Worker 拉取带租约的步骤；安全失败按退避重试，副作用不确定进入核对；每个能力池按自身队列
时延、服务时间、槽位和下游配额独立扩缩容。

### 技术追问补充

- 当前 `run_dataset()` 使用固定大小 `ThreadPoolExecutor`，`concurrency` 只是单进程并发上限，没有
  优先级、租户公平或能力感知调度。
- Dataset Driver 的 `max_attempts` 只重试抛出的基础设施异常；已完成但非零退出或评分失败不会
  自动重跑。
- `RemoteDockerBackend` 针对一个远程 Docker Daemon 执行 RunSpec；多机文档建议用带容量槽位的
  Worker Pool 包装多个 Backend，但该 Pool 尚未实现。
- `submit_dataset()` 每启动一个容器就持久化 RunHandle，`reconcile_dataset()` 可在新进程轮询；
  这提供长批次重入，不提供 Lease、心跳或 Worker 抢占协议。
- 调度记录至少包含 `run_id`、`step_id`、`tenant_id`、能力池、优先级、截止时间、`available_at`、
  资源需求、attempt、lease owner、lease expiry、fencing token 和 Run version。
- 可运行记录按能力池分区，再按 Run 做稳定散列，减少单分区热点；租户公平额度由共享配额服务或
  分片可合并的 deficit 账本维护，不能简单按 tenant 分片导致大租户形成热点。
- 同一 Run 的可运行 generation 应有唯一约束。领取步骤时使用条件更新或
  `SELECT ... FOR UPDATE SKIP LOCKED`，多个 Scheduler 可以并行工作，但同一步只有一个领取者。
- 调度优先级可以使用“租户权重 → 业务优先级 → 截止时间 → 等待提升”的顺序；高优任务仍受租户
  并发和预算限制，等待提升要设置上限，避免优先级完全失效。
- Worker 通过长轮询从匹配能力池拉取任务，并上报本地槽位；拉取模式天然把流量分给有空闲容量的
  Worker，Scheduler 不需要维护易过期的逐实例负载视图。
- 租约续期和提交都校验 fencing token。Worker 失联后先等待租约过期再重派；旧 Worker 即使继续
  运行，其提交也会因令牌过期被拒绝。
- 重试记录保留错误类别、attempt、`available_at` 和上次错误。退避只适用于安全重试；超过次数
  进入明确失败或人工处理状态，不能无限回队列。
- 每个能力池的目标并发可根据“步骤到达率 × 平均服务时间 ÷ 目标利用率”估算，再用最老等待时间
  做反馈修正。扩容上限取机器预算、供应商配额和租户承诺中的最小值。
- 沙箱或浏览器启动成本高时维护最小预热池，并把镜像拉取和环境准备时间计入服务时间；缩容前等待
  当前租约完成，不抢占有副作用步骤。
- 背压分三层：Worker 无槽位时停止拉取，调度器无下游令牌时不发放租约，入口在队列年龄和预算超过
  阈值时延迟、降级或拒绝新 Run。
- 验证水平扩容不能只看吞吐，还要测试租户公平、任务饥饿、租约接管、热点分片、重试风暴和下游
  限流场景；当前仓库没有这些生产验证结果。

## 95. Tool Execution 和 LLM Runtime 是否应该分离？

### 口述主回答

我的判断是工具控制面必须和工具执行面分开，但不要求所有工具都远程化。纯函数、低耗时、无额外
权限的工具可以保留进程内执行；运行不可信代码、占用浏览器或 GPU、访问内网和业务凭据、或者产生
外部副作用的工具，应放到独立进程、沙箱或专用服务。判断依据是权限、资源和故障影响范围，不是
统一追求微服务化。

无论本地还是远程，Runtime 都只处理同一套工具操作协议。模型产生 Tool Call 后，Runtime 先保存
操作记录，包含稳定操作编号、工具和参数版本、参数哈希、截止时间、幂等键以及本次调用允许访问的
资源；远程工具再通过事务 Outbox 投递。执行端只能使用短期能力令牌访问指定资源，不能直接修改
Agent State。它返回结构化状态和结果引用，Runtime 只有确认结果已经持久化后，才生成对应
Tool Result 并进入下一次模型调用。

远程执行至少要区分已接收、执行中、成功、明确失败和结果未知。消息重复投递时，执行端根据操作
编号和幂等键返回同一结果；网络超时只能把操作标成结果未知，不能立即重试副作用。恢复协调器先向
执行端或外部系统查询，确认未执行后才能重试。这样工具服务可以按自己的资源扩容和故障隔离，同时
不破坏 Tool Call 与 Tool Result 必须一一对应的 Agent 协议。

当前项目已经有合适的逻辑边界：`AgentTool` 分开保存模型可见声明和执行函数，工具事件与结果通过
`tool_call_id` 配对；但普通工具仍在线程池内运行，现有调用编号也不是跨服务幂等键。需要新增的是
持久操作账本、远程执行协议、能力令牌和结果核对，而不是重写核心 Agent Loop。

### 追问问题与回答

**追问：两者在权限、资源隔离、网络访问、扩缩容和故障域上有什么差异？**

Runtime 只持有模型访问和受控状态写入权限；工具执行端按单次操作获得文件、网络或业务接口的最小
权限，并使用独立沙箱。Runtime 按状态推进和模型配额扩容，工具端按 CPU、内存、GPU、浏览器会话
或外部接口配额扩容；两者使用独立故障域，工具崩溃不能破坏 Run 状态。

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
- 可以定义持久操作记录，字段至少包括 `operation_id`、`run_id`、`step_id`、`tool_call_id`、工具
  版本、参数哈希、幂等键、状态、attempt、截止时间、能力范围和当前 fencing token。
- Runtime 在同一事务中写入操作意图和 Outbox，再进入等待工具状态；执行端重复收到同一
  `operation_id` 时必须返回已有状态，不能再次产生副作用。
- 执行状态至少区分 accepted、running、succeeded、failed、cancelled 和 unknown。只有
  succeeded 或明确 failed 才能形成最终 Tool Result；unknown 先进入 Reconcile。
- 结果记录应包含小型结构化正文、错误分类和 Artifact 引用。大日志与文件不通过队列内联；所有结果
  都要经过大小、类型和敏感数据策略，再进入模型上下文。
- 能力令牌绑定操作编号、工具、参数哈希、资源范围、租户和过期时间。执行端重新校验，而不是信任
  Runtime 已经检查过。
- Tool Worker 提交结果时不能直接写任意 State，只能调用带 Run 版本和 fencing token 的结果确认
  接口；重复结果幂等，旧 Worker 的迟到结果被拒绝。
- 本地执行路径也应经过相同操作状态接口，只是省略远程队列和网络传输。这样工具从本地迁移到远程
  时，不改变 Agent Loop 看到的 Tool Result 语义。
- 物理拆分会增加序列化、排队和网络延迟。低风险工具保留本地路径，高风险、长时间或重资源工具
  使用远程路径，并通过压测比较隔离收益与额外延迟。

## 96. 长达两小时的 Agent Task 如何容错？

### 口述主回答

两小时任务不能被实现成一个连续占用 Worker 的函数调用。我会把它拆成很多短的、可提交步骤：模型
响应、工具操作完成、审批结果和 Workflow 节点结束都是状态边界。任务等待模型、外部工具、人工审批
或退避时间时，保存等待原因和唤醒条件并释放 Worker；结果到达或计时器触发后再恢复。

容错策略按故障类型决定。进程崩溃或 Worker 丢失，从最后一次已提交状态重新调度；模型限流和明确的
暂时错误可以在预算内退避重试；只读或有幂等保证的工具可以安全重试；外部服务暂时不可用则进入等待，
而不是占用线程。最危险的是副作用工具已经发出但没有确认结果，这种状态不是普通失败，必须先查询
外部系统，确认没有执行后才能重试。

平台还要检测“活着但没有进展”的任务：每个步骤有截止时间和心跳，Run 有总体墙钟、Token 和成本
预算；连续重复同一失败原因时进入阻塞，不无限重试。取消请求也要持久化并传播到模型调用、工具操作
和后代 Agent，不能只停止当前 API 请求。

当前项目有回合限制、墙钟中止、工具超时和部分模型退避；Eval 可以持久化容器句柄后由新进程继续
轮询。但 Agent State 仍在内存中，也没有持久等待状态和未决操作账本，因此尚不能在进程崩溃后恢复
到某个 Agent 步骤。

### 追问问题与回答

**追问：Process Crash、Worker 丢失、模型超时和外部 Tool 不可用时如何恢复？**

进程或 Worker 丢失后重新领取 Run 并从最后提交点恢复；模型暂时错误按请求策略有限重试；工具服务
不可用时保存等待时间并释放 Worker；副作用操作结果不确定时先核对外部状态，不能直接重放。

**追问：哪些阶段可以自动重试，哪些阶段必须 Reconcile 或人工介入？**

尚未开始的步骤、明确的暂时性模型错误、只读工具和有幂等保证的操作可以自动重试。已发出但结果
未知的副作用必须先做状态核对；外部系统既不能幂等重试也不能查询时，只能人工决定。

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
- 生产 Run 状态需要显式区分 runnable、waiting_model、waiting_tool、waiting_approval、
  waiting_backoff、reconciling、blocked 和终态；等待状态不绑定 Worker。
- 每种等待状态保存唤醒条件，例如模型请求编号、工具操作编号、审批编号或 `available_at`。回调和
  计时器只负责把条件满足的 Run 重新变为 runnable。
- 恢复策略表按“操作是否已提交、是否有副作用、结果是否确定、外部系统是否支持查询或幂等”决定
  resume、retry、reconcile 或人工处理，不能只按异常类型统一重试。
- 任务进展信号应记录最后成功状态转换、当前步骤开始时间和重复失败原因；超过步骤截止时间或连续
  相同阻塞阈值后停止自动推进。
- 取消状态先持久化，再由各执行层协作取消。无法取消的外部操作继续作为未决操作跟踪，晚到结果按
  取消策略采用或丢弃。

## 97. 如何实现 Checkpoint 和 Resume？

### 口述主回答

Checkpoint 不是另一份事实源，而是 Event 重放到某个位置后的不可变快照。Event 保存已经发生的
事实，Workflow 状态保存当前节点和等待条件；Checkpoint 只为了缩短恢复时间。即使 Checkpoint 丢失，
系统也应该能够从 Event 重新构建，只是恢复更慢。

生成 Checkpoint 时先确定一个已提交的 Event 序号，只读取这个位置之前的事实，构建消息、Active
Context、计划或工作流位置、预算和未决操作引用，再写入带版本和内容哈希的快照。对象上传完成后，
使用 Run 版本和防陈旧令牌做条件更新，原子发布 Checkpoint 引用；数据库不能指向半写入对象，也
不能让旧 Worker 覆盖新快照。

恢复时先领取 Run，再选择最新兼容且哈希正确的 Checkpoint，校验它覆盖的 Event 序号，然后重放后续
Event。接着按保存的配置版本重建 Agent、工具注册、Context Policy 和工作区，最后检查未决操作，
决定继续执行、等待回调还是状态核对。不能直接反序列化线程、连接和 Python 闭包。

Event 应在每个安全边界及时持久化，但 Checkpoint 不需要每个 Turn 都生成。频率由允许的恢复时间、
重放成本和快照大小决定；长历史可按事件数量或字节阈值生成，工作流节点结束时也可生成。副作用工具
前后必须持久化意图和结果，但这属于操作账本，不等于每次都写完整 Checkpoint。

当前 `State.events`、`StateSnapshot` 和 `rebuild_snapshot()` 已经体现事实与投影分离，
`resume()` 也能在同一进程继续；项目还缺少版本化快照、原子发布、跨进程加载和未决操作恢复。

### 追问问题与回答

**追问：Checkpoint 应该保存哪些状态，多久保存一次？**

保存从确定 Event 序号派生出的可执行投影，包括上下文、工作流位置、预算、配置版本、工作区和未决
操作引用。Event 每个安全边界提交；Checkpoint 根据恢复时间目标、重放长度和快照大小生成，不固定
每个 Turn 都写。

**追问：Event Sourcing、Snapshot 和 Workflow Engine 分别承担什么职责？**

Event 回答发生了什么；Checkpoint 回答怎样快速恢复到已知位置；Workflow 状态机根据恢复结果、
计时器、审批和未决操作决定下一步。Checkpoint 可以重建，控制决策不能从 Trace 展示结果反推。

### 技术追问补充

- 当前 `State.record_event()` 为 Event 分配递增 `index`、相对 `elapsed` 和 UUID，再更新
  `StateSnapshot`；`rebuild_snapshot()` 顺序重放 Event，但 Snapshot 只投影 Message 和
  ContextCompression，不覆盖预算、Workflow 节点和未决 I/O。
- `Agent.resume(state, followup)` 会在原 State 追加新的 task Message，再运行同一个循环；
  `State.task` 仍保留初始任务，恢复时 Agent name 必须一致。
- Trajectory v5 的 Header 和逐行 Event 支持审计、Viewer 和分析恢复，但 Reader 得到的是只读观察
  状态；继续执行还需要 Agent 配置、工具注册、ContextPolicy、工作区和凭据。
- Checkpoint 头至少记录 schema version、run id、checkpoint id、covered event index/uuid、Run
  version、配置版本、工具注册版本、内容哈希和创建者 fencing token。
- 快照正文只保存项目自有、可版本化的数据类型和外部引用：消息投影、Active Context、Workflow
  节点、计划状态、预算、等待条件、工作区版本与未决 operation id。
- 发布流程是“上传临时对象 → 校验哈希 → 数据库条件更新发布引用 → 标记对象已提交”；失败或失去
  租约时不发布，孤立对象由后台回收。
- 加载时验证 Schema 兼容、对象哈希、Event 连续性和配置可用性。快照版本不能直接读取时，先执行
  明确迁移；迁移失败则退回更旧快照或从 Event 重建。
- `ToolExecutionStartEvent` 只能说明 Runtime 开始处理调用，不能证明外部副作用状态。Checkpoint
  保存 operation id，真实 pending/unknown 状态由持久操作账本提供。
- 频率可以由最大允许恢复时间驱动：观测实际重放速度和 Event 增长，当预计重放时间接近目标时生成
  新快照；大型 Workflow 节点完成后可额外生成。
- 定期从头重放 Event 并与 Checkpoint 计算结果比较，验证投影代码和迁移逻辑；不一致时 Event 优先，
  Checkpoint 作废。

## 98. 两个 Worker 同时尝试恢复同一个 Agent Run 时，如何避免重复执行？

### 口述主回答

队列允许重复投递，真正需要保证的是同一 Run 同一时刻只有一个 Worker 能提交状态。两个 Worker
同时恢复时，都可以读取任务，但只有一个能通过数据库条件更新把 Run 从 runnable 改成 leased，并
获得新的租约代次；另一个领取失败后立即退出，不执行模型或工具。

每次 Event 追加、Checkpoint 发布和状态迁移都同时校验 Run 版本和租约代次。租约只说明“目前由谁
执行”，递增代次才负责拒绝旧 Worker：即使旧 Worker 因网络分区仍在运行，它携带的旧代次也无法
写入。状态机再限制这一步是否允许从当前状态提交，三者共同保证内部状态只有一个推进者。

长时间工具调用不应一直占用 Run 租约。Runtime 先创建持久操作记录，把 Run 切换为
waiting_external 并释放执行权；工具执行端使用自己的操作租约。结果回调根据 operation id、Run
版本和幂等键提交。新 Worker 接管 Run 时看到的是同一个未决操作，只查询或等待它，不会重新创建
第二个工具调用。

当前项目的 Event index 来自进程内列表长度，`tool_call_id` 只用于消息配对，没有分布式领取和条件
提交协议。因此现有 State 不能被两个 Worker 安全地同时恢复。

### 追问问题与回答

**追问：Lease、Lock、Fencing Token、Idempotency Key 和状态机如何配合？**

租约选出当前执行者；锁只减少竞争；递增防陈旧令牌拒绝旧执行者写入；版本条件和状态机保证迁移
合法；幂等键让外部系统识别同一次业务操作。它们分别保护不同边界，缺一不能覆盖全部重复执行。

**追问：Lease 过期与长时间 Tool Call 之间如何处理？**

Run 在提交工具意图后进入等待状态并释放 Run 租约；长工具由独立操作租约管理。Run 被重新领取时
只关联现有 operation id。操作租约过期且结果未知时先查询执行端或外部系统，不能直接重放。

### 技术追问补充

- 领取 Run 可以使用类似 `UPDATE ... WHERE status='runnable' AND lease_expired RETURNING ...` 的
  条件写，将 `lease_owner`、`lease_expires_at` 和递增 `fencing_token` 一次提交。
- 后续状态更新使用 `WHERE run_id=? AND lease_owner=? AND fencing_token=? AND version=?`；
  version/CAS 防止同一 Token 下的并发写，Fencing 防止过期 Worker 写。
- 当前 `State.record_event()` 依赖进程内 `len(events)` 分配 index，且不是线程安全的分布式追加；
  两个 Worker 不能共享同一个持久 Event 序号而不增加数据库序列或条件提交协议。
- Eval `reconcile_dataset()` 可以在新进程读取同一 manifest 并以 `result.json` 作为完成事实，但
  没有单一 Reconciler Lease；它也只轮询已启动容器，不会解决 Agent Step 的重复领取。
- 可运行步骤带唯一 generation；数据库对 `(run_id, generation)` 只允许一个有效领取记录，防止同一
  状态被重复生成多个队列项后同时执行。
- Worker 在执行模型调用前再次校验租约；若调用期间失去租约，可以完成本地清理，但模型响应只有
  通过条件提交才能被采用，晚到响应作为未采用尝试记录成本。
- 工具操作记录独立包含 operation lease、状态、幂等键和结果版本。Run Worker 不直接持有长工具
  的执行所有权，只等待 operation 状态变化。
- Tool Executor 也要验证 operation fencing token 或幂等键；若只有 Run 数据库拒绝旧 Worker，旧
  Worker 仍可能在外部系统产生重复副作用。
- 结果回调可能重复或乱序。确认接口以 operation id 和结果版本做幂等条件写，只接受第一个合法终态；
  相同结果返回成功，不同结果触发冲突告警。
- 租约时长只覆盖一次短状态推进，并大于正常网络抖动；心跳由独立控制路径维护，失去租约后 Worker
  停止提交和启动新操作。
- 状态机显式区分 leased、running、waiting_external、reconciling 和终态，使接管者知道应该推进
  Runtime、等待既有操作还是执行状态核对。

## 99. `send_email()` 成功后服务崩溃，如何避免重复发送？

### 口述主回答

如果邮件供应商既不支持幂等请求，也不能按业务编号查询发送状态，平台就无法严格保证恰好发送一次。
能做的是先选择明确的交付语义：宁可可能重复的“至少一次”、宁可可能漏发的“至多一次”，或者在
状态不确定时转人工确认。

在支持幂等或查询的前提下，我会先为“发送这封确定内容的邮件”创建稳定 `operation_id`，冻结收件人、
正文、附件和审批版本，并在同一数据库事务中写入操作记录、Run 等待状态和 Outbox。Dispatcher 可以
重复投递，但每次都使用同一个幂等键；发送内容发生变化时必须创建新的业务操作，不能复用旧键。

供应商确认成功后，先把供应商消息编号和回执写入操作账本，再把操作标记为 confirmed，最后由
Runtime 生成 Tool Result。若供应商已经接受邮件、但 Worker 在确认落库前崩溃，操作保持 unknown。
恢复协调器先按幂等键或业务查询接口核对：已发送就采用原结果，确认未发送才重试，无法确认就执行
预先选择的人工或交付策略，不能盲目再次发送。

当前项目只在工具前后记录开始、结束和 Tool Result，`tool_call_id` 也没有作为供应商幂等键使用；
因此现有实现不能解决“外部成功、内部未确认”这个崩溃窗口。

### 追问问题与回答

**追问：如何设计执行意图、幂等键、Outbox、结果确认和状态不确定时的 Reconcile？**

同一事务写入冻结后的业务操作、Run 等待状态和 Outbox；Dispatcher 始终使用同一幂等键；供应商
成功后先保存回执再确认操作。unknown 状态先查询，已执行则采用，未执行才重试，无法确认则按人工、
至少一次或至多一次策略处理。

### 技术追问补充

- 操作记录至少包含 `operation_id`、`run_id`、`step_id`、工具版本、收件人和内容哈希、审批版本、
  idempotency key、status、attempt、供应商消息编号、最后错误和时间戳。
- 业务状态与 Outbox 必须在同一数据库事务提交，避免“Run 认为要发但消息没入队”或“邮件入队但
  Run 没记录意图”；Dispatcher 对 Outbox 使用带 Lease 的领取协议。
- `operation_id` 表示一次业务发送，attempt 只是同一操作的传输尝试；幂等键不能包含 attempt，否则
  每次重试都会被供应商识别成新邮件。
- Dispatcher 收到重复 Outbox 时先读取操作状态：confirmed 直接确认消费，pending/unknown 使用同一
  键调用或查询，cancelled 不再发送。
- 供应商响应成功后，应先把原始回执和 Message ID 写入操作账本，再推进 Agent State；Agent 最终
  看到的 ToolResult 是 confirmed 事实的投影。
- 供应商超时、连接断开或进程崩溃都可能处于 unknown，而不是普通 failed。unknown 不进入自动
  重试队列，先由 Reconciler 按稳定键和外部 ID 查询。
- 发送内容变化必须生成新的业务操作和幂等键；同一操作的重试必须复用旧键，不能把 attempt 编号
  拼进幂等键导致供应商将其视为新邮件。
- 结果确认接口按 operation id 做条件写。重复相同回执返回成功，不同供应商编号或不同内容哈希触发
  冲突并进入人工核查。
- 取消也存在竞态：如果邮件尚未被供应商接受，可以取消；如果状态 unknown，不能向用户保证已取消，
  必须继续核对并报告最终状态。
- 即使内部状态只提交一次，跨越外部系统仍需要对方支持幂等或查询；“恰好一次”是端到端协议属性，
  不是单靠 Outbox 或消息队列可以声明的能力。
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
