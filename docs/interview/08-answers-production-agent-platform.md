# 08. Production Agent Platform

本文对应 [`question-checklist.md`](question-checklist.md) 中第八部分的问题。本篇是基于当前
Runtime 边界给出的生产化设计方案，不代表仓库已经实现这些平台能力。

## 92. 如何设计支持 10 万个同时运行任务的 Agent 平台？

### 口述主回答

我不会让 10 万个任务都对应一个长期占用线程。平台会把任务拆成等待、可运行和执行中状态，只有
可运行步骤进入调度。Control Plane 负责鉴权、任务配置和查询；Scheduler 负责持久队列、优先级、
租户公平和预算；Worker 只领取带租约的步骤并运行现有 Agent Runtime。

运行状态和租约放在事务型存储，大型 Trace、Checkpoint 和产物放对象存储；高风险 Tool Execution
使用独立沙箱服务。模型、工具和租户分别限流，队列积压触发背压和扩容，避免下游模型服务被瞬时
流量打穿。

### 技术追问补充

- Scheduler 按租户、优先级、模型和工具能力分片，并使用加权公平调度避免大租户独占。
- Worker 按 CPU、内存、工具类型和模型并发上报容量，平台据队列深度和等待时间自动扩缩容。
- 全局预算同时限制任务数、模型并发、Token、美元成本和墙钟时间。
- 当前项目只有轻量 Runtime 和 Eval 批量运行，不具备这套生产控制面。

## 93. Agent Run 如何持久化，Runtime 应该有状态还是无状态？

### 口述主回答

Worker 在执行一个步骤时可以把 State 放在内存里，但不能成为唯一事实源。生产上我会让 Worker
尽量无状态：每次通过租约加载最近 Checkpoint 和后续 Event，执行到安全边界后再持久化，Worker
丢失时其他实例可以继续。

Postgres 保存运行状态机、租约、事件索引和幂等记录；Redis 只做队列、缓存和限流，不作为最终
事实源；对象存储保存大 Checkpoint、Trace、工具产物和工作区快照。

### 技术追问补充

- Event 保存已经发生的事实，Checkpoint 保存可快速恢复的派生状态。
- Artifact 是文件、补丁和模型产物；Trace 是面向观察和审计的事件投影。
- Redis 数据应可重建，关键状态变更使用数据库事务和条件更新。
- 当前仓库的 `State` 是进程内对象，只支持同一 State 上继续运行。

## 94. Scheduler 如何设计，Worker 如何水平扩容？

### 口述主回答

Scheduler 使用持久队列保存可运行步骤，Worker 采用拉取模式领取任务和租约。任务按租户、优先级、
所需工具和运行环境分区，Worker 只领取自己具备能力且还有容量的任务。

Worker 通过心跳续租，失败后任务回到可调度状态；扩容依据队列长度、等待时间和下游限流，而不是
只看 CPU。模型或工具容量不足时，Scheduler 应停止继续派发并向上游施加背压。

### 技术追问补充

- 优先级队列之外还需要租户公平和最大并发配额，防止饥饿。
- 重试要区分基础设施失败、模型暂时失败和已经产生副作用的工具失败。
- Worker 镜像按能力分组，任务通过能力标签路由，不在任意 Worker 上临时安装所有依赖。
- 租约、心跳和状态条件更新共同避免任务永久丢失。

## 95. Tool Execution 和 LLM Runtime 是否应该分离？

### 口述主回答

生产环境中通常应该分离。LLM Runtime 主要做状态推进和模型调用，而工具可能执行代码、访问内网、
操作浏览器或调用有副作用的业务接口，两者的权限、资源和故障域完全不同。

我会让 Runtime 只提交带能力令牌的工具请求，Tool Worker 在独立沙箱中执行，并限制文件挂载、
网络和凭据。纯函数或低风险本地工具可以同进程运行，不需要为了形式统一全部远程化。

### 技术追问补充

- LLM Worker 主要受模型并发和网络延迟约束，Tool Worker 可能受 CPU、内存、GPU 或安全隔离约束。
- Tool Worker 使用短期凭据和最小权限，结果通过结构化协议返回。
- 工具平台故障不应拖垮调度和模型服务，可独立扩容、熔断和降级。

## 96. 长达两小时的 Agent Task 如何容错？

### 口述主回答

核心是不要把两小时工作绑定到一个进程。每个模型响应、工具结果和 Workflow Step 完成后都形成
持久化边界，Worker 定期续租并写 Checkpoint；进程崩溃后，新 Worker 从最后一个已提交边界恢复。

