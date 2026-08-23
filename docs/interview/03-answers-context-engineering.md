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

项目在每次模型请求前检查当前活跃上下文大小，再让配置的压缩策略决定压缩哪些消息。策略只
提出“替换哪些消息、用什么替代”，运行时负责保护任务和最近消息、校验工具调用配对、写入摘要
并更新活跃索引。

我没有直接截断最前面的固定 Token，因为最前面可能包含原始任务，截断点也可能拆开工具调用和
工具结果。当前方案按消息语义和稳定索引压缩，并保留完整历史，后续还能恢复。

### 追问问题与回答

**追问：你的 Context Management pipeline 是什么？**

每轮模型调用前先估算活跃上下文，压缩策略选择待替换消息，Runtime 校验并写入替代消息和压缩
事件，最后从新活跃上下文构建模型请求。

**追问：为什么不直接截断最前面的 10K tokens？**

固定截断不理解消息语义，可能删除原始任务或拆开工具调用与结果，而且被删除内容无法审计和恢复。

### 技术追问补充

- `_active_context_tokens()` 优先使用压缩后仍然有效的最新 Provider usage，并估算其后的新增消息；
  压缩刚发生时则对全部活跃消息重新估算。
- `CompressionStrategy` 只返回待压缩索引和替代 Message；`compression.runtime` 负责校验、追加
  MessageEvent、记录 ContextCompressionEvent 并更新活跃索引。
- 默认 `preserve_kinds` 包括 task、system、summary 和 context；策略还会保留最近若干消息。
- 执行顺序是 `TurnStart → maybe_compress_context → build_context_view → ModelRequest`。

## 23. 一个工具返回 50K Token 时，如何压缩并保持协议完整？

### 口述主回答

不能直接按字符把工具结果切掉，因为结果必须继续和原工具调用保持配对。项目的压缩运行时支持
两种安全方式：把完整工具调用与结果一起折叠成短摘要，或者对单条结果做一对一改写，但改写后
必须保留原消息角色和调用编号。

原始 50K 内容仍保存在完整历史中，活跃上下文只使用缩短后的替代消息。需要说明的是，当前核心
提供了安全改写机制，但不会自动理解任意 50K 输出该保留什么；仍需要配置合适的规则或摘要策略。

### 技术追问补充

- 普通 N→1 折叠调用 `_align_tool_pairs()`，如果只选中 Tool Call 或 Tool Result 一侧，会保守地
  将该侧移出压缩集合。
- 一对一改写使用 `CompressionDecision(rewrite=True)`，只允许替换一条消息；消息类型和
  `tool_call_id` 集合必须与原消息一致。
- `ToolCompactStrategy` 会把旧工具交换替换为工具名和结果短预览，默认保留最近一次交换。
- 原始 Tool Result Message 不从 `State.messages` 删除，仍可通过稳定消息索引读取。

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

不应该默认把代码直接总结掉。代码、补丁、错误行和测试结果经常是后续验证需要的精确证据，主
回答可以总结进度，但关键路径、符号、命令和错误内容应保留原文或保留可恢复入口。

当前项目的摘要提示会要求精确保留路径、符号、命令、错误和测试名，但还没有按“代码、日志、
结构化数据”自动选择不同压缩器，所以不能声称已经实现代码感知压缩。

### 追问问题与回答

**追问：哪些 Tool 输出不能被直接 summarize？**

后续需要逐字执行或核验的代码、补丁、命令、错误行、测试结果和精确结构化值不能只依赖自然语言
摘要。

**追问：原始代码、错误日志、测试结果和结构化数据分别应该如何处理？**

代码和补丁保留原文或按文件重读；日志保留关键错误和命令；测试保留用例与结果；结构化数据优先
做字段筛选或确定性聚合。

### 技术追问补充

- 当前 `SummarizeStrategy` 使用统一摘要提示，没有按代码、日志或 JSON 自动选择不同压缩器。
- 摘要提示要求路径、符号、命令、错误、测试名、ID 和数值保持原样，并记录已尝试但失败的方法。
- `ToolCompactStrategy` 只生成工具名和固定长度结果预览，不理解代码或结构化数据语义。
- 被压缩的原始代码和日志仍保存在完整 Message History，可通过 Recall 或重新读取工作区文件恢复。

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

项目里有两种触发方式。普通压缩策略由运行时在每轮模型请求前检查 Token 阈值；主动 Compact
则由主 Agent 自己调用 compact 工具并提交摘要，运行时在下一轮开始、构建模型请求之前安全应用。

