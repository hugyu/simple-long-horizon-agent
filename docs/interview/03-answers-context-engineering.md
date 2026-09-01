# 03. Context Engineering

本文对应 [`question-checklist.md`](question-checklist.md) 中第三部分的问题。每道题包含可直接
口述的主回答；存在清单子问题时，保留追问原文和简短回答；最后记录整组问题对应的仓库实现。

## 21. 为什么要区分完整历史和活跃上下文？

### 口述主回答

因为两者解决的问题不同。完整历史负责保留本次运行发生过的原始事实，活跃上下文负责决定
下一轮模型真正看到什么。项目里所有消息和事件继续保存在 `State` 中，压缩只更新活跃消息
索引，不会删除原始记录。

如果每轮都把完整历史发给模型，上下文会持续增长，成本、延迟和噪声都会增加，最终还会超过
模型窗口。分开以后，系统既能缩小当前输入，又能通过 Trace 或 Recall 找回旧证据。

### 追问问题与回答

**追问：Full History 和 Active Context 的数据结构分别是什么？**

完整历史由 `State.events` 和全部 Message 投影组成；Active Context 是
`StateSnapshot.active_context_indices` 指向的一组有序消息。

**追问：为什么不能直接让模型读取完整历史？**

长任务的历史会持续增长，全部发送会增加成本、延迟和噪声，并最终超过模型上下文窗口。

### 技术追问补充

- `State.events` 是完整运行事实；`StateSnapshot.messages` 由 `MessageEvent` 顺序投影得到，
  `State.messages` 对外返回其浅拷贝。
- `active_context_indices=None` 表示尚未压缩，此时全部消息活跃；压缩事件会把它替换成新的有序
  索引列表。
- 模型请求前，Runtime 先读取活跃消息，再由 `ContextView` 根据
  `model_invisible_kinds` 做最后的可见性过滤。

## 22. 上下文窗口不够时，系统如何决定保留、压缩或移除内容？

### 口述主回答

项目里不是直接截断 Token，而是把完整历史和模型当前使用的 Active Context 分开。每次模型
请求前，系统估算活跃上下文大小，再由配置的策略决定哪些旧消息退出活跃视图、用什么内容替代。

当前实现主要有三种方式：`ToolCompactStrategy` 用规则折叠旧的工具调用和结果，只保留工具名与
结果预览；`SummarizeStrategy` 调用独立 compressor，把旧对话总结成 working memory，默认 Agent
使用的是这一种；`AgentCompactStrategy` 则允许主 Agent 调用 `compact` 工具，并自己提供后续
需要保留的摘要。还可以用 `TieredStrategy` 组合它们，例如先做成本低、结果确定的工具压缩，
处理不了时再做模型摘要。

策略只负责提出压缩方案，Runtime 统一校验工具调用配对、写入替代消息和压缩事件，再更新活跃
索引。所谓移除只是从 Active Context 移除，原始消息仍保留在完整历史中，可以审计或通过 Recall
找回。我没有采用“截掉最前面 10K Token”，主要是因为它可能误删任务、拆开工具调用与结果，
而且丢失的内容无法恢复。

### 追问问题与回答

**追问：你的 Context Management pipeline 是什么？**

每轮从 `TurnStart` 开始，压缩策略先根据活跃上下文和 Token 阈值产生
`CompressionDecision`；Runtime 校验并应用它，追加替代消息和 `ContextCompressionEvent`，
然后基于更新后的 Active Context 构建模型请求。

**追问：为什么不直接截断最前面的 10K tokens？**

固定截断不理解消息边界和语义，可能删除原始任务，也可能只保留 Tool Call 或 Tool Result 的
一侧。当前方案按稳定消息索引压缩，并保留完整历史，因此更容易保证协议完整，也能够审计和恢复。

### 技术追问补充

- 默认 Agent 配置使用 `SummarizeStrategy`：阈值优先读取显式配置，否则按模型 Context Window
  的比例计算；默认保留最近 4 条普通消息。
- `SummarizeStrategy` 默认原样保护 task、system、summary 和 context；
  `ToolCompactStrategy` 只折叠较旧的完整工具交换；`AgentCompactStrategy` 在 Agent 调用
  `compact` 后，于下一轮安全点应用其摘要。
- `TieredStrategy` 按顺序选择第一个能够产生决策的阶段，它是组合策略，不是另一种摘要算法。
- `CompressionStrategy` 只返回待压缩索引和替代 Message；`compression.runtime` 负责对齐
  Tool Call/Tool Result、校验一对一 rewrite、追加 `MessageEvent` 和
  `ContextCompressionEvent`，并更新活跃索引。
- `_active_context_tokens()` 优先使用压缩后仍然有效的最新 Provider usage，并估算其后的新增
  消息；压缩刚发生时则对全部活跃消息重新估算。
- `model_invisible_kinds` 属于模型可见性过滤，不是压缩策略；执行顺序是
  `TurnStart → maybe_compress_context → build_context_view → ModelRequest`。

## 23. 一个工具返回 50K Token 时，如何压缩并保持协议完整？

### 口述主回答

如果工具返回了 50K Token，我会在工具结果写入 State 之后、下一次模型请求之前，增加一个专门的
Tool Result 压缩策略。它不会删除整条结果消息，而是定位其中超大的 `ToolResultBlock`，把原始
内容外置到文件或 Artifact Store，再生成一份有界的模型可见内容，包括执行状态、关键结论、必要
的头尾片段、原始产物引用，以及分页或继续读取方式。

协议完整性靠保持外壳不变：替代消息仍然是同一种 UserMessage，其中每个 `ToolResultBlock` 的
`tool_call_id`、工具名、顺序和 `is_error` 都与原结果一致，只改写具体的 `content`。这样
Provider Adapter 仍然能把结果关联到原来的 Tool Call，不会形成悬空调用。

