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

我把 Trace Schema 分成四层。第一层是文件头，标识格式版本、一次运行的 `trace_id`、生产者、任务
信息和元数据；第二层是每条事件共有的信封，包括运行内顺序 `index`、相对时间 `elapsed`、事件
`uuid` 和类型 `kind`；第三层是按事件类型区分的业务载荷，例如模型用量、工具调用编号或压缩后的
活跃索引；第四层是外置的大对象，供应商原始请求和响应放在相邻文件中，主事件只保存 `raw_ref`。

当前版本是 `simple-long-horizon-agent.trajectory.v5`，主文件采用“一个文件头加多条追加式事件”的
JSONL。Message、Span、模型轮次和成本不重复持久化，而是从 Event 派生；这样 Runtime 事实只有一份，
Viewer 和成本分析即使重新计算，也不会与事件流产生两套真相。

版本兼容上，我会保持公共事件信封稳定。新增可选字段时，旧 Reader 应忽略未知字段并使用默认值；
删除字段、改变语义或调整外置引用方式时必须提升版本。Reader 按版本先把旧记录转换成当前内存模型，
Writer 只写最新版本；无法识别的版本应明确拒绝或降级展示，不能悄悄按新语义解释旧数据。

### 追问问题与回答

**追问：一条 Trace Event 至少需要哪些身份、时间、类型和载荷字段？**

至少需要运行内 `index`、相对时间 `elapsed`、稳定 `uuid`、事件类型 `kind`，以及该类型所需的
业务字段；跨事件关联再由 `tool_call_id`、Agent 名称或消息索引等领域标识完成。

**追问：Schema 如何版本化并保持向后兼容？**

文件头保存明确版本。新增可选字段尽量保持旧 Reader 可忽略；破坏性变化提升版本，并由版本化
Reader 转换成当前内存结构。每个受支持版本都保留固定样例和 Viewer 契约测试。

### 技术追问补充

- `SCHEMA` 当前为 `simple-long-horizon-agent.trajectory.v5`；Header 还包含 type、trace_id、
  producer、任务文本和 meta，不包含不断增长的数组。
- `event_record()` 将 dataclass 转为 JSON-safe 字典，并移除可重建且体积持续增长的
  `llm_payload`。
- Event 的 `index` 表示追加顺序，`elapsed` 是单调运行相对时间，`uuid` 用于稳定引用；具体因果
  关系由事件载荷中的 Agent、调用编号和消息索引表达。
- `split_raw_from_record()` 把消息 sidecar 中的供应商原始请求和响应移入相邻 `*.raw.jsonl`，
  主事件只保留 `{raw_ref: n}`；读取方按需解析，缺少原始池时仍应能够查看基础事件。
- 当前 `read_jsonl()` 只负责增量解析完整 JSON 记录，遇到损坏尾部会保留可解析前缀；它不是版本
  迁移器，也不会把旧版本自动转换成 v5。
- 可以增加按版本注册的 Reader 和逐版本迁移函数，统一输出当前的内存 Event 结构；未知事件类型
  可以保留原始载荷并降级显示，但不能参与依赖其语义的成本或 Span 计算。
- 同一文件中的 Header、事件和原始数据引用必须属于同一版本；迁移时要么原子生成完整新文件和
  sidecar，要么继续只读旧文件，不能原地改写一半。
- `test_trace_fixture_golden` 当前保护 v5 生产端与 Viewer 样例一致，前端还有契约测试。扩展兼容期
  后应为每个仍受支持的旧版本保留读取样例，并测试迁移前后关键事件数量、顺序和引用不变。

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

当前项目有三种关联方式。单个 State 内，Span 提取器按事件顺序维护尚未结束的操作栈，所以
Agent Run 包含 Turn，Turn 再包含模型调用和工具调用；并行工具则通过 `tool_call_id` 配对开始和
结束事件。子 Agent 通过父 `task` 调用编号找到对应工具 Span，再把子运行根节点挂到下面。Workflow
的各步骤保留独立 Trace，外层总览通过子 Trace 路径引用它们。

这种方式适合当前单进程实现，但 Span 编号和父子关系主要在读取时派生。若扩展到并行 Workflow 或
跨进程执行，我会在创建操作时显式传递一份 Trace Context，至少包含整条调用链共享的 `trace_id`、
当前 `span_id` 和 `parent_span_id`。创建子 Agent 时，把父 `task` 工具 Span 作为父节点；创建
Workflow Step 时，把当前 Workflow Span 作为父节点；模型和工具调用则继承当前 Turn Span。

