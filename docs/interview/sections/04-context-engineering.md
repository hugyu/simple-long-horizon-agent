下面继续以“项目负责人 / 核心开发者”的第一人称回答。回答会明确区分上下文压缩、精确证据恢复和跨任务 Memory，也会说明当前索引引用与 Token 估算的实现边界。

## 1. 完整历史、活跃上下文、单轮模型输入分别是什么？

> 完整历史是 State 中所有 MessageEvent 投影出的 `state.messages`，按追加顺序保存任务、Assistant 输出和工具结果。压缩不会删除它们。
>
> 活跃上下文是完整消息列表的一组有序索引，也就是 `active_context_indices`。它表示经过压缩后，哪些原消息和替代摘要仍参与后续上下文。
>
> 单轮模型输入是 ContextView 再经过可见性过滤后形成的消息，加上 LLM 边界注入的 system prompt、工具定义和请求参数。
>
> 可以概括为：
>
> ```text
> Event Stream → 完整 Transcript → Active Context → ContextView → LLMRequest
> ```

## 2. 为什么要划分这三层？

> 因为审计、长任务运行和模型调用对信息的需求不同。
>
> 审计要求事实不能消失；长任务要求上下文能够缩小；单轮模型调用还需要过滤运行内部信息，并加入当前 Agent 的 system prompt 和工具。
>
> 如果只有完整历史，模型窗口会持续增长；如果只保存活跃上下文，压缩后无法恢复原始证据；如果把 ContextView 当历史保存，不同 Agent 的可见性策略会污染事实层。
>
> 三层分离后，历史负责真实性，活跃上下文负责规模，ContextView 负责本轮可见性。

## 3. 完整历史是否始终保留？存储成本怎么控制？

> 当前一次运行中的 Message 和 Event 采用追加式保存，压缩不会删除原始记录。但“完整历史”指系统已经记录的运行事实，不代表保存整个外部环境的所有字节。
>
> 模型可见存储通过工具截断、上下文压缩和 raw 外置来控制。Trajectory 使用增量 JSONL；Provider raw 可以写入独立池，避免每轮在主轨迹中重复不断增长的请求历史。
>
> 代价是 transcript 和 Event Stream 仍然随运行时间增长。当前项目优先保证可恢复和可审计，没有实现无限期历史的分层存储、冷热归档或自动保留策略。生产化需要把大对象放入 Artifact Store，并增加生命周期和容量限制。

## 4. 什么条件会触发上下文压缩？

> Runtime 在每次模型请求之前调用当前 ContextPolicy 的 CompressionStrategy。
>
> `ToolCompactStrategy` 和 `SummarizeStrategy` 主要通过 `threshold_tokens` 判断当前活跃上下文是否超过阈值。`AgentCompactStrategy` 则在发现最新的 compact 请求时触发。
>
> 如果使用 TieredStrategy，会按配置顺序寻找第一个能够返回 CompressionDecision 的阶段。本轮只执行第一个命中的压缩机制。

## 5. 压缩按照 Token 数、消息数还是任务阶段触发？

> 当前系统自动压缩主要按照估算 Token 数触发，不直接按照消息数量。
>
> 消息数量会间接影响 Token 估算，但两条很长的工具结果可能比几十条短消息更值得压缩。任务阶段不是 Runtime 自动识别的固定状态。
>
> 如果 Agent 判断一个子任务已经结束，可以主动调用 compact，这相当于由 Agent 根据任务阶段触发。系统自动策略和 Agent 主动策略可以通过 TieredStrategy 组合。

## 6. 工具结果压缩与模型摘要有什么区别？

> ToolCompact 是规则驱动的，只处理较旧且完整配对的 ToolCall/ToolResult Exchange。它保留工具名和有限结果预览，不调用模型，因此成本低、行为确定，但无法理解复杂语义。
>
> SummarizeStrategy 会把较旧消息交给独立 compressor，让模型生成 Goal、Done、State、Facts、Open、Next 和 Tried 等结构化 working memory。它能理解跨消息语义，但会增加 Token、延迟和摘要错误风险。
>
> 框架还支持一对一 rewrite，用缩短后的 Message 替换单条超大消息，同时要求保持相同角色和 Tool Call ID，但当前主要内置策略仍是 ToolCompact 和 Summarize。