这个设计可以直接复用项目现有的 `CompressionDecision(rewrite=True)`：策略负责生成缩短后的
Tool Result，Runtime 负责校验消息角色和调用编号、追加替代消息、记录
`ContextCompressionEvent`，并把 Active Context 指向替代版本。完整结果仍保存在原始 Message
或外部 Artifact 中，后续通过受限的 Recall、分页读取或范围查询恢复，而不是一次性重新注入
50K Token。

内容怎么压缩要根据结果类型决定：日志保留错误、统计信息和头尾；JSON 或表格保留 Schema、关键
字段和异常记录；代码或长文件优先保存路径并按范围读取；只有无法通过规则提取语义时，才做分块
的模型摘要。即使摘要失败，也至少返回确定性的截断内容和原始产物引用，不能直接丢掉结果。

### 技术追问补充

- 新策略可以设计为 `ToolResultRewriteStrategy`，在
  `TurnStart → maybe_compress_context` 阶段检查单条 `ToolResultBlock` 的估算 Token，超过单结果
  预算时产生一对一 rewrite。
- 若一个 UserMessage 包含多个并行工具结果，只缩短超限 Block 的 `content`，但保留全部 Block
  的数量、顺序、`tool_call_id`、工具名和错误状态。
- 替代内容应包含 `artifact_id/path`、内容大小、摘要方式、截断范围和 continuation cursor；
  Recall 也要受单次 Token 或字符预算限制。
- 对超过 compressor 自身窗口的内容不能单次摘要，应先按结构或固定预算分块，再聚合局部摘要；
  日志和结构化数据优先使用确定性规则。
- Runtime 继续使用 `rewrite=True` 校验消息类型和 `tool_call_id` 集合，并通过
  `ContextCompressionEvent` 记录原消息索引、替代消息索引、压缩前后 Token 和策略名称。
- 当前代码已经具备 rewrite、稳定消息索引、完整历史和压缩事件这些基础机制；需要扩展的是超限
  检测、Artifact 外置以及按内容类型生成替代结果的具体策略。

## 24. 工具结果压缩应该使用规则还是模型摘要？

### 口述主回答

我不会只选一种。重复日志、命令输出和结构明确的数据适合规则压缩，成本低而且结果稳定；跨多轮
的分析、决策和未完成事项更适合模型摘要，因为需要理解语义。

项目里分别提供规则工具压缩和模型摘要，还可以用分层策略让便宜、确定的规则先执行，规则无法
处理时再调用摘要模型。这样能控制成本，同时把不确定性限制在确实需要语义理解的部分。

### 追问问题与回答

**追问：规则压缩和模型压缩分别适合哪些输出？**

规则压缩适合重复日志、命令输出和已知结构；模型摘要适合跨多轮的目标、进度、决策和未完成事项。

**追问：如何控制压缩成本和不确定性？**

让无模型的规则策略优先执行，保留原始历史并记录摘要调用；只有规则无法表达语义时才调用摘要模型。

### 技术追问补充

- `ToolCompactStrategy` 不调用模型，配置项包括 `threshold_tokens`、
  `keep_recent_exchanges` 和 `preview_chars`。
- `SummarizeStrategy` 使用独立 compressor Agent，保留最近消息，并将内部
  ModelRequest/Response Event 一并写入主 State。
- `TieredStrategy` 按顺序选择第一个当前能产生压缩决策的阶段，因此可把规则压缩放在模型摘要前。
- 两种策略都只修改活跃投影，原始消息继续保存在完整 State。

## 25. 工具返回代码时，是否应该直接让模型生成摘要？

### 口述主回答

不应该让模型生成一段自然语言摘要后就替换掉原始代码。代码是后续编辑、执行和验证的精确输入，
摘要可以帮助模型理解意图，但不能成为唯一事实源。

基于当前设计，我会新增一个内容感知的 Tool Result 策略。它先根据工具类型、结果元数据和内容
结构判断这是代码、Diff、日志、测试报告还是结构化数据。对代码和补丁，原文写入工作区文件或
Artifact Store，并记录路径、内容哈希和范围；模型上下文中保留函数签名、相关 Diff hunk、报错
附近代码和可继续读取的定位信息。模型可以额外生成“改了什么、为什么”的语义摘要，但摘要必须
引用这些精确片段，不能替代它们。

如果结果超过预算，策略复用 `CompressionDecision(rewrite=True)`，只改写
`ToolResultBlock.content`，保留原消息角色、`tool_call_id`、工具名、顺序和错误状态。后续 Agent
可以按路径和范围重新读取原文，验证时则重新运行编译或测试，并用哈希确认读取的是同一份 Artifact。

对于其他输出也采用不同处理：日志保留命令、退出码、首个根因和关键堆栈；测试报告保留汇总以及
失败用例名称和原始错误；JSON 或表格先做 Schema 投影、字段筛选和确定性聚合。只有规则无法表达
跨片段语义时才调用模型摘要，摘要失败则退化为确定性截断和原始产物引用。

### 追问问题与回答

**追问：哪些 Tool 输出不能被直接 summarize？**

凡是后续要逐字执行、修改、比较或核验的输出，都不能只保留自然语言摘要，例如代码、补丁、
命令、堆栈、失败断言、测试用例名、Schema、ID 和精确数值。它们可以附带摘要，但必须保留原始
Artifact 或可定位的精确片段。

**追问：原始代码、错误日志、测试结果和结构化数据分别应该如何处理？**

代码和补丁保存原始 Artifact，并在上下文中保留哈希、符号和 Diff hunk；日志保留命令、退出码、
根因和关键堆栈；测试保留汇总、失败用例名和原始断言；结构化数据先按 Schema 做字段投影和
确定性聚合，同时保留原始数据引用。

