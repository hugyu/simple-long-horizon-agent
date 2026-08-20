# 06. Observability 与 Cost

本文对应 [`question-checklist.md`](question-checklist.md) 中第六部分的问题。每道题包含可直接
口述的主回答，以及精简的技术追问补充。

## 64. 为什么使用 JSONL 保存 Trace？

### 口述主回答

因为 Agent 运行时间长，事件需要边运行边落盘。JSONL 一行保存一个完整记录，首次写入 Header，
之后只追加新 Event，Viewer 可以实时读取已经完成的前缀；进程中断时也只需要忽略不完整的尾行。

相比单个大 JSON，它不需要每次重写整个数组。代价是查询和关联能力弱，不适合多写者并发、复杂
检索和事务场景；如果进入生产规模，数据库或日志系统更适合作为集中存储。

### 技术追问补充

- 当前 v5 格式第一行是 Header，后续每行是一条 Event。
- 增量 Writer 只追加新增事件，最终 Writer 可以通过临时文件加原子替换重建完整文件。
- Reader 遇到无法解析的尾部会停止，前面已经完整写入的记录仍可使用。
- JSONL 仍需要额外索引或导入数据库，才能支持高效条件查询和聚合。

## 65. Trace Schema 是什么样的？

### 口述主回答

当前持久化版本是 `simple-long-horizon-agent.trajectory.v5`。Header 保存格式版本、Trace ID、
生产者、任务摘要和元数据；每条 Event 保存运行内顺序、相对时间、全局唯一 ID、事件类型以及
对应业务字段。

消息、Span、模型轮次和成本不在 Header 中重复保存，而是从事件流派生，避免多份事实不一致。
Schema 变化时必须同步更新 Writer、Reader、Viewer 和固定测试样例。

### 技术追问补充

- Event 至少包含 `index`、`elapsed`、`uuid`、`kind` 和该事件的领域载荷。
- `index` 表示追加顺序，`elapsed` 表示运行内相对时间，`uuid` 用于跨文件引用。
- 当前 Reader 兼容旧的单行或缩进 JSON 记录，但没有通用 Schema 迁移框架。
- Golden fixture 用于发现序列化格式和 Viewer 之间的漂移。

## 66. 什么是 Span？

### 口述主回答

Span 是从开始和结束 Event 派生出的一个时间区间，用来表示一次 Agent 运行、一个 Turn、一次模型
调用、一次工具调用或一次上下文压缩持续了多久，以及它属于哪个父操作。

Event 是原始运行事实，Turn 是 Runtime 的控制循环边界，完整 Run 是一次 Agent 生命周期；Span
只是把这些事实整理成便于查看的树，不会反向控制 Runtime。

### 技术追问补充

- AgentStart/End 形成运行 Span，TurnStart/End 形成 Turn Span。
- ModelRequest/Response 和 ToolExecutionStart/End 分别形成模型与工具 Span。
- 耗时按 `end - start` 计算，父节点由开始事件发生时的活动栈确定。

## 67. 模型调用和工具调用是否都应该表示为 Span？

### 口述主回答

应该，因为两者都是有明确开始、结束和耗时的关键操作。模型 Span 需要展示当时的输入、输出类型、
模型和 Token 用量；工具 Span 需要展示调用编号、工具名、错误和主动终止状态。

项目不会把 Span 当成另一套日志，而是从对应事件配对生成。这样事件仍是事实来源，Span 只负责
性能分析和层级展示。

### 技术追问补充

- 模型 Span 从请求事件开始，在响应事件结束。
- 工具 Span 通过调用编号匹配开始和结束，支持并行工具交错完成。
- 工具错误和 `terminate` 写入 Span 属性；模型用量写入模型 Span 输出。
- 当前缺失结束事件的操作不会形成一个完整闭合 Span，需要回到原始 Event 排查。

## 68. 父 Span 和子 Span 如何关联？

### 口述主回答

普通运行中，Agent Run 是根节点，Turn 在其下，模型调用和工具调用再挂到当前 Turn。父子关系在
派生 Span 时根据开始事件栈确定，不需要 Runtime 额外维护第二套层级状态。

子 Agent 的事件保存在父 `task` 工具结果详情中，合并视图通过工具调用编号找到父工具 Span，再
把子运行根 Span 挂到下面。普通 Workflow Step 则保留独立 Trace，并用轻量总览组成外层树。

### 技术追问补充

- 子 Agent 关联键是 `tool_call_id`，不是工具名或完成顺序。
- 合并时只平移子 Span 时间并重设子根节点父 ID，不修改父子 State。
- Planner、Critic 等独立 Workflow Step 通常各写一份子 Trace，外层只保存索引和摘要。

## 69. 如何通过 Trace 调试失败任务？

### 口述主回答

我会从停止原因开始，再按最后一个 Turn 反向检查：模型当时看到了什么、输出了什么工具调用、工具
是否失败、上下文是否刚发生压缩。这样可以区分模型判断错误、工具执行错误、上下文缺失和预算
耗尽，而不是只看最终回答猜原因。

Trace 可能包含任务、文件内容、命令输出和模型原始报文。当前项目提供原始报文外置和清晰的数据
边界，但没有完整自动脱敏；敏感内容仍需要在上游最小化，并限制文件权限和保留周期。

### 技术追问补充

- 模型问题查看请求、响应和对应 Assistant Message；工具问题查看开始、结束和错误结果。
- 上下文问题查看压缩事件、活跃消息数量和模型请求中的可见上下文。
- 结束原因从 `AgentEndEvent` 或 Goal 状态事件读取。
- `raw` 单独写入相邻文件只是体积隔离，不等于安全隔离。