这样父子关系由稳定标识确定，不依赖事件恰好相邻或不同机器的时钟一致。每个进程仍用自己的单调
时间计算耗时，时间只用于展示；因果关系只看 Span 标识。子进程失败、Trace 文件晚到或结束事件
缺失时，仍能保留父节点和未完成状态，而不是把整棵子树丢掉。

### 追问问题与回答

**追问：Sub-Agent、Workflow Step、Model Call 和 Tool Call 如何形成层级 Trace？**

根 Run 包含 Turn，Turn 包含模型和工具调用；子 Agent Run 的父节点是发起委派的 `task` 工具
Span；Workflow Step 的父节点是对应的 Workflow Span。跨 State 时通过传递
`trace_id/span_id/parent_span_id` 保持关联。

### 技术追问补充

- `task_tool` 将子 State Event 列表保存在父 Tool Result 的
  `sidecar.details[tool_call_id].sub_events`。
- `merge_sub_agent_spans()` 先生成父 Span，再通过 tool_call_id 找到父 Tool Span，并单独派生子
  Span。
- 子 Span 时间加上父 Tool Span 起点；只把子根 Span 的 parent_id 改为父 Tool Span，子树内部
  关系保持不变。
- Workflow Trace helper 为每个不同 Step State 写独立子 Trace，外层组合 State 只保存步骤索引、
  摘要和子 Trace 路径。
- 当前 Span ID 由 `spans_from_events()` 根据 `trace_id`、Span 类型和读取顺序生成，不是 Runtime
  事件中已经存在的稳定操作编号；Workflow 子 Trace 也主要依赖文件路径关联。
- 可以增加可选 `TraceContext`，由 Workflow、`task` 工具和 Runtime 在创建子操作时传递。开始与
  结束事件携带同一 `span_id`，子操作携带父 `span_id`，读取方只负责组装，不再猜测父节点。
- 并行工具调用各自拥有独立 Span ID，完成顺序不会改变父子关系；`tool_call_id` 继续负责工具协议
  配对，可以同时作为调试属性，但不再承担跨系统唯一关联的全部职责。
- 子 Agent 使用父级 `trace_id`，但保留自己的 State 和事件 `index`；因此事件顺序只在各 State
  内有意义，跨 State 排序不能比较本地 index。
- 跨进程时每个运行使用本地单调时间记录时长，并可额外保存墙钟起点用于大致对齐。父子关系不能
  通过时间包含关系推断，避免时钟偏差破坏 Trace 树。
- 只有开始事件没有结束事件时，可生成 `incomplete` Span，并保留最后事件时间和停止原因；当前
  提取器只生成完整配对 Span，原始 Event Stream 虽保留异常，但 Viewer 中可能缺少对应节点。
- 迁移期间 Reader 可以优先使用显式 Trace Context，旧轨迹则继续使用事件栈、
  `tool_call_id` 和子 Trace 路径派生，保持向后兼容。

## 69. 如何通过 Trace 调试失败任务？

### 口述主回答

我调试 Trace 时不会只盯着最后一个 Turn，而是先明确“哪个验收条件失败”，再沿因果链回溯到首次
偏离。第一步看 `GoalStatusEvent` 和 `AgentEndEvent`，确认任务是模型声明完成、验证失败、预算耗尽、
工具要求停止还是被中止；如果本身就是控制预算导致结束，就不必先怀疑模型推理。

然后从失败证据向前找产生它的工具结果、工具调用和模型请求：如果关键事实根本没有进入当时的
Context，是可见性、压缩或 Recall 问题；事实已经在 Context 中但模型选择了错误动作，是模型决策
或提示问题；调用参数合理但执行报错，是工具或基础设施问题；工具结果正确但没有被模型使用，则
检查结果转换、调用编号配对和下一轮上下文；所有执行都正确但仍错误停止，则看 Workflow 和完成
检查。

最后我会用相同任务、配置和环境复现，或者与成功 Trace 对照，验证这个“首次偏离”是否稳定出现。
修复后也要重新运行原失败路径，而不是只根据 Viewer 中的解释判断问题已经解决。

### 追问问题与回答

**追问：如何定位模型决策错误、工具失败、上下文缺失和错误停止原因？**