### 技术追问补充

- 可以扩展 `ToolResultRewriteStrategy`，根据 `tool_name`、sidecar details 和内容形状路由到
  code/diff、log、test-report 或 structured-data handler；无法可靠分类时使用保守的通用截断。
- code/diff handler 先把完整内容写入 Artifact，记录 path、hash、语言、符号或 hunk 范围，再
  生成包含精确摘录和读取入口的替代 `ToolResultBlock.content`。
- 模型摘要是第二层语义索引。若调用 compressor，应把请求、响应和 usage 写入 Trace；摘要中的
  路径、符号和范围还应能在 Artifact 中校验，失败时退化为确定性提取。
- Runtime 复用 `rewrite=True` 保证消息类型和 `tool_call_id` 集合不变；策略还应保持并行结果
  Block 的数量、顺序、工具名和错误状态。
- 当前代码已有统一摘要、rewrite、完整 History、Recall 和工作区重读能力；需要扩展的是内容
  分类、Artifact 元数据、类型化提取器以及摘要引用校验。

## 26. 压缩遗漏关键信息时，系统如何发现并恢复？

### 口述主回答

压缩不会删除原始消息，被移出活跃上下文的内容仍保存在 `State.messages`。当模型发现摘要缺少
某个细节时，可以调用 Recall，按消息索引取回原始内容，结果会像普通工具结果一样进入下一轮。

当前标准机制是模型主动调用 Recall，运行时不会自动猜测模型缺少哪段信息。Workflow 或调用者
可以在更高层增加规则，但核心 Runtime 没有自动召回策略。

### 追问问题与回答

**追问：被移出 Active Context 的原始证据保存在哪里？**

原始 Message 仍保存在 `State.messages`，对应 MessageEvent 和压缩事件也继续留在
`State.events`。

**追问：Recall 由模型、Runtime 还是 Workflow 触发？**

当前标准实现由模型主动调用 Recall 工具；Runtime 不自动召回，Workflow 可以在外层增加自己的
触发规则。

### 技术追问补充

- `ContextCompressionEvent` 保存 `compressed_message_indices`、替代消息索引和新活跃索引，
  `StateSnapshot` 只重指向活跃视图。
- `make_recall_tool(state)` 闭包绑定当前 State，按 0-based 消息索引读取完整 Message 列表，不访问
  外部存储或向量索引。
- Recall 会校验索引、去重并保留首次出现顺序；默认最多 20 个索引、单条 4000 字符、整次
  8000 字符。
- Recall 返回普通 ToolResult Message，下一轮模型可见；当前没有自动发现上下文缺口的机制。

## 27. “主动 Compact”由谁在什么时候触发？

### 口述主回答

主动 Compact 是主 Agent 的模型主动触发的，不是 Runtime 等 Token 达到阈值后替它决定。比如
模型判断一个子任务已经结束、旧的搜索和调试过程不再需要逐字保留时，会像调用普通工具一样输出
`compact(summary=..., keep_recent=2)`。这里的 `summary` 不是一句“请压缩”，而是模型自己写好的
替代工作记忆，要保留后续仍需要的事实、决策、失败尝试和下一步；`keep_recent` 表示最近多少条
非保护消息继续原样保留。

这个 Tool Call 不会在工具线程里直接删除上下文。`compact` 工具只校验参数，把摘要封装成
`compact_request` 放进 ToolResult，Agent Loop 再把它记录到追加式 transcript。到下一轮开始、
构建模型请求之前，`AgentCompactStrategy` 读取这个最新请求，选出较旧且允许压缩的消息；Runtime
用 Agent 提交的摘要生成一条 summary Message，并通过 `ContextCompressionEvent` 更新 Active
Context 的索引。原始消息仍保留在完整 History 中，只是不再默认发给模型。因此“主动”指的是
压缩时机和摘要内容由主模型决定，真正修改上下文视图仍由 Runtime 在安全点完成。

### 追问问题与回答

**追问：主动 Compact 基于固定 Token 数、Context Window 使用比例，还是模型主动请求？**

主动 Compact 本身不看固定 Token 数或使用比例，而是模型根据任务阶段主动调用 `compact` 并提交
替代摘要。Token 阈值属于系统控制的压缩策略，可以作为没有主动请求时的 fallback。

**追问：触发阈值如何为不同模型配置？**

这只影响系统控制策略：可以为模型直接配置阈值；没有显式值时，再用该模型的 context window
乘压缩比例计算。主动 Compact 不依赖这个阈值。

### 技术追问补充

- 默认 Agent 压缩阈值由 `_compression_threshold()` 读取显式配置，或使用 Provider/模型元数据中的
  context window 乘配置比例。
- 主模型输出 `compact(summary, keep_recent?)` Tool Call；工具只校验参数，并将
  `compact_request` 写入 ToolResult details，不在并行工具线程中修改 State。
- 完整执行链是：`compact Tool Call → ToolResult(compact_request) → 下一轮 TurnStart →
  maybe_compress_context() → AgentCompactStrategy → CompressionDecision → summary Message →
  ContextCompressionEvent → build_context_view() → ModelRequest`。
- Strategy 默认保留受保护的 `task/system/summary/context` 消息以及最近 `keep_recent` 条非保护消息，
  其余候选按消息索引折叠；Runtime 还会对齐 Tool Call/Tool Result，避免只压缩工具交换的一半。
- `ContextCompressionEvent` 只重写 Active Context 索引，完整 transcript 不删除，因此后续仍可审计
  或通过 Recall 找回原文。
- 请求只有在承载它的 ToolResult 仍是当前活跃视图最新消息时才生效；摘要写入后会形成新的最高
  Message 索引，所以同一请求最多应用一次。若当时没有消息可折叠，请求会失效，不会延迟到未来
  错压它没有描述的新消息。

