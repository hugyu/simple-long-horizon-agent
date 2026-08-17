下面继续以“项目负责人 / 核心开发者”的第一人称回答。这里会明确区分当前实验型 Runtime 已有能力、生产化所需改造，以及基于现有架构推演的故障处理方案。

## 1. 当前架构最大的瓶颈是什么？

从生产化角度看，最大的瓶颈不是 Agent Loop 的执行速度，而是缺少持久化控制平面。

当前普通 Agent 的 `State` 主要存在于进程内，Runtime 由同步生成器驱动。Eval 虽然支持本地 Docker、远程 Docker、并发数据集运行和 Submit/Reconcile，但它解决的是评测任务执行，不是通用在线任务调度。

当前还缺少：

- 通用 State Checkpoint 和跨进程恢复协议。
- 持久化任务队列、租约、心跳和 Worker 调度。
- 工具副作用的幂等账本。
- 多租户、权限审批和强安全沙箱。
- 全局预算、Provider 配额和故障切换。

所以生产化第一步不是把同步循环改成异步，而是先解决状态持久化、幂等执行和任务所有权。

## 2. 单机 Runtime 如何扩展成分布式任务系统？

我会保留现有单 Agent Runtime，把分布式能力放在它的外层。

```text
API / Control Plane
  → 身份认证、任务创建、预算和策略
  → Durable Queue
  → Scheduler / Lease
  → Worker
      → 在隔离环境中运行现有 Agent Runtime
      → 持久化 Event、Checkpoint 和 Artifact
```

Control Plane 负责任务状态、租户、预算、调度和取消；Worker 负责消费一个任务并运行现有显式循环。

每个 Worker 通过租约获得任务，周期续租并发送心跳。租约到期后任务可以被重新调度，但必须先通过工具幂等账本判断哪些副作用已经发生。

现有 `Backend` 协议可以继续表达任务在哪里执行，但需要增加一个真正的队列和 Worker Pool，而不是把 `RemoteDockerBackend` 直接称为分布式平台。

## 3. Agent 状态保存在哪里？

当前 Agent 配置本身不保存历史。一次运行的事实保存在 `State` 中，包括：

- Message 历史。
- Event Stream。
- Active Context Indices。
- 压缩结果和停止原因。

这些状态主要驻留在当前 Python 进程内。`resume()` 可以在同一个 State 上继续，但跨进程恢复需要调用者自行序列化 State，并重新组装 Agent、工具、Context Policy 和外部资源。

`trajectory.jsonl` 是观察和审计产物，不是可以直接恢复执行的完整 Checkpoint；Filesystem Memory 是跨任务经验，也不是当前任务 State。

所以当前仓库拥有可回放事实模型，但还没有通用的生产级 State Store。

## 4. 如何实现任务恢复和幂等执行？

我会把恢复点设置在明确的事件边界，而不是任意 Python 调用栈位置。

一次模型或工具操作采用：

```text
持久化 operation intent
  → 执行外部操作
  → 持久化 operation result
  → 更新 State Snapshot
```

Event Stream 仍然追加写，Snapshot 用于快速恢复。恢复时从最近 Snapshot 开始，再重放后续 Event。

对工具操作，需要保存：

```text
operation_id
run_id
turn_id
tool_call_id
normalized arguments
status: pending / committed / failed / in_doubt
result reference
```

如果进程在外部副作用完成后、结果持久化前崩溃，这次操作必须标记为 `in_doubt`，先对账，不能直接重放。

当前 Eval 的中断 Chain 不支持原地恢复，需要新 run ID；生产化必须补充正式的 Checkpoint Schema 和迁移机制。

## 5. 如何避免同一个工具调用重复执行？

当前 Tool Call ID 可以保证一次运行中的调用和结果正确配对，但它不是跨进程幂等保证。

生产环境中，我会使用：

```text
idempotency_key = run_id + turn_id + tool_call_id
```

工具执行前先在持久化账本中注册。若同一个 key 已经 committed，直接返回原结果；如果是 pending 或 in_doubt，则根据工具语义对账。

- Read、Search 等只读工具通常可以安全重试。
- Edit 应使用文件版本 Hash、Compare-And-Swap 和原子替换。
- 支持幂等键的外部 API 应直接传递该 key。
- 支付、发送消息等不可逆操作必须先查询外部系统状态。

一般无法保证真正的 exactly-once，只能通过幂等设计实现 effectively-once。

## 6. 如何支持数千个并发 Agent？

我不会在一个 Python 进程中创建数千个无界线程。核心 Runtime 可以继续保持同步和可读，每个 Worker 只运行有限数量的活跃 Agent。