## 70. Token 使用量如何统计？

### 口述主回答

计费和报表优先使用模型服务返回的 usage，因为 Runtime 无法用一种 Tokenizer 精确计算所有模型。
每次模型响应事件都会记录实际模型和规范化用量，成本层再按模型聚合。

压缩模型调用也会记录模型响应事件，`task` 子 Agent 的事件可以递归统计。字符估算只用于模型调用
前判断上下文大小，不应当冒充真实计费数据。

### 技术追问补充

- 用量统一为输入、输出、缓存读取和缓存写入四个桶；全零表示未知，不计为一次精确调用。
- 成本聚合只读取模型响应事件，摘要 sidecar 中的重复用量不会再次计费。
- `task` 子 Agent 通过工具结果中的子事件递归统计；独立 Workflow Step 需要由上层汇总各 State。
- 当前 Provider 重试的中间尝试没有逐次写入 Runtime Event，重试成本可能被低估。

## 71. 不同模型使用不同 Tokenizer 时怎么办？

### 口述主回答

模型调用完成后以供应商 usage 为准；调用前无法取得精确值时，Runtime 只做保守估算。项目会优先
使用最近一次可信用量作为前缀，再对新增消息按字符估算，图片使用固定的保守等价大小。

为了避免估算误差撑满窗口，还需要预留模型输出空间和安全缓冲。这个估算用于触发压缩，不用于
声称精确 Token 计费。

### 技术追问补充

- 文本缺少精确用量时按约 3.5 字符一个 Token 估算。
- Assistant 已有 `output_tokens` 时优先使用该精确值估算它再次进入上下文的大小。
- 压缩改变活跃历史后，旧的完整窗口 usage 会失效，系统改为逐消息估算。
- 模型上下文窗口来自 Provider 配置或模型元数据表。

## 72. Cached Token 和 Reasoning Token 如何统计与计费？

### 口述主回答

缓存 Token 在统一协议中单独分成缓存读取和缓存写入，价格表也分别计费。OpenAI 报告的输入量
通常已经包含缓存部分，适配器会先减掉缓存量；Anthropic 原生就是分开的，最终都转成同一种加法
口径，避免重复计算。

当前统一 `TokenUsage` 没有单独的 Reasoning Token 桶。供应商如果把推理 Token 包含在输出
Token 中，成本会跟随输出计费；更细的推理明细只能从原始响应查看，当前成本层不会单独定价。

### 技术追问补充

- 完整上下文占用是普通输入、输出、缓存读取和缓存写入之和。
- `total_tokens` 不把缓存桶混进去，因为缓存价格与普通输入不同。
- Reasoning 内容可以作为 ThinkingBlock 保存，但内容保存与 Token 计费是两条边界。

## 73. 工具结果是否计入 Input Token？

### 口述主回答

工具执行本身不产生模型 Token；但工具结果写入消息后，如果进入下一次模型请求，就会成为输入
上下文的一部分，并由下一次供应商 usage 统计。

因此要区分两种成本：工具执行的时间、外部 API 或计算成本，和工具结果带来的模型输入 Token
成本。当前 `RunCost` 只计算模型 Token 美元成本，不统计任意外部工具费用。

### 技术追问补充

- Provider 只报告整次请求的输入总量，Runtime 不会把它反向精确分摊到某条 Tool Result。
- 下一次 usage 返回前，ContextView 会对新增工具结果做字符近似估算。
- 工具耗时由 Tool Span 记录；外部服务账单需要工具或调用者另行上报。

## 74. 一个任务的最终成本如何计算？

### 口述主回答

项目从每个模型响应事件读取模型和 TokenUsage，按模型分组后使用价格表计算输入、输出和缓存读写
成本，再汇总为整次运行成本。主 Agent、压缩模型以及 `task` 子 Agent 都可以进入这个聚合。

Planner、Reflection 等独立 Workflow Step 拥有不同 State，需要上层对各步骤成本去重后相加。
未知模型仍记录调用次数和 Token，但美元成本标记为未定价，因此总金额只是下界。

### 技术追问补充

- `RunCost.from_run()` 可递归读取 `task` 工具保存的子事件，避免漏掉嵌套子 Agent。
- Workflow 汇总应按不同 State 去重，避免 Goal Loop 多个 Step 共享同一 State 时重复计算。
- 当前重试尝试没有完整事件，无法保证把所有重试成本计入。
- Trace 保存模型和 usage，不冻结历史价格；需要可审计账单时应额外保存当时价格表版本。

## 核对依据

- [`messages.py`](../../src/simple_long_horizon_agent/messages.py)
- [`model_metadata.py`](../../src/simple_long_horizon_agent/model_metadata.py)
- [`trace/jsonl.py`](../../src/simple_long_horizon_agent/trace/jsonl.py)
- [`trace/run_trace.py`](../../src/simple_long_horizon_agent/trace/run_trace.py)
- [`trace/spans.py`](../../src/simple_long_horizon_agent/trace/spans.py)
- [`trace/live.py`](../../src/simple_long_horizon_agent/trace/live.py)
- [`workflow/trace.py`](../../src/simple_long_horizon_agent/workflow/trace.py)
- [`09-trace-and-observability.md`](../design/09-trace-and-observability.md)
- [`test_live_trace.py`](../../tests/unit/test_live_trace.py)
- [`test_model_metadata.py`](../../tests/unit/test_model_metadata.py)
- [`test_trace_fixture_golden.py`](../../tests/unit/test_trace_fixture_golden.py)