## 28. Compact 的粒度是什么？

### 口述主回答

当前真正执行压缩的粒度是 Message 索引集合，不是 Span 或 Episode。策略可以选择一条消息做
安全改写，也可以选择一组连续消息折叠成摘要；运行时最后都按稳定消息索引更新活跃上下文。

Turn 可以作为策略理解上的一组交互，但不是当前压缩协议的正式单位；Span 只是可观测视图，
Episode 也没有进入核心数据模型。

### 追问问题与回答

**追问：Message、Turn、Span 和 Episode 分别适合什么场景？**

Message 是当前正式压缩单位；Turn 可帮助策略理解一组模型和工具交互；Span 用于观察耗时；
Episode 当前没有进入核心协议。

**追问：如何避免压缩后破坏 Tool Call 与 Tool Result 的配对关系？**

Runtime 根据 `tool_call_id` 对齐调用与结果；普通折叠不能只压缩一侧，一对一改写也必须保留相同
调用编号集合。

### 技术追问补充

- `CompressionDecision.compress_indices` 使用稳定 Message 索引；默认是多条消息折叠成一条替代
  Message，`rewrite=True` 表示单消息一对一改写。
- `_align_tool_pairs()` 通过 `ToolCallBlock.id` 和 `ToolResultBlock.tool_call_id` 查找配对消息；
  压缩集合出现孤立一侧时会将其保留。
- `_validated_rewrite()` 要求替代消息类型相同，并暴露完全相同的工具调用编号集合。
- 替代 Message 先追加到完整历史，再通过 `active_context_indices` 插回原压缩区域的逻辑位置。

## 29. Summary Prompt 是如何设计的？

### 口述主回答

摘要不是泛泛概括，而是给主 Agent 继续工作的工作记忆。当前提示要求按目标、已完成事项、当前
状态、关键事实、未解决问题、下一步和失败尝试几个方面整理，并要求路径、符号、命令、错误、
测试名和数值保持原样。

允许舍弃的是寒暄、重复描述、已经被新结论取代的过程和不影响后续工作的措辞。摘要必须能够和
最近保留的消息一起支持 Agent 继续执行，而不是让它重新分析整个任务。

### 追问问题与回答

**追问：哪些信息必须保留，哪些内容允许舍弃？**

必须保留目标、约束、已验证进展、关键事实、精确标识、未解决问题和下一步；可以舍弃寒暄、重复
措辞、已过时过程和不影响后续决策的细节。

### 技术追问补充

- `SummarizeStrategy` 给 compressor 的提示固定包含 Goal、Done、State、Facts & identifiers、
  Open、Next 和 Tried & rejected 等可选章节。
- 提示要求文件路径、符号、命令、错误、测试名、ID 和数值保持原样，并明确要求不能编造。
- 默认 `preserve_kinds` 保护 task、system、summary 和 context；最近 `keep_recent` 条普通消息
  也不进入本次摘要。
- 摘要正文成为 `kind="summary"` 的 UserMessage；摘要模型请求、响应、模型和 usage 作为独立
  Event 与 sidecar 证据记录。

## 30. 如何降低 Summary 产生幻觉的风险？

### 口述主回答

这里的风险是：主 Agent 把一批旧消息交给另一个 compressor 模型，这个模型如果把“尝试过”总结成
“已经完成”，或者改写了路径、命令和数值，错误就会作为 working memory 继续影响主 Agent。我
主要从输入范围、摘要任务和错误影响面三层控制，而不是假设换一个模型就自然可靠。

当前 `SummarizeStrategy` 会把选中的原始 Message 直接交给独立 compressor，不先经过主 Agent 的
二次转述；task、system、context、已有 summary 和最近几条消息默认保留原文，不交给它改写。
compressor 本身不挂工具，只做一次受限的摘要调用，提示要求按 Goal、Done、State、Facts、Open、
Next 和 Tried & rejected 整理，并要求路径、符号、命令、错误、测试名和数值保持原样，不确定就
省略。这样能减少自由发挥，也能避免它把当前任务和最近状态一起压坏。

但我不会说 Prompt 能消除幻觉。当前 Runtime 只校验压缩的消息范围和 Tool Call/Result 结构，
还不会判断摘要内容是否忠于原文。因此项目把完整 History 保留为追加式事实，记录 compressor 的
请求、响应以及被折叠的 Message 索引；Summary 只替换 Active Context，原消息没有被删除，必要时
可以通过 Recall 找回。也就是说，当前做到的是限制 compressor 的输入、职责和错误影响范围，并让
错误可追溯、可恢复；如果要进一步做发布级保证，我才会再增加逐条来源引用和摘要验收门禁。

### 追问问题与回答

**追问：Runtime 能验证 Summary 与原始历史一致吗？**

当前不能。Runtime 能确认 `CompressionDecision` 只作用于有效 Message 索引，并保持 Tool Call
和 Tool Result 配对，但 compressor 返回的自然语言是否遗漏、矛盾或编造事实，目前没有语义门禁。
现有保障是保留原始 History、记录压缩证据并支持 Recall；如果扩展，我会要求摘要 claim 携带来源
索引，先验证精确标识和证据锚点，再决定是否接受摘要。

### 技术追问补充

- `SummarizeStrategy` 先确定 `compress_indices`，把这些索引对应的原始 Message 加上一条摘要指令
  直接交给独立 compressor；默认不改写 `task/system/summary/context` 和最近 `keep_recent` 条消息。
- `context` 不是独立的 Message 类，而是 `Message.kind` 的一个枚举值，表示框架注入的补充上下文，
  例如子 Agent 委派背景、Session Start Memory、Skill 内容和 Turn Limit 提醒。它通常表现为
  `RuntimeMessage(kind="context")`，Skill 内容则使用 `UserMessage(kind="context")`；压缩策略按
  `kind == "context"` 保护它们，不依赖底层的具体 Message 类型。