扩展依靠：

- Durable Queue 和水平扩展 Worker。
- 每个 Worker 的有界并发。
- 按 Provider、模型和租户设置并发门。
- 对长工具操作使用独立容器或执行进程。
- Artifact 和 State 放在共享持久化层。
- 队列背压和优先级调度。

实际瓶颈通常先出现在模型 TPM/RPM、容器启动、工作区存储和外部工具，而不是 Agent Loop 的 Python 控制流。

控制平面可以使用异步 I/O，但不需要为了高并发重写核心 Agent 语义。

## 7. 如何实施租户隔离？

当前项目不是多租户系统，简单的 `cwd`、本地 run directory 和 Memory namespace 不能提供完整隔离。

生产化需要在所有资源上携带不可省略的 `tenant_id`：

- 每租户独立 State、Artifact 和 Memory namespace。
- 独立容器或更强 VM 沙箱。
- 独立工作区和只允许最小挂载。
- 网络出口和服务访问策略。
- 租户级 Provider 凭据、预算和并发配额。
- 加密、访问控制和审计日志。
- 禁止跨租户 Prompt Cache、Memory 和检索结果复用。

路径中包含 tenant ID 还不够，Store、数据库查询和权限检查都必须强制带租户条件，避免调用者漏传后退化成全局访问。

## 8. 如何管理 API Key 和用户凭证？

当前 Provider 凭据主要通过环境变量注入，Profile 明确不应保存长期密钥。这适合本地实验，不适合多租户生产环境。

生产化我会使用 Secret Manager 或 KMS：

1. 数据库只保存 Secret 引用，不保存明文。
2. Worker 在任务开始时按租户和用途获取短期凭据。
3. 凭据只注入需要它的 Provider Adapter 或 Tool。
4. 不写入 Message、State、Trace、Memory 或模型上下文。
5. 日志和 raw Provider 数据进入存储前做脱敏。
6. 支持轮换、吊销和访问审计。

MCP Server 和子 Agent 不能默认继承主进程全部环境变量，只能获得显式允许的最小凭据集合。

## 9. 如何设计工具权限和审批流程？

当前 PRE_TOOL_USE Hook 可以检查并阻止工具调用，也会留下 HookFiredEvent，但它还不是完整的人工审批系统。

生产化会将工具调用划分风险等级：

| 风险 | 示例 | 策略 |
| --- | --- | --- |
| 低 | 工作区内只读 | 自动允许 |
| 中 | 修改工作区文件 | Policy 自动判断或预授权 |
| 高 | 网络写入、删除、发布 | 人工审批 |
| 禁止 | 越权路径、秘密导出 | 强制拒绝 |

高风险调用进入 durable approval 状态，暂停任务并保存 State。审批通过后生成绑定以下内容的短期 Capability：

```text
tenant + run + tool + normalized arguments + expiry
```

Runtime 恢复后只能执行已经批准的精确操作，不能把“允许一次发布”扩展为任意发布权限。

Hook 负责可组合策略，工具和操作系统仍必须强制执行路径、身份和网络等硬约束。

## 10. 如何限制单任务 Token、成本和运行时间？

当前已经有多层局部预算：

- Runtime 的 `max_turns`。
- LLMRequest timeout。
- Tool timeout。
- 外部 Abort。
- Goal Loop 的 turn、输出 Token 和 wall-clock budget。
- Trace 中的 Token 和成本统计。

生产化需要统一的层级预算账户：

```text
Tenant Budget
  → Run Budget
    → Workflow Step Budget
      → Parent / Child Agent Budget
```

每次模型调用前预留预算，完成后按 Provider usage 结算。Task Tool 的子 Agent 消耗必须计入父任务总预算。

应同时限制输入 Token、输出 Token、美元成本、模型调用次数、工具次数、墙钟时间和并发资源。接近软限制时提醒 Agent 收敛；达到硬限制时禁止新调用、触发 Abort，并返回当前最好结果和明确的 `budget_exhausted` 状态。

## 11. 模型服务限流时怎么处理？

当前模型访问层已经对 429 和部分临时服务错误做有限次数退避重试。这适合单次运行，但数千个 Agent 独立重试会形成重试风暴。

生产化需要集中式 Provider 配额管理：

- 按 Provider、模型、租户维护 RPM、TPM 和并发令牌桶。
- 请求进入 Adapter 前先取得配额。
- 使用 Provider 的 `Retry-After` 和带抖动的退避。
- 队列背压，而不是不断创建失败请求。
- 对持续异常启用 Circuit Breaker。
- 对低优先级任务限流或降级。

