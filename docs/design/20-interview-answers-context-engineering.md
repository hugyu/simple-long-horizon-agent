# 20. 面试专题回答：Context Engineering

本文覆盖 [`17-interview-question-checklist.md`](17-interview-question-checklist.md)
中第 3 个专题的全部问题。回答重点是当前项目真实实现的完整历史、活跃上下文、压缩和
索引式 Recall，不把 Memory、RAG 或未来方案混为一谈。

## 1. “完整运行历史”和“模型活跃上下文”分别是什么？

完整运行历史是 State 中已经发生过的全部 Message 和 Event。它服务于审计、Trace、
状态重建和信息恢复，不会因为上下文压缩而删除。

模型活跃上下文是当前仍被选中、准备参与下一轮模型请求的消息集合。State 用
`active_context_indices` 指向这些消息，`ContextView` 再根据可见性策略做最后过滤。

所以两者的关系是：完整历史保存事实，Active Context 是事实在当前这一轮的受控视图。
压缩只改变模型现在看什么，不改写过去发生过什么。

## 2. 为什么完整历史不能直接作为模型上下文？

因为长任务的历史会持续增长，而模型窗口、调用成本和注意力都是有限的。把所有工具输出、
中间分析和旧错误每轮都重新发送，最终会超过窗口，或者让真正重要的目标和最新状态被大量
旧内容淹没。

另外，完整历史中还有模型不需要直接看到的内容，例如工具执行事件、Trace 元数据和
Provider raw。它们对审计有价值，但不是下一步决策所需的对话内容。

因此我没有用“删除历史”解决窗口问题，而是把完整事实与当前输入分开。这样既能控制模型
输入，也能在摘要遗漏时回到原始证据。

## 3. 活跃上下文由谁生成，在什么时候更新？

活跃上下文由 State 的 Snapshot 维护。没有发生压缩时，所有 Message 默认都是活跃的；
发生压缩后，`ContextCompressionEvent.active_context_indices` 会给出新的活跃顺序，
Snapshot 在应用这个 Event 时立即更新。

每轮模型请求前，Runtime 都会先运行可选压缩，再从最新的活跃消息构建
`ContextView`。所以压缩如果在本轮触发，会直接影响本轮尚未发出的模型请求，不需要等到
下一轮。

`ContextView` 本身不修改 State，它只负责可见性过滤和上下文大小统计。选择哪些旧消息
退出活跃视图的是压缩策略，真正应用变更的是压缩执行层。

## 4. 工具结果压缩、模型摘要和 Compact 有什么区别？

工具结果压缩主要针对体积很大的工具交换。当前一类做法是在工具边界直接截断可见输出，
例如 Read 和 Bash 返回有界内容；另一类是 `ToolCompactStrategy`，把较旧的 Tool
Call/Result 对折叠成确定性的短预览。它成本低，但只理解工具交换的结构，不理解复杂语义。

模型摘要由 `SummarizeStrategy` 完成。它选择较旧消息，调用独立 compressor 生成一段
working memory，再由 Runtime 把摘要写入 State，并更新活跃索引。它更能保留目标、
进度和关键事实，但会增加一次模型调用，而且摘要可能出错。

项目里的 Compact 是更宽泛的上下文收缩动作。Agent-controlled compact 是其中一种：
主 Agent 通过 compact 工具提交自己写好的摘要，下一轮开始时由 Runtime 安全应用。也
就是说，工具结果压缩和模型摘要是具体策略，Compact 表示改变活跃上下文的控制动作。

## 5. Compact 的触发条件是什么？

当前规则压缩和模型摘要主要按估算 Token 阈值触发，不是按固定 Turn 数，也不是让模型
自由判断。策略会读取当前活跃上下文的估算大小，超过 `threshold_tokens` 且存在可压缩
内容时才返回压缩决策。

Agent-controlled compact 由模型主动调用 compact 工具触发。工具先记录摘要申请，真正
压缩发生在下一轮请求前。这样工具本身不能直接修改 State，申请也能进入 Trace。

上下文估算优先使用最近一次可信的 Provider usage，再估算其后的新增消息；如果刚发生
压缩，旧 usage 已经包含被移出的内容，就暂时退回逐消息估算。

## 6. 摘要可能遗漏信息，如何防止错误不断累积？

当前最重要的防线是摘要不替代原始证据。被压缩的 Message 仍保留在完整 State 中，摘要
只替代它们在 Active Context 里的位置。模型如果发现摘要缺少细节，可以通过 Recall
按索引取回原始消息。

