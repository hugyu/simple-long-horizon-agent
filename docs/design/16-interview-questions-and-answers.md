# 16. Agent 项目面试问题与参考回答

本文逐项回答[第 14 篇面试问题地图](14-interview-question-map.md)中的问题。回答重点是
问题、设计、流程、边界和取舍；代码只作为事实锚点，不要求面试官阅读仓库。

## 0. 使用规则与证据标签

回答使用三种标签：

- **[事实回答]**：当前代码、测试、README 或设计文档能够直接支撑；
- **[模拟回答]**：仓库不能证明团队分工、个人经历、私有实验细节等事实，给出的是合理
  表达模板，必须替换为候选人的真实情况；
- **[设计回答]**：当前项目没有实现或不以此为目标，回答的是合理演进方案，不能说成
  已上线能力。

面试时不需要念标签，但必须保留同样的事实边界。一个完整回答通常采用：

```text
问题是什么 → 为什么简单方案不够 → 当前设计 → 运行流程 → 收益与代价 → 证据/限制
```

### 本章审查

- **覆盖审查**：定义了后续每题必须使用的事实边界；
- **事实审查**：没有把模拟回答或未来设计写成当前能力；
- **逻辑审查**：后文若与标签冲突，应以后文降级表述而不是扩大事实；
- **风险审查**：所有 `[模拟回答]` 都需要候选人亲自替换，不能原样背诵。

## 1. 面试官首先会判断什么

### Q1：这份简历首先会被验证哪些内容？

**[事实回答]** 我会把面试官的验证目标归纳为五项：项目所有权、Runtime 核心机制、
复杂模块的设计必要性、异常与安全边界、Benchmark 的实验可信度。原因是简历同时提出
“从零设计”、完整 Agent 技术链路和三项量化提升；最合理的面试策略不是逐个背模块，
而是先验证这些强主张能否形成一条自洽的因果链。

### Q2：P0、P1、P2 应该怎样准备？

**[事实回答]** P0 要达到作者级：能画流程、写伪代码、讲不变量和失败路径；P1 要能
比较替代方案并解释成本；P2 不要求声称已经实现，但要能把现有边界平滑推演到生产
方案。准备顺序应先完成 P0，再为每条 P0 准备至少两轮追问，最后才准备 P2。

### 本章审查

- **覆盖审查**：覆盖了问题地图第 1 节的五类判断与三级优先级；
- **事实审查**：这是面试策略，不是项目实现声明；
- **逻辑审查**：后文所有回答按这五项展开；
- **风险审查**：不能用 P2 的“合理设计”回答冒充现有生产能力。

## 2. 从简历文字到提问方向

### Q1：如何从简历的一句话预测追问？

**[事实回答]** 每个强动词和专有名词都是一个可验证承诺。“从零”对应所有权和替代
方案；“显式循环”对应控制流；`Message/Event/State` 对应数据语义；“控制 Token”
对应压缩前后实验；“相对提升”对应公平 Baseline 和计算口径。回答时应先复述该简历
信号解决的问题，再说明设计与证据，避免只解释名词。

### Q2：哪些简历信号风险最高？

**[事实回答]** 风险从高到低是：三项 Benchmark 提升、项目作者/从零、精确信息恢复、
控制 Token、多种工作流名称。数字容易被审计；所有权需要真实经历；“精确”和“控制”
隐含效果承诺；工作流名词越多，随机抽问面越大。

### 本章审查

- **覆盖审查**：问题地图表格中的每类信号都被纳入后续专题；
- **事实审查**：没有根据代码推断候选人的个人贡献；
- **逻辑审查**：强主张需要强证据的原则与 Benchmark 章节一致；
- **风险审查**：最终简历应减少无法承受两轮追问的名词。

## 3. 一场面试可能采用的完整路径

### Q1：怎样用一个案例贯穿整场面试？

**[事实回答]** 我会选“修改仓库并通过外部测试”的长任务：任务先成为 Message 并进入
State，Runtime 构建当前 Context，模型决定调用 Read/Bash/Edit，工具结果追加回状态，
上下文接近预算时压缩旧内容并保留 Recall 路径，必要时委派子 Agent，最终模型给出
候选完成，再由测试或 Eval scorer 判断客观成功。Event 派生 Trace，Eval 保存 result
和评分证据。这一个案例可以连起控制流、数据流、上下文、工具、委派和验证。

### Q2：为什么按“宽到窄再反证”回答？

**[事实回答]** 先讲系统目标和边界，面试官才能理解局部设计；再深入一个模块可以验证
真实掌握；最后用失败、替代方案和实验限制检查答案是否过度包装。这个顺序也符合项目
自身“核心 Runtime → 能力层 → 编排 → Trace/Eval”的依赖方向。

### 本章审查

- **覆盖审查**：覆盖项目定位、主链路、深挖、数字审计和反事实；
- **事实审查**：“修改仓库”是项目支持的典型场景，不声称是某次已公开运行；
- **逻辑审查**：模型 final 始终只被称为候选完成；
- **风险审查**：正式面试必须换成自己真实做过的一次任务。

## 4. 项目定位与所有权验真

### 4.1 项目解决什么问题

#### Q1：请用两分钟介绍项目，不要罗列模块。

**[事实回答]** Simple Long Horizon Agent 解决的是：模型怎样在有限上下文中持续观察
真实环境、选择行动、读取结果、修正方案，并让完成结论接受外部证据验证。模型只负责
产生下一步决策，Runtime 负责状态连续性、工具生命周期、停止与预算；上下文层解决
长期运行的信息选择；Trace/Eval 让过程和结果可解释、可比较。项目选择小而显式的核心，
因为目标是教学、实验和真实多步骤任务，而不是先建设生产级多租户平台。

#### Q2：“长周期任务”如何定义？

**[事实回答]** 它不是单一的“上下文很长”。更准确地说，任务需要跨多个模型回合与
外部行动推进，环境状态会变化，后续决策依赖前面真实结果，而且完成需要检查。回合、
时间、上下文和工具链长度只是表现，核心是存在持续的“观察—决定—行动—验证”闭环。

#### Q3：为什么单轮回答和普通 ReAct 不够？

**[事实回答]** 单轮回答不能执行和观察真实行动。最小 ReAct 能循环工具，但长任务还
需要明确状态事实、有限上下文、停止原因、中止、并行工具、Provider 隔离、子任务边界、
轨迹和外部评分。项目不是否定 ReAct，而是把它作为核心，再补足长运行所需的工程边界。

#### Q4：项目目标用户和场景是什么？

**[事实回答]** 主要面向学习者、Agent 开发者、小团队和评测研究者。场景包括确定性
教学 Demo、仓库修改和终端任务、子 Agent/工作流实验，以及在本地或容器中运行
Benchmark。它不定位为组织级 Agent SaaS 或不可信代码的完整安全平台。

#### Q5：“轻量级”体现在哪里？

**[事实回答]** 轻量不等于功能少，而是核心循环直接、依赖方向清楚、配置显式、没有
动态插件魔法。一个 Agent 由生成函数、工具集合、Context Policy 和 Hook 组成，外围
能力通过普通协议组合。衡量方式更适合用“能否沿一次运行读完核心控制流、替换某层
不波及其他层”，而不是仅用代码行数。

#### Q6：最核心的三个部分是什么？删掉外围后还是长周期 Runtime 吗？

**[事实回答]** 第一是显式多轮循环和停止语义；第二是 Message/Event/State 形成的运行
事实；第三是模型—工具—结果反馈边界。删除 Memory、Workflow 和 Viewer 后，它仍是
一个可运行的长周期 Agent 核心，只是信息跨度、复杂协作和可视化能力下降。反过来，
没有前三者，外围模块无法形成统一系统。

#### Q7：为什么没有直接使用 LangGraph、AutoGen 等框架？

**[事实回答]** 项目目标之一是让学习者能够检查模型调用、状态变化、工具反馈和停止
条件。成熟框架的图执行、动态注册和大量配置适合更复杂产品，但会把本项目最想研究的
机制藏起来。代价是需要自己维护 Provider、工具和 Eval 边界；收益是行为透明、容易做
受控实验。若未来场景需要分布式 durable execution，可以在外围接调度器，而不是先把
核心替换成重量级图框架。

### 4.2 你究竟做了什么

#### Q8：个人项目还是团队项目？“作者”和“核心开发者”哪个准确？

**[模拟回答]** “这是一个 `[个人/团队]` 项目，周期是 `[时间]`。我的准确身份是
`[项目作者或核心开发者二选一]`，我负责 `[目标、架构、模块、实验]`；其他成员负责
`[真实分工]`。简历最终只保留一个准确称谓。”仓库不能证明团队结构，因此不能从当前
文档虚构人数或贡献比例。

#### Q9：哪些是你提出、实现、接入或维护的？

**[模拟回答]** 建议按四列作答：“我提出并落地的是 `[A]`；我参与实现的是 `[B]`；
我基于外部协议接入的是 `[C]`；我只做过使用或维护的是 `[D]`。”例如 MCP、官方
Benchmark Harness 天然包含外部协议和工具，不能描述为全部原创；核心 Runtime 是否
由本人从零完成必须按真实经历说明。

#### Q10：项目怎样演进？三个最重要决策是什么？

**[模拟回答]** 合理但需核实的叙述是：先完成 Fake Provider 驱动的最小模型—工具循环，
再把消息和事件变成项目自有协议，随后增加上下文压缩与恢复，最后建设组合、Trace 和
Eval。三个决策可以从“Provider 对象不进入核心”“历史事实与活跃上下文分离”“Eval
任务、执行环境和产物存储正交”中选择，但必须说明自己真实参与的决策过程。

#### Q11：错误决策、最难 Bug、没有你会缺什么？

**[模拟回答]** 必须准备真实案例，使用“症状—错误假设—证据—根因—修复—代价”的
结构。可参考的技术类型包括：压缩后仍采用旧 usage baseline 导致 Token 估算失真、
工具 call/result 被拆开导致 Provider 拒绝、惰性事件未消费导致以为 Agent 已运行。
这些是代码能反映的风险类型，但不能冒充候选人的亲身经历。

#### Q12：最熟和相对不熟的模块是什么？重做会保留或删除什么？

**[模拟回答]** 建议把 Runtime 或 Context 选为主深挖方向，把一个真实参与较少的外围
集成明确说清。重做时可保留自有领域模型、追加事实和正交 Eval 边界；是否删除某个
工作流、压缩策略或 Viewer 功能要基于自己的维护经验。承认边界比声称全部精通更可信。

### 4.3 为什么相信这是你做的

#### Q13：现场怎样画架构、写主循环？

**[事实回答]** 我会先画五层：调用者/任务；Runtime 与 State；模型和工具边界；Context
与组合能力；Trace/Eval。主循环伪代码是：记录开始 → 每轮检查 abort → 压缩 → 构建
Context → 记录 ModelRequest → generate → 记录响应和 Message → 调度工具并追加结果
→ 判断 final/工具终止/预算 → 记录统一结束原因。

#### Q14：怎样讲失败定位、被迫引入的设计和抽象判断？