重试发生在请求还没有进入 Runtime State 的边界，避免制造多个虚假的 Agent Turn，但每次真实 API 尝试仍应进入成本和故障统计。

## 12. Provider 故障时能否切换模型？

架构上可以在干净的 Turn 边界重新组装 Agent，并对同一个 Provider-neutral State 调用 `resume()`；但当前没有实现自动透明 Failover。

安全切换点是：

- 上一轮响应已经完整规范化并写入 State。
- ToolCall 和 ToolResult 已经完成配对。
- 当前没有状态未知的 Provider 请求。

如果请求可能已经被 Provider 接收，但客户端没有收到响应，我会把该调用标记为 `in_doubt`。重新调用另一个模型会产生新的决策，不能假装是原请求的无损恢复。

自动切换还要检查模型能力、上下文窗口、工具调用、多模态和 reasoning 支持。不能只把模型名称替换掉。

## 13. 切换 Provider 后上下文语义是否保持一致？

项目自己的 Message、Content Block、ToolCall 和 ToolResult 语义可以保持一致，但 Wire 行为不保证完全等价。

不同 Provider 在以下方面存在差异：

- System Prompt 的位置。
- 工具结果的消息结构。
- Thinking 和 Reasoning 连续性。
- 多模态支持。
- Stop Reason。
- 上下文窗口和 Token 估算。
- 缓存和供应商特有能力。

Adapter 可以隔离格式差异，但不能创造目标模型本身不支持的能力。

OpenAI Responses 的 reasoning item 连续性是典型特例。切换到 Anthropic 时，命名空间中的 OpenAI continuity 数据会被忽略，通用文本和工具事实仍可使用，但推理连续性可能下降。

因此 Failover 应通过 Capability Matrix 校验，并在 Trace 中记录模型切换，而不能称为完全无感切换。

## 14. 如何做版本管理和灰度发布？

一次运行不能只记录代码版本，还需要记录完整行为版本：

```text
Runtime commit
Message/Event schema
Prompt version
Agent flavor
Tool schema和实现版本
Context/Compression policy
Provider Adapter
Model alias和实际服务模型
Eval suite和镜像
Permission policy
```

新任务在创建时绑定版本，运行中不能因为默认配置改变而静默漂移。State 和 Checkpoint Schema 需要显式迁移。

灰度发布可以先经过：

1. Unit Test 和 deterministic smoke。
2. 固定 Eval 回归集。
3. Shadow Run，不产生真实副作用。
4. 小比例内部租户。
5. 按成功率、成本、错误率逐步扩大。

回滚只影响新任务；已经运行的任务默认继续使用绑定版本，除非有安全原因强制停止。

## 15. 如何定义 Agent Runtime 的 SLO？

我会把平台可靠性和 Agent 任务质量分开。

平台 SLO 可以包括：

- 任务接收可用率。
- P95 排队到开始时间。
- State/Event 持久化成功率。
- 无丢失终态事件的比例。
- Abort 生效延迟。
- 工具调用结果配对完整率。
- Worker 故障后的恢复时间。
- Trace 和 Artifact 完整率。

任务成功率、Benchmark 分数和用户接受率属于质量指标，不应与基础设施可用率混成一个数字。

例如模型 Provider 故障导致任务失败时，平台可能仍正确记录了 `provider_error`。这对平台 SLO 和任务质量指标的影响应分别统计。

当前项目没有正式生产 SLO，以上是生产化目标设计，不是已经达到的指标。

## 16. 如何监控成功率、延迟、Token 和成本？

当前 Event Stream 已经包含 Agent、Turn、Model、Tool、Compression 和停止原因；Trace 能派生 Span、ModelTurn、Token 和成本。

生产化会从同一事实流导出指标：

- 成功率：外部 Completion Check、Verifier 或业务结果。
- 延迟：排队、模型、工具、每轮和总墙钟时间。
- Token：输入、输出、Cache Read 和 Cache Write。
- 成本：按实际响应模型和 PriceBook 聚合。
- 稳定性：Provider 重试、工具错误、超时和 in-doubt 数量。
- 资源：队列长度、Worker 利用率和容器启动时间。
- 安全：Hook 拒绝、越权路径和审批事件。

指标按 tenant、模型、Agent flavor 和版本聚合，但避免把完整 Prompt、文件内容或高基数 ID 直接写入 Metrics。

原始 Trace 只按权限和保留策略存储，监控系统主要消费脱敏后的结构化字段。

