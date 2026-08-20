# 面试专题回答：Trace 与可观测性

本文覆盖 [`question-checklist.md`](question-checklist.md) 中第 7 个专题。回答以
Trajectory v5、增量 writer、Span 派生和成本统计代码为事实依据。

## 1. 为什么使用 JSONL，而不是数据库或普通 JSON？

因为当前最重要的是本地长运行能增量追加、实时 tail，并在进程中断后保留完整前缀。
Trajectory v5 第一行是 header，后续一行一个 Event；writer 每次只追加新 Event，不需要
重写不断增长的数组。

相比数据库，JSONL 不需要服务和迁移基础设施，适合教学、实验和容器产物；相比一个大 JSON，
它的恢复单元是一条完整行。代价是跨运行查询、索引和并发多写者能力较弱；production 规模可
把相同 Event Schema 写入数据库或对象存储，但不应改变 Runtime 事实协议。

## 2. 一条 Trace Event 包含哪些字段？

每条 Event 共有 `kind`、递增 `index`、相对运行时间 `elapsed` 和唯一 `uuid`，再带该事件类型
自己的字段。例如模型请求包含 agent、轮次、可见消息数和请求投影；模型响应包含 model、API、
usage 和 stop reason；工具事件包含 Tool Call ID、工具名、错误和 terminate 状态。

文件 header 另有 schema、trace ID、producer、task 摘要和 meta。这些文件级身份不能和 Event
顺序混在一起。Provider raw 是调试证据，会外置到相邻 raw pool，通过 `raw_ref` 引用。

## 3. Turn 和 Span 分别如何定义？

Turn 是核心 Agent Loop 的一次迭代，从 `TurnStartEvent` 到 `TurnEndEvent`，通常包含一次模型
调用和可选的一组工具调用。Span 是观察层从开始/结束 Event 派生的时间区间，可以表示整个
agent run、turn、model call、tool call 或 compression。

所以 Turn 是 Runtime 事实和控制边界，Span 是为了查看耗时和父子关系生成的只读视图。Span
不会反向决定超时或停止。

## 4. Tool Call、模型调用和子 Agent 如何建立父子关系？

普通模型和工具 Span 挂在当时的 Turn Span 下，开始和结束事件通过轮次或 Tool Call ID 配对。
Task Tool 的父子关系依赖同一个 Tool Call ID：父工具结果的
`sidecar["details"][call_id]["sub_events"]` 保存子 State 事件。

合并视图先找到父 Tool Span，再单独从子事件生成 Span，把子时间轴平移到父工具开始时间，并
只把子根节点的 parent ID 指向父 Tool Span。父子 State 都不会被修改；这是展示合并，不是把
两个运行强行改成共享历史。

## 5. 流式输出如何增量记录？

当前需要区分两种“流式”。Trace 文件是增量写的：后台 writer 定期快照 State，追加新增完整
Event，最终再写规范文件。模型访问层也定义了 text delta、thinking delta、Tool Call 参数增量
等统一 `StreamEvent`。

但当前三个真实 Provider Adapter 仍是阻塞请求，拿到完整响应后再映射统一流事件，因此项目还
没有把网络级 token streaming 的每个 delta 持续写成 Runtime Event。不能把“Trace 实时追加”
说成“真实 Provider token 已逐个落盘”。

## 6. Runtime 中途崩溃时，最后一条 JSONL 损坏怎么办？

Reader 使用增量 JSON decoder，遇到无法解析的尾部就停止，保留此前完整对象。因此最多丢掉
未完整写入的最后一条记录，不会把半条 Event 当成事实。

增量 writer 一次写完整 JSON 行并 flush，尽力 fsync；第一次 header 和最终 whole-file 输出使用
临时文件加原子 rename。这个方案保护完整前缀，但不是事务数据库：系统掉电时最后尚未落盘的
Event 仍可能丢失。

## 7. Trace 是否可以完整重放？

它可以重建和回放观察视图，但不能仅凭 Trace 安全地继续执行。Reader 可以从 MessageEvent
重建 transcript，从压缩事件恢复 active context 变化，并重新派生 Span、ModelTurn 和成本。