- 默认 `context_compressor` 与主 Agent 使用同一 Provider，但拥有独立、窄职责的 system prompt，且
  `tools=()`；它通过 `generate()` 完成一次模型调用，不运行完整 Agent Loop。
- compressor 输出的文本当前会直接包装成 `UserMessage(kind="summary")`。Runtime 会记录它的
  `ModelRequestEvent`、`ModelResponseEvent`、模型与 usage，但目前没有摘要内容 acceptance gate。
- `ContextCompressionEvent` 保存被压缩索引和新的 Active Context 索引；原 Message 仍在完整 State
  中，Recall 可以按索引取回，所以 compressor 出错不会破坏原始证据。
- Recall 的触发条件是“下一步需要精确证据但摘要不足或相互矛盾”，不是压缩后的固定动作；当前由
  模型显式调用，Runtime 不自动召回。
- `make_recall_tool(state)` 通过闭包绑定当前 State；输入是 `indices: integer[]`，输出包含消息索引、
  role、sender、target、kind、正文、Tool Call 和 Tool Result。默认最多请求 20 个索引，单条最多
  4000 字符，单次合计最多 8000 字符。
- 若继续增强，可以要求 compressor 返回带 `source_message_indices` 和原文 anchor 的结构化 claim，
  验证失败时重试、退化为抽取式摘要或取消压缩；这部分是扩展设计，不是当前已实现能力。

## 31. Summary 被再次 Summary 时，如何控制信息丢失？

### 口述主回答

我控制信息丢失的核心原则是，尽量不做 Summary-of-Summary。项目当前默认把已有 Summary 放在
`preserve_kinds` 中，所以普通压缩只处理新的旧消息，不会反复改写上一版摘要。

如果上下文继续增长，连多个 Summary 也需要合并，我会沿 `ContextCompressionEvent` 中的消息索引
找到这些摘要对应的原始消息，再用“原始证据加新增消息”重新生成一份 Summary，而不是只把旧摘要
交给模型继续概括。任务约束、未完成事项、路径、命令、错误和测试结果仍作为必须保留项；新摘要
没有通过这些检查时，就保留旧摘要并取消本次合并。

这个方案的代价是重新读取原始消息会增加一次压缩成本，但它避免了信息只沿着摘要文本逐代衰减。
完整 History 仍然不删除，因此摘要出错时也有恢复依据。

### 追问问题与回答

**追问：如果 Summary 出错，如何从原始历史恢复？**

根据产生该 Summary 的 `ContextCompressionEvent` 找到被压缩消息索引，必要时递归展开更早的
Summary，再从完整 History 读取原始证据并重新生成摘要。

**追问：恢复后的内容如何避免再次撑爆 Active Context？**

恢复过程按 Token 预算分批读取原始消息，只把重新生成的有界 Summary 放回 Active Context，不把
完整历史永久展开；普通 Recall 仍限制索引数量、单条长度和总返回量。

### 技术追问补充

- `SummarizeStrategy.preserve_kinds` 默认包含 `"summary"`，因此当前普通压缩不会产生摘要的摘要。
- `ContextCompressionEvent` 保存 `summary_message_index` 和 `compressed_message_indices`；扩展策略
  可以据此递归解析一个 Summary 最终覆盖的原始 Message 索引，不需要新增另一套 History。
- 合并时按输入预算分批读取原始消息，再复用现有 compressor 生成新 Summary；Runtime 仍通过普通
  `CompressionDecision`、`MessageEvent` 和 `ContextCompressionEvent` 应用结果。
- 新摘要至少要检查旧摘要中的任务约束、未完成事项和精确标识是否仍然存在；检查失败时不更新
  Active Context，继续保留旧 Summary。
- Recall 默认限制 20 个索引、每条 4000 字符和整次 8000 字符；恢复后只保留有界的新摘要，原始
  消息仍留在完整 History 中。
- 当前没有实现这种 source-aware refresh；它可以直接复用现有稳定消息索引、压缩事件、compressor
  和追加式 State，新增部分主要是索引展开、预算读取和摘要接受检查。

## 32. Recall 是如何实现的？

### 口述主回答

Recall 是一个只读工具，它按稳定消息索引读取当前 `State` 的完整消息列表。即使某条消息已经
退出活跃上下文，原始内容仍在，所以 Recall 可以把它作为工具结果重新交给模型。

当前实现不访问外部存储，也不做向量检索。我选择模型主动 Recall，是因为模型最清楚当前缺少
哪个细节，而且显式工具调用更容易控制成本、限制返回量和记录因果链。

### 追问问题与回答

**追问：Recall 从 Full History、外部存储还是向量索引中读取信息？**

当前从同一次运行的完整 `State.messages` 中按稳定索引读取，不访问外部存储或向量索引。

**追问：为什么选择模型主动 Recall，而不是每轮自动执行 RAG？**

模型可以只取当前确实缺少的原始消息，避免每轮隐式检索带来的额外成本、噪声和不可解释注入。

### 技术追问补充

- `make_recall_tool(state)` 将具体 State 闭包进 `AgentTool`；输入 Schema 只要求一个 0-based
  `indices` 整数数组。
- `_coerce_indices()` 校验数组非空、索引为非负整数、原始请求数量不超限，并按首次出现顺序去重。
- 工具从 `state.messages` 读取并渲染角色、发送方、接收方、kind、正文、Tool Call 和 Tool Result。
- Recall 自身只读 State；返回值由普通工具调度路径包装成 ToolResult Message 并进入下一轮。

## 33. 模型忘记信息后，如何意识到应该 Recall？