**[模拟回答]** 失败案例必须提供可以复述的输入、Event 顺序或测试证据。设计不是“看了
某篇文章所以加上”，而是“原方案在某个场景违反了不变量，所以引入最小边界”。抽象
只在它拥有状态、稳定重复或隔离第三方差异时增加；否则保留普通函数和显式装配。

#### Q15：哪个功能可能很多但收益有限？

**[设计回答]** 没有消融时，不能把任何工作流称为已证明有效。多种 Workflow 和复杂
Trace 视图都有维护成本，可能在简单任务上没有净收益。判断依据应是任务成功率、成本、
时延、失败分布和使用频率，而不是功能数量。

### 本章审查

- **覆盖审查**：覆盖定位、长周期定义、ReAct 差异、轻量边界、非目标、框架选择、
  团队分工、演进、失败和真实性检查；
- **事实审查**：个人经历全部标为模拟；当前项目定位与 README/设计文档一致；
- **逻辑审查**：核心三部分与后文 Runtime/领域模型/工具主线一致；
- **风险审查**：不能原样使用团队人数、个人贡献和 Bug 案例模板。

## 5. Agent Runtime 核心循环

### 5.1 一次运行如何推进

#### Q1：从任务输入到停止的完整链路是什么？

**[事实回答]** `Agent.run(task)` 先建立新 State 并追加 task Message，但返回的 Event
Iterator 还没有执行主循环。调用者开始消费后，Runtime 记录 AgentStart；每轮记录
TurnStart，执行可能的压缩，基于活跃消息构建 ContextView，记录本轮真实 LLM Payload，
调用模型并把响应转成项目 Message。如果响应含工具调用，Runtime 执行工具，把结果按
call id 打包为下一条用户侧工具结果；如果响应是 final、工具要求终止、外部 abort 或
回合耗尽，则以对应 reason 记录 AgentEnd。

#### Q2：纯文本、工具调用、空响应和非法响应怎样处理？

**[事实回答]** 纯文本若 Provider stop reason 为 `end_turn`，LLM Agent 将其映射为
`kind=final`；否则作为 step 继续。工具调用进入统一调度。结构非法的 Message/Block 在
项目边界校验，模型产生的 malformed tool call 由 LLM 重试层尝试修复；不可恢复异常会
显式失败。空响应的具体含义由 Adapter 标准化，不能由核心猜测为成功。

#### Q3：一轮、一次 Run 和 Session 的边界是什么？

**[事实回答]** 一轮从 TurnStart 到 TurnEnd，包含一次模型决策以及该决策产生的一组
工具调用。一次 Run 从 AgentStart 到 AgentEnd，可以包含多轮。`resume` 在同一 State
追加 follow-up 并启动新的 Run，因此消息和事件连续；Session 还可覆盖 MCP 等资源的
打开到惰性 Event Stream 完整消费的生命周期。

### 5.2 为什么使用生成器

#### Q4：为什么用生成器，调用方获得什么？

**[事实回答]** 生成器把“推进运行”和“观察事件”合并为一个显式协议：调用者每消费
一个事件，循环向前推进，同时能流式显示、增量写 Trace 或检查 abort。它比一次返回
最终结果更可观察，又没有在核心引入复杂异步框架。代价是调用者必须消费 Iterator，
资源生命周期必须覆盖消费过程，而且真正并发 I/O 不如 async 原生。

#### Q5：`Agent.run()` 返回后是否已执行？不消费会怎样？

**[事实回答]** 只初始化了 State 和 task Message，主循环尚未执行。不消费 Iterator，
不会调用模型和工具，也不会产生 AgentStart/End。这是最容易误解但非常重要的惰性语义。

#### Q6：为什么不是 async generator？若改成 async 会怎样？

**[设计回答]** 当前核心优先可读性，工具并行通过线程池完成。改成 async generator 后，
Provider、MCP、工具和取消传播可以更自然地统一，但同步工具需要线程桥接，调用入口和
测试复杂度会上升。应在大量 I/O 并发成为核心需求时演进，而不是仅为了语法现代化。

#### Q7：异常后 State 是否可检查？

**[事实回答]** 已经通过 `state.record_event` 写入的事实仍在 State 中；但没有通用事务
保证把任意 Python 异常自动转换成 AgentEnd。调用方必须把“有部分事实可审计”和
“一定产生完整终止事件”区分开，生产化时可在外层增加失败终态记录。

### 5.3 停止状态机

#### Q8：模型 final 是否等于客观完成？

**[事实回答]** 不等于。普通 Runtime 的 `done` 只表示模型发出了 final。需要测试、文件
检查或评分器验证的任务，应由外层 Goal Loop/Eval 判断；未通过时追加反馈并 resume。

#### Q9：四种停止原因是什么？优先级怎样？

**[事实回答]** 当前普通 Runtime 只有 `done`、`max_turns`、`tool_terminate`、`abort`。
每轮开始先检查 abort；模型响应和工具事实仍按顺序记录；工具 terminate 会在本轮结束时
优先退出；否则 final 在 TurnEnd 后形成 done；循环自然耗尽则 max_turns。不要把 Goal
Loop 的 complete/blocked/budget_exhausted 混入核心结束原因。

#### Q10：工具超时、无限循环和多维预算怎样处理？

**[事实回答]** 单工具可配置 timeout，超时转换为错误 ToolResult，让模型有机会修正；
Runtime 用 max_turns 防止无限回合，外部 abort 可中止继续推进。Goal Loop 额外管理
累计输出 Token 和墙钟时间。当前核心没有通用成本预算器，因此不能声称四种预算都在
Runtime 内完整实现。

#### Q11：外部检查失败后怎样继续？

**[事实回答]** Goal Loop 在每次候选 Run 后调用独立 CompletionCheck；未通过且预算
允许时，把审计型反馈作为 follow-up，用 `Agent.resume` 继续同一 State。这样保留之前
消息和事件，同时把模型自述与完成权威分开。

### 5.4 错误、重试和副作用

#### Q12：Provider 哪些错误重试？

**[事实回答]** LLM Agent 默认对瞬时限流/429和 malformed tool call 做有界重试。
鉴权、确定性参数错误不应盲目重试。关键原则是只重试尚未产生外部业务副作用的模型
请求，并留下能够解释最终失败的错误。

#### Q13：为什么工具重试更危险？

**[事实回答]** 模型请求通常只产生返回值，工具可能已改文件、发消息或调用外部服务。
当前 Runtime 不提供通用 exactly-once 工具事务，因此超时不能证明工具没有执行完成，
更不能自动重试所有工具。幂等键、执行凭证或人工确认属于工具/生产编排层责任。

#### Q14：工具异常怎样进入系统？

**[事实回答]** 未知工具、执行异常、Hook 阻止和超时都转换为 `is_error=True` 的
ToolResult，并按原 call id 写回 transcript，模型下一轮可自我修正。Runtime 异常与
业务错误应区分；前者可能终止运行，后者是模型可见环境反馈。

#### Q15：中止、Trace 失败和崩溃窗口怎样处理？

**[事实回答]** abort 会在回合、工具执行和子 Agent 流程中被轮询，但线程池 timeout
返回并不等于底层任务被强制杀死；Bash 自己负责进程管理。Trace writer 作为观察者，
写入失败通过回调暴露，不应伪装成 Agent 行为失败。工具已执行但结果未记录的崩溃窗口
当前没有通用恢复协议，生产化需要调用日志和幂等设计。

### 本章审查

- **覆盖审查**：覆盖主链路、返回形态、惰性执行、async 取舍、四种停止、预算、重试、
  副作用、中止和崩溃窗口；
- **事实审查**：没有把线程 timeout 说成能强杀任意任务，也没有把 Goal 状态混入 Runtime；
- **逻辑审查**：模型 final 始终与外部 complete 分离；
- **风险审查**：生产恢复和 exactly-once 明确属于未实现的设计问题。

## 6. Message、Event 与 State

### 6.1 三个概念为什么分开

#### Q1：三者分别表达什么？

**[事实回答]** Message 是模型、用户、Runtime 和工具之间可投影到对话的数据，内容由
Text/Image/Thinking/ToolCall/ToolResult 等项目自有 Block 组成。Event 是运行中已经发生
的结构化事实，例如请求、响应、消息追加、工具生命周期和压缩。State 持有追加式 Event
Stream，并维护从事件派生的 Snapshot，供当前运行高效读取消息和活跃上下文。

#### Q2：为什么 Message 不能承担全部状态？

**[事实回答]** Message 能表达“对话里有什么”，却无法完整表达某轮实际请求、Token、
工具开始/结束、Hook、压缩前后索引和停止原因。把这些都塞进消息既污染模型上下文，
也无法区分模型可见数据和运维事实。

#### Q3：事实源和派生视图分别是什么？

**[事实回答]** `State.events` 是追加事实源；`StateSnapshot.messages`、活跃索引、Trace
Span 和 ModelTurn 是派生视图。Snapshot 可通过事件重建。State 不是一个可随意覆盖的
当前字典；虽然有 scratch data，但核心语义不依赖它承担审计事实。

#### Q4：ToolCall 与 ToolResult 如何关联？

**[事实回答]** Assistant 的 `ToolCallBlock.id` 与用户侧 `ToolResultBlock.tool_call_id`
建立因果关联。并行调用可以完成顺序不同，但 Runtime 最终按原调用顺序打包，并保留每个
id，所以 Provider 和 Trace 都能正确配对。

#### Q5：为什么不用 Provider SDK 对象？

**[事实回答]** SDK 对象的角色、工具结果、图片、reasoning 和 stop reason 结构不同，
会让核心状态绑定供应商版本。项目先转换成不可变的自有 Message/Block，Provider raw
只作为 sidecar/Trace 调试事实保留，从而兼顾可移植性与诊断能力。

### 6.2 一致性与重放

#### Q6：Event 如何保持顺序与身份？

**[事实回答]** 所有 State 写入都在主记录路径中按追加顺序盖上序号和时间；工具线程
只返回结果，不直接修改 State。压缩策略和 Hook 也把决定交回主循环记录。这种单写入点
比让并发工作线程共享 Event list 更容易维持确定顺序。

#### Q7：能否根据 Event 重建 State？

**[事实回答]** 可以重建消息 Snapshot 和活跃上下文投影，因为 MessageEvent 追加消息，
ContextCompressionEvent 更新活跃索引。但这叫状态重建，不是重新调用模型或重新执行
工具；外部副作用和随机模型输出不会因此被重演。

#### Q8：Event 增长和 Schema 演进怎么办？

**[事实回答]** 单次运行保留完整事实，结束后序列化为版本化 Trace；大型 raw 可外置。
当前策略优先可审计而不是无限在线内存。更长运行可以分段持久化或冷存，但不能在没有
替代证据时删除事实。Trace 使用明确 schema version，Viewer 和 fixture 随版本校验。

#### Q9：派生视图错误会污染事实源吗？

**[事实回答]** 正确设计下不会；Snapshot 可以重建，Span/Turn按需派生，不回写 Event。
若派生逻辑有 Bug，修复后可重新生成观察产品。这正是事实与视图分开的收益。