## 7. 如何避免摘要丢失关键事实？

> 不能从理论上保证模型摘要完全无损，只能通过多层设计降低风险。
>
> 第一，默认保护 `task`、`system`、`summary` 和 `context`，不让核心任务、运行规则和委派上下文被普通摘要替代。
>
> 第二，保留最近若干消息原文，避免当前工作状态立即被压缩。
>
> 第三，压缩框架自动对齐 ToolCall 与 ToolResult，不能只压缩其中一侧。
>
> 第四，摘要 Prompt 要求路径、符号、命令、错误、测试名和关键值保持原文，并记录已经失败的方法。
>
> 第五，原始消息仍然保留，可以通过 Recall 恢复。摘要是工作记忆，不是原始证据的替代品。

## 8. 摘要错误会不会持续污染后续任务？

> 在同一次运行中会。摘要进入活跃上下文后，会影响后续模型决策，而且默认策略还会保护已有 summary，避免它被普通压缩再次折叠。
>
> 缓解方式包括：通过 Recall 查看原始消息、用当前工作区和测试重新验证摘要中的事实、由用户追加纠正信息，以及配置级联摘要重新整理旧 summary。
>
> 压缩摘要不会自动写入跨运行 FilesystemMemory。Memory 沉淀有独立边界，临时、未经验证的运行状态不应直接污染未来任务。
>
> 当前没有自动的摘要事实校验器或置信度机制，因此对关键结论仍必须依赖工具证据和外部验证。

## 9. 如何保留压缩前的原始证据？

> CompressionStrategy 不直接修改 State。它只返回待压缩的稳定 Message 索引和替代 Message。
>
> Runtime 先追加替代摘要，再记录 ContextCompressionEvent，其中保存：
>
> ```text
> compressed_message_indices
> summary_message_index
> active_context_indices
> before_tokens
> after_tokens
> strategy
> ```
>
> StateSnapshot 只根据该事件重指向活跃上下文。旧 MessageEvent、原始 Message 和工具结果仍在完整历史中，Trace 也能同时显示压缩前证据、摘要和压缩动作。

## 10. Recall 如何定位已经退出活跃上下文的信息？

> Recall 使用完整 transcript 的稳定、从零开始的 Message 索引，不使用当前活跃位置，也不使用 Event index。
>
> 工具闭包持有对应 State，收到 `indices=[...]` 后直接读取 `state.messages[index]`。即使消息已经退出 active context，它在完整 Message 列表中的索引也不会改变。
>
> ContextCompressionEvent 会记录被折叠的 Message 索引。理想情况下摘要也应该告诉 Agent 相关索引，使它能够主动调用 Recall。

## 11. Recall 是关键词检索、向量检索还是结构化索引？

> 当前 Recall 是确定性的结构化索引读取，不是关键词搜索，也不是向量检索。
>
> 它适合回答“把 transcript 第 7、8、9 条原始消息重新给我”，优势是精确、可解释、无需 embedding，也不会因为相似度错误返回其他内容。
>
> 限制是 Agent 必须先知道索引。当前 ContextCompressionEvent 保存了来源索引，但内置摘要正文并没有在所有策略中自动加入索引范围。因此完全自动的 Agent Recall 仍依赖摘要自己保留索引，或由 Trace、调用者和上层策略提供索引。这是当前可以继续完善的地方。

## 12. Recall 结果如何防止再次把上下文撑爆？

> Recall 有三层默认限制：
>
> - 一次最多请求 20 个原始索引。
> - 每条消息最多返回 4,000 个字符。
> - 整次调用最多返回 8,000 个字符。
>
> 输入会先检查原始数组长度，再去重并保持首次出现顺序。批量结果达到总预算后会明确提示还有多少条未返回，让 Agent 分多次继续 Recall。
>
> Recall 只恢复模型可见的 Message 内容，不会把 sidecar、Provider raw 或完整工具 details 无限制注入模型上下文。