### 口述主回答

我不会依赖模型自己意识到“我忘了”，因为模型通常不知道自己缺了什么。更可靠的做法是让压缩
结果显式暴露 Recall 入口：Runtime 应把本次被折叠的稳定消息索引范围确定性地写进模型可见的
Summary，例如“原始证据在 transcript messages 12-18，需要精确细节时调用 Recall”。

同时我会在 Agent 指令里定义 Recall 的触发条件：当下一步需要精确代码、命令、错误、数值或先前
决策，但 Summary 中没有原文证据；或者当前信息相互矛盾、模型只能猜测时，必须先 Recall，再继续
执行。这样模型不是靠模糊的遗忘感知，而是根据“缺少精确证据”这个可观察条件调用工具。

当前项目已经有稳定消息索引、`ContextCompressionEvent` 和 Recall 工具，缺的是把这些索引自动
投影到 Summary 正文。这个扩展可以放在压缩结果应用阶段完成，不需要让 Runtime 猜测模型内部状态，
也不需要每轮自动召回全部旧内容。

### 追问问题与回答

**追问：Runtime 能否自动判断模型缺少了哪段信息？**

不能可靠判断模型内部忘了什么。Runtime 更适合确定性地暴露被压缩范围和召回入口，再由模型根据
明确的证据缺口选择索引；全自动召回反而可能注入无关历史。

**追问：Summary、索引或显式引用如何为 Recall 提供线索？**

Summary 至少应带有被压缩消息的稳定索引范围；关键结论还可以附来源索引。路径、错误、命令等
标识帮助模型判断缺的是哪类证据，索引则直接成为 Recall 的调用参数。

### 技术追问补充

- `ContextCompressionEvent` 保存被压缩索引，但事件本身不会自动进入模型上下文。
- 压缩 Runtime 在 `_align_tool_pairs()` 后已经得到最终 `compress_set`，可以使用现有
  `format_index_ranges()` 生成确定性的模型可见 footer，再附加到 `kind="summary"` 的替代消息。
- compressor 可以为关键事实生成 `[message 12]` 形式的细粒度引用，但最外层索引范围应由 Runtime
  写入，不能依赖模型正确复制。
- Agent 的 system prompt 或 Recall 工具描述应列出触发条件：需要逐字证据、Summary 缺少来源、
  信息冲突或准备基于不确定事实执行操作时，先调用 Recall。
- 需要增加测试，验证 Summary footer 使用工具配对对齐后的最终索引、索引能够直接传给 Recall，
  并且未发生压缩时不会出现虚假的召回提示。
- 当前没有自动缺口检测、关键词检索或语义检索；最小扩展是打通
  `ContextCompressionEvent → Summary 索引提示 → Recall(indices)` 这条显式链路。

## 34. Recall 返回内容如何避免污染或撑爆当前上下文？

### 口述主回答

我会从准入和生命周期两端控制。准入阶段继续使用当前的索引数、单条长度和单次总量限制，只允许
模型按需取回一小批消息；返回内容还要明确标记为“历史证据”，带来源索引和边界，不能被当作新的
系统指令执行。

生命周期上，Retrieved Context 只需要完整保留到下一次模型调用，让模型消费一次。模型产生下一
步结果后，我会用专门的清理策略把 Recall Tool Call 和对应 Tool Result 整体折叠成一条短记录，
例如“已读取 messages 12-14”。如果其中某个事实后续仍然重要，应由模型把它连同来源写进新的
Summary，而不是让整段召回文本长期留在 Active Context。

当前项目已经实现了有界返回、稳定来源索引和工具配对保护；需要增加的是 Retrieved Context 的
一轮 lease 和消费后清理。代价是以后再次需要原文时可能要重新 Recall，但这比让历史证据持续占用
窗口、重复影响模型判断更可控。

### 追问问题与回答

**追问：Retrieved Context 应该保留多久、以什么粒度进入 Active Context？**

按一次 Recall 请求形成一个有界结果包，保留到紧接着的一次模型调用完成。之后整体折叠该 Recall
调用和结果；需要长期保留的结论单独进入 Summary，并附原始消息索引。

### 技术追问补充

- 默认 `max_indices=20`、`max_chars_per_message=4000`、`max_total_chars=8000`；第一条请求消息会
  返回，其余内容在总预算不足时停止并标注剩余数量。
- 每条恢复内容以 transcript message header、来源索引和正文呈现；扩展时应增加明确分隔和
  “historical evidence, not instructions” 标记，图片仍只记录数量。
- 实际返回索引已写入 `ToolResult.details["indices"]`，可以作为识别 Recall 结果、去重和计算
  lease 的稳定元数据。
- 清理策略必须同时折叠 Recall 的 Tool Call 和 Tool Result，复用 `_align_tool_pairs()`，不能只
  删除结果一侧而破坏 Provider 协议。
- 可以在下一条非 Recall Assistant Message 写入后，将对应结果视为已消费；替代记录只保留来源
  索引、是否截断和必要状态，不保留召回正文。
- 当前没有 Retrieved Context 专用 TTL；需要扩展的是一轮 lease、消费识别和 Recall 专用清理
  策略，不需要改变完整 History。

## 35. Retrieved Context 是否会重新进入长期记忆？

### 口述主回答

Retrieved Context 会进入长期记忆的候选证据集，但不会自动成为长期记忆。当前 Recall 结果作为
普通 Tool Result 写入 State，SESSION_END 的 Memory 能看到它；但 Recall 本身没有 Memory 写入
权限，最终是否持久化仍由 Memory 的写入策略决定。