### 6.3 压力问题

#### Q10：只保存最终回答或 Message 会失去什么？

**[事实回答]** 只保存最终回答会失去每轮输入、工具证据、失败和成本；只保存 Message
仍无法知道当时的 system prompt、可见消息数、工具 schema 和 Provider 请求。压缩后最终
transcript 与历史某轮输入不同，因此 ModelRequestEvent 必不可少。

#### Q11：Event 与普通日志有什么区别？

**[事实回答]** 日志主要给人排障，格式和语义常不稳定；Event 是有类型、有顺序、可重建
状态和派生产品的领域事实。日志可以由 Event 渲染，不能反过来依靠字符串日志作为协议。

#### Q12：不可变 Message 的复制成本值得吗？

**[事实回答]** 不可变值使历史不会被后续代码悄悄修改，适合事件溯源、并发读取和 Trace。
代价是修改需要创建新值，但 Message 本身以小型元数据和 Block 引用为主；大型 raw 另行
外置。对这个以可理解和可审计优先的项目，收益大于局部复制成本。

### 本章审查

- **覆盖审查**：覆盖语义、事实源、工具关联、重建、增长、Schema、日志和不可变取舍；
- **事实审查**：只声称可重建 Snapshot，不声称可确定性重演外部副作用；
- **逻辑审查**：与 Trace 章节坚持同一事实源；
- **风险审查**：无限长运行的分段存储属于合理演进，不是当前完整能力。

## 7. Provider Adapter 与多模型支持

### 7.1 Adapter 边界

#### Q1：为什么核心不能使用 SDK 对象？三层数据是什么？

**[事实回答]** Runtime Message 表达项目内部角色、路由和内容块；LLM Message 去掉
Runtime 路由头，表达一次模型调用所需的中性消息；Provider Wire 最后转换成 Chat、
Responses 或 Anthropic 的具体请求。这样 SDK 变化停留在 Adapter，核心只处理稳定语义。

#### Q2：Adapter 双向做什么？

**[事实回答]** 请求方向把 LLMRequest、tools、system prompt、reasoning effort 和内容块
转成供应商格式；响应方向把文本、thinking、tool call、usage、model 和 stop reason
归一化，再由 bridge 提升为 AssistantMessage。Adapter 还保存可诊断 raw snapshot，
但 raw 不成为核心状态类型。

#### Q3：哪些统一，哪些保留扩展？

**[事实回答]** 文本、图片、thinking、tool call/result、usage 和停止语义尽量统一；
供应商独有而又影响重放的提示放入受控 `extra/sidecar`。原则是不能静默丢失重要能力，
也不能因为一个供应商特性让所有核心模块理解其 SDK 类型。

#### Q4：未知内容和 Fake Provider 怎么办？

**[事实回答]** 未知结构应在 Adapter 边界显式报错或转为明确占位，不能默默吞掉。
Fake Provider 实现相同 LLM 边界，提供确定性响应，用于教学、单元测试和接线检查；Fake
成绩不能代表真实模型能力。

### 7.2 供应商协议差异

#### Q5：Chat、Responses、Anthropic 的主要差异是什么？

**[事实回答]** 三者对 system、tool call/result、多内容块、reasoning、流事件和 usage
字段的组织不同。项目把并行工具结果在内部表示为一个含多个 ToolResultBlock 的用户消息；
Anthropic 可自然发送该形态，OpenAI Adapter 可拆成多条 role=tool wire message。reasoning
effort 也由一个中性配置映射到不同字段。

#### Q6：图片、reasoning、缓存 Token 如何处理？

**[事实回答]** 图片保留为 ImageBlock，由 Adapter 转换 URL/base64 形态；ThinkingBlock
保留文本、签名或脱敏标志，必要时支持多轮 reasoning continuity；TokenUsage 统一为非缓存
输入、输出、cache read/write 四个桶，避免 OpenAI inclusive input 重复计算缓存。

#### Q7：OpenAI-compatible 只实现部分协议怎么办？

**[事实回答]** Provider 通过 `api` 明确选择 wire adapter，兼容端点只承诺它实际实现的
协议。可通过 `base_url` 接入，但不应仅因名称兼容就假设支持 Responses、temperature、
reasoning 或所有工具形态；Adapter/配置应允许省略不支持字段并通过 smoke test 验证。

#### Q8：流中断和不同错误是否同策略？

**[事实回答]** 不能一概而论。限流和暂时网络错误可重试；鉴权、非法参数通常不可重试；
流已经产生可见输出或工具调用时，要考虑重复与不完整响应，不能盲目重发。当前回答的
核心原则是 Adapter 把错误分类后交给有界重试层，而非主循环猜测 SDK 异常字符串。

### 7.3 反事实问题

#### Q9：只有一个 Provider 时 Adapter 还有价值吗？

**[事实回答]** 仍有价值：它隔离 wire 对象、便于 Fake 测试、降低 SDK 升级影响，并让
核心领域模型稳定。但如果永远只有一个简单端点，可以缩小 Adapter 层，不必预先建设
复杂注册系统。

#### Q10：为什么不在主循环里写 `if provider == ...`？

**[事实回答]** 因为每加入一种协议，核心循环的请求、响应、错误和工具路径都会分叉，
破坏控制流可读性。Adapter 注册让分支集中在模型边界，Runtime 对所有 Provider 保持同一
语义。

#### Q11：中性协议无法无损表达新能力时怎么办？

**[设计回答]** 先判断能力是否跨供应商且影响核心决策；若是，提升为项目自有显式 Block
或字段；若只用于某 Provider 的 wire round-trip，放入受控 extra；若既不影响行为也不
需要审计，可仅保留 raw。不能无边界地用 `dict[str, Any]` 逃避建模。

### 本章审查

- **覆盖审查**：覆盖三层转换、特性保真、三种 API、图片/reasoning/usage、兼容端点、
  流中断、单 Provider 和新能力演进；
- **事实审查**：没有声称所有 OpenAI-compatible 端点完整兼容；
- **逻辑审查**：Provider raw 始终是诊断证据而非核心对象；
- **风险审查**：具体 SDK 的每个字段可能演进，面试重点应放在边界而非背版本细节。

## 8. 长上下文、压缩与信息恢复

### 8.1 三层上下文

#### Q1：完整历史、活跃上下文和单轮输入是什么？

**[事实回答]** 完整历史是 State 中所有 Message，服务审计和 Recall；活跃上下文是压缩
事件选择的 Message 索引集合；单轮输入是在活跃集合上再应用目标 Agent 可见性、Context
Policy，并于请求边界加入固定 system prompt 和工具定义后的真实 LLM Payload。

#### Q2：为什么三层而不是两层？

**[事实回答]** 如果历史等于活跃上下文，压缩就只能删除证据；如果活跃上下文等于单轮
Wire Payload，就无法区分路由过滤、system 注入和 Provider 转换。三层分别回答“事实是否
存在”“当前候选可见什么”“本次实际发送什么”。

#### Q3：system prompt 在哪一层？Context 构建是否修改 State？

**[事实回答]** Agent 固定 system prompt 不进入 transcript，而在每次模型请求边界加入；
Runtime 注入的 Context/Skill/Memory 则是可追踪的 RuntimeMessage。`build_context_view`
是纯投影；真正压缩由策略产生 decision，再由 Runtime 记录 replacement 和压缩事件。

#### Q4：如何知道历史某轮实际输入？

**[事实回答]** 从该轮 ModelRequestEvent 保存的 `llm_payload`、context view、工具 schema
和计数读取，不能从最终 transcript 猜。后续压缩、resume 和 system prompt 都会让最终
消息集合与历史请求不同。

### 8.2 压缩策略

#### Q5：何时压缩，有哪些策略？

**[事实回答]** ContextPolicy 可配置策略；现有策略可基于 Token/工具输出等条件，也支持
Agent 调用 compact 主动提交摘要。工具结果 rewrite 是 1→1 保持 call id 的缩减；模型摘要
或 Compact 可把 N 条折叠成一条 continuation summary。Runtime 本身只应用 decision，
不把某个压缩算法写死在循环里。

#### Q6：为什么不是统一摘要？

**[事实回答]** 工具输出常可用确定性截断或结构化缩写，成本低且不引入摘要幻觉；长对话
需要跨消息语义摘要；Agent 主动 Compact 能利用它对当前进度的理解。不同信息风险不同，
统一调用模型摘要既贵又可能破坏工具配对。

#### Q7：压缩替换历史还是活跃视图？

**[事实回答]** 原消息和事件永不删除。Runtime 追加 summary Message，再由
ContextCompressionEvent 把活跃索引更新为“保留消息 + summary”。工具 call/result 必须
成对压缩；rewrite 还必须保持角色和 call id 集合不变。

#### Q8：如何处理幻觉、遗漏和多次衰减？

**[事实回答]** 摘要明确引用被折叠的 transcript indices，原文仍可通过 Recall 读取；
保留最近消息和关键 kind，避免所有内容反复摘要；工具结构校验防止协议损坏。它不能保证
摘要零幻觉，所以需要把摘要当导航而非唯一事实。

#### Q9：代码、错误栈、路径和自然语言用同一策略吗？

**[设计回答]** 不应完全相同。精确字符串优先确定性保留、引用或结构化截断；自然语言
进度适合摘要；大型产物应外置后返回句柄。当前通用策略提供边界，但领域策略仍可按任务
配置，不能声称自动识别所有语义类型。

#### Q10：怎样评价压缩，能否归因 Benchmark？

**[事实回答]** 至少同时看模型输入 Token、总成本、时延、Recall 次数、成功率和关键失败
类型。仓库测试证明压缩会缩小活跃上下文并可恢复原消息，但没有完整组件消融时，不能说
它单独造成三项 Benchmark 提升。

### 8.3 Recall、Skills、Memory 与 Handoff

#### Q11：五个概念分别解决什么？

**[事实回答]** State 保存当前 Run 的事实；Recall 按 transcript index 读回本 Run 被压缩
的原消息；Skills 按需提供方法说明；FilesystemMemory 跨 Run 保存经筛选的经验和证据；
Handoff 在父子或阶段间显式传递任务与必要上下文。它们生命周期和权威性不同，不能统称
一个万能 Memory。

#### Q12：Recall 怎样工作，返回摘要还是原文？

**[事实回答]** 压缩 summary 引用原消息索引，Recall Tool 校验有界 index 列表，从
`state.messages` 渲染原始可见文本、tool call/result，并设置单消息和单次总字符上限。
它返回有界原文而不是二次摘要；图片只报告存在，不把无限数据重新注入上下文。

#### Q13：Skills 为什么不是 Memory？

**[事实回答]** Skill 是维护者提供、按名称加载的稳定方法知识；Memory 是从历史运行中
沉淀、可能过期的经验。Skill 的权威来自版本化说明，Memory 必须受当前工作区事实校验。

#### Q14：文件型 Memory 何时读写，如何去重和纠错？