## 13. Agent 主动 Compact 和系统自动 Compact 有什么区别？

> 系统自动 Compact 根据 Token 阈值触发。ToolCompact 使用规则生成摘要；Summarize 使用独立 compressor 模型生成摘要。
>
> Agent 主动 Compact 由主 Agent 调用 `compact(summary, keep_recent)`，摘要由主 Agent 自己写，不增加一次 compressor 模型调用。
>
> compact 工具不会在线程池里直接修改 State，而是把 `compact_request` 写入 ToolResult details。下一轮开始时，AgentCompactStrategy 在统一安全点读取申请并执行压缩。
>
> 申请只在它仍是最新活跃消息时执行一次。如果当时没有内容可压缩，申请会失效，不会在未来错误地压缩申请之后产生的新内容。

## 14. 如何决定哪些消息必须保留？

> 默认保护集合是：
>
> ```text
> task
> system
> summary
> context
> ```
>
> 它们分别代表任务目标、运行规则、已有工作记忆和委派上下文。策略还会保留最近若干普通消息。
>
> 除此之外，压缩框架强制保护 ToolCall/ToolResult 的因果完整性。如果策略只选择其中一侧，框架会保守地取消该侧压缩。
>
> 判断原则是：会改变目标、接受标准、权限、当前执行位置或恢复路径的信息，应保持原文或明确进入摘要；仅有过程性、重复性和可重新取得的信息可以更积极压缩。

## 15. 工具错误、用户约束和任务计划可以被压缩吗？

> 能否压缩取决于 Message kind 和策略，而不是文本内容。
>
> `run()` 和 `resume()` 写入的用户要求通常是 `kind="task"`，默认不会压缩。Runtime 规则通常是 system 或 context，也默认保护。
>
> 普通 Assistant 计划和旧工具错误可以被压缩，但摘要必须保留失败原因、关键错误码、已经尝试过的方法和下一步，否则模型可能重复失败。
>
> 如果重要约束被错误地保存成普通 message，而不是 task、system 或 context，它就可能进入压缩候选。这说明信息在进入系统时必须使用正确的 Message kind，不能指望压缩器仅靠自然语言识别所有约束。

## 16. 如何处理超大 Bash 输出或代码文件？

> 首先在工具边界限制，而不是等整个结果进入上下文后再处理。
>
> Bash 默认分别将 stdout 和 stderr 限制在约 4,000 字符，保留头尾并明确提示输出已截断，建议使用 `head`、`tail`、`sed`、`grep` 或更精确的命令重新获取。
>
> Read 默认限制为 2,000 行或 50 KiB，以先达到者为准，并返回 `offset` 续读提示。超长文件应按范围读取，而不是一次加载。
>
> 图片附件有大小上限，Context 估算时每张图片使用保守固定权重。旧工具交换进入历史后，还可以通过 ToolCompact 折叠。
>
> 需要注意：Recall 只能恢复当时记录进 Message 的可见结果。如果 Bash 在工具边界已经截断，Recall 不会凭空恢复被截掉的全部输出；Agent 应重新执行更窄的命令，或者读取原始文件。

## 17. Token 预算如何在系统提示、历史、工具结果和输出之间分配？

> 概念上的有效输入预算是：
>
> ```text
> context_window - output_reserve - safety_buffer
> ```
>
> 当前默认 Output Reserve 是 32,000 Token，Safety Buffer 是 20,000 Token，但这两个值面向大窗口模型，实际应按模型调整。
>
> 剩余预算由 system prompt、工具定义、活跃历史和工具结果共同使用。工具结果先在工具边界限流，历史超过阈值后由压缩策略处理，输出空间提前保留。
>
> ContextView 优先使用最近一次 Provider Usage 作为已知前缀，再估算新增消息；压缩之后旧 Usage 会失效，改为逐消息估算，直到下一次 Provider 返回新的 Usage。
>
> 当前字符回退估算主要覆盖 Message 内容。首次调用或刚压缩后，固定 system prompt 和工具 Schema 的完整 Token 开销不一定全部进入该估算，主要依赖 Safety Buffer 吸收。更严格的生产实现应把这些固定开销显式计入预算。