摘要 Prompt 也要求保留目标、已完成事项、当前状态、精确路径、符号、命令、错误和下一步，
并明确要求不虚构。默认还会保护 task、system、summary 和 context 等锚点，避免把所有
约束都交给一次摘要重写。

但当前没有自动校验摘要事实正确性的模型或规则，也没有为每条摘要做事实一致性评分。
因此它降低了错误累积风险，但不能保证摘要不会出错。进一步工程化时，我会增加摘要引用、
关键字段抽取和原文核对，而不是让摘要继续层层覆盖。

## 7. 如何处理摘要中的事实冲突？

当前项目没有专门的冲突检测和自动合并机制。活跃上下文按
`active_context_indices` 决定当前模型看到哪些摘要和消息；如果多个摘要包含冲突内容，
系统不会自动判断哪一个正确。

实际处理方式是回到完整历史，通过 Recall 读取被引用的原始 Message，再以工具输出、
用户纠正和更晚的已验证事实为准。摘要属于有损 working memory，不能覆盖原始证据的
可信级别。

如果要进一步实现，我会让摘要保存结构化来源范围，并对路径、数值、命令结果等关键事实
保留引用；发现冲突时向模型同时提供冲突项和原文，而不是静默选择最新摘要。

## 8. 哪些内容绝对不能被压缩？

当前实现没有一个全局写死的“绝对不可压缩类型”，而是由策略的 `preserve_kinds`
配置决定。默认会保留 task、system、summary 和 context，让任务目标、运行规则和已有
工作记忆继续保持原文。

还有一条更强的不变量：Tool Call 和对应 Tool Result 不能被拆开。普通 N 对 1 压缩会
自动对齐工具调用对；一对一 rewrite 也必须保持相同 role 和 `tool_call_id` 集合，否则
Runtime 会拒绝应用。

从设计上说，验收标准、用户明确纠正、安全规则和仍在使用的精确标识符都不应该被无保护地
折叠。但敏感信息不应靠“不可压缩”保护，它在进入模型和 Trace 前就应该被脱敏或隔离。

## 9. 原始证据如何保存，摘要如何关联原始证据？

原始 Message 通过 `MessageEvent` 永久追加到 `State.events`，同时保留在
`StateSnapshot.messages` 中。压缩不会修改这些旧 Message，只会新增摘要消息和
`ContextCompressionEvent`。

`ContextCompressionEvent` 会记录 `compressed_message_indices`、
`summary_message_index` 和新的 `active_context_indices`。Trace 或本地代码可以据此
回答“哪些原始消息被哪一条摘要替代，以及替代后模型看到什么”。

当前关联粒度是 Message index，不是句子级引用，也不是向量库文档 ID。这里还有一个需要
明确的实现缺口：内置摘要目前不会稳定地把 `compressed_message_indices` 自动写进摘要
正文，所以 State 和 Trace 知道来源关系，不代表主模型一定能从摘要里直接看到这些 index。

## 10. 被 Compact 移出活跃上下文的信息如何定位并恢复？

当前底层通过稳定的 Message index 定位。Trace 或调用者可以从
`ContextCompressionEvent` 找到被折叠的消息范围，再把需要的 index 交给 Recall。

Recall 读取的是 `state.messages` 的完整副本，不是 Active Context，所以即使旧消息已经
被压缩出去，仍然可以读取。它会返回消息的 role、sender、target、kind、文本、Tool Call
和 Tool Result 等模型可见内容。

恢复是只读的：Recall 不修改旧历史，也不会把原 Message 永久重新插回原位置。返回内容
作为新的工具结果追加到当前 transcript，供下一轮模型使用。当前主模型若没有从外部获得
index，也没有在摘要里看到 index，就不能仅凭现有 Recall 自动定位任意被折叠消息。

## 11. Recall 由谁触发？

当前设计的调用方式是 LLM 主动使用 Recall 工具，但前提是组装 Agent 时显式绑定了
`make_recall_tool(state)`，而且模型已经知道要读取的 Message index。Runtime 不会根据
摘要内容或上下文缺口自动触发，Workflow 也没有默认替模型调用 Recall。

这样做的好处是触发原因可见：模型在当前任务中发现缺少某个细节，明确请求对应索引，调用
和结果都会进入正常 Tool Call 轨迹。Runtime 不需要猜模型到底缺什么信息。