**[事实回答]** Session start 注入 Memory 路径/策略，模型通过普通 Read/Bash 按需读取；
Session end 收集有界证据，由 distiller 对完整 `MEMORY.md` 做重写合并。根目录通过进程锁
保证单 writer，文件原子替换，失败不影响主任务。去重、更新和 pruning 由 distiller 负责，
结构/大小护栏拒绝破坏性重写；陈旧事实仍必须回到仓库验证。

#### Q15：Handoff 应传什么？

**[事实回答]** Task Tool 传明确 task、可选默认/调用 context；子 Agent 建立独立 State，
父 Agent只接收最终文本 ToolResult，子事件作为 details 用于 Trace。固定 Workflow 通常
传原任务与上一步输出。原则是传目标、约束、已验证证据和期望产物，不复制完整父 State。

### 8.4 安全与失败

#### Q16：怎样处理持久 Prompt Injection？

**[事实回答]** Memory 不应保存秘密、原始大输出或未经验证的通用建议，Runtime 注入内容
必须以可见 Message 出现在 Trace；当前事实优先于旧 Memory。Goal objective 还以 untrusted
data 包裹。完整生产方案仍需来源标签、内容扫描、权限隔离和人工审批，现有 Memory 不能
被描述为已经消除 Prompt Injection。

#### Q17：旧经验冲突、Recall 错索引怎么办？

**[事实回答]** 工作区、测试和当前工具结果优先；Memory 是候选经验。Recall 使用显式
索引并检查范围，不做可能误召回同名片段的语义搜索；模型选错索引仍可能得到不相关内容，
因此 summary 引用和可检查 header 很重要。

#### Q18：并发写 Memory 怎么办？

**[事实回答]** FilesystemMemory 在 root 级对完整 read-distill-commit 加进程锁，并用原子
替换写单文件，确保第二个 writer 基于第一个已提交快照继续。它选择串行写一致性，而不是
复杂多主合并。

#### Q19：大窗口还需要上下文选择吗？“精确恢复”依赖什么？

**[事实回答]** 仍需要，因为无关内容会增加成本和注意力干扰，工具输出也可能无限增长。
“精确恢复”只适用于原始 Message 仍存在且 Recall 按稳定索引返回原文；模型摘要本身是
有损的，不能称为精确恢复。

### 本章审查

- **覆盖审查**：覆盖三层上下文、触发与策略、结构不变量、效果评价、五概念比较、
  Memory 生命周期、安全、并发和精确恢复；
- **事实审查**：只把测试支持的上下文缩减/Recall 称为事实，不虚构 Token 消融数据；
- **逻辑审查**：完整历史不删除与 Event 事实源保持一致；
- **风险审查**：不能把摘要说成精确，也不能把文件型 Memory 说成向量自动检索。

## 9. 工具、MCP、Hook 与安全边界

### 9.1 工具契约

#### Q1：工具最小契约是什么？

**[事实回答]** AgentTool 包含名称、描述、JSON Schema 参数、执行函数、parallel/sequential
模式和可选 timeout。执行接收 call id、参数、abort 与 update callback，返回包含可见
Text/Image、details、is_error、terminate 的 ToolResult。

#### Q2：参数、未知工具和非法调用在哪里处理？

**[事实回答]** Schema 先提供给模型，项目消息边界校验 call id/name；执行时未知工具
转换成错误结果，各工具在入口进一步校验参数和值域。不能只信模型遵守 JSON Schema，
验证必须落在受信 Runtime/Tool 边界。

#### Q3：成功、失败、异常、超时和终止怎样表达？

**[事实回答]** ToolResult 的 `is_error` 表示模型可见失败，`terminate` 请求 Runtime 在
结果记录后停止；Python 异常和 timeout 被包装为错误结果。ToolExecutionStart/Update/End
记录生命周期，最终 ToolResult Message 承载下一轮模型输入。

#### Q4：过长输出和并行配对怎么办？

**[事实回答]** 具体工具实施大小限制，Context 压缩还可 rewrite 大工具结果；并行调用在
线程池完成，但结果以 call id 存 map，最终按模型原始调用顺序构建 bundle。只要一项工具
声明 sequential，当前整批 worker 数降为 1，避免危险混并发。

### 9.2 Bash、Read 与 Edit

#### Q5：Bash 怎样处理 cwd、env、退出码和 timeout？

**[事实回答]** Bash Tool 在构造时绑定工作目录和命令策略，执行时捕获 stdout/stderr、
exit code，轮询 abort，并设置可配置的最大 timeout。环境来自受控快照并清理交互变量。
面试重点是“Runtime 统一结果语义，Bash 自己拥有进程生命周期”，而不是背命令实现。

#### Q6：timeout 后是否能保证杀死进程树？

**[事实回答]** 通用 AgentTool 的线程 timeout 只能停止等待，不能强杀正在运行的 Python
函数。Bash Tool 可针对子进程实现终止，但跨平台完整进程树清理仍是高风险边界。回答时
不能把 Future timeout 等同于操作系统级强制取消。

#### Q7：Read/Edit 怎样限制路径和并发冲突？

**[事实回答]** 工具绑定 root/cwd，在路径解析和输入边界限制目标；Read 对行数和字节
截断，Edit 检查明确替换条件。当前通用线程池不会自动锁同一文件，所以可能写同一资源的
工具应标为 sequential，或由上层工作区隔离。它不是任意并行编辑的冲突合并系统。

#### Q8：部分成功怎样记录，是否自动修复？

**[事实回答]** 工具应把实际产物、错误和 details 明确返回；Runtime 不猜测并回滚外部
副作用。可恢复错误交给模型依据结果修正，危险或不可幂等操作不应自动重试。

### 9.3 MCP

#### Q9：MCP 在哪一层，如何统一？

**[事实回答]** MCP 位于工具集成层。连接发现的 MCP Tool 被包装成普通 AgentTool，
inputSchema 映射为 parameters，文本/图片映射成项目 Block，之后与 Bash/Task 走同一
调度、错误、Trace 和上下文路径。

#### Q10：连接和 Schema 生命周期由谁管理？

**[事实回答]** MCP connection 持有后台事件循环、长生命周期 session 和可能的 server
subprocess，必须在 context manager/AgentSession 内打开，并覆盖惰性 events 的完整消费，
退出时关闭。Schema 在连接后发现并快照为工具集合；运行中动态变化需要重新连接/组装，
核心不会隐式热加载。

#### Q11：断开、使用理由和资源结果怎么处理？

**[事实回答]** 调用失败转换为显式 ToolResult；MCP 的价值是复用标准工具协议和 server
生态，而不是每个服务手写 HTTP Tool。当前集成范围只有 tools，resources/prompts/sampling
不在范围；嵌入文本/图片资源可作为结果 Block，audio/binary/link 变成明确占位而非静默丢弃。

### 9.4 Hook 与安全

#### Q12：Hook 能做什么，是否是强制边界？

**[事实回答]** Hook 只有 SessionStart/End、PreToolUse、PostToolUse 四个点；返回不可变
decision，可以阻止工具或追加 Message，不能修改既有历史。PreToolUse 的 block 在执行前
同步完成，并生成错误 ToolResult，因此在当前进程内是不可由模型绕过的调用门；但 Hook
本身不是完整组织级 Policy Engine。

#### Q13：为什么 PreToolUse 不能任意注入消息？

**[事实回答]** 在 Assistant tool_call 与对应 tool_result 之间插入普通消息可能让 Provider
认为工具配对断裂。因此 PreToolUse 只阻止，PostToolUse 在结果 bundle 已追加后才能注入
提醒。这体现协议不变量优先于 Hook 灵活性。

#### Q14：Bash Agent 的安全边界是什么？

**[事实回答]** 可以通过 cwd/root、参数验证、Hook、timeout、容器和评测网络隔离降低风险，
但项目明确不宣称解决任意不可信代码、多租户秘密管理和操作系统沙箱。Prompt 不能绕过
Runtime 的 Hook，但若 Hook 策略未覆盖某行为，模型仍可能执行危险合法调用。

#### Q15：Trace 和多租户还缺什么？

**[设计回答]** 生产环境需要最小权限凭据、每租户工作区/网络隔离、不可绕过的策略服务、
审批、数据分类与脱敏、访问审计、保留/删除策略。Trace raw pool 尤其要独立权限和周期。
这些是合理演进，不是当前轻量项目已完成的承诺。

### 本章审查

- **覆盖审查**：覆盖工具契约、验证、并行、Bash/Read/Edit、MCP 生命周期、Hook 时点和
  安全非目标；
- **事实审查**：没有把线程 timeout、Docker 或 Hook 夸大为完整安全沙箱；
- **逻辑审查**：工具工作线程不直接写 State，与 Event 顺序章节一致；
- **风险审查**：路径限制和进程树行为应按具体平台回答，不能泛化为绝对安全。

## 10. 子 Agent 与工作流

### 10.1 动态委派

#### Q1：Task Tool 与普通工具有什么相同和不同？

**[事实回答]** 相同点是它们都遵守 AgentTool 契约：模型产生 ToolCall，Runtime 调度，
结果以 ToolResult 回到下一轮。不同点是 Task Tool 的执行体内部启动另一个普通 Agent
Run，并把可选 context 与 task 传给受控的子 Agent 注册表；它不是第二套 Runtime。

#### Q2：父 Agent 如何决定是否委派？

**[事实回答]** 父模型根据 Task Tool 的描述和可用 `subagent_type` 枚举自行决定，但
只能选择组装时注册的名称，不能让模型动态导入任意代码。适合委派的信号是任务可以
清楚切分、需要不同角色/工具权限，或希望隔离上下文；如果委派描述不清，子 Agent
只会放大错误分解。

#### Q3：子 Agent 是否有独立 State 和预算？

**[事实回答]** 是。Task Tool 调用 `agent.run` 建立独立 State，使用自己的消息、事件、
Context Policy、工具和 max_turns；父调用的 abort 会传给子运行。当前 Task Tool 显式
配置子 Agent 的 max_turns，但更细的 Token/成本配额需要调用者或更外层 Workflow 管理。

#### Q4：父子之间传什么？如何返回证据？

**[事实回答]** 输入是必须非空的 task 和可选 context/default context，输出默认是子 Agent
最后一条 final 的文本。子 State 的完整 Event 列表放入 ToolResult details 的 `sub_events`，
供 Trace 合并和审计；父模型不会把整棵子 transcript 自动塞进上下文，避免上下文爆炸。

#### Q5：子 Agent 没有 final、超时或被中止怎么办？

**[事实回答]** 没有 final 时返回 `is_error=True` 的 ToolResult，并附带子事件；max_turns
的最后可用回答是否由上层 Workflow采用，取决于 `run_agent` 的 fallback 语义。abort
则在父/子循环中传播，最终由外层区分中止和业务失败，不能伪造成功。

#### Q6：如何限制递归委派？

**[设计回答]** 当前受控注册表和子 Agent max_turns 能限制第一层，但递归深度、总调用数、
累计 Token/成本和并发度应由一个共享 Budget/DelegationPolicy 传播。每次委派生成 trace
parent id，超过深度或预算返回可解释错误；不要依赖 Prompt 要求模型“不要递归”。

### 10.2 动态委派与确定性编排

#### Q7：Task Tool 与固定 Workflow 的本质区别？