从失败验收项沿“停止状态 → 最后影响它的结果 → 工具调用 → 产生调用的模型请求 → 当时可见
Context”回溯。证据缺失是上下文问题，证据存在但选择错误是模型问题，执行失败是工具问题，错误
完成则是 Workflow 或验证问题。

**追问：密钥、用户数据和 Provider 原始响应等敏感信息如何避免进入 Trace？**

先减少采集，Secret 和无关用户数据不进入 Message 或 Tool Result；序列化前再按结构化字段脱敏；
原始供应商报文默认关闭或进入独立加密存储，并设置更严格的访问权限和保留期。脱敏失败时高敏环境
应拒绝落盘，`raw_ref` 外置本身不等于安全处理。

### 技术追问补充

- `AgentEndEvent.reason` 区分 done、max_turns、tool_terminate 和 abort；`GoalStatusEvent` 记录
  complete、blocked、budget_exhausted 和 aborted，调试入口应先确认这两个停止层级。
- 内存 `ModelRequestEvent` 带当时的 `llm_payload`、ContextView 统计和工具声明；v5 持久化会删除
  可重建的 `llm_payload`，需要从 Message Event 和 Agent 提示重建，或按 `raw_ref` 查看供应商报文。
- 模型响应应同时核对 `ModelResponseEvent` 和随后写入的 Assistant Message，区分供应商返回、
  Adapter 转换和 Runtime 路由三个边界。
- 工具问题通过 ToolExecutionStart/Update/End、`is_error` 和 Tool Result Message 定位；还要核对
  Tool Call 与 Result 的调用编号是否一致，以及并行完成顺序是否影响了模型可见顺序。
- 上下文问题通过 `ContextCompressionEvent` 的压缩前后 Token、原索引和新活跃索引定位，并结合
  ModelRequest 时的可见消息确认关键证据是在压缩前丢失、被过滤，还是摘要遗漏。
- 可以增加只读诊断器，在 Trace 上检查开始和结束事件不配对、悬空 Tool Call/Result、非法活跃索引、
  子 Trace 引用缺失和完成声明缺少证据等不变量，并输出涉及的 Event index；诊断结果不能回写
  Runtime。
- 自动诊断应给出“最早可疑事件”和支持证据，而不是直接声称根因；最终仍通过重放、对照运行或原
  失败用例验证。
- Trace 重放默认只能重建状态和观察决策，不能安全地重新执行有副作用的工具；复现应在隔离环境中
  使用相同模型配置、工具版本、工作区基线和预算。
- 当前 Writer 没有通用脱敏。任务、消息、工具参数、结果正文、details 和 raw 都可能含敏感数据；
  `raw_ref` 只解决体积。扩展应在采集、序列化和存储三层分别实施最小化、结构化脱敏、加密访问控制
  和分类保留策略。

## 70. Token 使用量如何统计？

### 口述主回答

Token 统计应以“每次真正发给模型服务的请求尝试”为最小单位，而不是按 Agent Turn 或最终消息
计数。每次尝试都应有稳定调用编号，记录 Agent、模型、用途、尝试次数和供应商返回的 usage；计费
以供应商实测为准，调用前估算只能标记为 estimate，不能混入实际账单。

项目当前把成功调用的模型和 `TokenUsage` 写入 `ModelResponseEvent`，成本层只从这个 Event 聚合；
AssistantMessage 和摘要 sidecar 中的副本不再计数。压缩模型调用也会生成正式响应事件，`task`
子 Agent 则递归读取子事件，所以主 Agent、压缩和递归委派可以沿同一条规则统计。

如果要做到严格账单级统计，还需要把供应商重试和工具调用修复的每次真实请求都记录为独立尝试，
包括失败但可能已计费的请求。跨 Workflow 汇总时按稳定调用编号去重，而不是只按 State 或文件路径
猜测是否重复；缺少 usage 的调用保留为 unknown，不能当成零成本。

### 追问问题与回答

**追问：应该优先使用 Provider 返回的 usage，还是在 Runtime 中自行估算？**

计费、配额结算和报表使用供应商 usage；Runtime 估算只用于调用前预算、窗口保护和异常情况下的
参考值，并且必须明确标记为估算。

**追问：Model Call、压缩调用和 Sub-Agent 调用如何避免漏算或重复计算？**

每次真实供应商尝试分配唯一调用编号并只生成一条计量记录；压缩和子 Agent 继承同一 Trace Context，
汇总时递归遍历全部记录并按调用编号去重。Message 和 sidecar 只作为证据副本，不参与加总。

### 技术追问补充