代价是模型可能忘记 Recall，或者不知道该查哪个 index。当前工具描述说明了按 index
恢复的方式，但内置压缩策略还没有把来源 index 稳定注入摘要正文，所以自主恢复链路没有
完全闭环。

## 12. LLM 主动 Recall 和 Runtime 自动 Recall 如何取舍？

项目当前提供的是 LLM 主动 Recall 的工具能力，因为 Runtime 很难仅凭规则判断模型当前
缺少哪条事实。主动调用更符合按需加载，也容易控制每次恢复的范围和记录调用原因。

Runtime 自动 Recall 适合有结构化依赖的场景，比如下一步明确引用了某个已压缩 artifact
ID，或者某个工具参数必须来自固定事件。这时系统能确定缺失项，不需要猜模型意图。

当前没有实现自动 Recall。如果以后增加，我会只对显式引用和可验证缺口自动恢复；对开放
问题仍让模型主动调用，避免 Runtime 把大量“可能有用”的旧内容重新塞回上下文。

## 13. Runtime 自动 Recall 时，如何判断模型缺少哪段信息？

当前 Runtime 不做这件事，所以我不会说项目已经能自动识别上下文缺口。

如果要实现，我会优先依赖结构化信号，而不是让另一个模型泛泛判断。例如摘要中保留
`source_indices`，当前请求引用某个文件、错误 ID 或 artifact，但 Active Context 中没有
对应内容时，Runtime 才能确定候选范围。

另一种办法是让模型先输出明确的缺失信息声明，再由 Runtime 执行检索。无论哪种方式，都
需要把自动恢复的原因、来源和注入范围记录成 Event，否则上下文会出现来源不明的信息。

## 14. Recall 使用关键词、语义检索、事件定位还是结构化引用？

当前 Recall 使用结构化的 Message index 定位，不做关键词检索，也没有向量语义检索。
它读取 `state.messages[index]`，因此结果确定、可测试，而且不会因为 embedding 或排序
变化而返回不同内容。

它也不是按任意 Event 检索。工具读取的是 Message 投影，所以模型请求、工具开始事件和
Span 不能直接通过 Recall 取回。

这种方案适合调用者或摘要已经给出来源 index 的情况，优点是简单和精确；缺点是模型必须
先知道 index。当前内置摘要不会稳定提供这个引用。如果以后加入关键词或语义检索，我会
把它作为另一种检索工具，而不是悄悄改变现有 Recall 的确定性语义。

## 15. Recall 的恢复粒度是什么？

当前恢复粒度是完整 Message 的模型可见表示。一次调用可以请求多个 Message index，
每条返回 role、路由信息、普通文本、Tool Call 和 Tool Result 文本；图片只会说明存在，
不会把图片数据重新注入。

它不是 Event、Span 或任意文本片段级恢复，也不能按一条 Message 内的行号或字符范围
读取。每条消息有字符上限，整次调用也有总字符预算。

所以如果一条原始 Tool Result 本身非常大，Recall 只会返回它的有界前缀并标记截断。
要精确读取更后面的内容，当前更可靠的做法通常是重新使用 Read 等原始工具读取来源文件。

## 16. Recall 粒度过大或过小分别有什么问题？

粒度过大，会把刚刚压缩掉的大量内容重新塞回上下文，增加 Token、干扰当前决策，甚至形成
“压缩后立即膨胀”的循环。

粒度过小，例如只返回一句没有来源和调用身份的文本，模型可能无法判断它来自哪一步、是否
是工具错误、与哪个 Tool Call 对应，也更容易断章取义。

当前以 Message 为基本粒度，是在因果完整性和返回大小之间取中间值；再通过每条和每次调用
的字符上限控制体积。它仍不是所有场景的最优粒度，尤其不适合从超大单条日志中按行定位。

## 17. Recall 返回内容后如何重新插入上下文？

Recall 是普通 `AgentTool`。它返回 `ToolResult` 后，Runtime 会把结果包装成
`ToolResultBlock`，与同轮其他工具结果一起写入新的 `kind="tool_result"` 用户消息。

这条新消息会自然进入下一轮 Active Context，因此模型能读取恢复内容。原 Message 不会
被移动，也不会重新加入 `active_context_indices` 的旧位置。