**[事实回答]** Task Tool 把“是否委派、委派给谁、任务如何描述”交给模型，控制流动态；
Workflow 由代码决定步骤、顺序、并行和停止，模型只在步骤内部决策。动态方式灵活但
难以复现，确定性方式可审计但可能僵化；二者最终都调用同一 core.run。

#### Q8：为什么 Workflow 不应该复制另一套循环？

**[事实回答]** 如果工作流自己处理消息、工具、停止和事件，就会产生与核心不一致的
异常语义和 Trace。当前每个 Step 通过 `run_agent` 消费普通 Agent 的惰性事件，返回
StepResult(name/role/task/output/state)，WorkflowResult 只组合这些结果。

#### Q9：为什么保存 StepResult.state 而不只留字符串？

**[事实回答]** 字符串只能支持下一步输入，不能解释某步如何得出结论、是否 max_turns、
用了什么工具或花了多少 Token。保留独立 State 让单步可审计、可复盘；最终合并 Trace
是展示视图，不覆盖父子事实。

### 10.3 具体工作流

#### Q10：Planner/Executor 比单 Agent 有什么价值？

**[事实回答]** Planner 不持有工具，只产出可检查计划；Executor 具有实际工具权限并
执行计划。这样“决定做什么”和“改变环境”分开，方便审批、审计和失败定位；代价是
额外模型调用和计划可能过时，所以 Executor 仍需根据真实结果修正。

#### Q11：Reflection 如何避免只是多花一轮钱？

**[事实回答]** Generator 产出草稿，Critic 必须给出具体问题，并使用约定 approval marker；
未批准才进入 revise，达到轮数预算返回最新草稿而不伪造批准。是否值得要看错误率下降
是否超过额外 Token、时延和 Critic 误判成本，不能只凭直觉。

#### Q12：Routing、Parallel、PDR 和 Goal Loop 怎么区分？

**[事实回答]** Routing 选择一个专家；Parallel 同时产生独立候选再按声明顺序聚合；
PDR 是多轮“并行尝试—蒸馏 findings—下一轮条件化—最终化”，复用跨轮信号；Goal Loop
不负责多专家分工，而是在普通 Run 外用独立 CompletionCheck 验证完成并继续同一 State。

#### Q13：PDR 为什么不是简单多次并行？

**[事实回答]** 纯并行只产生候选并选择/汇总；PDR 每轮把尝试中的假设、进展和死路蒸馏成
brief，作为下一轮输入，属于顺序 test-time scaling。每个 attempt、distiller 和 finalizer
仍是普通 Step，成本和证据可单独统计；如果第一轮已通过 CompletionCheck，可提前结束。

#### Q14：Goal Loop 谁判断完成？

**[事实回答]** 由独立 CompletionCheck 读取完成本轮的 State，返回 done/blocked/reason；
模型 final 只是候选结果。连续同一 blocker 达到阈值才返回 blocked，预算耗尽是另一种
状态，abort 也单独记录。这样“模型说完成”和“外部事实通过”不会混淆。

### 10.4 收益与失败模式

#### Q15：多 Agent 为什么可能更差？

**[事实回答]** 委派会增加上下文转换、Token、延迟和错误传播；路由错、任务分解错、
聚合器误判都可能比单 Agent 更差。并行结果还可能互相矛盾，Reflection 可能把正确答案
改坏。因此应按任务的独立性、角色差异和外部验证收益启用，而不是因为“多 Agent”听起来
更强。

#### Q16：怎样验证 Workflow 真有价值？

**[设计回答]** 固定模型、任务集合、工具权限和总预算，比较单 Agent、Workflow 及其消融；
报告成功率、成本、时延、Token、失败类型和方差。若 Workflow 预算更高，必须报告成本
归一化结果，否则不能把分数差异归因于编排。

### 本章审查

- **覆盖审查**：覆盖 Task Tool、独立 State、上下文传递、递归、动态/确定性、六类工作流、
  PDR、Goal Loop、成本和失败；
- **事实审查**：PDR 的“尝试—蒸馏—再尝试”和 Goal Loop 的独立检查来自当前设计；
- **逻辑审查**：所有组合最终回到同一 core.run，和 Runtime 章节一致；
- **风险审查**：总预算/递归 Policy 标为设计回答，不能声称 Task Tool 已完整提供。

## 11. Event、Trace 与可观测性

### 11.1 为什么 Event 之外还需要 Trace

#### Q1：Event 已有，为什么还要 RunTrace、Span、Model Turn？

**[事实回答]** Event 是运行事实的细粒度记录；RunTrace 是一次运行的规范打包；Span
把 Agent、模型、工具和子运行组织成时间层级；ModelTurn 提取“某次请求实际输入—输出”
对。不同视图回答不同问题，且都从 Event 派生，不能替代事实源。

#### Q2：子 Agent Span 如何合并？Viewer 为什么不能控制 Runtime？

**[事实回答]** 子事件暂存于 Task Tool details，Trace 层根据父调用和子事件重建展示树，
不修改父子 State。Viewer 只读 JSONL、渲染 transcript/Span/工具/压缩等视图；若 Viewer
反向改变 Runtime，观察数据就不再是可审计事实，调试结果会被 UI 行为污染。

### 11.2 某轮模型看到了什么

#### Q3：Model Turn 输入从哪里来？

**[事实回答]** 从 ModelRequestEvent 的 `llm_payload` 读取；事件还包含 visible_count、
llm_message_count、ContextView、tools、api 等元数据。它保存的是请求当时的事实，不是
事后对最终 transcript 做切片。

#### Q4：Provider raw 与规范数据为什么同时保留？

**[事实回答]** 规范数据支持跨 Provider 的 Trace、统计和训练导出；raw 保留 wire 级错误、
字段差异和重放诊断。raw 可外置到 raw pool，轨迹中保存引用，既不让核心依赖 SDK，也不
因为规范化而丢掉排障证据。

### 11.3 增量 JSONL 与 Viewer

#### Q5：为什么选择 JSONL？

**[事实回答]** Agent 长运行期间可以追加新事件，Viewer 能扫描和 tail，单行结构方便
故障恢复和按事件处理；相比数据库，教育项目不需要额外服务。代价是需要 schema、原子
行写、坏行处理和最终规范化逻辑。

#### Q6：进程崩溃、多线程和最终文件怎样处理？

**[事实回答]** IncrementalTraceWriter 后台只读 State 浅快照，追加新增 Event，停止时
final flush；首次 header 和最终 whole-file replacement 使用原子策略，raw sidecar 同步
追加。writer 不驱动 Runtime，写失败通过回调暴露。Event 的产生顺序仍由主循环决定。

#### Q7：Schema 和 Viewer 如何防漂移？

**[事实回答]** RunTrace 使用版本化 trajectory schema；固定 run root、instance/out 路径
和 `trajectory.jsonl` 文件名；golden fixture 覆盖 raw、sub-agent、失败工具、压缩等
读取路径。Viewer 与 writer/fixture 必须同步演进，不能只改前端显示。

### 11.4 成本、隐私和训练导出

#### Q8：Token 和成本如何统计？

**[事实回答]** 每个 ModelResponseEvent 从 AssistantMessage 保存 model、api 和 TokenUsage；
成本层使用 price book 聚合普通输入、输出和缓存读写，子 Agent 调用从 sidecar 事件合并。
usage 缺失时应标未知，而不是精确记为零；价格表是可更新元数据，不改写历史 usage 事实。

#### Q9：Trace 隐私怎么处理？

**[事实回答]** Trace 可能含任务、代码、命令输出、raw 和工具细节，因此生产或共享时要
做上游最小化/脱敏、限制 raw pool 访问和保留周期、避免私有 gold 进入模型可见轨迹，并
控制 artifact root 权限。轨迹完整不等于可以无限保存秘密。

#### Q10：训练导出怎样保证和运行一致？

**[事实回答]** 先用 ModelRequestEvent/ModelResponseEvent 提取 Provider-neutral ModelTurn，
Provider-specific OpenAI 导出再复用真实 Adapter 的 wire 转换。这样训练样本不会从最终
transcript 猜请求，也不会复制另一套不一致的协议转换。

### 本章审查

- **覆盖审查**：覆盖 Event/Trace/Span/Turn、子 Agent、Viewer、JSONL、崩溃、Schema、成本、
  隐私和训练导出；
- **事实审查**：明确 writer 只观察不驱动，ModelTurn 来自请求事件；
- **逻辑审查**：Trace 是派生产品，和 State/Event 章节一致；
- **风险审查**：脱敏和多租户治理属于部署责任，不声称当前 Viewer 自动完成。

## 12. Eval 基础设施

### 12.1 Suite、Backend、ArtifactStore

#### Q1：三者分别负责什么？

**[事实回答]** Suite 负责任务语义：公开 task_input、私有 eval_inputs、镜像/workdir 和
container module；Backend 负责在哪里启动、等待、停止和收集执行环境；ArtifactStore
负责按 key 传递 instance、result、trajectory 和 manifest。Runner 只做通用装配。三者
正交后，更换执行位置或存储传输不必改任务逻辑。

#### Q2：为什么 `result.json` 是解耦点？

**[事实回答]** Agent 只负责在工作区形成任务产品，container half 的 extract_result
把它写成 result；评分可在环境内、后续 Judge Run 或官方 Harness 完成。这样 Runner 不
需要解析终端 stdout，也不会把某个 Benchmark 的评分协议塞进核心 Agent。

### 12.2 输入、结果和评分隔离

#### Q3：模型可见 task 和私有评测数据怎样分开？

**[事实回答]** Suite 的 task_input 从 instance 去除 gold/private 字段，写入 input/instance；
eval_inputs 单独写 input/eval，只有评分 hook 读取。Oracle provider 才可以使用完整实例
作为模型无关接线检查，不能与真实模型分数混合。

#### Q4：Agent 完成但评分未通过是什么？

**[事实回答]** 这是有效的 Eval 结果，表示 Agent 形成了候选产品但外部标准未通过；只有
容器启动、产物传输或评分器本身异常才是基础设施失败。三者分开分类，否则重试和对比会
把“没做对”伪装成“运行失败”。

### 12.3 Backend 与 Store

#### Q5：LocalProcess、LocalDocker、RemoteDocker 怎么选？

**[事实回答]** LocalProcess 反馈快、便于调试但共享 workspace，不宜并发；LocalDocker
提供独立环境；RemoteDocker 将计算和 Host 分开，适合资源隔离但需要明确 artifact 方向。
同一个 RunSpec 驱动三者，开发时可先用 LocalProcess，再替换 Backend。

#### Q6：不同网络方向怎样传轨迹？

**[事实回答]** Worker 能访问 Host 时可用 HostHttpStore push/pull；只有 Host 能访问
Worker 时，RemoteDockerBackend 可通过 Docker archive host-pull；双方都只能访问第三方
存储时才适合对象 Store，但当前对象 Store 是扩展点，不能说已提供。

### 12.4 批量、重试和恢复

#### Q7：哪些错误可重试？