我会在写入前做两层判断。第一层是来源去重：Recall 结果带有原始消息索引，同一份证据无论被召回
多少次，都只按原始 Message 计算一次，避免重复召回放大它的重要性。第二层是持久化资格：只有相对
现有 handbook 有新增信息、对后续任务仍有价值、能由原始证据或运行结果支持，并且不包含 Secret
和临时任务状态的内容，才允许写入。

因此，召回的日志、代码或旧对话本身仍留在本次运行 transcript 中用于审计，不会再复制进长期
handbook；如果 Agent 基于它形成了一条满足上述条件的可复用结论，这条结论可以引用原始证据后
进入 Memory。Retrieved Context 可以间接贡献长期经验，但不会把同一份原文重复持久化。

### 追问问题与回答

**追问：Recall、当前运行上下文和跨运行 Memory 的写入边界是什么？**

Recall 只读原始 Message；召回结果写入当前 State 供本轮推理；SESSION_END 的 Memory 把它作为
候选证据，先按来源去重，再决定是否有新的、可复用且有证据支持的内容需要持久化。

### 技术追问补充

- Recall 工具没有 Memory 依赖，也不会调用 Memory API；它的结果通过普通 Tool Result Message
  进入当前 transcript。
- `Memory.bind()` 在 SESSION_START 调用 `initial()` 注入上下文，在 SESSION_END 调用
  `finish()` 进行最佳努力持久化。
- `FilesystemMemory` 按 `{root}/{memory_name}` 建 namespace，核心文件是 `MEMORY.md`、
  `memory_summary.md`、`INDEX.md` 和 `runs/<run_id>/`；默认根目录是 `~/.simple/memory`。
- `initial()` 不做语义检索，只注入 Memory 路径、namespace 概览或简短 summary；Agent 再通过普通
  文件工具按需读取 handbook、索引和运行证据。
- `finish()` 把 task、完整 transcript 和 artifacts 交给可选 distiller。模型负责返回是否保留、
  namespace、运行摘要、索引行和完整 handbook rewrite；空 rewrite 表示保留旧 handbook。
- `make_filesystem_distiller()` 构造一个 `tools=[]` 的单次 LLM Request；默认输出上限 32000 Token、
  Timeout 600 秒，temperature 未显式指定时沿用 Provider 默认值。
- `FilesystemMemoryPayload` 包含 task、有界 transcript、artifacts、现有 index/handbook/navigation
  summary、run path、可用 namespace 和 `MemoryContext`；完整 transcript 仍单独落盘，送入 distiller
  的版本会按预算保留头尾并截断中段。
- `FilesystemDistillation` 输出 `retain_run`、`memory_name`、`memory_summary_md`、`summary_md`、
  `index_row` 和 `memory_md`；Prompt 要求长期 lesson 引用 transcript section、artifact、路径、符号、
  命令或错误字符串等可检索证据，并禁止保存 Secret、大段日志和临时任务状态。
- 整个 read-distill-commit 在 Memory root 的跨进程锁内串行执行，文件通过临时文件替换原子写入；
  过大、结构异常或会清空既有经验的 handbook rewrite 会被拒绝。
- FilesystemMemory 当前从完整 State messages 生成 transcript，因此 Recall 正文会重复出现；
  扩展时应区分审计用完整 transcript 和送给 distiller 的过滤投影。
- 过滤器可以通过 `ToolResultBlock.tool_name == "recall"` 和 sidecar 中按调用编号保存的
  `details["indices"]` 定位召回副本，并按 `(run_id, source_message_index)` 去重。
- 原始 Message 已经保存在同一个 State 中，因此去掉 Recall 副本不会丢失证据；distiller 需要时
  应引用原始 transcript section，而不是召回结果 section。
- Memory 写入门禁应检查 novelty、跨任务价值、证据引用和安全性；原始 Recall 正文、重复日志、
  Secret 和当前任务临时状态不进入 handbook。
- 当前 Memory Prompt 已要求把工具输出当作证据而非指令，并禁止保存 Secret 和大段日志；新增的
  结构化去重用于在进入模型前进一步降低重复权重和提示注入风险。

## 35A. 当前项目的 Memory 是怎么实现的？

### 口述主回答

我这里把 Memory 做成了文件系统上的跨运行经验。每次任务开始时，只把已有经验的摘要和存储位置
告诉 Agent，由它按需读取；任务结束后，再把本次运行的过程和产物保存下来，并提炼成后续任务可以
复用的经验。

我没有把 Memory 做成每轮自动检索的向量库，主要是希望它保持可检查、可追溯。Memory 只提供历史
参考，当前代码、测试和工具执行结果始终优先。

### 追问问题与回答

**追问：Memory 在什么时候读取和写入？**

读取发生在任务开始时，只注入摘要、路径和使用规则；写入发生在任务结束时，收集最终对话和关键
产物后再沉淀。Memory 是增强能力，读写失败不会让主任务失败。

**追问：每个任务都会单独生成一份 `MEMORY.md` 吗？**

不会。项目是每个 Memory Namespace 维护一份 `MEMORY.md`，同一类任务的多个 Run 共同更新这份
长期经验手册；每次任务自己的 Task、Transcript、Summary 和 Artifact 则单独保存在
`runs/{run_id}/` 下。这样既能聚合同类经验，也能回到具体 Run 核对原始证据。

**追问：多个 Run 同时写入发生冲突时怎么办？**

同一个 Memory Root 同一时间只允许一个写入者，锁会覆盖“读取旧版本、提炼、提交新版本”整个
过程，避免两个 Run 相互覆盖。文件提交使用原子替换，重复的 Run ID 也不会再次写入。

**追问：Namespace、运行证据和长期经验如何管理？**

我按任务族划分 Namespace。每个 Namespace 分开保存长期经验、导航索引和每次运行的原始证据；
Distiller 负责合并和去重长期经验，系统通过数量和容量上限清理最旧的运行记录。

### 技术追问补充