尚未提交结果的模型请求通常可以重试；只读工具也可以重试。有副作用工具如果状态不确定，不能
直接重放，必须先查询外部系统做 Reconcile，无法确认时转人工。

### 技术追问补充

- Worker 丢失由租约超时发现，恢复前先获取新租约和更高 Fencing Token。
- 模型超时在没有响应提交时可以有限重试，并记录尝试和成本。
- 外部工具不可用时可延迟重试、使用明确 fallback，或把任务标记为 blocked。
- 工作区和大文件需要快照或持久卷，不能只恢复对话消息。

## 97. 如何实现 Checkpoint 和 Resume？

### 口述主回答

Checkpoint 应保存恢复执行所需的最小完整状态：运行和版本标识、最后事件位置、消息与活跃上下文
投影、预算消耗、当前 Workflow Step、Agent 配置版本，以及正在等待的外部操作编号。

我会在每个模型响应、工具结果和步骤结束后保存，长步骤再周期保存。Event Log 负责事实审计，
Snapshot 或 Checkpoint 负责快速加载，Workflow Engine 负责决定下一步执行哪个节点。

### 技术追问补充

- 恢复时先加载最近 Checkpoint，再重放其后的 Event，并校验 Schema 和配置版本。
- Checkpoint 不能把“工具开始”误写成“工具完成”，未决副作用必须保留不确定状态。
- 频率由恢复成本和写放大权衡，关键副作用前后必须强制落盘。
- 当前项目只有内存 `rebuild_snapshot()`，没有跨进程 Checkpoint 协议。

## 98. 两个 Worker 同时恢复同一个 Run 时如何避免重复执行？

### 口述主回答

我会使用带过期时间的 Lease 加 Fencing Token，而不是只依赖普通分布式锁。Worker 领取任务时通过
条件更新获得租约和单调递增的 Token，之后每次写状态和提交工具结果都必须携带该 Token；旧 Worker
即使恢复运行，它的写入也会被拒绝。

状态机通过 CAS 限制合法迁移，工具调用再使用稳定幂等键。这样租约解决所有权，Fencing 防止旧
持有者回写，幂等键防止外部动作重复。

### 技术追问补充

- 长工具调用期间 Worker 必须续租；续租失败后停止提交后续结果。
- Lease 过期不代表外部工具一定停止，接管者必须先 Reconcile 再决定是否重试。
- 每个副作用操作使用 `run_id + step_id + operation_id` 形成稳定幂等键。
- 数据库条件更新同时检查状态、租约所有者和 Fencing Token。

## 99. `send_email()` 成功后服务崩溃，如何避免重复发送？

### 口述主回答

这类场景不能只靠重试解决。我会先在数据库事务中记录发送意图和稳定幂等键，并通过 Outbox 派发；
邮件 Worker 调用供应商时携带同一个幂等键，成功后保存供应商消息 ID 和完成状态。

如果邮件已发出但状态还没写回，恢复时任务处于不确定状态。系统应先按幂等键或供应商消息 ID
查询发送结果，确认未发送后才能重试；无法查询时不能盲目再次发送，应转人工或选择明确的
“至多发送一次”策略。

### 技术追问补充

- 发送意图、Outbox 和业务状态在同一事务中提交，避免任务存在但消息丢失。
- 供应商支持幂等键时，重复请求应返回同一发送结果。
- 不支持幂等查询的外部系统无法保证严格“恰好一次”，只能在重复和漏发之间明确取舍。
- 当前项目没有事务 Outbox、外部操作账本和 Reconcile 机制。

## 核对依据

- [`state.py`](../../src/simple_long_horizon_agent/state.py)
- [`core.py`](../../src/simple_long_horizon_agent/core.py)
- [`src/simple_long_horizon_agent/evals/batch.py`](../../src/simple_long_horizon_agent/evals/batch.py)
- [`src/simple_long_horizon_agent/evals/protocols.py`](../../src/simple_long_horizon_agent/evals/protocols.py)
- [`src/simple_long_horizon_agent/evals/backends/remote_docker.py`](../../src/simple_long_horizon_agent/evals/backends/remote_docker.py)
- [`01-product-goals-and-scope.md`](../design/01-product-goals-and-scope.md)
- [`02-system-architecture.md`](../design/02-system-architecture.md)
- [`10-evaluation-and-operations.md`](../design/10-evaluation-and-operations.md)
- [`multi-machine-eval.md`](../multi-machine-eval.md)