**[事实回答]** 暂时的基础设施异常，如容器启动或网络传输失败，可按 max_attempts 重试；
任务已完成但不通过、exit code 非零或评分明确失败，不应自动重跑成成功。每实例独立目录，
结果按输入顺序汇总。

#### Q8：Submit/Reconcile 和 Chain State 解决什么？

**[事实回答]** 长运行 Backend 先 submit，持久化每个已成功启动的 RunHandle 到 batch
manifest；新进程 poll/reconcile，发现 result.json 即使 daemon 状态丢失也可判定终态。
Chain State 显式把连续实例的环境状态从一个节点传给下一节点，既不等于 Agent State，
也不等于跨运行 Memory。

### 本章审查

- **覆盖审查**：覆盖三正交协议、结果解耦、输入隔离、Oracle、Backend/Store、网络方向、
  重试、manifest 和 Chain；
- **事实审查**：没有把对象 Store 或生产调度能力说成已实现；
- **逻辑审查**：任务失败/评分失败/基础设施失败的分类与 Runtime 错误语义分离；
- **风险审查**：面试应说明具体 Benchmark 的官方 Harness 边界，而非只讲通用框架。

## 13. Benchmark 数字审计

### 13.1 基础口径

#### Q1：三项评测分别测什么？

**[事实回答]** SWE-bench Pro 关注软件工程仓库任务的补丁解决率；Terminal-Bench 2.1
关注终端环境中的长步骤任务并使用 Harbor/官方 verifier；PostTrainBench Lite 组合
AIME 2025、BFCL、GSM8K 和 HumanEval 的归一化 reward 做 weighted average。具体任务数、
运行次数和私有执行日志必须以实际 run artifact 为准，不能从 README 分数反推。

#### Q2：三项结果的模型、指标和数字是什么？

**[事实回答]** README 当前发布口径是：SWE-bench Pro 63.20%（GPT-5.4 xHigh），
Terminal-Bench 2.1 77.53%（GPT-5.3-Codex xHigh），PostTrainBench 45.88%（GPT-5.5 xHigh）。
对应 Baseline 是 59.10%、64.70%、43.97%。前两项是各自公开/官方评分语义，第三项是
weighted average；面试时必须同时说明模型、版本、任务预算和评分器。

需要特别复核一个公开材料口径：README 表格将 PostTrainBench 的 Model 写为 GPT-5.5
xHigh，但脚注又写明评测 Qwen3-4B-Base 的 AIME/BFCL/GSM8K/HumanEval。两者在面试前必须
根据实际 run manifest 和评分产物统一；在未核对前，不要把其中任一模型说成唯一确定事实。

#### Q3：“同模型、同任务预算”具体包括什么？

**[事实回答]** 至少包括相同模型快照/API、reasoning effort、任务集合/版本、最大回合、
工具权限、时间/Token/重试规则、环境镜像和评分器。若任一项不同，只能说“参考对比”，
不能强称受控 Baseline。

### 13.2 相对提升与百分点

#### Q4：三组相对提升如何计算？

**[事实回答]** 公式是：绝对提升（百分点）=项目结果−Baseline；相对提升=绝对提升/
Baseline×100%。因此 SWE-bench Pro 是 4.10 个百分点、6.94% 相对提升；Terminal-Bench
是 12.83 个百分点、19.83% 相对提升；PostTrainBench 是 1.91 个百分点、4.34% 相对提升。

#### Q5：为什么不能说 Terminal-Bench “提升 19.83 个百分点”？

**[事实回答]** 19.83% 是相对增幅，12.83 才是百分点差。混用会夸大或误解成果。面试
时我会先说两个原始分数，再明确“绝对 +12.83 个百分点，相对 +19.83%”。

### 13.3 公平性和可信度

#### Q6：Baseline 是自己复跑还是公开结果？

**[事实回答]** 公开 README 对 SWE-bench Pro/Terminal-Bench 的 Baseline 以链接形式展示，
当前材料不足以证明每个 Baseline 都由同一环境重新复跑。因此稳妥说法是“按文档发布的
同模型同预算对比口径”，并准备提供实际 run manifest、版本、任务集合和评分产物；如果
某项只是引用公开榜单，就必须主动承认这一限制。

#### Q7：怎样证明任务集合、版本、随机性和失败处理公平？

**[设计回答]** 对每个实验保存 immutable profile：模型/API、任务 ID hash、Benchmark
版本、镜像 digest、工具/Prompt、budget、seed/temperature、attempt policy 和 scorer
版本；每任务保留 result、trajectory 和原始评分证据。重复运行报告均值/方差或置信区间，
基础设施异常单独分类，不从分母删除任务。当前仓库有 Profile、artifact 和评分边界，
但公开分数页没有展示全部统计，因此不能声称已完成这套统计审计。

#### Q8：是否存在隐藏测试或 gold 泄漏？

**[事实回答]** Eval 设计明确把 task_input 与 eval_inputs 分离，SWE-bench 的 gold patch、
test patch 只给官方 harness/评分路径，ProgramBench 还要求命令级网络隔离。回答时仍应以
实际 artifact 和 harness 日志确认，没有因为设计文档存在就自动证明每次运行绝无泄漏。

### 13.4 能证明什么，不能证明什么

#### Q9：这些数字证明了什么？

**[事实回答]** 在声明的模型、任务预算、评分器和运行口径成立时，它们支持“这套 Agent
配置在三类任务上取得了组合效果”。它们同时反映模型、Prompt、工具、上下文、Workflow、
环境和评分器的联合行为。

#### Q10：能否把提升归因于压缩、Task Tool 或 Handoff？

**[事实回答]** 不能。没有控制变量和组件消融，不能做组件级因果归因。最安全的回答是：
“结果证明组合方案有效，但还不能证明某一个模块贡献了多少。”这不是回避，而是正确的
实验边界。

#### Q11：如何设计消融？

**[设计回答]** 固定模型、任务、镜像、工具、最大总预算和评分器，建立 Basic Agent；
逐层加入 Context Compression、Recall、Task Tool、Handoff、Workflow，并设置去掉某组件
的反向实验。每组报告成功率、Token、美元、时延、外部检查通过率、失败类型和方差；若
加组件导致调用更多，额外成本必须纳入预算或单独归一化。

### 13.5 成本收益

#### Q12：成绩提升是否只是多调用模型？

**[事实回答]** 可能是，所以只报告分数不够。应比较总模型调用数、输入/输出/缓存 Token、
美元、墙钟时延和成功率，画成本—成功率 Pareto。PDR/Reflection/多 Agent 的增益只有在
相同总预算或明确的成本换收益条件下才有意义。

#### Q13：如果预算减半，保留什么？

**[设计回答]** 优先保留能直接提供外部验证和低成本恢复的能力：清晰工具结果、基本
Context 预算、必要的测试检查和 Trace；减少重复 Reflection、宽 PDR、多余并行和过度
摘要。最终选择应由消融和失败分布决定，不应凭模块名排序。

### 本章审查

- **覆盖审查**：覆盖评测含义、模型/指标、Baseline、预算、计算公式、公平性、泄漏、
  因果边界、消融和成本；
- **事实审查**：发布数字来自 README，未把公开页面扩展成未验证的运行统计；
- **逻辑审查**：相对提升与百分点严格区分，组合效果与组件归因严格区分；
- **风险审查**：正式面试要准备真实 manifest 和 scorer 证据，否则应主动降低表述强度。

## 14. 生产化与系统设计扩展

### 14.1 恢复与幂等

#### Q1：运行一半崩溃如何恢复？

**[事实回答]** 当前 State/Event 和增量 Trace 能保留已写入事实，但核心没有宣称能对
任意工具副作用自动恢复。合理方案是把 Event log、Tool invocation、外部 operation id
和 workspace snapshot 持久化；恢复时先判断某 call 的外部操作是否已提交，再决定跳过、
查询状态或人工确认，而不是盲目重新执行。

#### Q2：如何设计幂等 Tool？

**[设计回答]** 每次副作用调用带稳定 operation id，服务端以 id 去重并返回已存在结果；
文件修改采用目标版本/hash 或 patch precondition；不可幂等动作需要两阶段“准备—确认”
或 Policy 审批。Runtime 只负责传递 call id/结果和记录事实，不应伪造 exactly-once。

### 14.2 并发与分布式运行

#### Q3：一千个 Agent 最先遇到什么瓶颈？

**[设计回答]** 可能依次遇到 Provider 限流、模型成本、Trace 写入、容器启动、共享存储
和工具工作区隔离，而不一定是 Python 主循环。可把单 Run 保持单写入事件模型，外层用
队列/租约调度，按 Provider、租户和任务设置并发；每个实例独立 workspace 和 artifact root。

#### Q4：线程、进程和 async 怎么选？

**[设计回答]** I/O 型 Provider/MCP/网络工具适合 async 或受控线程池；CPU 型解析/评分
适合进程或独立容器；不应让一个全局线程池承担所有语义。核心生成器可保留作为观察协议，
外围 Adapter 提供 async bridge，避免把分布式调度塞回 Runtime。

#### Q5：如何处理限流、租约、心跳和孤儿任务？

**[设计回答]** 调度层发放带过期时间的 lease，Worker 定期 heartbeat，artifact/result
作为幂等终态；租约过期后由 reconcile 判断是否可接管。Provider limiter、租户配额和
单任务预算在调度层统一计算，不能只依赖模型 Prompt。

### 14.3 可靠性、安全和治理

#### Q6：Provider 降级、密钥和租户隔离怎么办？

**[设计回答]** 按错误类型做有界重试、熔断和明确 fallback；密钥只通过受控 Secret
注入，不进入 Message/Trace；每租户独立 workspace、网络、artifact 权限和成本账本。
高危工具经过不可绕过的 Policy Enforcement Point 和人工审批。当前项目的 Hook、参数
校验和 Docker 是可复用构件，不等于完整治理系统。

#### Q7：哪些生产问题不该放进核心 Runtime？

**[事实回答]** 多租户调度、GPU、组织权限、Secret 管理、SLA、跨区域故障转移、官方
Benchmark 评分都属于外围。核心只保留稳定的 Message/Event/State、模型调用、工具反馈、
预算和停止语义，避免教学路径被平台治理污染。

### 本章审查

- **覆盖审查**：覆盖崩溃恢复、幂等、并发、调度、降级、密钥、租户和核心边界；
- **事实审查**：生产方案全部标为设计，未声称当前项目达到千并发或 exactly-once；
- **逻辑审查**：外层治理不改变核心 Runtime 语义；
- **风险审查**：面试中要先说当前非目标，再说演进方案，避免被误判为夸大项目成熟度。

## 15. 面试官常用的真实性检查方法

### 15.1 现场画图

#### Q1：至少画出哪些图？

**[事实回答]** 我会画六张最小图：一次 Agent Run；Message/Event/State；Context 三层
与压缩/Recall；父 Agent/Task/子 Agent；Suite/Backend/Store；Benchmark 输入到评分。
每张图都标出数据 owner、转换边界、事实存储、错误路径和停止条件，而不是只画方框。

### 15.2 现场写伪代码

#### Q2：主循环伪代码应包含什么？

