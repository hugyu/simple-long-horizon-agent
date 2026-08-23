# 06. Observability 与 Cost

本文对应 [`question-checklist.md`](question-checklist.md) 中第六部分的问题。每道题包含可直接
口述的主回答；存在清单子问题时，保留追问原文和简短回答；最后记录整组问题对应的仓库实现。

## 64. 为什么使用 JSONL 保存 Trace？

### 口述主回答

因为 Agent 运行时间长，事件需要边运行边落盘。JSONL 一行保存一个完整记录，首次写入 Header，
之后只追加新 Event，Viewer 可以实时读取已经完成的前缀；进程中断时也只需要忽略不完整的尾行。

相比单个大 JSON，它不需要每次重写整个数组。代价是查询和关联能力弱，不适合多写者并发、复杂
检索和事务场景；如果进入生产规模，数据库或日志系统更适合作为集中存储。

### 追问问题与回答

**追问：与单个 JSON、数据库或日志系统相比，JSONL 解决了什么问题，又有哪些限制？**

它适合追加、实时读取和中断后恢复完整前缀；但查询、索引、多写者并发和事务能力弱于数据库或
日志平台。

### 技术追问补充

- `event_stream()` 生成一条 Trace Header 和按 `state.events` 顺序排列的 Event records；主文件中
  不重复保存 messages、spans 和 cost。
- `IncrementalTraceWriter` 首次原子写 Header，之后只 append 尚未写入的完整 Event 行；没有新
  Event 时不写文件。
- 最终 `write_event_stream()` 使用临时文件和原子替换重建规范文件；Provider raw 写入相邻
  `*.raw.jsonl`。
- `read_jsonl()` 使用增量 JSON 解码，遇到无法解析的尾部时停止，已完整读取的前缀仍然保留。

## 65. Trace Schema 是什么样的？

### 口述主回答

当前持久化版本是 `simple-long-horizon-agent.trajectory.v5`。Header 保存格式版本、Trace ID、
生产者、任务摘要和元数据；每条 Event 保存运行内顺序、相对时间、全局唯一 ID、事件类型以及
对应业务字段。

消息、Span、模型轮次和成本不在 Header 中重复保存，而是从事件流派生，避免多份事实不一致。
Schema 变化时必须同步更新 Writer、Reader、Viewer 和固定测试样例。

### 追问问题与回答

**追问：一条 Trace Event 至少需要哪些身份、时间、类型和载荷字段？**

至少需要运行内 index、相对时间 elapsed、全局 UUID、事件 kind，以及该事件自己的领域字段。

**追问：Schema 如何版本化并保持向后兼容？**

文件 Header 使用明确版本号；变更时同步更新序列化器、Reader、Viewer 和 Golden Fixture。当前
主要依赖兼容读取与测试，没有通用迁移框架。

### 技术追问补充

- `SCHEMA` 当前为 `simple-long-horizon-agent.trajectory.v5`；Header 还包含 type、trace_id、
  producer、任务预览和 meta。
- `event_record()` 将 dataclass 转为 JSON-safe 字典，并移除可重建且体积持续增长的
  `llm_payload`。
- Event 的 index 表示追加顺序，elapsed 是单调运行相对时间，UUID 用于跨文件和派生视图引用。
- `test_trace_fixture_golden` 从真实 Event 类型生成 Viewer 样例，字段变化未同步时测试会失败。

## 66. 什么是 Span？

### 口述主回答

Span 是从开始和结束 Event 派生出的一个时间区间，用来表示一次 Agent 运行、一个 Turn、一次模型
调用、一次工具调用或一次上下文压缩持续了多久，以及它属于哪个父操作。

Event 是原始运行事实，Turn 是 Runtime 的控制循环边界，完整 Run 是一次 Agent 生命周期；Span
只是把这些事实整理成便于查看的树，不会反向控制 Runtime。

### 追问问题与回答

**追问：Span 与 Runtime Event、Turn 和完整 Run 分别是什么关系？**

Event 是原始事实；Turn 和 Run 是 Runtime 的生命周期边界；Span 把对应的开始与结束 Event 配成
带父子关系的时间区间。

### 技术追问补充

- `spans_from_events()` 是纯派生函数，通过栈跟踪尚未结束的 Agent Run、Turn 和 Model Call。
- AgentStart/End 形成 `agent_run`，TurnStart/End 形成 `turn`，ModelRequest/Response 形成
  `model_call`，工具开始/结束形成 `tool_call`。