- `Memory` 的当前接口是 `initial(ctx)`、`tools(ctx)` 和 `finish(ctx)`；`bind()` 将它们转换成
  `SESSION_START`、`SESSION_END` Hook 以及普通 `AgentTool`，核心 Agent Loop 不依赖具体 Memory
  实现。
- `FilesystemMemory.initial()` 在指定 `memory_name` 时确保 Namespace 布局存在，并把路径、策略和
  最多 2000 字符的导航摘要包装成 `sender="memory"`、`kind="context"` 的 Runtime Message。
- 未指定 `memory_name` 时，启动阶段只注入已有 Namespace 的有限概览，由模型决定读取哪个目录；
  当前实现不会在每次模型请求前执行向量检索或自动召回。
- `finish()` 只在 Memory 启用且最终 State 存在时执行。它从 State 生成有界 Task 和 Transcript，
  收集显式 `memory_artifacts` 或最终 Submission，再写入本次 `runs/{run_id}/`。
- 配置 Distiller 时，它读取现有 Summary、Index、Handbook 和本次证据，返回完整的新
  `MEMORY.md`、Namespace 摘要、单次运行摘要和 Index Row；未配置或 Distill 失败时仍保存运行证据
  和回退摘要。
- 一个 Namespace 的固定结构是 `MEMORY.md`、`memory_summary.md`、`INDEX.md` 和
  `runs/{run_id}/`；每个 Run 保存 `task.md`、`transcript.md`、`summary.md`、Artifact 清单及实际
  Artifact。

```text
{memory_root}/{namespace}/
|-- MEMORY.md
|-- memory_summary.md
|-- INDEX.md
`-- runs/
    `-- {run_id}/
        |-- task.md
        |-- transcript.md
        |-- summary.md
        |-- artifacts.md
        `-- artifacts/
```

| 概念 | 作用 |
| --- | --- |
| `memory_name` | 本次 Run 使用的 Namespace 名称，例如 `python-repo-repair`；它决定从哪个目录读取经验，以及任务结束后写回哪个目录。 |
| `memory_summary.md` | Namespace 的冷启动导航摘要，只帮助 Agent 快速判断这份 Memory 是否相关，不代替完整的 `MEMORY.md`。 |
| `MEMORY.md` | Namespace 级长期经验手册，保存多个相关 Run 提炼出的高价值经验，不属于某一个任务。 |
| `INDEX.md` | 运行证据索引，把简短结论、适用范围和关键词关联到具体的 `runs/{run_id}/summary.md` 与 Artifact。 |
| Distiller | 可选的模型提炼步骤。它在 Run 结束时读取旧 Memory 和本次证据，决定是否保留本次 Run、写入哪个 Namespace，并返回合并去重后的完整 `MEMORY.md`。 |
| `runs/{run_id}/` | 单次任务的证据目录，用于保存 Task、Transcript、Summary 和 Artifact，支持长期结论回溯。 |

- 调用方可以通过 `memory_name` 明确指定 Namespace；没有指定时，Distiller 可以根据任务和已有
  Memory 选择 Namespace；既没有指定、也没有 Distiller 时写入 `default`。`memory_summary.md`
  只负责导航，真正需要使用经验时仍读取 `MEMORY.md` 或通过 `INDEX.md` 回到具体 Run 证据。
- `_memory_lock(root)` 使用 Root 下共享的 File Lock，覆盖读取旧文件、Distiller 模型调用和写入
  提交。这个设计避免 Lost Update，代价是同一个 Root 的 Memory 沉淀过程会串行。
- `_write_text_atomic()` 使用同目录临时文件替换目标文件，保证单文件不会暴露部分写入；它不能替代
  覆盖整个逻辑事务的 Root Lock。
- 完整 Run 以 `.complete` 标记；相同 Run ID 已经完成时 `finish()` 是 No-op，避免重试生成重复
  证据或重复更新 Index。
- 默认限制为每个 Root 最多 128 个 Namespace，每个 Namespace 最多 64 个 Run、总大小 128 MiB；
  Task、Transcript 和 Artifact 也分别有限额，写入完成后裁剪最旧 Run。
- Distiller 对 `MEMORY.md` 做完整重写而不是追加 Delta；空更新保留旧手册，超长、结构为空或会
  清空全部经验的异常重写会被拒绝，并在对应 Run 下记录 `memory_error.md`。
- `initial()` 失败会产生模型和 Trace 可见的跳过说明；`finish()` 或 Distill 失败按 Best-effort
  处理并记录错误，不把 Memory 变成主任务的单点依赖。

## 核对依据

- [`context_view.py`](../../src/simple_long_horizon_agent/context_view.py)
- [`state.py`](../../src/simple_long_horizon_agent/state.py)
- [`compression/runtime.py`](../../src/simple_long_horizon_agent/compression/runtime.py)
- [`compression/strategies.py`](../../src/simple_long_horizon_agent/compression/strategies.py)
- [`compression/agent_control.py`](../../src/simple_long_horizon_agent/compression/agent_control.py)
- [`tools/recall.py`](../../src/simple_long_horizon_agent/tools/recall.py)
- [`memory/base.py`](../../src/simple_long_horizon_agent/memory/base.py)
- [`memory/filesystem.py`](../../src/simple_long_horizon_agent/memory/filesystem.py)
- [`memory/transcript.py`](../../src/simple_long_horizon_agent/memory/transcript.py)
- [`memory.md`](../memory.md)
- [`07-context-and-long-horizon.md`](../design/07-context-and-long-horizon.md)
- [`test_compression_control.py`](../../tests/unit/test_compression_control.py)
- [`test_compression_effectiveness.py`](../../tests/unit/test_compression_effectiveness.py)
- [`test_memory.py`](../../tests/unit/test_memory.py)