**[事实回答]** 至少包含：初始化 State；循环检查 abort；压缩和 Context；记录 request；
调用 generate；记录 response/message；按 call id 执行工具；记录 result；判断 final、
terminate、turn budget；统一记录 end。若忽略“消费惰性 Iterator”，就没有真正表达当前
实现的控制语义。

#### Q3：并行工具伪代码最容易漏什么？

**[事实回答]** 不能让 worker 线程直接追加 State/Event；应先收集 future→call 映射和
update，主生成器按完成结果记录 lifecycle，再按原 call 顺序生成 bundle。还要处理
sequential 工具、abort、timeout、异常和缺失工具。

### 15.3 失败案例

#### Q4：失败案例怎样回答才可信？

**[模拟回答]** 使用“任务输入—观察到的 Event/错误—错误假设—定位实验—根因—最小修复—
 性能/复杂度代价—回归验证”的结构。可以选上下文压缩、Provider wire 差异、工具超时、
 Trace 崩溃或 Eval 产物隔离，但必须是本人真实经历；当前仓库的测试失败不能直接冒充
 个人现场事故。

### 15.4 反事实问题

#### Q5：不使用生成器、Event、压缩、Adapter、Docker 或 Trace 会怎样？

**[事实回答]** 不使用生成器仍可用普通循环，但难以在每个事件边界流式观察/中止；不
保存 Event 可做聊天 Demo，却失去运行事实、停止和请求重建；不压缩会在长任务窗口和
成本上失控；不使用 Adapter 会把 Provider 分支带入核心；不使用 Docker 仍可本地进程，
但环境隔离和可复现性下降；不做 Trace 仍可运行，却无法解释每轮输入、成本和失败。
每个能力不是“越多越好”，而是为一个具体缺口付出复杂度。

### 本章审查

- **覆盖审查**：覆盖画图、伪代码、并发细节、失败证据和反事实替代方案；
- **事实审查**：把当前行为与候选人的真实事故严格分开；
- **逻辑审查**：每个“如果删除”都回到对应设计收益和代价；
- **风险审查**：不要用泛化的“性能更好/更稳定”替代具体证据。

## 16. 简历表述的高风险追问

### Q1：“项目作者 / 核心开发者”为什么同时出现？

**[模拟回答]** 这是简历措辞问题，不应同时保留。我的事实身份是 `[二选一]`；项目中
我直接负责 `[范围]`，其他贡献者负责 `[范围]`。如果是公开项目的后续维护，应明确“在
现有项目上主导某部分”，不要把整个历史写成个人从零原创。

### Q2：“从零设计并实现”中的“从零”是什么意思？

**[事实回答]** 对当前项目，合理限定是“自研核心 Runtime 的领域模型、循环和边界”，
不是没有使用 Python、Provider SDK、MCP 协议、Docker 或官方 Harness。面试时要主动说清
依赖和外部标准，避免把集成工作包装成全部基础设施原创。

### Q3：“控制 Token 消耗”是否有量化证据？

**[事实回答]** 当前设计和测试证明提供了 Context Policy、压缩前后估算、Recall 限制和
TokenUsage 统计；公开材料没有给出一个组件级完整的压缩成本/成功率消融。因此更稳妥的
说法是“提供控制 Token 的机制”，除非候选人有私有实验可以给出前后数字。

### Q4：“精确信息恢复”是否夸大？

**[事实回答]** 只有 Recall 根据 summary 引用的稳定 transcript index 返回原始消息时，
才可以说恢复原始证据；摘要本身仍有损。建议简历写“保留原始证据并支持按索引恢复”，
不要让读者以为模型摘要不会出错。

### Q5：每个 Workflow 名称都需要准备什么？

**[事实回答]** 每个名称都要能说明问题、最小流程、停止条件、增量价值、额外成本、失败
模式和不适用场景。无法承受两轮追问的名称应从简历删除或合并为“多种可组合工作流”。

### Q6：Benchmark 结果应怎样谨慎表达？

**[事实回答]** 同时给原始结果、Baseline、绝对百分点、相对提升、模型/预算口径和
消融限制。不要说“压缩带来 19.83% 提升”，应说“整套配置相对同口径 Baseline 提升
19.83%，尚无组件级归因”。

### 本章审查

- **覆盖审查**：覆盖问题地图中的六类高风险措辞；
- **事实审查**：把能由设计证明的机制与不能由仓库证明的效果分开；
- **逻辑审查**：简历、问答和 Benchmark 章节使用同一数字口径；
- **风险审查**：最终投递前优先缩小过满的身份、效果和原创性表述。

## 17. 必须优先准备的十道问题

### Q1：画出一次任务从输入到停止的完整链路。

**[事实回答]** 任务 Message → State/Event → ContextView → ModelRequest/Provider →
Assistant Message → ToolExecution/ToolResult → 下一轮；模型 final 只是 Runtime done，
外部 Goal Check 通过才是可验证完成；Trace 保存每个边界，Eval 读取 result 和评分证据。

### Q2：Message、Event、State 为什么必须分开？

**[事实回答]** Message 是对话数据，Event 是运行事实，State 是事件日志和派生 Snapshot。
分开才能同时满足 Provider-neutral、模型可见性、工具生命周期、压缩索引、停止、成本
和重建需求。

### Q3：为什么使用生成器？

**[事实回答]** 生成器让事件消费驱动执行，调用方可流式观察和中止；代价是必须消费
Iterator，资源生命周期不能提前结束，异步 I/O 需要额外桥接。

### Q4：模型完成和客观完成有什么区别？

**[事实回答]** 模型 final 只是候选输出；测试、文件状态、官方 verifier 或 CompletionCheck
才是外部完成标准。Goal Loop 在同一 State 上追加反馈继续运行。

### Q5：三层上下文有什么区别？

**[事实回答]** 完整历史保留事实，活跃上下文是压缩后的索引投影，单轮输入再叠加可见性、
system 和 tools 后才是实际 Payload；因此不能从最终 transcript 猜历史请求。

### Q6：Recall、Skills、Memory、Handoff 分别是什么？

**[事实回答]** Recall 是本 Run 原文恢复，Skills 是按需方法知识，Memory 是跨 Run 经验，
Handoff 是阶段/父子间显式传递，State 是当前事实。生命周期和权威性各不相同。

### Q7：Task Tool 与 Workflow 区别是什么？

**[事实回答]** Task Tool 将委派决策交给模型，Workflow 由代码固定编排；两者都运行独立
Agent State，最终通过 ToolResult/StepResult 传递，不复制第二套核心循环。

### Q8：Suite、Backend、ArtifactStore 为什么正交？

**[事实回答]** 分别隔离任务语义、执行位置和字节传输，使 LocalProcess/Docker/Remote
可以替换而不改任务和 Agent；result.json 是执行产品与评分器的解耦点。

### Q9：三项相对提升怎样计算？

**[事实回答]** 绝对百分点分别为 4.10、12.83、1.91；除以各自 Baseline 后是 6.94%、
19.83%、4.34%。必须同时说明模型、任务、预算和 scorer，避免把相对增幅说成百分点。

### Q10：没有组件消融能证明什么？

**[事实回答]** 只能证明整套配置在声明口径下的组合效果，不能证明某个 Tool、压缩、
Handoff 或 Workflow 的独立因果贡献。要回答模块价值，必须做固定总预算的 controlled
ablation 并报告成功率、成本、时延和失败类型。

### 本章审查

- **覆盖审查**：十题均与第 14 篇的优先题目一一对应；
- **事实审查**：所有答案都能回指前面事实章节，没有新增未经证实数字；
- **逻辑审查**：十题是前文压缩版，术语和停止/评测边界一致；
- **风险审查**：这十题只能作为主线，不能替代个人失败案例和实验凭证。

## 18. 贯穿项目的案例题

### Q1：请从一个真实任务讲完所有模块。

**[模拟回答模板]** 我会选择一个自己确实运行过的仓库任务，按以下顺序回答：

1. 目标是 `[任务]`，客观完成标准是 `[测试/评分/文件检查]`；
2. 初始任务成为 user Message，进入 State；
3. 第一轮实际输入由 `[可见历史/system/tools]` 组成；
4. 模型选择 `[工具]` 的原因是 `[信息缺口/行动需求]`；
5. 工具返回 `[成功/错误/超时]`，Event 记录 start/update/end，Result 按 call id 回填；
6. Context 在 `[条件]` 下压缩，summary 引用原始索引，必要时 Recall `[索引]`；
7. `[是否]` 委派子 Agent，子 State 独立，父侧只接收 `[结果/证据]`；
8. 模型发出 final 后，外部检查 `[通过/未通过]`，Goal Loop `[结束/继续]`；
9. Trace 保存 `[ModelTurn/工具/成本/压缩]`，Eval 由 `[Suite/Backend/Store]` 生成 result
   并交给 `[评分器]`；
10. 最终失败或成功由 `[具体证据]` 支持，不能只引用模型自述。

### Q2：如果案例中某模块没有用到怎么办？

**[事实回答]** 不应为了覆盖简历而虚构。可以说“这个任务没有触发压缩/委派，因此我
只讲系统在该场景中的实际路径；该能力在另一个真实任务中通过 `[证据]` 验证。”面试官
更看重事实边界和迁移理解，不要求一次运行使用所有模块。

### 本章审查

- **覆盖审查**：案例模板覆盖任务、消息、模型输入、工具、上下文、委派、停止、Trace、
  Eval 和证据；
- **事实审查**：所有方括号内容必须由候选人填入真实值；
- **逻辑审查**：案例路径与全文主链一致，不强制每个任务触发全部功能；
- **风险审查**：禁止虚构线上规模、生产事故、消融结果或个人贡献。

## 19. 自我检查与最终审查

### Q1：每条回答怎样做事实审查？

**[事实回答]** 对每条回答标记来源类型：代码/测试事实、设计文档约束、已发布实验、
合理模拟设计或待补证据。若只能由设计推断，就使用“当前合理方案是”；若仓库没有记录，
就说“需要以实际 artifact/个人经历确认”。

### Q2：怎样做前后逻辑一致性审查？

**[事实回答]** 至少检查八个全局不变量：

- `Agent.run()` 惰性，必须消费 events；
- State.events 是事实源，Snapshot/Trace/ModelTurn 是派生视图；
- 模型 final 不等于外部 goal complete；
- 压缩改变活跃索引，不删除原始消息；
- Recall 返回有界原文，不等于摘要；
- 子 Agent/Workflow 使用独立 State，统一调用 core.run；
- Suite、Backend、ArtifactStore 正交，result 与评分解耦；
- Benchmark 数字证明组合效果，没有消融就不做组件归因。

### Q3：怎样做最终面试准备审查？

**[模拟回答模板]** 我会逐条检查：能否不看代码画图；能否写最小伪代码；能否讲两个
真实失败案例；能否说明自己负责和没负责的部分；能否说出每项数字的原始结果、Baseline、
百分点和相对值；能否明确当前非目标；能否承受面试官连续两轮反事实追问。无法通过的
名词从简历删除或降级，不用背模板掩盖证据不足。

### 最终审查结论

