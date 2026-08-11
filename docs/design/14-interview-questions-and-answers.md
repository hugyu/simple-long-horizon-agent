# 14. 简历驱动的面试问题与回答

> 本篇假设面试官只能看到[简历项目描述](15-resume-project-description.md)，看不到
> 仓库、源码和设计文档。问题先从简历中的公开主张开始，只有候选人在回答中引入
> 生成器、追加式状态、压缩策略等内部概念后，面试官才会继续追问实现。

## 1. 使用方法与统一案例

这不是需要逐字背诵的标准答案。每个回答按“结论、机制、边界”组织：先在十几秒内
回答问题，再用一条数据流或设计取舍解释，最后说明当前系统没有承诺什么。

全文使用“修复代码仓库中的缺陷”作为贯穿案例：任务进入 State 后，模型通过 Read、
Bash 和 Edit 定位并修改代码；工具结果成为下一轮观察；上下文过长时只压缩活跃视图；
模型给出 final 后，再由测试或官方评分器判断任务是否真正完成；Event Stream 记录
整条事实链，Trace 和 Eval 从中派生。

面试官通常先问第 2 节，再根据回答选择一个技术分支深入，不会机械问完 87 题。

## 2. 最高概率问题

### Q1. 请用 1～2 分钟介绍这个项目

**回答：**

Simple Long Horizon Agent 是我从零设计并实现的一套轻量级长周期 Agent Runtime。
它解决的不是单轮文本生成，而是模型如何在真实环境中持续规划、调用工具、观察结果、
修正方案，并由外部事实验证任务是否完成。

系统以显式 Agent 循环为核心，用项目自有的 `Message`、`Event`、`State` 保存运行
事实，在边界处适配 OpenAI、Anthropic 等模型协议；外围再组合上下文压缩、Recall、
Memory、MCP、子 Agent、Trace 和容器化 Eval。这个结构既保持核心可检查，也能处理
软件工程和终端操作等多步骤任务。

效果上，项目在 SWE-bench Pro、Terminal-Bench 2.1 和 PostTrainBench 上分别达到
63.20%、77.53% 和 45.88%。这些结果证明指定配置下的整套 Agent 方案优于同模型、
同任务预算 Baseline，但不代表每个组件都已经完成独立消融，也不代表系统已经是
生产级多租户平台。

### Q2. 什么是长周期 Agent？它和普通多轮聊天有什么区别？

**回答：**

普通多轮聊天主要由用户推动对话，系统关注每轮回答是否合适；长周期 Agent 要自己
在一个持续变化的外部环境中推进任务。一次代码修复可能经历检索、修改、测试失败、
重新定位和再次验证，任何一步都可能改变工作区。

因此长周期系统除对话外还必须管理工具副作用、运行状态、上下文预算、错误反馈、
停止条件和外部验证。关键不是“轮数更多”，而是每轮行动都有身份、有观察、有预算、
可追溯，并且完成由环境事实确认，而不只由模型口头声明。

### Q3. 为什么自己实现 Runtime，而不是使用 LangGraph、AutoGen 或其他框架？

**回答：**

项目目标首先是让 Agent 的核心行为能够被完整理解、修改和精确实验。大型框架擅长
快速接入生态，但循环、状态转换、重试和上下文策略通常分布在多层抽象中，替换一个
变量时不容易判断还改变了什么。

我选择保留一个短而显式的运行模型，再把 Provider、Tool、Memory、Workflow、Trace
和 Eval 放到边界层。这样可以用 Fake Provider 和 Fake Tool 做确定性验证，也可以
直接回答“这一轮模型看到了什么、为什么调用工具、为什么停止”。代价是需要自己维护
适配和评测基础设施；这是为了可理解性与实验控制做出的主动取舍，不是认为现有框架
没有价值。

### Q4. 一次任务从输入到最终完成，完整流程是什么？

**回答：**

任务先被规范化为用户消息并写入 State。每轮开始时，Runtime 检查 abort 和上下文
策略，必要时压缩活跃上下文，然后构造本轮 ContextView，记录模型请求并调用 Provider。
模型输出会被转换成项目自己的 AssistantMessage 并写入 State。

如果输出包含工具调用，Runtime 先经过 Hook 策略门，再执行工具，把成功或失败结果
封装成带对应调用 ID 的 ToolResult，写回下一轮上下文。循环持续到模型输出 final、
工具要求终止、外部中止或回合预算耗尽。对于代码修复，final 只是候选完成，测试、
Goal Check 或官方 Harness 才负责确认补丁是否真的正确。

### Q5. 这个项目最难的技术问题是什么？

**回答：**

最难的是有限模型窗口与完整运行证据之间的冲突。始终发送全部历史会不断增加成本并
最终溢出；直接删除旧消息又会丢失关键命令结果、决策依据和调试证据。

我的解决方案是把完整历史、活跃上下文和模型本轮输入分成三层。Event 和原始消息
追加保存，压缩只重写活跃索引并插入带来源的摘要；模型需要精确值时再通过 Recall
有界读取原消息。这样上下文可以缩小，但 Trace、审计和恢复依据仍然存在。

### Q6. 你做过的最关键架构决策是什么？为什么？

**回答：**

最关键的决策是让系统只拥有一套运行事实和一套普通 Agent 循环。Message、Event、
State 是底层事实语言；子 Agent 仍运行普通循环，Workflow 只负责外层编排，Trace
从 Event 派生，Eval 只负责装配和验证。

如果为多 Agent、Memory 或评测分别发明新的 Runtime，就会出现多套停止语义、错误
协议和轨迹格式。统一核心让复杂能力通过组合获得，同时依赖始终从外围指向核心，核心
不需要知道 MCP、某个 Benchmark 或某种工作流的具体实现。

### Q7. 项目目前的能力边界是什么？

**回答：**

它是面向学习、研究和小团队实验的 Agent Runtime，不是完整生产控制面。当前已经有
工具超时、Hook 拦截、容器化评测、上下文预算和结构化 Trace，但这些能力不等于安全
沙箱、敌对多租户隔离或任意崩溃后的无损恢复。

项目也没有发布最大稳定并发、p95 延迟、完整威胁模型和组件级消融。因此我可以承诺
核心行为可检查、真实任务可运行、结果可评测；不能承诺生产 SLA、所有副作用 exactly-once
或某个单一组件贡献了多少 Benchmark 分数。

### Q8. 三项 Benchmark 成绩如何获得？Baseline 是否公平？

**回答：**

三项公开结果分别覆盖软件工程、终端任务和模型后训练：SWE-bench Pro 使用 GPT-5.4
xHigh，得到 63.20%；Terminal-Bench 2.1 使用 GPT-5.3-Codex xHigh，得到 77.53%；
PostTrainBench 使用 GPT-5.5 xHigh，得到 45.88%。对应 Baseline 是 59.10%、64.70%
和 43.97%。

README 对 Baseline 的定义是同模型、同任务预算下的对照，所以比较目标是尽量隔离
Agent 方案，而不是用更强模型击败较弱模型。这个设计比跨模型比较公平，但当前材料
仍缺重复运行、置信区间和完整逐组件消融，所以结果支持“组合方案有效”，不支持更细的
因果结论。

### Q9. 指标提升究竟来自模型，还是 Agent 架构？

**回答：**

基础模型决定能力上限，Agent 架构决定模型怎样使用上下文、工具和计算预算。采用同
模型、同任务预算 Baseline，是为了减少模型差异，让结果更接近整套 Agent 配置带来的
增量。