- Adapter 将一次调用的 usage 规范化为 `TokenUsage`，同时写入 AssistantMessage 和
  `ModelResponseEvent`；成本聚合以 Event 上的 model/usage 为正式来源。
- 全零 TokenUsage 表示 Provider 未提供可信用量，`RunCost` 会跳过，避免把未知调用误报成精确零。
- `SummarizeStrategy` 将 compressor 的 ModelRequest/Response Event 写入主 State；摘要 sidecar
  中的 usage 只是就近证据，不会再次聚合。
- `RunCost.from_run()` 会递归读取 task Tool Result 中的 `sub_events`；独立 Workflow Step 和当前
  未逐次记录的 Provider 重试需要上层另行汇总，可能存在低估。
- 可以给每次供应商请求增加 `model_call_id` 和 `attempt_id`，并记录用途，例如主推理、压缩、
  Judge、规划或重试。请求开始、成功、失败和 usage 更新都引用同一编号。
- 供应商限流重试和无效工具调用修复当前发生在模型访问层，中间尝试没有完整 Event。扩展后即使
  最终失败，也要记录尝试状态；供应商未返回 usage 时标记 unknown。
- Workflow 汇总应收集所有独立 State 的计量记录，并按 `model_call_id + attempt_id` 去重；共享
  State 的多次 Step 引用不能重复计费。
- 可以定期把内部汇总与供应商账单对账，识别供应商未返回 usage、网络失败后仍计费或价格映射错误，
  但外部账单属于最终结算证据，不反向修改原始 Trace。

## 71. 不同模型使用不同 Tokenizer 时怎么办？

### 口述主回答

不同模型的 Tokenizer 不一致，所以调用前计数应该是分层降级，而不是依赖一个全局字符比例。优先
使用模型 Adapter 提供的精确计数器，并对最终序列化请求计数，包括系统提示、工具声明、消息和图片；
没有精确计数器时使用模型族估算器，最后才退化到保守字符估算。

项目当前的做法是利用最近一次可信供应商 usage 作为历史基线，只估算之后新增的消息；没有基线时
按可见字符和图片等价大小估算。压缩刚改变活跃上下文后，旧 usage 已经包含被移除内容，因此会放弃
旧基线并重新逐消息估算。

不论使用哪种估算器，都要从上下文窗口中预留本轮最大输出和安全余量。调用结束后再比较预估输入与
供应商实测，按模型和内容类型统计误差并调整安全余量。未知模型或误差波动较大时使用更大的余量，
宁可提前压缩，也不能把估算结果当作精确上限。

### 追问问题与回答

**追问：Runtime 如何在精确计数不可用时估算 Context 大小并保留安全余量？**

依次使用模型精确计数器、最近一次供应商 usage 加新增消息估算、模型族估算器和保守字符估算；最终
可用输入预算还要减去最大输出空间和根据历史误差计算的安全余量。

### 技术追问补充

- `estimate_message_tokens()` 对带可信 usage 的 AssistantMessage 使用其 `output_tokens`；其他文本
  按 `ceil(可见字符数 / 3.5)` 估算，图片使用固定等价字符数。
- `estimate_context_tokens()` 会寻找最近一条可信 Assistant usage，并只估算其后的新增消息。
- 压缩改变活跃视图后，旧 usage 仍包含已移除历史，`_active_context_tokens()` 会禁用该基线并
  重新逐消息求和。
- `effective_token_budget()` 使用 `context_window - output_reserve - safety_buffer`；模型窗口来自
  Provider 配置或模型元数据表。
- 当前估算主要针对模型可见消息，没有通过每个 Provider 的真实序列化器精确计算系统字段、工具
  Schema 和图片计费规则；安全缓冲承担了这部分误差。
- 可以在 Adapter 边界增加 `count_request_tokens()`，输入与真实请求使用同一规范对象；不支持本地
  Tokenizer 的模型返回 unknown，由上层继续降级。
- 每次 `ModelResponseEvent` 可同时记录调用前估算值，观测层按模型、语言、代码、JSON、图片等内容
  类型计算高分位误差，并据此调整安全缓冲。
- 估算器版本和误差策略应进入 Trace 元数据，便于解释为什么某次运行提前压缩或仍然超过窗口。

## 72. Cached Token 和 Reasoning Token 如何统计与计费？

### 口述主回答