## 18. 如何衡量压缩策略是否有效？

> 当前可以从 Event Stream 计算：
>
> - 模型请求次数；
> - 压缩次数；
> - 活跃上下文峰值和最终大小；
> - 每次压缩前后 Token；
> - 总共删除的活跃 Token；
> - 平均保留比例；
> - 完整 transcript 消息数量；
> - 各压缩策略实际触发次数。
>
> 但“压得更多”不等于“更有效”。还要结合任务成功率、平均 Token、成本、延迟、Recall 次数、重复工具调用和关键事实遗漏率。
>
> 一个有效策略应该在保持成功率的前提下，让长任务的活跃上下文峰值保持有界，而不是只追求最低 Token。

## 19. 如何测试压缩前后 Agent 行为没有明显退化？

> 第一层是结构测试：验证 task 不被压缩、工具调用和结果不被拆开、Snapshot 可以重建、旧 Usage 失效、Recall 可以读取被折叠消息。
>
> 第二层是确定性场景测试：使用 Fake Agent 运行同一工具密集任务，比较无压缩和有压缩时的上下文增长，验证压缩后峰值保持有界，而且每次 fold 确实缩小上下文。
>
> 第三层是信息恢复测试：故意让 compressor 从摘要中遗漏一个关键值，再验证它仍能通过原始索引被 Recall 找回。
>
> 第四层是真实 Benchmark 对照：固定模型、任务、预算和工具，比较成功率、Token、成本及失败类型，并进行多次运行。
>
> 当前已有结构和效果测试，但没有完整的组件消融和统计置信区间，因此不能声称压缩在所有真实任务上都没有退化。

## 20. 长周期任务失败通常是上下文不足，还是规划能力不足？

> 两者都会发生，而且还需要区分工具和验证问题。
>
> 如果关键事实存在于完整历史，却没有进入当轮 ModelRequest，属于上下文选择或摘要问题；如果关键事实已经在模型输入中，模型仍选择错误行动，更可能是规划或模型能力问题。
>
> 如果计划正确但命令、权限或环境失败，是工具执行问题；如果模型宣称完成但测试未通过，是完成验证问题。
>
> Trace 的价值就是把这些失败拆开。长周期任务通常不是单纯“窗口不够”，而是信息遗失、目标漂移、重复失败、工具副作用和缺少外部验证共同累积。

## 21. 场景题：第 5 轮错误日志在第 100 轮变得关键，如何恢复？

> 首先需要区分“第 5 轮”和 transcript Message index。系统会通过 Trace 和当时的 ContextCompressionEvent 找到包含该错误日志的原始 Message index，例如 12。
>
> 即使消息 12 已经退出 active context，它仍存在于 `state.messages[12]`。Agent 调用：
>
> ```python
> recall(indices=[12])
> ```
>
> Recall 将原始消息以有界 ToolResult 返回。这个结果作为新的 tool_result Message 写入 State，第 101 轮重新构建 ContextView 后，模型就能看到该错误并继续分析。
>
> 如果日志超过单条 Recall 预算，返回值会截断，Agent需要分段恢复或重新执行更精确的 `grep`、`sed`、`tail` 命令。
>
> 当前的完整证据链是：
>
> ```text
> 原始 Message 12
>     ↓
> ContextCompressionEvent 记录其被折叠
>     ↓
> 摘要替代它进入 active context
>     ↓
> Trace/摘要提供索引
>     ↓
> Recall(12)
>     ↓
> 新 ToolResult Message
>     ↓
> 下一轮模型重新看到原始错误
> ```
>
> 需要诚实说明：当前 ContextCompressionEvent 一定保存来源索引，但内置摘要正文并不保证自动显示这些索引。如果模型不知道索引，需要由 Trace、调用者或增强后的摘要前缀提供。一个直接的后续改进，是在每条替代摘要前自动加入 `[Compressed from transcript messages 12-18]`。