但“整套配置优于 Baseline”不等于已经知道提升来自哪一项。Task Tool、压缩、
Handoff、Prompt 和 Workflow 可能共同作用；没有配对消融前，我不会把提升单独归因
给任何组件。准确说法是：现有证据证明系统组合有效，组件贡献仍是待验证假设。

### Q10. 如果重新做一次，你会改变什么？

**回答：**

我会更早把实验设计和实现设计绑定在一起。每增加压缩、Task Tool 或 Handoff，就
同步定义 on/off 对照、任务分层和成本指标，而不是等总分出来后再补归因。

工程上我还会更早设计副作用工具的幂等键与 turn checkpoint，并把真实成功/失败轨迹
复盘纳入发布流程。当前架构已经提供 Event、Trace 和显式策略开关，这些补充可以在
不重写核心循环的前提下完成。

## 3. 核心 Runtime

### Q11. Agent 核心循环具体执行哪些步骤？

**回答：**

一次运行先记录 `agent_start`。每轮依次执行：检查 abort、记录 `turn_start`、应用
上下文压缩、构造 ContextView、记录模型请求、生成并记录模型响应、写入 AssistantMessage、
调度工具调用、写入工具结果、记录 `turn_end`。

循环根据输出和运行边界选择停止原因，最后统一触发 session end Hook 并记录
`agent_end`。这条顺序保证模型决策、环境行动和停止原因都能从 Event Stream 重建，
而不是只留下最终答案。

### Q12. 为什么使用生成器和惰性 Event Stream？

**回答：**

生成器让执行和观察使用同一条边界。调用 `agent.run()` 会先返回 State 和事件迭代器，
调用者消费事件时，模型调用和工具执行才真正推进；因此终端、Trace Writer 或上层
Workflow 可以边运行边观察，而核心不需要依赖任何 UI。

惰性的代价是调用者必须明确消费事件；如果不消费，State 里只有初始化任务，Agent
不会继续工作。这个约束是有意的：谁驱动运行、谁处理 abort、谁观察事件都清楚可见。

### Q13. Message、Event、State 分别解决什么问题？

**回答：**

Message 表达模型可理解的交流事实，例如任务、模型输出、工具调用和工具结果。Event
表达完整运行事实，除 MessageEvent 外还包括 turn、模型请求、工具开始/结束、压缩、
Hook 和停止原因。State 保存追加式 Event Stream，并维护由事件派生的消息快照和
活跃上下文索引。

可以把它们理解为：Message 是对话内容，Event 是运行黑匣，State 是黑匣记录加当前
投影。三者分开后，模型只看到应该看到的消息，而 Trace 仍能解释超时、压缩和停止等
不应伪装成聊天内容的行为。

### Q14. 为什么聊天记录不能直接作为完整状态？

**回答：**

聊天记录不知道一次工具何时开始、是否超时，也不知道模型请求用了多少 Token、哪次
压缩改变了活跃视图、运行为什么结束。只保存聊天内容，事后无法区分模型主动完成与
预算耗尽，也无法还原某一轮模型实际看到的上下文。

所以 transcript 是 State 的一个投影，不是全部事实。Runtime 控制事件留在 Event
Stream，模型可见内容留在 Message，两者通过 MessageEvent 连接。

### Q15. 为什么 State 采用追加式事实，而不是原地修改？

**回答：**

追加式记录能保留因果链，适合回放、审计和派生多个视图。上下文压缩时，系统不会
改写或删除旧消息，而是追加摘要和 ContextCompressionEvent，再改变活跃索引；这样
仍能回答压缩前发生过什么。

StateSnapshot 可以为了读取效率维护缓存，但它必须能从事件重新构建。这个设计减少
Runtime、Trace 和 Eval 各维护一份真相而产生漂移的风险。

### Q16. 模型返回工具调用后，Runtime 如何执行并反馈结果？

**回答：**

Adapter 先把供应商响应规范化为带 ToolCallBlock 的 AssistantMessage。Runtime 按工具
名查找本地 AgentTool，记录 start 事件，依次执行 PRE_TOOL_USE Hook，然后在线程池
中执行未被阻止的工具，并把异常或超时也规范化成 ToolResult。

所有结果最终被封装进一个用户角色的 tool-result Message，写回目标 Agent。下一轮
ContextView 包含原调用和结果，模型由此观察环境并决定继续修正还是结束。

### Q17. Tool Call 和 Tool Result 如何保证正确配对？

**回答：**

每个 ToolCallBlock 都有稳定 `id`，对应结果携带相同的 `tool_call_id` 和工具名。
Runtime 不依赖“结果刚好紧跟调用”这种脆弱位置关系，而是按 ID 建立因果关系。

并行执行时结果可能以不同顺序完成，Runtime 先按 ID 收集，再按照模型原始调用顺序
构造结果 bundle。上下文压缩也会自动对齐调用及其结果，避免只保留一半导致 Provider
协议失效。

### Q18. Agent 有哪些停止条件？final、工具终止、abort、预算耗尽有什么区别？

**回答：**

`final` 表示模型在当前循环中主动给出最终回答，对应 `agent_end(reason="done")`。
`ToolResult.terminate=True` 表示某个工具要求 Runtime 在记录结果后停止，对应
`tool_terminate`。`abort` 来自调用者或时间边界，表示外部取消。

达到 `max_turns` 则是预算耗尽，说明模型没有在允许轮数内结束。四种状态必须分开，
因为只有第一种是模型主动完成；即使是 final，在 Goal Loop 或 Benchmark 中也仍可能
因外部验证失败而继续或被判定未完成。

### Q19. 工具执行异常为什么要返回给模型，而不是直接让 Runtime 崩溃？

**回答：**

参数错误、命令失败和文件不存在通常是任务环境中的可恢复观察。把它们转换为
`is_error=True` 的 ToolResult，模型下一轮可以修正路径、参数或方案；直接崩溃会丢掉
这种自我纠错机会。

这不代表所有异常都应吞掉。Provider 认证错误、State 不变量破坏或宿主进程故障仍应
向调用者暴露。判断标准是：错误是否属于模型可理解并可能修正的工具边界事实。

### Q20. 如何处理模型生成的非法工具参数或不存在的工具？

**回答：**

模型输出阶段先做有限修复：如果工具不在本轮声明中，或参数不是合法 JSON，LLM 层会
附加纠正提示重新请求，最多三次模型调用。它与网络重试分开，避免温度为零时无提示地
重复同一个错误。

如果仍未修复，Runtime 不会执行未知工具，而是生成可见错误结果；具体工具还会在
执行入口验证参数类型、范围和路径。这样协议错误有界，最终仍能进入正常反馈循环。

### Q21. 并行工具调用如何保证结果顺序和状态一致性？

**回答：**

工作线程只执行 `tool.execute`，不直接写 State。Runtime 主线程先按模型顺序记录所有
start 和 Hook 事件，再按实际完成顺序记录 update/end，最终使用调用 ID 按原调用顺序
构造唯一结果 Message。

因此时间线保留真实并发完成顺序，模型输入又保持确定性的调用顺序。每个调用还有独立
更新缓冲区，避免多个线程交叉修改非线程安全的事件账本。

### Q22. 为什么同一批工具中出现 sequential 工具就要串行执行？

**回答：**