这里最重要的是区分“上下文占用”和“计费分类”。缓存读取、缓存写入和普通输入共同构成输入窗口，
但单价可能不同；适配器必须先确认供应商字段是总量还是子集，再转换成互斥的计费桶，不能把缓存
Token 既算进普通输入又单独加一次。

推理 Token 更复杂，因为有的供应商把它包含在输出总量中，有的只提供额外明细，未来也可能单独
计价。我不会直接把 reasoning token 加到 output token 上，而是在统一 usage 中同时记录数值和
包含关系：它是输出的子集、独立计费桶，还是仅供分析的供应商明细。成本层根据该模型当时的计费
规则生成互斥金额。

当前项目只有普通输入、输出、缓存读取和缓存写入四个正式字段；OpenAI 的缓存子集会先从总输入中
扣除，Anthropic 的独立字段直接映射。推理 Token 尚未进入统一协议，因此当前只能跟随供应商输出
总量计费，无法单独分析推理成本。

### 追问问题与回答

**追问：不同 Provider 的 usage 字段不一致时，统一协议应该如何表达？**

统一协议保存跨供应商都有明确语义的互斥计费桶；供应商特有字段放入带命名空间的明细，并明确它
是否已经包含在某个总量中。不能为了字段统一而丢失包含关系。

### 技术追问补充

- `TokenUsage` 字段为 `input_tokens`、`output_tokens`、`cache_read_tokens` 和
  `cache_write_tokens`；`context_tokens` 是四项之和。
- OpenAI 报告的输入总量包含缓存子集，Adapter 通过 `from_inclusive_input()` 减掉缓存后转为
  加法口径；Anthropic 原生缓存字段直接映射。
- `PriceBook` 为普通输入、输出、缓存读和缓存写分别保存每百万 Token 价格。
- 当前没有 `reasoning_tokens` 正式字段；ThinkingBlock 保存推理内容，raw 可保留供应商推理用量
  明细，但成本层不单独聚合。
- 扩展后的 usage 可以增加 `reasoning_tokens`、`visible_output_tokens` 和供应商明细，同时记录
  reasoning 是否包含在 `output_tokens` 中；总成本只能从互斥的计费桶相加。
- 未来上下文估算应使用真正会在后续请求中重发的可见输出 Token，而不是盲目使用可能包含隐藏推理
  的总输出量；当前 `estimate_message_tokens()` 使用 `output_tokens`，在部分模型上可能偏保守。
- 价格表也需要声明每个桶的计费语义。若某供应商把推理包含在输出单价中，reasoning 只做分析字段；
  若独立收费，再单独增加价格和金额，不能同时采用两种算法。
- 原始供应商字段、适配器映射版本和规范化结果应同时可审计，便于发现 API 字段语义变化导致的
  重复计费或漏算。

## 73. 工具结果是否计入 Input Token？

### 口述主回答

工具结果只有实际进入后续模型请求时，才计入该次请求的输入 Token。工具执行完成但结果在下一次
调用前被过滤、压缩或任务已经终止，就不会以原始形态产生模型输入费用。供应商最终只报告整次请求
的输入总量，因此这个总量是权威账单，不能精确反推某一条工具结果用了多少 Token。

如果需要分析哪个工具导致上下文膨胀，我会在构建模型请求时记录每条输入消息的来源索引和估算
Token，再把供应商实测总量作为校准上限。单条 Tool Result 的贡献属于归因估算，不应和供应商实测
总额相加。

另外要把工具自身成本分开记账。数据库查询、搜索 API、容器 CPU、存储和网络费用属于工具执行
账本；Tool Result 进入模型产生的是模型输入成本。当前项目只记录工具耗时和错误，`RunCost` 只计算
模型费用，还没有统一工具成本协议。

### 追问问题与回答

**追问：Tool 自身执行成本与 Tool Result 进入后续模型请求产生的 Token 成本如何区分？**

工具执行时记录外部服务、计算和存储费用；下一次模型请求再记录包含该结果的输入 Token。两类记录
通过 `tool_call_id` 关联，但分别计价，不能混成一个数字。

### 技术追问补充

- ToolResult content 被包装进 `kind="tool_result"` UserMessage；下一轮 Bridge 将它投影进模型
  请求，因此其文本和图片占用输入窗口。
- Provider 只报告整次请求输入总量，Runtime 不会把精确 input_tokens 反向拆分到单条 Tool Result。
- 下一次 Provider usage 返回前，ContextView 按 Tool Result 可见正文、调用 ID、工具名和图片
  等价大小做近似估算。