- Span 保存 id、parent_id、start、end、输入、输出和属性；耗时由 `end - start` 计算。
- Span 不写回 State，Viewer 对 Span 的筛选和展示不会改变 Agent 运行。

## 67. 模型调用和工具调用是否都应该表示为 Span？

### 口述主回答

应该，因为两者都是有明确开始、结束和耗时的关键操作。模型 Span 需要展示当时的输入、输出类型、
模型和 Token 用量；工具 Span 需要展示调用编号、工具名、错误和主动终止状态。

项目不会把 Span 当成另一套日志，而是从对应事件配对生成。这样事件仍是事实来源，Span 只负责
性能分析和层级展示。

### 追问问题与回答

**追问：不同类型 Span 的开始、结束、错误和耗时如何定义？**

模型 Span 由请求和响应事件配对；工具 Span 由同一调用编号的开始和结束事件配对。错误写入属性，
耗时统一由结束时间减开始时间。

### 技术追问补充

- ModelRequestEvent 入栈时保存规范模型输入和请求属性，ModelResponseEvent 出栈时补充输出类型、
  模型和 usage。
- 并行工具的 Event 可以交错，提取器按 `tool_call_id` 在栈中反向查找对应工具开始记录。
- ToolExecutionEndEvent 的 `is_error` 和 `terminate` 写入 Tool Span 属性；Turn Span 记录
  `terminated`。
- 当前只有开始而没有匹配结束的操作不会生成完整 Span，原始 Event Stream 仍保留该异常事实。

## 68. 父 Span 和子 Span 如何关联？

### 口述主回答

普通运行中，Agent Run 是根节点，Turn 在其下，模型调用和工具调用再挂到当前 Turn。父子关系在
派生 Span 时根据开始事件栈确定，不需要 Runtime 额外维护第二套层级状态。

子 Agent 的事件保存在父 `task` 工具结果详情中，合并视图通过工具调用编号找到父工具 Span，再
把子运行根 Span 挂到下面。普通 Workflow Step 则保留独立 Trace，并用轻量总览组成外层树。

### 追问问题与回答

**追问：Sub-Agent、Workflow Step、Model Call 和 Tool Call 如何形成层级 Trace？**

Run 包含 Turn，Turn 包含模型和工具调用；task 工具对应的子 Agent Run 挂在父 Tool Span 下；
独立 Workflow Step 保留自己的 Trace，由外层总览引用。

### 技术追问补充

- `task_tool` 将子 State Event 列表保存在父 Tool Result 的
  `sidecar.details[tool_call_id].sub_events`。
- `merge_sub_agent_spans()` 先生成父 Span，再通过 tool_call_id 找到父 Tool Span，并单独派生子
  Span。
- 子 Span 时间加上父 Tool Span 起点；只把子根 Span 的 parent_id 改为父 Tool Span，子树内部
  关系保持不变。
- Workflow Trace helper 为每个不同 Step State 写独立子 Trace，外层组合 State 只保存步骤索引、
  摘要和子 Trace 路径。

## 69. 如何通过 Trace 调试失败任务？

### 口述主回答

我会从停止原因开始，再按最后一个 Turn 反向检查：模型当时看到了什么、输出了什么工具调用、工具
是否失败、上下文是否刚发生压缩。这样可以区分模型判断错误、工具执行错误、上下文缺失和预算
耗尽，而不是只看最终回答猜原因。

Trace 可能包含任务、文件内容、命令输出和模型原始报文。当前项目提供原始报文外置和清晰的数据
边界，但没有完整自动脱敏；敏感内容仍需要在上游最小化，并限制文件权限和保留周期。

### 追问问题与回答

**追问：如何定位模型决策错误、工具失败、上下文缺失和错误停止原因？**

先看停止事件，再回到最后一个 Turn：核对模型请求与响应、工具开始结束和错误结果，以及压缩事件
和当时可见上下文。

**追问：密钥、用户数据和 Provider 原始响应等敏感信息如何避免进入 Trace？**

优先在采集前避免写入，必要字段在序列化时脱敏，并限制 raw 文件权限与保留期。当前 raw 外置只
解决体积，不等于脱敏。

### 技术追问补充