## 17. 如何处理不可重复的线上故障？

核心原则是尽量复原“当时发生了什么”，而不是要求模型再次生成完全相同的输出。

我会保留：

- 不可变运行 Manifest。
- Message/Event Stream。
- 每轮规范化 ModelRequest 和 Response。
- 受限访问的 Provider raw sidecar。
- Tool 参数、结果、错误和副作用 ID。
- Prompt、工具 Schema 和策略版本。
- 容器镜像、代码 Commit 和工作区产物。

重放时可以使用 Fake Provider 和录制的 Tool Result 验证控制流，但不会重新执行真实副作用。

一个符合当前架构的模拟故障是：

Provider 已经接收请求，但网络在返回响应前断开。客户端无法判断模型是否完成并计费。系统将该 Model Call 标记为 `in_doubt`，保留请求 raw 和 Attempt ID，不向 State 伪造 AssistantMessage。若策略允许重试，则创建新的 Attempt；两个 Attempt 都进入成本和事故记录。

如果是写工具返回确认前进程崩溃，则先使用 idempotency key 查询外部状态，不能直接重放写操作。

## 18. 如何应对 Prompt Injection、数据泄漏和命令执行风险？

不能只依赖 System Prompt。安全需要多层强制边界：

1. 将文件、网页、日志、MCP 和子 Agent 输出视为不可信数据。
2. 工具采用最小权限和明确 Allowlist。
3. Read/Edit 做规范路径和符号链接检查。
4. Bash 在非特权容器中运行，限制挂载、网络、进程和资源。
5. PRE_TOOL_USE Hook 检查工具、路径、目标服务和参数。
6. 高风险操作进入人工审批。
7. 凭据不进入模型 Context，工具只获得最小短期凭据。
8. Trace、raw 和 Memory 做脱敏、访问控制和保留期限。
9. Memory 写入时拒绝秘密、临时状态和无证据结论，防止长期污染。

当前项目有 Hook、超时、容器 Eval 和输入隔离等基础，但 Read/Edit 还不是严格路径沙箱，Bash 也不是完整安全沙箱，尚无污点追踪和人工审批产品界面。因此不能宣称已经满足生产安全要求。

## 19. 哪些模块可以保持简单，哪些必须升级？

可以保持简单的部分：

- `Message`、`Event` 和 Provider-neutral 数据协议。
- 显式 Agent Loop。
- Context View 和 Compression 策略边界。
- AgentTool 和 ToolResult 契约。
- Provider Adapter。
- 普通 Python Workflow。
- Suite、Backend、ArtifactStore 的正交边界。
- 从 Event 派生 Trace 的模式。

必须升级为生产级基础设施的部分：

- Durable State Store、Checkpoint 和 Schema Migration。
- 队列、调度器、租约、心跳和 Worker Pool。
- 工具幂等账本和副作用对账。
- 强安全沙箱和工作区隔离。
- 多租户、RBAC、Secret Manager 和审计。
- 权限 Policy 与人工审批。
- 全局预算、Provider 配额、Circuit Breaker 和 Failover。
- 分布式 Trace、Metrics、日志和告警。
- 高可用 Artifact Store、备份和灾难恢复。

生产化不应该把核心 Loop 变成一个大型分布式框架，而应让可靠基础设施围绕现有明确语义工作。

## 20. 如果继续做三个月，优先级是什么？

**第一个月：持久化和恢复。**

建立 State/Event 的正式序列化协议、Checkpoint、任务 API、持久化队列、Worker Lease 和工具幂等账本。

验收标准是：在模型响应、工具执行和 State 写入的不同阶段强制杀死进程，任务能够恢复，并且写操作不会重复执行。

**第二个月：安全和多租户。**

完成工作区路径限制、非特权容器、网络策略、租户命名空间、Secret Manager、RBAC、工具 Policy 和 durable approval。

验收标准是：恶意仓库文本无法让 Agent 读取其他租户文件、导出秘密或绕过高风险审批。

**第三个月：可靠性和规模化。**

增加 Provider 配额调度、Circuit Breaker、受控 Failover、统一预算账户、分布式指标和故障演练；然后使用固定 Eval 和合成负载进行灰度。

验收标准是：Worker、Provider 和存储发生可控故障时，系统能明确区分恢复、失败和 in-doubt，且没有状态丢失、重复副作用或跨租户泄漏。

这三个月我不会优先增加更多 Workflow，也不会先上复杂 Kubernetes 编排。先让现有 Agent 在崩溃、越权和限流条件下可靠运行，价值更高。