`execution_mode="sequential"` 表示该工具可能修改共享资源，或调用顺序本身有语义。
如果同一批中仍让其他工具并行，可能出现一边修改文件、一边读取旧内容等竞态。

当前实现采用保守策略：只要有效调用中有一个 sequential 工具，整批 worker 数就降为
1；只有全部工具都声明可并行时才并发。这可能牺牲部分吞吐，但让共享工作区行为更
容易理解。

## 4. 模型适配

### Q23. 为什么不能把 OpenAI/Anthropic 的响应对象直接存进 State？

**回答：**

供应商对象包含各自的角色、工具、流事件和 SDK 类型。如果直接进入 State，Runtime、
Memory、Trace、Eval 都会被某个 Provider 绑定，换模型会扩散到整个系统，也难以用
Fake Adapter 做确定性测试。

项目在边界处把响应转换成自己的 ContentBlock 和 AssistantMessage。供应商专有字段
若确实需要保留，可以进入 sidecar raw 作为调试证据，但不会成为核心控制语义。

### Q24. Runtime Message、LLM Message、Provider Wire 三层有什么区别？

**回答：**

Runtime Message 面向 Agent 系统，除内容外还有 sender、target、kind、usage 和
sidecar 等路由及运行语义。LLMMessage 是供应商无关的模型请求语言，只保留 role、
有序 ContentBlock 和少量 namespaced extra。Provider Wire 则是某个 API 最终需要的
JSON 或 SDK 对象。

请求方向是 Runtime Message 投影为 LLMMessage，再由 Adapter 翻译成 Wire；响应方向
反过来。每层只表达自己负责的语义，避免路由字段泄漏给 Provider，也避免供应商格式
污染 Runtime。

### Q25. OpenAI Chat、Responses API 和 Anthropic Messages 的差异如何被屏蔽？

**回答：**

统一层定义文本、图片、Thinking、ToolCall、ToolResult 和 TokenUsage；各 Adapter
负责具体映射。例如工具结果在 Runtime 中是同一种 ToolResultBlock，OpenAI Chat
输出为 `role="tool"` 消息，Responses API 输出为 `function_call_output`，Anthropic
则保留在 user 消息的 `tool_result` block 中。

System prompt、工具 Schema、reasoning effort、stop reason 和 usage 也由 Adapter
转换。核心循环只依据规范化 stop reason 和 ToolCallBlock 工作，不包含供应商分支。

### Q26. Thinking、图片和工具调用如何跨 Provider 保留语义？

**回答：**

项目用有序 ContentBlock 序列统一内容：TextBlock、ImageBlock、ThinkingBlock、
ToolCallBlock 和 ToolResultBlock 保留产生顺序。图片携带规范化 MIME 与数据；工具
调用保留 ID、名称和结构化参数；Thinking 保留文本及可用的连续性元数据。

Adapter 只负责把这些块翻译成目标 API 允许的形状。无法进入通用协议但对调试或连续性
有价值的信息放入 namespaced extra 或 raw sidecar，不让“统一”变成静默丢字段。

### Q27. Provider 为什么设计成数据对象，而不是继承体系？

**回答：**

Provider 表达的是一份可序列化配置：API 类型、模型、base URL、密钥环境变量、推理
强度和上下文窗口。真正的行为由 `Provider.api` 在 Adapter Registry 中选择，不需要
为 Ollama、OpenRouter 或每个部署创建子类。

这样配置可以来自 JSON、环境变量或 Run Profile，测试也能直接比较数据。新增全新
wire protocol 时注册 Adapter；同协议的新服务通常只需换 Provider 数据。

### Q28. 限流重试和非法工具调用修复为什么是两种重试？

**回答：**

限流或 500 是 Provider 暂时不可用，请求语义没有错，适合使用有上限的指数退避重放。
非法工具调用则是模型输出语义有错，原样重放可能重复同一结果，需要追加纠正消息后
重新生成，而且尝试次数应更小。

两者的触发条件、修复动作和副作用边界不同，混成一个通用 retry 会掩盖真实失败原因，
也可能对不可重试的认证或 Schema 错误反复请求。

### Q29. 如何支持 Ollama 或其他 OpenAI-compatible 服务？

**回答：**

如果服务兼容 Chat Completions，只需创建 `api="openai-chat"` 的 Provider，设置目标
`base_url` 和模型名；本地无鉴权服务可以不配置密钥。Runtime、Message 和工具调度
都不需要修改。

如果目标服务存在不兼容的 wire 语义，应新增独立 Adapter 并注册新的 API kind，
而不是把例外条件散落到核心循环。

### Q30. 多模型 Registry 有什么用途？为什么需要 strong/fast 等角色？

**回答：**

不同步骤对质量、速度和成本的要求不同。主任务可以使用 strong 模型，摘要、路由或
轻量子任务可以使用 fast 模型；调用方依赖角色别名，而不是在各处硬编码具体模型。

ModelRegistry 将 alias 严格映射到 Provider，可从 JSON、环境变量或单模型配置加载。
部署可以替换别名对应模型而不改工作流代码；未知别名会明确失败，避免静默回退到错误
模型。

## 5. 长上下文

### Q31. 完整历史、活跃上下文和模型本轮输入有什么区别？

**回答：**

完整历史是追加式事实账本，保存本次运行的全部 Message；活跃上下文是其中当前仍参与
后续推理的索引投影，压缩只改变这一层；模型本轮输入再从活跃上下文经过可见性过滤、
角色转换、system prompt 和工具声明组装而成。

三层分别回答“发生过什么”“当前保留什么”“模型这次实际看到了什么”。分层后可以
缩小模型输入而不删除审计证据，也不能用最终 transcript 反推早期某轮的真实请求。

### Q32. 为什么不能简单截断最早的消息？

**回答：**

最早消息可能包含原始任务、验收条件或仍被后续决策引用的精确结果；按位置硬截断还
可能拆开 Tool Call 和 Tool Result，形成供应商无法接受的孤立协议块。

项目先保护 task、system、context、summary 和最近交互，再按完整工具交换或连续历史
区段压缩，并保留来源索引。目标不是假设旧内容都不重要，而是让信息损失可控、可观察、
必要时可恢复。

### Q33. 上下文压缩什么时候触发？阈值如何确定？

**回答：**

Runtime 在每次模型请求前调用 ContextPolicy 中的 CompressionStrategy。是否触发由
具体策略判断；例如 ToolCompact 和 Summarize 都比较当前活跃上下文估算 Token 与各自
配置的 `threshold_tokens`，默认没有策略就不压缩。

阈值不应直接等于模型窗口上限，而应使用“上下文窗口减输出预留再减估算安全缓冲”的
有效输入预算。估算优先采用 Provider 报告的 usage baseline，对新增尾部或压缩后的
视图再使用逐消息估算，从而在准确性与通用性之间折中。

### Q34. Tool Compact、LLM Summary、Agent Compact 有什么区别？

**回答：**

Tool Compact 是确定性低成本策略，只折叠较旧的工具调用/结果交换，保留工具名和短
结果预览；LLM Summary 让独立 compressor 阅读旧历史并生成包含目标、进度、事实、
未决问题和失败尝试的 working memory；Agent Compact 则由主 Agent 通过 compact
控制工具主动提交阶段摘要，下一轮再应用。