- 模型决策可通过 ModelRequestEvent 的可见消息统计和请求投影、ModelResponseEvent 与对应
  Assistant Message 对照；持久化 v5 中完整 Provider Wire 需要按 raw_ref 读取 sidecar。
- 工具失败通过 ToolExecutionStart/End、`is_error` 和 ToolResult Message 定位；上下文问题通过
  ContextCompressionEvent 的原索引、新活跃索引和前后 Token 定位。
- `AgentEndEvent.reason` 区分 done、max_turns、tool_terminate 和 abort；GoalStatusEvent 记录外层
  complete、blocked、budget_exhausted 和 aborted。
- 当前 Trace Writer 没有通用自动脱敏；任务、工具正文、details 和 raw 都可能含敏感数据，raw
  外置只减少主文件体积。

## 70. Token 使用量如何统计？

### 口述主回答

计费和报表优先使用模型服务返回的 usage，因为 Runtime 无法用一种 Tokenizer 精确计算所有模型。
每次模型响应事件都会记录实际模型和规范化用量，成本层再按模型聚合。

压缩模型调用也会记录模型响应事件，`task` 子 Agent 的事件可以递归统计。字符估算只用于模型调用
前判断上下文大小，不应当冒充真实计费数据。

### 追问问题与回答

**追问：应该优先使用 Provider 返回的 usage，还是在 Runtime 中自行估算？**

计费和报表优先使用 Provider usage；Runtime 估算只用于调用前判断上下文大小和触发压缩。

**追问：Model Call、压缩调用和 Sub-Agent 调用如何避免漏算或重复计算？**

统一从每次 `ModelResponseEvent` 计数；压缩调用也记录正式事件，task 子 Agent 递归读取子事件，
同一 usage 的 sidecar 副本不再次计费。

### 技术追问补充

- Adapter 将一次调用的 usage 规范化为 `TokenUsage`，同时写入 AssistantMessage 和
  `ModelResponseEvent`；成本聚合以 Event 上的 model/usage 为正式来源。
- 全零 TokenUsage 表示 Provider 未提供可信用量，`RunCost` 会跳过，避免把未知调用误报成精确零。
- `SummarizeStrategy` 将 compressor 的 ModelRequest/Response Event 写入主 State；摘要 sidecar
  中的 usage 只是就近证据，不会再次聚合。
- `RunCost.from_run()` 会递归读取 task Tool Result 中的 `sub_events`；独立 Workflow Step 和当前
  未逐次记录的 Provider 重试需要上层另行汇总，可能存在低估。

## 71. 不同模型使用不同 Tokenizer 时怎么办？

### 口述主回答

模型调用完成后以供应商 usage 为准；调用前无法取得精确值时，Runtime 只做保守估算。项目会优先
使用最近一次可信用量作为前缀，再对新增消息按字符估算，图片使用固定的保守等价大小。

为了避免估算误差撑满窗口，还需要预留模型输出空间和安全缓冲。这个估算用于触发压缩，不用于
声称精确 Token 计费。

### 追问问题与回答

**追问：Runtime 如何在精确计数不可用时估算 Context 大小并保留安全余量？**

优先使用最近一次可信 usage 作为历史基线，再估算新增消息；没有可用基线时按消息字符估算，并从
模型窗口中预留输出空间和安全缓冲。

### 技术追问补充

- `estimate_message_tokens()` 对带可信 usage 的 AssistantMessage 使用其 `output_tokens`；其他文本
  按 `ceil(可见字符数 / 3.5)` 估算，图片使用固定等价字符数。
- `estimate_context_tokens()` 会寻找最近一条可信 Assistant usage，并只估算其后的新增消息。
- 压缩改变活跃视图后，旧 usage 仍包含已移除历史，`_active_context_tokens()` 会禁用该基线并
  重新逐消息求和。
- `effective_token_budget()` 使用 `context_window - output_reserve - safety_buffer`；模型窗口来自
  Provider 配置或模型元数据表。

## 72. Cached Token 和 Reasoning Token 如何统计与计费？

### 口述主回答

缓存 Token 在统一协议中单独分成缓存读取和缓存写入，价格表也分别计费。OpenAI 报告的输入量
通常已经包含缓存部分，适配器会先减掉缓存量；Anthropic 原生就是分开的，最终都转成同一种加法
口径，避免重复计算。