真正 `resume()` 还需要原始 State、Agent 配置、工具注册、Context Policy 和外部资源状态；v5
还会移除可重建的每轮 `llm_payload` 以避免历史平方增长。因此我不会把 Trajectory 描述成通用
durable execution log。

## 8. 重放是重放状态，还是重新执行工具？

默认只重放已记录事实和派生视图，绝不自动重新执行工具。重新执行 Bash、写文件或外部 API
可能产生不同结果和重复副作用，已经不是观察重放，而是一次新的实验。

如果需要可执行 replay，应显式使用 Fake/recorded Provider 和无副作用工具替身，或者为外部
操作实现幂等协议。当前 Trace 的主要用途是审计、Viewer、训练导出和分析。

## 9. Token 和成本如何计算？不同 Provider 口径如何统一？

Adapter 先把 Provider usage 规范化为 `TokenUsage`，区分 input、output、cache read 和 cache
write。`ModelResponseEvent` 保存 model、API 和 usage，`RunCost` 再按模型聚合并应用可覆盖的
`PriceBook`。

不同 Provider 的缓存口径在 Adapter 边界转换，例如 OpenAI 的 cached input 通常包含在 input
总量中，需要拆分；Anthropic 的缓存字段可独立映射。未知模型或缺失 usage 不能产生虚假的精确
成本；当前显示会标记 unpriced，美元总额只能看作下界。

## 10. 停止原因有哪些枚举？

核心 Agent 的 `AgentEndEvent.reason` 当前包括 `done`、`max_turns`、`abort` 和
`tool_terminate`。Goal Loop 另有目标生命周期状态：active、complete、blocked、
budget_exhausted 和 aborted。

这两层不能混用：`done` 只是内层模型运行正常结束，Goal Loop 仍可能因 verifier veto 继续；
`max_turns` 是内层截断，外层对应预算耗尽，而不是完成。

## 11. 如何避免 Trace 记录密钥、用户数据或超长工具输出？

当前有一些体积控制：工具结果本身限制输出，Provider raw 外置并减少主文件重复，主 Trace 和
raw pool 分开加载。但 sidecar、工具结果和 raw 仍可能包含敏感数据；“非模型可见”不等于“可
安全持久化”。

项目当前没有完整自动脱敏和多租户访问控制，所以不能声称已经解决隐私。生产化时应在 Trace
sink 前做字段级 allowlist/redaction、密钥模式过滤、大小上限、加密和租户隔离，并让 raw 采集
可关闭或使用更短保留期。

## 12. 并发写 Trace 时如何保证顺序和完整性？

单个 State 由 Runtime 统一追加 Event 并分配 index；并行工具的执行完成顺序可以不同，但写回
事件仍经过父 Runtime。增量 writer 对后台 flush 和显式 flush 使用同一把 lock，只追加自上次
写入后的 Event 前缀。

当前契约是一个 run 一个 writer，不支持多个进程或多个独立 writer 同写一份 trajectory。
跨进程并发需要单写者服务、文件锁或数据库 sequence，这不是现有本地 JSONL 实现的能力。

## 13. Trace 对性能有什么影响？

主要开销是 Event 序列化、State 快照、磁盘 flush/fsync，以及保存 Provider raw 的体积。当前
writer 按固定间隔批量追加，而不是每个 Event 都写一次；raw 外置也避免主轨迹随历史重复接近
平方增长。

仓库没有公开 Trace on/off 的延迟基准，所以我不会给出具体百分比。需要评估时应固定任务和
Provider，比较关闭 Trace、只写规范 Event、再加 raw 的 wall-clock、CPU、I/O 和文件大小。

## 核对依据

- [`trace/jsonl.py`](../src/simple_long_horizon_agent/trace/jsonl.py)
- [`trace/live.py`](../src/simple_long_horizon_agent/trace/live.py)
- [`trace/run_trace.py`](../src/simple_long_horizon_agent/trace/run_trace.py)
- [`trace/spans.py`](../src/simple_long_horizon_agent/trace/spans.py)
- [`09-trace-and-observability.md`](../docs/design/09-trace-and-observability.md)
- [`tests/unit/test_live_trace.py`](../tests/unit/test_live_trace.py)
- [`tests/unit/test_trace_fixture_golden.py`](../tests/unit/test_trace_fixture_golden.py)