默认阈值可以由模型上下文窗口乘配置比例得到，也可以直接覆盖。Agent 主动 Compact 不依赖固定
阈值，它适合在一个子任务结束、旧过程不再需要逐字保留时主动整理工作记忆。

### 追问问题与回答

**追问：主动 Compact 基于固定 Token 数、Context Window 使用比例，还是模型主动请求？**

系统控制策略按 Token 阈值触发；主动 Compact 则由模型调用 compact 工具并提交自己写的摘要。

**追问：触发阈值如何为不同模型配置？**

可以直接配置阈值；未覆盖时根据模型上下文窗口乘压缩比例计算。

### 技术追问补充

- 默认 Agent 压缩阈值由 `_compression_threshold()` 读取显式配置，或使用 Provider/模型元数据中的
  context window 乘配置比例。
- compact 工具只校验 `summary` 和可选 `keep_recent`，将 `compact_request` 写入 ToolResult
  details，不在工具线程中修改 State。
- `AgentCompactStrategy` 在下一轮请求前读取最近请求，生成 summary Message 和
  `ContextCompressionEvent`。
- 请求只有在它仍是当前活跃视图最新消息时才生效，因此最多应用一次，也不会延迟压缩后续新消息。

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

我主要通过缩小摘要职责来降低风险：提示要求保留精确标识、禁止编造，最近消息继续保留原文，
原始历史也不会删除。摘要模型的请求、响应和用量都会进入 Trace，出了问题可以回到证据定位。

但当前 Runtime 只能校验压缩索引、消息结构和工具配对，不能自动判断摘要内容是否忠于原文。
所以摘要仍然是有损工作记忆，不能替代原始证据。

### 追问问题与回答

**追问：Runtime 能验证 Summary 与原始历史一致吗？**

当前不能。Runtime 只能验证压缩目标和消息结构，无法自动判断摘要是否遗漏、歪曲或编造事实。

### 技术追问补充

- `compression.runtime` 会校验压缩索引仍在活跃视图、工具调用与结果没有被拆开，以及 rewrite
  是否保持消息类型和调用编号。
- Runtime 不比较 summary 文本与原 Message 内容，也没有事实抽取、蕴含判断或独立摘要 Judge。
- 完整 Message History 不会删除，因此重要事实可以通过 Recall 或重新运行工具核验。
- 摘要模型的请求与响应进入 Trace，便于事后定位摘要从哪些输入生成，但 Trace 不等于自动验证。

## 31. Summary 被再次 Summary 时，如何控制信息丢失？

### 口述主回答

当前默认策略会保护已有 summary，不把它再次交给摘要模型，所以默认不会形成摘要反复摘要的链路。
只有显式调整保护类型时，才允许级联摘要，这时信息损失风险会更高。

如果摘要有误，原始消息仍在完整历史中，可以通过 Recall 恢复。恢复内容作为有界工具结果进入
当前上下文，之后仍可按普通规则再次压缩，而不是永久展开全部旧历史。

### 追问问题与回答

**追问：如果 Summary 出错，如何从原始历史恢复？**

根据被压缩消息的稳定索引调用 Recall，重新读取完整 Message History 中的原始内容。

**追问：恢复后的内容如何避免再次撑爆 Active Context？**

Recall 限制索引数量、单条长度和总返回长度；恢复结果之后仍受普通上下文压缩策略管理。

### 技术追问补充

- `SummarizeStrategy.preserve_kinds` 默认包含 `"summary"`，因此已有摘要默认不会再次进入摘要输入。
- 调用者可以从 `preserve_kinds` 移除 summary 以允许级联摘要，但当前没有针对多代摘要的信息损失
  度量或一致性校验。
- 每次实际压缩都会新增 replacement Message 和 `ContextCompressionEvent`，原始消息及旧摘要仍
  保存在完整历史中。
- Recall 默认限制 20 个索引、每条 4000 字符和整次 8000 字符；返回内容作为普通工具结果，后续
  可以再次被压缩。

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

模型需要从摘要中的缺口、显式引用或任务中的精确标识意识到自己缺少原始信息，再主动调用 Recall。
Runtime 当前不会自动判断“模型缺了哪段历史”，因为它无法可靠知道模型内部还记得什么。

这里还有一个实际边界：压缩事件虽然记录了被折叠的消息索引，但当前摘要正文并不保证自动带上
这些索引，所以 Recall 的可发现性还不够完整，这是后续可以改进的地方。

### 追问问题与回答

**追问：Runtime 能否自动判断模型缺少了哪段信息？**

当前不能。Runtime 不知道模型内部遗忘了什么，也没有自动上下文缺口检测器。

**追问：Summary、索引或显式引用如何为 Recall 提供线索？**