这样做的好处是恢复行为本身有完整因果链：模型请求 Recall、Runtime 执行、结果作为新观察
进入下一轮。代价是恢复内容会形成新的 transcript 副本，所以必须限制大小。

## 18. 如何防止模型不断 Recall，导致上下文重新膨胀？

当前主要靠三层限制：每次最多请求有限数量的 index；每条 Message 有字符上限；整次
Recall 有总字符预算。重复 index 会去重，超出总预算时会明确提示剩余内容需要后续调用。

外层的 `max_turns` 也会限制无限调用，但它不是 Recall 专用预算。当前没有“每次运行最多
Recall N 次”的硬限制，也没有自动检测重复查询同一 index。

如果实际评测发现模型反复 Recall，我会增加按 run 和 index 统计的调用预算，并把已经返回
过的范围提示给模型；但仍要允许模型在新问题出现时重新读取原证据，不能简单全局禁止重复。

## 19. 上下文压缩降低了多少 Token？任务成功率是否下降？

当前系统能记录每次压缩前后的估算 Token，`ContextCompressionEvent` 中有
`before_tokens` 和 `after_tokens`；单元测试也验证了压缩后上下文会下降，并且长序列中的
上下文峰值可以保持在阈值附近。

但仓库目前没有发布一组可以对外引用的“平均降低多少 Token”数据，也没有把任务成功率按
不同压缩策略做完整统计。因此我不会给出一个没有实验记录的百分比。

现阶段能确认的是机制层结果：完整历史保留、Active Context 有界、被折叠事实可以通过
Recall 恢复。压缩对真实任务成功率和成本的净影响，还需要在固定模型和任务集上做对照
实验。

## 20. 如何设计实验，证明收益来自上下文机制？

我会固定模型版本、system prompt、工具、任务集、最大 Turn、输出预算和运行环境，只改变
上下文策略。至少比较四组：不压缩、只做规则 Tool Compact、模型摘要、摘要加 Recall。

指标不能只看 Token，还要同时看任务成功率、上下文溢出率、总输入 Token、压缩额外调用
成本、Recall 次数、延迟和失败类型。因为一种策略可能省 Token，但由于摘要遗漏让成功率
下降；也可能成功率不变，却引入更多 compressor 调用。

对有随机性的模型需要多次重复，并报告均值、方差或置信区间。还要单独构造需要使用早期
精确信息的长任务，验证 Recall 是否真正恢复了成功率。当前项目只有机制单测和压缩指标
结构，没有完成这套公开组件级 Ablation。

## 21. 场景题：第 80 个 Turn 需要第 3 个 Turn 工具输出中的一行错误信息

在当前设计里，第 3 个 Turn 的工具结果即使已经被 Compact，也仍保留在完整
`state.messages` 中，`ContextCompressionEvent` 也记录了被折叠的 Message index。
因此从 State 和 Trace 的角度，这条错误没有丢失。

如果调用者把对应 index 提供给模型，或者摘要正文已经包含这个 index，模型就可以调用
Recall。Recall 会从完整历史读取这条 Message，把原始工具结果的有界内容作为新的 Tool
Result 返回；Runtime 再把它追加到当前上下文，模型下一轮就能使用这行错误，而不需要
恢复第 3 到第 79 个 Turn 的全部内容。

当前还缺两步完整闭环。第一，内置压缩摘要不会稳定地把来源 index 展示给主模型，所以在
没有外部提示时，第 80 个 Turn 的模型未必知道该 Recall 哪条消息。第二，Recall 按
Message 返回并有单条字符上限；如果目标错误位于超长 Tool Result 的截断部分，现有工具
也不能按行号继续读取。要完整支持这个场景，我会让压缩摘要自动携带来源 index，并让
Recall 支持 Message 内部的 offset 或范围读取。

## 核对依据

- [`context_view.py`](../../src/simple_long_horizon_agent/context_view.py)
- [`compression/`](../../src/simple_long_horizon_agent/compression/)
- [`tools/recall.py`](../../src/simple_long_horizon_agent/tools/recall.py)
- [`state.py`](../../src/simple_long_horizon_agent/state.py)
- [`07-context-and-long-horizon.md`](07-context-and-long-horizon.md)
- [`tests/unit/test_compression_control.py`](../../tests/unit/test_compression_control.py)
- [`tests/unit/test_compression_effectiveness.py`](../../tests/unit/test_compression_effectiveness.py)
- [`tests/unit/test_context_usage.py`](../../tests/unit/test_context_usage.py)