前者便宜可预测但理解能力有限；第二种语义更强但增加模型调用和摘要误差；第三种让
当前 Agent 决定何时交接，但依赖它主动维护高质量摘要。TieredStrategy 可以先尝试
便宜策略，再在它无法继续缩减时使用摘要策略。

### Q35. 压缩时如何保护任务目标、最近消息和工具调用配对？

**回答：**

策略通过 `preserve_kinds` 固定 task、system、context、summary 等消息，并用
`keep_recent` 或 `keep_recent_exchanges` 保留最近工作。公共压缩 Runtime 不完全信任
策略返回的索引，还会检查 Tool Call 与 Tool Result 的伙伴关系。

如果策略只选中配对的一侧，Runtime 会保守地取消对这一侧的压缩；一对一 rewrite 还
必须保持消息角色和工具调用 ID 集合不变。宁可多保留上下文，也不破坏模型协议和行动
因果链。

### Q36. 摘要失败或质量不合格时如何降级？

**回答：**

这里要区分策略。Agent 主动 Compact 会在工具入口拒绝空摘要和非法参数；SWE-bench
Chain Handoff 在独立 scratch state 中生成交接文档，异常或空输出时保留原上下文，
不会让交接失败杀死任务。

通用 SummarizeStrategy 当前会把空文本替换成显式占位，并保留 compressor 的请求、
响应、模型和 usage 证据，但还没有自动判断摘要是否语义完整，也没有通用“摘要过大
就回滚”的质量评分器。因此可靠表述是：结构和来源可验证，部分策略有失败降级；摘要
语义质量仍需要评测、Recall 和更严格的应用门继续加强。

### Q37. Recall 如何定位已经被压缩的原始消息？

**回答：**

压缩事件保存被替换的原始消息索引，摘要文本也使用稳定 transcript index 指示来源。
Recall 工具读取 State 的完整 `messages`，按起止索引返回原消息，而不是从摘要里猜测。

返回结果有最大消息数和字符数限制，并带截断说明，所以 Agent 可以恢复某段精确信息，
但不能一次把全部历史重新塞回上下文。这是“原始事实仍在、模型按需查看”的恢复路径。

### Q38. Recall、Memory、Skills、Handoff 分别解决什么问题？

**回答：**

Recall 恢复本次运行早先发生的精确事实；Skills 提供某类任务通常应该怎样做的静态
方法知识；Memory 保存跨运行沉淀的经验；Handoff 在上下文接近窗口上限时生成一份
可独立继续工作的交接说明，并重置活跃窗口。

它们的来源、生命周期和可信度不同：Recall 来自当前 transcript，Skills 来自受控
技能库，Memory 可能过时，Handoff 是当前模型生成的有损阶段状态。统一成一个“记忆”
接口会让模型无法判断信息的新鲜度和证据等级。

### Q39. 文件型 Memory 为什么没有一开始就使用向量数据库？

**回答：**

这个项目中的长期经验是稀疏、高价值且容易随代码变化而过时，不是一个必须大规模
相似度检索的文档库。文件型 Memory 可直接阅读、版本化和审计，模型通过普通 Read
或 Bash 工具按需访问，不需要让 Runtime 隐式注入检索结果。

实现使用 namespace、单写锁、原子替换、容量限制和每次运行的证据目录。未来若场景
确实出现大规模知识和稳定召回需求，可以新增向量型 Memory 实现；不应先引入基础设施
再寻找问题。

### Q40. Memory 过期或错误时如何避免误导 Agent？

**回答：**

Memory 被定义为经验而不是当前事实。注入给模型的策略明确要求：涉及当前文件、配置、
命令和环境时，优先核对工作区；用户纠正、工具证据和当前代码的可信度高于旧摘要。

写入端只保留高价值经验，拒绝过大、结构为空或会抹掉全部既有经验的重写；失败只记录
证据和错误标记，不让 Memory 故障破坏有效任务。它降低风险但不能保证模型永不误用，
所以“读取后核验”仍是必要步骤。

### Q41. 如何证明压缩确实降低 Token，而没有显著损害任务成功率？

**回答：**

机制层可以从 ContextCompressionEvent 读取压缩前后估算 Token、压缩区间和额外
compressor usage，并用压缩测试验证任务、最近消息和工具配对不被破坏。这能证明压缩
确实改变了输入规模和协议仍完整。

效果层必须在同一模型、任务集合和预算下做 paired 实验，对比 none、Tool Compact、
Summary、Agent Compact 或 Handoff 的成功率、Token、成本和失败类型，再报告置信区间。
当前项目有机制和端到端总分，但没有完整公开的组件消融，所以不能声称压缩已经被证明
在所有任务上无损。

## 6. 工具与多 Agent

### Q42. 一个 Agent Tool 的统一接口是什么？

**回答：**

AgentTool 由模型可见声明和本地执行行为组成：名称、描述、JSON Schema 参数、
`execute(call_id, args, abort, on_update)`、执行模式以及可选超时。执行返回 ToolResult，
其中 content 是模型下一轮可见的文本或图片，details 只供本地 UI/Trace，另外带
`is_error` 和 `terminate`。

声明与执行放在同一能力对象中，但投影给模型时只发送安全可序列化的 Tool 定义，
callable 始终留在 Runtime 本地。

### Q43. Bash、Read、Edit 为什么分成不同工具？

**回答：**

Bash 是通用环境操作，能力强但风险和输出不确定性高；Read 提供有界行读取、目录展示
和图片返回；Edit 使用明确的 old/new 字符串替换，能检测目标不存在、不唯一或文件
过大。拆分后模型可以对常见文件操作使用更窄、更可验证的契约。

如果全部塞进 Bash，权限、错误语义和测试都只能围绕任意命令处理。独立工具并不禁止
Shell，而是让高频动作拥有结构化输入、大小限制和更清楚的失败反馈。

### Q44. Hook 能做什么？为什么不能让 Hook 任意修改历史？

**回答：**

当前 Hook 覆盖 session start/end、tool use 前后等有限生命周期点。它可以观察、追加
消息，或在 PRE_TOOL_USE 阶段给出 block reason 阻止调用；每次决策都会形成
HookFiredEvent。

Hook 不能编辑既有事件，因为那会破坏追加式事实和 Trace 可信度。策略如果需要纠正
模型，应追加新的说明或错误结果，让“原行为”和“策略决定”都能被审计。

### Q45. MCP 工具如何转换成普通 Agent Tool？

**回答：**

连接建立后，MCP 客户端发现服务端工具，把其 `inputSchema` 映射为 AgentTool 的参数
Schema，并用 `<server>_<tool>` 前缀避免多个服务器重名。执行 wrapper 再用原始工具名
调用 MCP Server。

返回内容映射到 Runtime 的 TextBlock 或 ImageBlock；暂不支持的音频、二进制资源和
ResourceLink 会变成明确文本占位，而不是静默丢失。映射完成后，MCP 工具走与 Bash、
Read 相同的 Runtime 调度路径。

### Q46. MCP 连接为什么需要 Session 生命周期？

**回答：**

普通工具只是值，绑定后即可执行；MCP 连接包含子进程或 HTTP 会话、后台事件循环和
需要关闭的网络资源。AgentSession 在进入 `with` 时打开 Toolset，构建 Agent，在退出
时统一关闭资源。

由于 Event Stream 是惰性的，必须在 Session 的 `with` 块内消费完事件。如果先退出
再迭代，工具仍在模型声明中但底层连接已经关闭，生命周期会与执行脱节。