当前统一 `TokenUsage` 没有单独的 Reasoning Token 桶。供应商如果把推理 Token 包含在输出
Token 中，成本会跟随输出计费；更细的推理明细只能从原始响应查看，当前成本层不会单独定价。

### 追问问题与回答

**追问：不同 Provider 的 usage 字段不一致时，统一协议应该如何表达？**

适配器统一成普通输入、输出、缓存读取和缓存写入四个桶；供应商特有的推理明细保留在 raw，不
假装成跨 Provider 都一致的字段。

### 技术追问补充

- `TokenUsage` 字段为 `input_tokens`、`output_tokens`、`cache_read_tokens` 和
  `cache_write_tokens`；`context_tokens` 是四项之和。
- OpenAI 报告的输入总量包含缓存子集，Adapter 通过 `from_inclusive_input()` 减掉缓存后转为
  加法口径；Anthropic 原生缓存字段直接映射。
- `PriceBook` 为普通输入、输出、缓存读和缓存写分别保存每百万 Token 价格。
- 当前没有 `reasoning_tokens` 正式字段；ThinkingBlock 保存推理内容，raw 可保留供应商推理用量
  明细，但成本层不单独聚合。

## 73. 工具结果是否计入 Input Token？

### 口述主回答

工具执行本身不产生模型 Token；但工具结果写入消息后，如果进入下一次模型请求，就会成为输入
上下文的一部分，并由下一次供应商 usage 统计。

因此要区分两种成本：工具执行的时间、外部 API 或计算成本，和工具结果带来的模型输入 Token
成本。当前 `RunCost` 只计算模型 Token 美元成本，不统计任意外部工具费用。

### 追问问题与回答

**追问：Tool 自身执行成本与 Tool Result 进入后续模型请求产生的 Token 成本如何区分？**

工具执行成本属于外部资源或服务账单；Tool Result 只有在进入下一次模型请求后，才作为输入 Token
计费。两者需要分开统计。

### 技术追问补充

- ToolResult content 被包装进 `kind="tool_result"` UserMessage；下一轮 Bridge 将它投影进模型
  请求，因此其文本和图片占用输入窗口。
- Provider 只报告整次请求输入总量，Runtime 不会把精确 input_tokens 反向拆分到单条 Tool Result。
- 下一次 Provider usage 返回前，ContextView 按 Tool Result 可见正文、调用 ID、工具名和图片
  等价大小做近似估算。
- Tool Span 只记录耗时和错误；当前没有统一外部 API 费用、CPU 时间或存储成本字段。

## 74. 一个任务的最终成本如何计算？

### 口述主回答

项目从每个模型响应事件读取模型和 TokenUsage，按模型分组后使用价格表计算输入、输出和缓存读写
成本，再汇总为整次运行成本。主 Agent、压缩模型以及 `task` 子 Agent 都可以进入这个聚合。

Planner、Reflection 等独立 Workflow Step 拥有不同 State，需要上层对各步骤成本去重后相加。
未知模型仍记录调用次数和 Token，但美元成本标记为未定价，因此总金额只是下界。

### 追问问题与回答

**追问：如何汇总主 Agent、Sub-Agent、Planner、Reflection、Compact 和重试产生的成本？**

主 Agent 和 Compact 从同一 Event Stream 聚合，task 子 Agent 递归读取子事件；Planner 和
Reflection 的独立 State 由 Workflow 去重后相加。未记录的重试尝试当前无法完整汇总。

**追问：模型价格变化或未知价格时如何记录和展示？**

Trace 保留模型和 Token；价格由当前 PriceBook 计算。未知模型标记为 unpriced，总美元成本按下界
展示；严格审计还需要保存运行时价格表版本。

### 技术追问补充

- `RunCost.from_run()` 遍历 `model_response` Event，并递归读取 Tool Result
  `details[call_id].sub_events`，可覆盖多层 task 子 Agent。
- `workflow_steps_breakdown()` 通过 State 对象身份去重输出 Token，避免 Goal Loop 多个 Step 共享
  同一 State 时重复统计；完整美元成本仍需对各独立 State 调用 RunCost 后求和。
- 未找到价格的模型仍生成 ModelCost 和 Token 汇总，美元字段为 0，并加入
  `unpriced_models`，因此总额明确是下界。
- Trace 保存模型与 usage，不保存不可变价格快照；Provider 重试的中间尝试也没有完整 Event，
  当前无法生成严格账单级总成本。

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