最可靠的线索是摘要或运行说明中的稳定消息索引，以及任务里再次出现的路径、错误、命令等精确
标识；模型再据此选择要 Recall 的消息。

### 技术追问补充

- `ContextCompressionEvent` 保存被压缩索引，但事件本身不会自动进入模型上下文。
- 当前 `SummarizeStrategy` 和 `AgentCompactStrategy` 生成的摘要正文不保证自动附带
  `compressed_message_indices`，因此模型未必能直接看到可召回索引。
- Recall 工具描述会告诉模型根据压缩摘要中的 transcript index 取回原文，但索引能否被看到取决于
  上游摘要或运行说明是否提供。
- 当前没有自动缺口检测、关键词检索、语义检索或每轮自动召回。

## 34. Recall 返回内容如何避免污染或撑爆当前上下文？

### 口述主回答

Recall 在入口和输出两端都做限制：索引数量有上限，重复索引会去重，每条消息和整次调用都有
字符预算，超出时明确标记截断。这样模型可以分批取回证据，而不是一次重新加载全部历史。

当前返回内容会作为普通工具结果进入活跃上下文，没有专门的 Retrieved Context 类型或独立
生命周期；它会一直保留到后续压缩策略再次折叠它。

### 追问问题与回答

**追问：Retrieved Context 应该保留多久、以什么粒度进入 Active Context？**

当前按单条原始 Message 的有界文本作为普通 Tool Result 进入上下文，并保留到后续压缩策略将其
折叠；没有专门的一轮 TTL。

### 技术追问补充

- 默认 `max_indices=20`、`max_chars_per_message=4000`、`max_total_chars=8000`；第一条请求消息会
  返回，其余内容在总预算不足时停止并标注剩余数量。
- 每条恢复内容以 transcript message header 加正文呈现；图片只记录数量，不重新内联图片数据。
- 返回顺序按去重后的请求顺序保持稳定，实际返回索引写入 `ToolResult.details["indices"]`。
- 当前没有 Retrieved Context 专用 Message kind、TTL 或“只保留一轮”策略。

## 35. Retrieved Context 是否会重新进入长期记忆？

### 口述主回答

Recall 本身不会写长期记忆。它只读取同一次运行的完整历史，并把结果作为当前会话中的工具结果；
跨运行 Memory 则在独立的生命周期 Hook 中读取运行证据并决定是否沉淀经验。

因此被 Recall 的内容可能出现在本次运行 transcript 中，被会话结束时的 Memory 蒸馏器看到，
但是否进入长期手册由 Memory 策略决定，不能因为被召回过就自动永久保存。

### 追问问题与回答

**追问：Recall、当前运行上下文和跨运行 Memory 的写入边界是什么？**

Recall 只读取本次运行历史；工具结果写入当前 State；长期 Memory 只在独立 Memory 生命周期中
根据整次运行证据决定是否持久化。

### 技术追问补充

- Recall 工具没有 Memory 依赖，也不会调用 Memory API；它的结果通过普通 Tool Result Message
  进入当前 transcript。
- `Memory.bind()` 在 SESSION_START 调用 `initial()` 注入上下文，在 SESSION_END 调用
  `finish()` 进行最佳努力持久化。
- FilesystemMemory 的 `finish()` 会读取完整 State messages 并生成 transcript，因此可能看到
  Recall 结果，但是否保留由 distiller 的 `retain_run` 和 handbook 重写结果决定。
- Memory 提示明确禁止保存 Secret、大段原始日志和临时当前任务状态；Recall 结果不会因为被读取过
  就自动升级为长期经验。

## 核对依据

- [`context_view.py`](../../src/simple_long_horizon_agent/context_view.py)
- [`state.py`](../../src/simple_long_horizon_agent/state.py)
- [`compression/runtime.py`](../../src/simple_long_horizon_agent/compression/runtime.py)
- [`compression/strategies.py`](../../src/simple_long_horizon_agent/compression/strategies.py)
- [`compression/agent_control.py`](../../src/simple_long_horizon_agent/compression/agent_control.py)
- [`tools/recall.py`](../../src/simple_long_horizon_agent/tools/recall.py)
- [`memory/transcript.py`](../../src/simple_long_horizon_agent/memory/transcript.py)
- [`memory.md`](../memory.md)
- [`07-context-and-long-horizon.md`](../design/07-context-and-long-horizon.md)
- [`test_compression_control.py`](../../tests/unit/test_compression_control.py)
- [`test_compression_effectiveness.py`](../../tests/unit/test_compression_effectiveness.py)
- [`test_memory.py`](../../tests/unit/test_memory.py)