### Q47. Task Tool 委派和固定 Workflow 有什么区别？

**回答：**

Task Tool 是动态委派：父模型根据当前上下文决定是否调用、选择哪个 subagent_type，
子 Agent 的结果作为普通工具观察返回。Workflow 是程序确定的编排：例如固定先规划
再执行，或并行多个 Worker 后聚合。

任务路径事先未知、需要模型按现场情况选择专家时用 Task Tool；步骤和验收结构明确、
需要稳定复现与预算控制时用 Workflow。两者都复用普通 Agent run，但决策所有者不同。

### Q48. 父 Agent 与子 Agent 为什么必须使用独立 State？

**回答：**

每个 Agent 有自己的 system prompt、工具、上下文策略和停止预算。如果父子共享一个
可变 State，消息路由、压缩索引和并发工具结果会互相污染，也无法准确计算各自成本。

Task Tool 为子 Agent 创建独立运行，只把任务和可选 context 传入，完成后把最终文本
返回父 Agent。展示层可以合并轨迹，但事实账本不合并，这也是并行子任务安全的基础。

### Q49. 子 Agent 的结果和完整轨迹分别如何返回或保存？

**回答：**

父 Agent 模型可见的是 ToolResult 的文本内容，也就是子 Agent 的 final，或在预算耗尽
时回退到最后一个可用 Assistant 输出。完整子 State 不塞入父模型上下文，而是作为
`sub_events` 等 details 保存在 tool-result sidecar。

Trace 层读取这些 sidecar，把子 Agent Span 平移并挂到父 Task Tool Span 下，形成统一
观察树；父 State 和子 State 本身不被修改。这样结果传递简洁，审计证据仍然完整。

### Q50. Sequential、Planner-Executor、Reflection、Routing、Parallel 分别适合什么任务？

**回答：**

Sequential 适合固定流水线；Planner-Executor 适合先产出可检查计划再操作环境；
Reflection 适合有明确质量标准、可通过 critic 迭代的代码或写作；Routing 适合先分类
再交给单个专家；Parallel 适合独立候选、投票或 map-reduce，之后可用 aggregator 汇总。

选择标准不是哪个更复杂，而是任务结构是否真的需要对应控制流。普通单 Agent 能解决
时不应为了形式使用 Workflow，因为每个额外步骤都会增加调用成本和协调失败面。

### Q51. PDR 与普通并行工作流有什么区别？

**回答：**

普通 Parallel 在同一轮独立运行多个 Worker，最后聚合结果，各 Worker 之间不共享中间
发现。PDR 是多轮的 Parallel-Distill-Refine：每轮并行探索后，由 distiller 提炼有效
假设、已完成工作和死路，下一轮 Worker 在这个 brief 上继续，最后再由 finalizer 输出。

它用顺序轮次复用前一轮信号，适合需要 test-time compute 的难题，但成本明显更高。
若某轮有外部 check 验证某个尝试已完成，PDR 可以提前停止，避免花完整预算。

### Q52. Goal Loop 为什么不能直接相信模型声明“任务完成”？

**回答：**

模型输出 final 只证明它停止生成，不证明测试通过、文件达到目标或评分器认可。Goal
Loop 在普通 Agent run 外层执行 CompletionCheck，可以组合模型声明、命令验证、已执行
证据或独立 judge；未通过时在同一 State 上追加继续任务。

它还独立管理 turn、Token、时间、blocked streak 和 abort，最终区分 complete、blocked、
budget_exhausted、aborted。这样语言声明与环境事实不会混为一谈。

### Q53. 多 Agent 是否一定优于单 Agent？它增加了哪些成本和风险？

**回答：**

不一定。多 Agent 的价值来自明确的任务分解、独立探索、角色差异或外部验证；如果任务
本身简单，额外 Agent 只会重复上下文和模型调用。

成本包括更多 Token、延迟、结果汇总、上下文传递损失和并发工作区冲突；风险包括专家
选择错误、子结果被过度压缩、多个 Agent 在不同假设上发散。项目因此同时保留单 Agent、
动态 Task Tool 和确定性 Workflow，由任务结构决定使用哪一层。

## 7. Trace 与评测

### Q54. 为什么 Trace 必须从 Event 派生，而不是解析终端日志？

**回答：**

终端日志面向人类阅读，格式会变化，也经常截断，无法可靠表达 Tool Call ID、模型本轮
输入、压缩索引和停止原因。Event 是 Runtime 在行为发生时记录的结构化事实，Trace
只负责序列化和派生视图。

这样同一 Event Stream 可以生成 transcript、Span、ModelTurn 和成本，而不需要每个
消费者重新猜测。Trace 失败也不能反向改变已经完成的 Agent 结果。

### Q55. JSONL 为什么适合长时间增量写入？

**回答：**

Trajectory v5 第一行写 header，后续一行一个完整 Event。实时 Writer 只追加自上次
flush 后的新事件，长任务不需要反复重写不断增长的 JSON 数组，Viewer 也可以边运行
边 tail。

完整行是天然恢复边界：崩溃时最多忽略不完整尾行，已有事件仍可读取。最终 writer
可以再用原子 whole-file 写入重建规范文件。

### Q56. Span 和 Model Turn 分别提供什么观察视角？

**回答：**

Span 把线性事件配对成时间结构，展示 Agent、Turn、Model、Tool、Compression 和子
Agent 的父子关系、耗时与错误，适合定位慢在哪里、失败发生在哪个操作。

ModelTurn 则把一次 ModelRequest 与对应 Assistant 输出配对，保留当时输入、可用工具、
模型、usage 和响应，适合分析模型行为或导出训练数据。一个偏运行时间结构，一个偏
模型决策样本，都从 Event 派生。

### Q57. Provider raw 为什么放在 sidecar，而不是主轨迹中？

**回答：**

每轮 raw request 可能重复携带越来越长的历史，直接嵌入每个事件会让长会话文件接近
平方增长，也会把供应商调试字段混入规范协议。

Writer 把 raw blob 外置到相邻 `*.raw.jsonl` 池，主轨迹只保留稳定 `raw_ref`，最终
写入还可以去重。普通 Trace 没有 raw 仍可使用；Wire Debug 按需解析 sidecar，同时
权限和保留策略也能单独控制。

### Q58. Trace Viewer 如何展示父子 Agent、工具错误和上下文压缩？

**回答：**

Viewer 左侧把 Event 派生成 Agent→Turn→Model/Tool/Compression 的 Span 树，Task
Tool 下内联子 Agent Span；中间提供时间瀑布和 messages/events/model turns 三种流；
右侧 Inspector 展示选中对象及 Wire Debug。

工具错误和压缩使用独立视觉标记，顶部汇总错误数、压缩次数、子 Agent、耗时、Token
和退出原因。UI 只读取轨迹并派生展示，不补造事件，也不控制 Runtime。

### Q59. Token usage 如何转换为成本？未知模型价格如何处理？

**回答：**

Adapter 将供应商 usage 规范化为 input、output、cache read、cache write 四类 Token，
ModelResponseEvent 同时记录实际模型 ID。RunCost 按模型聚合调用，再与 PriceBook 的
费率计算成本，并递归统计 Task Tool sidecar 中的子 Agent 调用。

如果 usage 缺失就不伪造调用；如果模型没有价格，Token 仍计入，但美元贡献暂记为零，
模型进入 `unpriced_models`，总价明确是下界而不是“真实零成本”。