- Tool Span 只记录耗时和错误；当前没有统一外部 API 费用、CPU 时间或存储成本字段。
- 可以让 `ModelRequestEvent` 记录实际进入请求的稳定消息索引及逐项估算，这样能够确认某个
  Tool Result 是否真的被发送，而不是仅凭它存在于 State 就归因。
- 请求完成后，逐项估算可以按供应商实测输入总量进行比例校准，但必须标记为 attributed estimate；
  官方总 Token 和各项估算不能再次求和。
- 工具成本记录可包含 `tool_call_id`、服务名、计量单位、数量、币种、供应商金额、估算或实测状态
  和账单引用；没有价格的资源保留数量并标记未定价。
- 图片、音频和结构化结果的模型输入成本应遵循具体 Provider 规则；字符等价值只用于调用前估算，
  最终仍以请求 usage 为准。

## 74. 一个任务的最终成本如何计算？

### 口述主回答

最终成本不应该只是“当前价格表乘以 Token 总数”，而应是一份按实际调用展开的成本账本。每条记录
至少包含唯一调用编号、所属 Agent 和 Workflow 阶段、模型或工具服务、实测用量、调用时采用的单价
及价格版本。最终汇总先按调用编号去重，再按主 Agent、压缩、子 Agent、Planner、Reflection、Judge、
重试和工具服务分类求和。

项目当前已经能从 `ModelResponseEvent` 聚合主 Agent 和压缩调用，并递归统计 `task` 子 Agent；
Workflow 的独立 State 需要上层再汇总。未知模型会保留 Token 和调用次数并标记未定价，因此当前
美元总额只是已定价部分的下界；未记录的供应商重试和外部工具费用也可能造成低估。

为了可复现，我会在运行开始时冻结价格快照，或保存价格表版本、币种、生效时间和哈希。历史任务
默认按运行时价格展示，不能随着今天的价格表变化；如果要按新价格重算，应单独标记为模拟结果。
最后再与供应商账单对账，差异作为独立调整项保留，不能覆盖原始 usage。

### 追问问题与回答

**追问：如何汇总主 Agent、Sub-Agent、Planner、Reflection、Compact 和重试产生的成本？**

把所有模型尝试和工具调用写入同一成本账本，使用稳定调用编号去重，并按 Trace 父子关系归属到主
Agent、压缩、子 Agent 和各 Workflow 阶段。重试的每次真实请求都单独计费。

**追问：模型价格变化或未知价格时如何记录和展示？**

保存运行时价格快照或不可变版本引用。未知价格保留用量并列入未定价清单，分别展示已定价小计和
未知部分；使用新价格重算时明确标记为模拟，不改写历史账单。

### 技术追问补充

- `RunCost.from_run()` 遍历 `model_response` Event，并递归读取 Tool Result
  `details[call_id].sub_events`，可覆盖多层 task 子 Agent。
- `workflow_steps_breakdown()` 通过 State 对象身份去重输出 Token，避免 Goal Loop 多个 Step 共享
  同一 State 时重复统计；完整美元成本仍需对各独立 State 调用 RunCost 后求和。
- 未找到价格的模型仍生成 ModelCost 和 Token 汇总，美元字段为 0，并加入
  `unpriced_models`，因此总额明确是下界。
- Trace 保存模型与 usage，不保存不可变价格快照；Provider 重试的中间尝试也没有完整 Event，
  当前无法生成严格账单级总成本。
- 可以新增不可变成本记录，统一表示模型尝试和工具费用，并携带 `trace_id`、`span_id`、调用编号、
  阶段、计量桶、数量、单价、金额、币种、价格版本和实测或估算标记。
- 汇总器遍历 Workflow 和子 Agent Trace，使用调用编号集合去重；同一 usage 在
  AssistantMessage、sidecar、父级 overview 或派生 Span 中出现时都不能再次计费。
- 压缩、Judge、Planner、Critic 和 Aggregator 都是普通模型调用，应通过阶段标签归类，而不是另写
  特殊加法；供应商重试则通过 attempt 记录计入。
- 账单展示至少分为模型费用、工具和基础设施费用、未定价用量及外部账单调整，并同时给出总 Token、
  调用次数和费用，避免一个美元总数掩盖缺失项。
- 价格快照应随 Trace 保存或由内容哈希引用不可变 Artifact；对历史轨迹使用新价格重算时生成新的
  分析视图，不能回写 Runtime Event。

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