本文已覆盖第 14 篇问题地图的全部一级主题：项目定位与所有权、Runtime、领域模型、
Provider、上下文、工具/MCP/Hook、子 Agent/Workflow、Trace、Eval、Benchmark、生产化、
真实性检查、高风险措辞、十道必答题和贯穿案例。对于当前代码无法证明的内容，均已明确
标为 `[模拟回答]` 或 `[设计回答]`，没有将其伪装成项目事实。

正式面试前仍有三项必须由候选人补齐的外部证据：

1. 真实团队分工、个人贡献和项目演进时间线；
2. 每个 Benchmark 的实际 run manifest、任务集合、重复运行和评分产物；
3. 至少两个真实失败案例及其修复/回归验证。

### 本章审查

- **覆盖审查**：逐项覆盖问题地图一级主题，并给出最终检查清单；
- **事实审查**：列出仍缺少的证据，没有以文档存在代替证据存在；
- **逻辑审查**：八个全局不变量与全文回答逐一一致；
- **风险审查**：在上述三类证据补齐前，不应扩大简历的所有权、效果或生产化表述。

## 20. 补充追问逐题回答

本节专门补上第 14 篇题库中没有在前文单独成题、但面试官可能逐字追问的短问题。
它们与前文答案共享同一事实边界，不引入新的项目能力主张。

### 项目定位与 Runtime

#### Q1：这个项目明确不解决什么问题？

**[事实回答]** 它不是生产级多租户平台、完整操作系统沙箱、模型训练系统或保证模型
永远正确的产品。它提供清楚的调用边界、容器和 Hook 等构件，但不承诺组织权限、海量
并发、秘密治理、任意不可信代码隔离或生产 SLA。

#### Q2：Runtime 如何区分模型决策、工具执行和调用者控制？

**[事实回答]** Assistant Message/ModelResponse 记录模型决定了什么；ToolExecution
事件和 ToolResult 记录 Runtime 实际执行与返回什么；abort、max_turns、resume 和
外部 Workflow 记录调用者/编排层施加的控制。三者都进入事实流，但来源和责任不同。

#### Q3：同一 Session 收到后续任务时，状态如何继续？

**[事实回答]** 使用 `Agent.resume` 将 follow-up 作为新 task Message 追加到已有 State，
继续同一消息和 Event 历史。若要更换模型配置，使用相同 Agent name 的新 Agent resume，
否则 Context 可见性路由可能改变。新建 `Agent.run` 则是全新的 State。

#### Q4：多个停止条件同时出现时，怎样回答优先级？

**[事实回答]** 先看调用者在新回合开始是否已 abort；本轮模型和工具事实仍先记录；工具
明确 terminate 时，本轮按 tool_terminate 结束；没有工具终止且模型发 final 才是 done；
循环未产生终止信号而达到上限是 max_turns。Goal Loop 的状态在外层另行记录。

### Message、Event 与 Provider

#### Q5：Event 是领域事实、Trace 还是 UI 通知？

**[事实回答]** 首先是领域运行事实。Trace、Span 和 UI 都是其派生消费者；Event 可以被
Viewer 展示，但其语义不依赖 Viewer，也不是为了某个前端组件临时设计的日志通知。

#### Q6：供应商未知 Block 或停止原因如何处理？

**[事实回答]** Adapter 应在边界做显式映射：能归一化的转成项目 Block/停止语义，必须
保留但核心不理解的放入受控扩展或 raw；无法安全解释的返回明确错误。不能默默把未知
停止原因当作成功，也不能静默丢弃未知内容。

### Context 与工具

#### Q7：压缩的触发条件到底是什么？

**[事实回答]** 它由 ContextPolicy/Strategy 决定，可以基于估算的 Token、消息/工具结果
大小或 Agent 的 compact 请求。核心只负责应用 decision 和记录前后索引，不把某一个
阈值写成所有任务通用的固定事实。

#### Q8：两条并行 Edit 修改同一文件时怎么办？

**[事实回答]** 当前通用调度不提供文件级冲突合并；危险或共享资源工具应声明 sequential，
或让每个子 Agent 使用独立 workspace/worktree。若产品需要并行同文件，应增加版本/hash
前置条件和冲突结果，让上层决定重试或人工处理。

#### Q9：MCP Schema 动态变化时怎么办？

**[事实回答]** 当前 MCP 连接建立时发现并包装工具集合，连接生命周期覆盖运行；运行中
Schema 变化不应被核心静默接受，合理处理是使连接/Toolset 失效并重新组装 Agent，或
显式返回 schema mismatch。动态热更新是扩展设计，不是当前默认行为。

#### Q10：MCP 资源能否直接进入上下文？

**[事实回答]** 当前工具结果中的文本/图片资源可以作为 ToolResult 可见 Block；audio、
binary 和 link 会变成说明性占位。MCP resources/prompts/sampling 本身超出当前集成范围，
不能说已实现任意资源自动预取和上下文注入。

### Multi-Agent 与 Workflow

#### Q11：什么任务适合动态委派，什么适合固定步骤？

**[事实回答]** 任务边界不稳定、专家选择依赖当前信息时适合 Task Tool；顺序、权限和
完成条件固定时适合 Planner/Executor、Sequential 或 Goal Loop。混合方式也可以让代码
固定安全边界、让模型只在安全范围内选专家。

#### Q12：Router 路由错了如何发现和回退？

**[事实回答]** 路由目标必须解析到注册 Route；无法解析时可以只保留 Router Step，或
使用显式 default。更强的方案是在专家执行后做任务适配检查，失败则回到 Router/备用
专家，但回退会增加成本，不能无条件循环。

#### Q13：Parallel Workflow 如何聚合冲突结果？

**[事实回答]** 基础 `run_parallel` 保留每个独立 StepResult；有 Aggregator 时由另一个
Agent 综合，无 Aggregator 时返回带标签的拼接。若冲突会影响正确性，应让 Aggregator
引用各结果证据或由外部 Checker 仲裁，而不是按完成先后覆盖。

#### Q14：父 Agent 分解错了，子 Agent 能否纠正？

**[事实回答]** 子 Agent 可以在自己的任务范围内返回不可执行、缺少前置条件或验证失败，
父 Agent 再根据 ToolResult 修正；它不能自动修改父目标或绕过父权限。需要跨层纠正时，
应在 Handoff 协议中加入阻塞原因和建议，而不是只返回一段模糊文本。

#### Q15：哪些工作流只是策略组合，不应进入 Runtime 核心？

**[事实回答]** Plan/Execute、Reflection、Routing、Parallel、PDR 和 Goal Loop 都是外层
对普通 Agent Run 的组合；它们改变步骤如何排列，不改变 Message、ToolResult、Event
和停止的基本语义。只有稳定的新事实、不变量或资源责任才值得提升为核心抽象。

### Trace 与 Eval

#### Q16：如何关联 ModelRequest、ModelResponse 和最终 Message？

**[事实回答]** 它们按同一 State Event Stream 的顺序和事件身份关联：Request 记录实际
Payload，Response 记录 stop/model/usage 摘要，随后 MessageEvent 记录规范化 Assistant
Message。工具生命周期和 ToolResult 紧随其后，Span/ModelTurn 用这些事实派生更高层关系。

#### Q17：为什么大型 raw request/response 要外置？

**[事实回答]** raw 可能含完整上下文、代码和 SDK 对象，直接塞进规范 JSONL 会让增量写、
Viewer 和传输膨胀。外置 raw pool、主轨迹保留 ref，既保持规范事件可读，也能按需调试；
外置不能改变事件身份和语义。

#### Q18：Runner 为什么不能吸收 Benchmark 逻辑？

**[事实回答]** Runner 只负责把 Suite、Backend、Store 和 RunSpec 装配起来；Benchmark
任务含义、gold/private 字段、官方评分器属于 Suite 或 Host Harness。否则每接一个评测
都要修改通用 Runner，替换 Backend/Store 也会破坏任务语义。

#### Q19：实例 ID 如何防止路径逃逸？

**[事实回答]** 运行目录由 run_root/run_id/instance_id 规范化构造，路径片段必须拒绝
绝对路径、`..` 逃逸和分隔符污染；清理只作用于当前实例 out 目录，不能递归误删其他
run。路径隔离是 ArtifactStore/Runner 的不变量，不依赖调用者自觉。

#### Q20：结果顺序为什么不按完成顺序？

**[事实回答]** 并发完成时间受调度和网络影响，不稳定；按输入实例/声明 Worker 顺序汇总
才能让报告、对比和重试结果确定。每个实例的 State/Artifact 仍独立保存，顺序稳定不等于
共享运行状态。

### Benchmark 与生产化

#### Q21：为什么选择相对提升而不是百分点？

**[事实回答]** 相对提升能表达相对于 Baseline 的比例变化，但容易被误读，所以必须同时
给出原始分数和绝对百分点。面试或论文若强调实际通过数量，百分点更直观；简历若使用
相对值，必须附 Baseline 和计算公式。

#### Q22：提升一个百分点是否有业务价值？

**[设计回答]** 取决于任务价值、单任务成本和失败代价。应把一个百分点对应的额外成功
任务数、Token、美元、延迟和人工返工成本换算后评估；Benchmark 分数高但成本/延迟过大
可能不适合线上。

#### Q23：如何设置单任务和租户成本上限？

**[设计回答]** 预算层在每次 Provider 调用前读取已用 Token/成本估计，预留本次最大可能
消耗，超过阈值就停止或降级；租户层再按时间窗口聚合并发和美元。预算事实写入 Event，
不能只在外部日志中估算。

#### Q24：如何建立不可绕过的 Policy Enforcement Point？

**[设计回答]** 在 Tool dispatch 与资源/网络执行前设置独立、默认拒绝的策略服务，校验
租户、工作区、命令风险、审批 token 和预算；模型只能获得策略允许的 ToolResult。Hook
可作为当前进程的前置门，但生产策略不能只依赖同一模型可影响的 Prompt。

### 本节审查

- **覆盖审查**：补齐了题库中被前文合并的 Runtime、Provider、Context、并发 Edit、MCP、
  Router、Aggregator、Runner、路径、顺序、相对提升、成本和 Policy 问题；
- **事实审查**：当前行为与合理扩展分别标为事实/设计，未扩大现有能力；
- **逻辑审查**：补充答案继续遵守八个全局不变量；
- **风险审查**：补充题是逐字追问的参考，不替代真实实验与团队贡献证据。

## 21. 相关材料

- [Agent 项目面试问题地图](14-interview-question-map.md)
- [项目目标与范围](01-product-goals-and-scope.md)
- [核心概念与数据流](03-domain-model-and-data-flow.md)
- [Agent 核心运行机制](04-agent-runtime.md)
- [模型访问边界](05-model-access.md)
- [工具与集成](06-tools-and-integrations.md)
- [上下文与长周期能力](07-context-and-long-horizon.md)
- [Agent 组合与工作流](08-agent-composition-and-workflows.md)
- [轨迹与可观测性](09-trace-and-observability.md)
- [评测与运行体系](10-evaluation-and-operations.md)
- [简历项目描述](15-resume-project-description.md)