### Q60. Suite、Backend、ArtifactStore 为什么必须正交？

**回答：**

Suite 定义任务语义和公私输入；Backend 定义在哪里以及如何启动、等待执行；
ArtifactStore 定义输入、结果、轨迹和 manifest 以什么字节通道传递。三者变化原因不同，
混在一起会让每个 Benchmark 都绑定某种 Docker 和存储实现。

正交后，同一 Suite 可以在 LocalProcess 调试、LocalDocker 隔离运行或 RemoteDocker
扩展；Store 也可以根据网络方向选择共享目录或 HTTP，而 Container Task 逻辑不变。

### Q61. LocalProcess、LocalDocker、RemoteDocker 的适用场景是什么？

**回答：**

LocalProcess 启动快，适合开发和小型无隔离任务，但固定 workspace 不能安全并发；
LocalDocker 为每个实例提供独立环境，可运行真实依赖并支持 detached batch；
RemoteDocker 把计算移到远程 daemon，适合多机或本机资源不足的实验。

三者消费同一 RunSpec。选择主要取决于隔离需求、镜像、网络方向和 Host 是否需要离线，
而不是由 Benchmark 业务代码硬编码。

### Q62. 为什么 result.json 和 trajectory.jsonl 要分开？

**回答：**

`result.json` 是任务产品和可选评分，例如补丁、答案或 verdict；`trajectory.jsonl` 是
Agent 如何得到它的过程。评分器通常只需要稳定结果 Schema，不应解析庞大轨迹；调试
工具则需要完整事件，不应把它当提交答案。

分开后可以独立重评分、控制轨迹保留与隐私，也能以 result 的存在作为长任务终态事实。

### Q63. 如何防止 gold answer 或私有测试泄漏给 Agent？

**回答：**

Suite 在 Host 侧把输入拆成两条通道：`task_input` 清洗后写入 `input/instance.json`，
只包含模型完成任务需要的公开信息；`eval_inputs` 写入 `input/eval.json`，只传给运行
环境内的 evaluate hook 或外部评分器。

Container Task 构造 Prompt 时只读取前者。Oracle 模式可以显式使用完整参考数据做
模型无关的布线检查，但必须单独标记，不能与模型成绩混合。

### Q64. Agent 失败、评分不通过和基础设施故障如何区分？

**回答：**

Agent 失败指运行完成但没有形成有效产品，例如预算耗尽或工具错误未修复；评分不通过
指产品已产生并被 verifier 判错，这是有效实验结果；基础设施故障是容器启动、网络、
Store 或 Provider 暂时异常导致运行链路未完成。

三者必须分别记录。把评分失败当基础设施异常重跑，会选择性地把失败刷成成功，破坏
Benchmark 统计；把容器故障当模型失败，又会错误评价 Agent 能力。

### Q65. 哪些失败可以重试，哪些不能重试？

**回答：**

限流、暂时 500、容器或传输异常等尚未形成有效任务结果的故障可以有限重试；模型非法
工具调用可以带纠正提示有限重生成。已完成但测试未通过、exit code 非零或 verifier
判错不应自动重跑成成功。

对有副作用的工具还要更谨慎：如果不能确认操作是否执行，盲目重试可能重复修改。
当前系统没有通用 exactly-once 协议，这类恢复需要幂等键、checkpoint 或人工确认。

### Q66. 长时间评测中 Host 进程退出后如何找回任务？

**回答：**

可脱离 Host 的 Backend 使用 submit/reconcile 两阶段。每成功启动一个实例，就把可
序列化 RunHandle 原子写入 batch manifest；新进程读取 manifest，通过 backend poll
重新协调，`result.json` 的存在被视为终态事实。

这解决的是 Host 协调进程退出后找回仍在运行或已完成的容器，不等于 Agent 在任意工具
副作用中间实现无损续跑。LocalProcess 不能脱离 Host，因此不假装支持 submit/poll。

### Q67. 如何保证不同 Benchmark 的运行配置可复现？

**回答：**

统一入口把 Suite、instance、Provider/model/reasoning、Agent flavor、回合与时间预算、
并发、镜像和评分方式显式化；Run Profile 用 JSON 保存公开 env 与 CLI 选项，显式命令
行仍可覆盖，密钥只由环境注入。

每个实例使用独立 run directory，保留 result、trajectory、raw pool、配置身份和评分
证据。真正可复现的是“配置 + 环境 + 任务 + 轨迹 + 评分路径”，不是单独保存一个最终
分数。

## 8. 实验结果

### Q68. SWE-bench Pro 63.20% 的具体实验配置是什么？

**回答：**

已发布口径是 GPT-5.4、xHigh，在 SWE-bench Pro 上得到 63.20%，使用类似 ChainSWE
的链式流程并增加 Task Tool。仓库中的对应 repo-chain runner 使用
`ScaleAI/SWE-bench_Pro` test split；完整 split 有 731 个 instance，deep chain
manifest 覆盖 47 条 multi-issue chain、261 个 chain instance。正式运行入口使用
OpenAI Responses，每个 instance 最多 250 个 solver turn，默认保存完整 trajectory；
272,000 Token 窗口按 80% 留出输出余量，在估算活跃上下文达到 217,600 Token 时
触发 handoff。

这些数字中，模型、分数和链式 Task Tool 是结果页公开口径；731、250、217,600 和
trajectory 是当前 runner 与设计文档可核对的运行条件。当前仓库没有把 63.20% 对应的
不可变 run manifest、每个开关和实例级产物一起发布，因此我不会继续补造并发数、
Prompt 版本或每次 handoff 次数。严格复现实验时还需要锁定 commit、镜像 digest、
chain manifest、Agent flavor、所有策略开关、密钥 lane 和官方评分器版本。

### Q69. 为什么 63.20% 对 59.10% 是提升 6.94%，而不是 4.10%？

**回答：**

4.10 是绝对百分点差，即 `63.20 - 59.10`；6.94% 是相对提升，即
`(63.20 - 59.10) / 59.10`。两种说法都正确，但含义不同。

简历使用的是相对提升，因此必须同时保留项目分数和 Baseline，避免让人误以为提高了
6.94 个百分点。口头表达我会说“提高 4.10 个百分点，相对 Baseline 提升 6.94%”。

### Q70. Terminal-Bench 为什么提升更明显？

**回答：**

可以确认的是 Terminal-Bench 2.1 从 64.70% 提高到 77.53%，绝对提高 12.83 个
百分点、相对提高 19.83%。它的任务天然依赖长时间 Shell 操作、环境观察、失败修正
和验证，和显式工具循环、较长命令超时及 Task Tool 的能力形态更匹配，所以这是一个
合理机制假设。

但当前没有按任务类型拆分的成绩，也没有 Task Tool on/off 或工作流消融，不能据此
断言“提升更大就是由 Task Tool 导致”。模型差异、Baseline 实现、任务组成和预算利用率
也可能共同影响结果。准确结论仍然只是：组合方案在该 Benchmark 上观测到更大增量，
原因需要配对实验验证。

### Q71. 每任务成本如何计算？是否包含子 Agent 调用？

**回答：**

每次 `ModelResponseEvent` 记录模型 ID 以及 input、output、cache read、cache write
四类 usage。成本层按 PriceBook 将各类 Token 换算成美元，先按模型聚合，再把整批
有效任务成本除以任务数。`RunCost.from_run` 会递归读取 Task Tool sidecar 中保存的
子 Agent events，因此按这套实现计算时，子 Agent 模型调用包含在父任务成本内。

未知价格的模型仍统计 Token，但进入 `unpriced_models`，美元总价只能视为下界；
usage 缺失也不会被伪装成一次零成本调用。已发布的 SWE-bench Pro 和 Terminal-Bench
平均成本分别是 7.7823 美元和 0.5667 美元，但当前材料没有发布两次成绩的完整成本
manifest，因此不能声称它们已经用完全相同的价格快照和缺失值规则重新核算过。

### Q72. Baseline 是否使用相同模型、推理强度和任务预算？

**回答：**

项目对 Baseline 的公开定义是“同模型、同任务预算”，这排除了用更强模型或更多回合
制造提升；项目结果行同时公开了 xHigh 推理设置。这个口径足以说明比较目标，但不足以
完成第三方审计。

完整公平性还要逐项核对模型快照、API、reasoning effort、Prompt、工具集合、上下文
窗口、最大模型 Token、子 Agent 是否共享预算、墙钟超时、重试和评分器版本。当前材料
没有把这些 Baseline 运行参数全部固化到同一 manifest，所以我会说“按项目定义是同
模型同预算对照”，不会扩大成“所有变量已被证明完全一致”。

### Q73. 为什么没有报告置信区间或多次运行方差？

**回答：**

当前发布的是完整评测提交的点估计，没有多 seed 重复运行和任务级不确定性分析。
官方评分器可以确定性判断某个产物是否通过，却不能消除模型采样、供应商服务和工具
环境带来的运行波动，因此一次总分不能证明提升稳定在某个置信水平。

补充方案是在相同 instance 上让 Baseline 和方案各运行多次，保留配对结果，用 paired
bootstrap 或按任务配对的区间报告分数差，同时给出成本和失败类型分布。实验成本是没有
立即这样做的现实原因，但不是省略统计边界的理由；在补齐之前，简历数字应被称为已发布
结果，而不是稳定性结论。

### Q74. Task Tool、压缩、Handoff 各自贡献了多少？

**回答：**

当前无法给出各自贡献的百分点。仓库提供这些组件和显式开关，端到端结果证明整套配置
有效，但没有逐组件消融表；组件之间还可能有交互，例如 Task Tool 增加模型调用，
Handoff 和压缩则改变它能够持续工作的上下文长度。

因此不能用总提升减法做事后归因，也不能把机制解释当实验结果。现阶段正确口径是
“组合方案有效，单组件因果贡献未知”；任何具体百分点都要来自固定其他条件的配对消融。

### Q75. 如果做消融实验，你会如何设计？

**回答：**

先固定数据 split、模型快照、API、xHigh、Prompt、工具版本、官方评分器、最大回合、
总模型 Token、墙钟时间和重试策略。第一阶段在同一组 instance 上分别做 Task Tool
on/off、Handoff on/off、compression/none 三组配对实验；不能只固定 turns，因为
子 Agent 会改变实际模型调用量，还要同时报告 Token 和美元成本。

第二阶段在分层样本上做小型析因实验，观察 Task Tool、Handoff 和 compression 的
交互，再把最有价值的组合跑完整 split。每个 arm 多次运行，报告成功率差、paired
bootstrap 区间、成本、turn、工具错误、压缩次数和按任务类型拆分的结果。这样既能回答
“是否有效”，也能回答“以多少额外计算换来了什么”。

### Q76. 能否讲一个真实成功案例和一个失败案例？

**回答：**

当前仓库只保留总分、runner 和通用轨迹能力，没有发布可核对 instance id 的成功/失败
复盘，`evals/out` 也不包含真实运行产物，所以不能从现有材料编造两个案例。面试时若被
问到这里，我会明确这一点，而不是把假想流程说成亲历事实。

正式面试前应从同一次 run 中各选一个官方评分通过和未通过的 instance，按同一结构准备：
任务与难点、关键 Model Turn、工具证据、压缩或子 Agent 行为、最终 verifier 结果，以及
成功的关键决策或失败的最早偏离点。失败可按定位错误、工具执行失败、上下文丢失、验证
不足和预算耗尽分类，但只有附上真实 trajectory 与评分产物后，才能称为真实案例。

### Q77. Benchmark 成绩能否代表生产环境效果？

**回答：**

不能直接代表。Benchmark 的价值是任务集、预算、容器环境和评分器相对稳定，可以比较
Agent 配置；它覆盖了软件修复、终端操作和模型后训练三种长任务，说明系统不只在玩具
Demo 上工作。

生产环境还包含私有任务分布、权限、脏数据、人工协作、延迟和成本 SLO、安全事件以及
长期漂移。把系统用于真实业务前，需要用目标任务回放和 shadow run 验证成功率、风险
操作、人工接管率、p95 延迟和单位价值成本。Benchmark 是外部有效性的一个证据，不是
生产 SLA 或安全认证。

## 9. 压力问题

### Q78. 项目叫 Simple，但代码和模块很多，是否自相矛盾？

**回答：**

这里的 Simple 指核心运行模型和依赖方向简单，不是文件数量必须少。最小路径仍然是
项目自有 Message/Event/State、一个显式 Agent 循环、一个 Provider 和几个 Tool；
MCP、Memory、Workflow、Trace Viewer、Docker Eval 都是可选外围层。

把不同生命周期拆成小模块反而避免一个“万能 Agent”隐藏状态和副作用。当然模块增长
确实会消耗理解预算，所以新能力必须能映射到既有协议、保持核心不反向依赖外围，并有
独立反馈信号。Simple 是受约束的架构目标，不是“功能越少越好”的口号。

### Q79. 为什么不用百万 Token 上下文直接放入全部历史？

**回答：**

窗口大不等于每轮都应该重发全部历史。长任务每一轮都携带增长中的历史，会持续增加
输入成本和延迟；旧工具输出还有大量重复噪声，并可能让关键目标被埋没。不同 Provider
的窗口和计费也不一致，把系统正确性绑定到某个超大窗口会失去可移植性。

项目因此追加保存完整事实，只向模型提供受预算控制的活跃视图，用压缩减少冗余、用
Recall 恢复精确原文。若某个模型确实支持百万 Token，也可以提高阈值或关闭压缩；是否
更好应在同任务、同成本口径下测量，而不是假设更长上下文必然提高成功率。

### Q80. 为什么不用数据库或消息队列保存 State？

**回答：**

当前 State 的所有者是一次 Agent run，主要操作是按顺序追加 Message/Event 并派生
ContextView；进程内对象和 JSONL artifact 已能满足教学、调试和单任务回放。引入数据库
或消息队列会增加 schema migration、事务、部署和故障模式，却不能自动解决工具副作用
一致性。

评测层已经把 ArtifactStore 和 Backend 放在外围，可按部署需要换成本地目录、HTTP 或
远程搬运。只有出现多进程共同写状态、强查询、跨机 durable scheduling 或明确 RPO/RTO
时，才应在控制面增加数据库和队列，而不是让核心 Runtime 直接依赖它们。当前取舍也
意味着 Agent State 本身还不是完整的跨进程恢复协议。

### Q81. 如果工具已经产生副作用，但进程在记录结果前崩溃怎么办？

**回答：**

当前系统不能保证 exactly-once。Runtime 会先记录工具开始、执行工具，再记录结束和
ToolResult；进程若在副作用完成后、结果持久化前退出，恢复方无法仅凭 State 确认是否
应该重放，盲目重试可能重复 Edit、命令或外部 API 操作。

补齐方案是给副作用调用稳定 operation id 和幂等性声明：只读操作允许重放，幂等写按
id 去重，非幂等操作恢复时先查询外部事实，无法确认则人工批准、补偿或 abort；同时在
turn 边界保存版本化 checkpoint，并用故障注入覆盖工具执行和事件 flush 前后。这是
明确的后续设计，不是当前已经实现的无损续跑能力。

### Q82. 如何防御 Prompt Injection、恶意仓库和 Bash 数据外传？

**回答：**

首先不把模型当安全边界。当前已有工具入口校验、超时和输出预算、PRE_TOOL_USE Hook
拦截、容器化 Eval、公私评分输入分离以及环境注入密钥；这些机制能限制误操作并保留
证据，但不能阻止拥有宽权限 Bash 的模型被恶意文件诱导读取或外传数据。

面对不可信任务，需要在 Runtime 外强制最小权限：临时 workspace、非 root、只读基础
文件系统、网络默认拒绝或 allowlist、短期最小权限凭据、CPU/内存/进程/磁盘 quota，
高风险命令走结构化策略或人工审批，Trace 写入前做秘密扫描和访问控制，并用 prompt
injection、路径逃逸与外传用例回归。当前项目不是通用安全沙箱，不能把这些建议描述成
已经完成的生产防护。

### Q83. 这套系统是否能用于多租户生产环境？

**回答：**

当前不能按多租户生产平台承诺。每实例目录和容器解决了评测运行的基础隔离，但项目没有
完整的租户身份、授权、密钥托管、网络策略、资源配额、持久调度、审计保留、SLO 和事故
响应体系；“能并发跑多个容器”不等于通过了敌对租户隔离。

可行路径是把现有 Runtime 作为受限 Worker 内核，在外部建设控制面和强沙箱：控制面
负责租户、队列、预算、策略与审计，Worker 只拿单任务短期凭据和临时工作区。完成威胁
建模、渗透与隔离测试之前，项目定位仍是学习、研究和小团队实验。

### Q84. 当前最大的技术债是什么？

**回答：**

按项目当前的研究与简历场景，最大的技术债是实验事实还没有和不可变证据完全绑定。
已有三项总分、runner、Trace 和官方评分路径，但还缺统一 run manifest、实例级成功/
失败复盘、重复运行区间、组件消融和跨 Benchmark 一致的成本明细。这会限制结果复现和
架构归因，比再增加一个 Workflow 更值得优先解决。

如果目标切换到生产，优先级会变成副作用 crash consistency 和安全隔离。两者不能混为
一谈：前者是当前研究结论的可信度债务，后者是扩大部署范围前必须补齐的工程债务。

### Q85. 如果只能保留三个能力，你会保留什么？

**回答：**

第一，保留显式的模型-工具循环和 Message/Event/State 事实协议，因为这是所有行为的
共同语义；第二，保留“完整历史 + 活跃视图 + 压缩/Recall”的上下文机制，因为它让
任务能够真正跨长周期继续；第三，保留 Event 派生的 Trace 和外部 Eval，因为没有事实
观察与环境验证，就无法判断 Agent 是完成任务还是只声称完成。

MCP、文件型 Memory、多种 Workflow 和 Viewer 都很有用，但可以在这三个基础之上重新
组合。被保留的三项分别回答“如何行动”“如何持续”“如何证明”，共同构成项目最小闭环。

### Q86. 如何新增一个 Provider、Tool 或 Eval Suite？

**回答：**

新增 Provider 时，先确认现有 LLM Request/Response 能表达共同语义，再实现 Adapter，
完成 request 到 wire、response 到统一 StreamEvent 的双向转换，规范化 stop、usage、
thinking 和 tool call，注册 API kind，并用 stub SDK 验证请求形状；Provider SDK 不能
进入 core 或 State。

新增 Tool 时实现名称、描述、JSON Schema 和执行函数，在入口校验真实参数，把可恢复
错误转成 ToolResult，并声明 timeout、输出上限、abort 和 sequential/parallel 语义；
有连接资源的集成再增加 Toolset/Session。新增 Eval Suite 则分别实现 Host Half 和轻量
Container Half，隔离 task_input 与 eval_inputs，复用统一 result/trajectory key，并先
用 LocalProcess、Fake 和 Oracle 验证，再接 Docker 与官方评分器。三个扩展都遵守同一
原则：适配发生在边界，不为单个扩展修改核心循环。

### Q87. 如果 Benchmark 分数下降，你会怎样通过 Trace 定位？

**回答：**

先排除实验漂移：核对数据、模型/API、reasoning、Prompt、预算、镜像和评分器，再把
基础设施失败从真正评分失败中分开。然后对同一批 instance 做新旧版本配对，按退出原因、
工具错误、turn/Token/成本、压缩次数和子 Agent 使用情况聚合，找出回归集中在哪类任务
和哪个阶段。

对代表性实例沿 Event 顺序检查：ModelRequestEvent 还原该轮真实输入，Model Turn 对比
模型决策，ToolCall/ToolResult 判断环境反馈，CompressionEvent 检查关键信息何时离开
活跃视图，Span 定位超时，最后核对产物与官方 verifier。只有怀疑 Adapter 时才进入 raw
sidecar 比较 Provider wire。定位到最早分叉点后，把它固化为 Fake、单元或最小 Eval
回归测试，再重跑配对样本确认修复，而不是只看最终答案猜原因。

## 10. 证据索引与面试使用建议

回答不需要背文件名，但准备时应能把主张落到以下证据：

| 主题 | 设计说明 | 代码或运行证据 |
| --- | --- | --- |
| 核心循环与状态 | [03](03-domain-model-and-data-flow.md)、[04](04-agent-runtime.md) | `messages.py`、`protocols.py`、`core.py` |
| Provider 边界 | [05](05-model-access.md) | `llm/types.py`、`llm/provider.py`、`llm/adapters/` |
| 上下文与长期信息 | [07](07-context-and-long-horizon.md) | `context_view.py`、`compression/`、`memory/`、`tools/recall.py` |
| Tool 与多 Agent | [06](06-tools-and-integrations.md)、[08](08-agent-composition-and-workflows.md) | `tools/`、`mcp/`、`workflow/` |
| Trace 与成本 | [09](09-trace-and-observability.md) | `trace/`、`model_metadata.py`、Trace Viewer |
| Eval 与恢复 | [10](10-evaluation-and-operations.md) | `src/simple_long_horizon_agent/evals/protocols.py`、`src/simple_long_horizon_agent/evals/runner.py`、`src/simple_long_horizon_agent/evals/batch.py` |
| 实验与边界 | [13](13-interview-deep-dive.md)、[简历描述](15-resume-project-description.md) | `README.md`、`evals/swebench/`、`evals/harbor/`、`runs/` |

使用顺序建议是：先熟练 Q1、Q4、Q5、Q8 的整体叙事，再选择 Runtime/上下文和
Trace/Eval 两条自己最熟悉的分支深入。Q68～Q77 所有数字必须坚持相同口径；Q76 的
实例案例需要用本人真实运行产物补齐。遇到现有证据不支持的问题，使用“当前能证明什么、
不能证明什么、下一步如何验证”的结构，不把未来方案说成已经实现。
