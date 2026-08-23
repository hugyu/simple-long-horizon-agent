# 面试问题清单

## 一、Agent Runtime 与核心抽象

对应回答：[`01-answers-agent-runtime.md`](01-answers-agent-runtime.md)

1. 用 3 分钟介绍一下 Simple Long Horizon Agent，它解决的核心问题是什么？
2. 你为什么要自己做 Agent Runtime，而不是直接使用 LangGraph、AutoGen、
   OpenAI Agents SDK 之类的框架？
3. 你说这是一个 Long Horizon Agent，你怎么定义 Long Horizon？
   - Turn 数？
   - Token 数？
   - 执行时间？
   - Tool call 数？
   - 任务复杂度？
4. 整个 Agent Runtime 的核心架构是什么？能不能画一下从
   `User Input → Model → Tool → State → Model → Stop` 的完整数据流？
5. Runtime 里面最核心的 abstraction 是什么？

6. 把你的 Agent Loop 写成伪代码，并说明每一步由谁负责。
7. 一个 Agent Turn 的严格定义是什么？
   - 一次 Tool Call 是否单独算一个 Turn？
   - 工具结果写回属于当前 Turn，还是下一个 Turn？
8. 模型一次返回多个 Tool Call 时，Runtime 如何调度、记录并把结果返回给模型？
9. Tool 执行失败后，系统应该重试、把错误返回给模型、中断运行，还是执行
   fallback？这个决策由 Tool、Runtime、模型还是 Workflow 负责？
10. Agent Loop 有哪些停止条件，如何避免无限循环？
    - 如果模型不断重复调用同一个 Tool，Runtime 如何检测和处理？
    - `max_turns` 是否是一种过于粗粒度的停止机制？
    - 达到 `max_turns` 时任务只差一步完成，系统应该如何处理？
11. 模型声明“任务完成”时，Runtime 是否应该相信？
    - Agent 的“完成”应该由模型判断、Runtime 判断，还是由外部验证器确认？

12. 为什么要把 `Message`、`Event` 和 `State` 分开？三者分别包含哪些核心字段，
    各自由谁创建和消费？
13. Tool Result 和 Model Response 分别属于 `Message` 还是 `Event`？
    - 为什么 Tool Result 可能同时需要消息表示和执行事件？
    - Model Response 写入上下文与记录运行事实时，是否需要两种表示？
14. `State` 是 append-only 还是 mutable？
    - 哪些部分只允许追加，哪些部分是可变的派生投影？
    - 如果存在可变投影，如何通过 Event Replay 重建？
15. Event 是否需要全局唯一 ID？
    - 运行内递增 index、时间戳和全局唯一 ID 分别解决什么问题？
16. Tool 已经执行成功，但在结果写入 `State` 前程序崩溃，恢复时怎么办？
    - Replay Trace 时如何避免再次执行有副作用的 Tool？
    - 如何区分已经发生的历史事件和恢复后仍需要执行的动作？
17. 为什么不直接维护一个 `messages: list`？
    - 只有消息列表时，哪些运行事实、恢复信息和可观测性会丢失？

## 二、模型 Adapter：OpenAI / Anthropic

对应回答：[`02-answers-model-adapters.md`](02-answers-model-adapters.md)

18. OpenAI 和 Anthropic 的 Tool Calling 协议有哪些差异？
19. 你的 Adapter abstraction 是什么样的？
    - Runtime、统一模型协议和具体 Provider Adapter 的边界分别在哪里？
20. 如何统一不同 Provider 的消息、Tool Call、Tool Result 和模型响应语义？
    - 哪些字段可以统一，哪些 Provider 特有信息需要保留在边界层？

## 三、Context Engineering

对应回答：[`03-answers-context-engineering.md`](03-answers-context-engineering.md)

21. 为什么要区分 Full History 和 Active Context？
    - 两者的数据结构分别是什么？
    - 为什么不能直接让模型读取完整历史？
22. Context Window 不够时，系统如何决定保留、压缩或移除哪些内容？
    - 你的 Context Management pipeline 是什么？
    - 为什么不直接截断最前面的 10K tokens？
23. 一个 Tool 返回 50K tokens 时，系统如何压缩并保持 Tool Call 和 Tool Result
    的协议完整性？
24. Tool Result Compression 应该使用规则压缩还是 LLM 压缩？
    - 两种方式分别适合哪些输出？
    - 如何控制压缩成本和不确定性？
25. 如果 Tool 返回的是代码，是否应该让 LLM 直接生成 Summary？
    - 哪些 Tool 输出不能被直接 summarize？
    - 原始代码、错误日志、测试结果和结构化数据分别应该如何处理？
26. 如果压缩遗漏了关键错误信息，系统如何发现并恢复？
    - 被移出 Active Context 的原始证据保存在哪里？
    - Recall 由模型、Runtime 还是 Workflow 触发？

27. 你所说的“主动 Compact”由谁在什么时候触发？
    - 基于固定 Token 数、Context Window 使用比例，还是模型主动请求？
    - 触发阈值如何为不同模型配置？
28. Compact 的粒度是什么？
    - Message、Turn、Span 和 Episode 分别适合什么场景？
    - 如何避免压缩后破坏 Tool Call 与 Tool Result 的配对关系？
29. Summary Prompt 是如何设计的？
    - 哪些信息必须保留，哪些内容允许舍弃？
30. 如何降低 Summary 产生 hallucination 的风险？
    - Runtime 能验证 Summary 与原始历史一致吗？
31. Summary 被再次 Summary 时，如何控制信息逐渐丢失的问题？
    - 如果 Summary 出错，如何从原始历史恢复？
    - 恢复后的内容如何避免再次撑爆 Active Context？

32. Recall 是如何实现的？
    - 它从 Full History、外部存储还是向量索引中读取信息？
    - 为什么选择模型主动 Recall，而不是每轮自动执行 RAG？
33. 如果模型已经忘记某条信息，它如何意识到自己应该调用 Recall？
    - Runtime 能否自动判断模型缺少了哪段信息？
    - Summary、索引或显式引用如何为 Recall 提供线索？
34. Recall 返回的内容如何避免污染或撑爆当前 Context？
    - Retrieved Context 应该保留多久、以什么粒度进入 Active Context？
35. Retrieved Context 是否会重新进入长期记忆？
    - Recall、当前运行上下文和跨运行 Memory 的写入边界是什么？

## 四、Tools 与集成

对应回答：[`04-answers-tools-integrations.md`](04-answers-tools-integrations.md)

36. 模型一次返回多个 Tool Call 时，Runtime 如何并发执行？
    - 一个 Tool 执行 3 秒、另一个执行 30 秒时，整轮如何等待和记录进度？
    - 当前实现使用什么并发模型？
37. Timeout 是 per-tool 还是 per-turn？
    - 某个 Tool timeout 后，其他 Tool 是否继续执行？
    - 部分成功和部分失败如何反馈给模型？
    - Tool timeout 后，如何真正终止仍在运行的 Tool，而不只是停止等待结果？
    - 用户取消整个 Agent Run 时，cancellation 如何从 Runtime 向 Tool 和外部进程传播？
38. Tool A 和 Tool B 同时修改同一个文件或共享资源时怎么办？
    - Runtime 如何避免竞态条件和不可预测的副作用？
39. 如何判断两个 Tool Call 可以安全并行？
    - 并行意图由模型表达，还是由 Runtime 根据 Tool 属性决定？
    - Tool、Runtime 和 Workflow 分别承担什么责任？
40. 多个 Tool Result 返回模型时，如何保证顺序稳定？
    - 使用提交顺序、完成顺序，还是原始 Tool Call 顺序？

41. MCP 在你的架构中位于哪一层？
    - MCP Client、Agent Runtime、Tool Protocol 和资源生命周期之间是什么关系？
42. MCP Server 如何发现和注册 Tool？
    - MCP Tool Schema 如何转换成统一 Tool Protocol 和模型 Tool Calling Schema？
    - Schema 不兼容或动态变化时如何处理？
43. MCP Server 不可用、连接中断或执行失败时怎么办？
    - MCP Tool timeout 如何处理？
    - 错误应该返回模型、中断 Run，还是触发 fallback？
44. MCP Tool 的权限如何控制？
    - 权限策略应该由 MCP Server、Client、Runtime 还是上层 Session 管理？
45. Agent 是否可以访问已连接 MCP Server 暴露的全部 Tool？
    - 如何按 Agent、任务或运行环境限制 Tool 的发现范围和调用权限？

## 五、Workflow 与完成判断

对应回答：[`05-answers-workflows-completion.md`](05-answers-workflows-completion.md)

46. 什么时候应该创建 Sub-Agent，而不是让当前 Agent 继续执行？
    - Sub-Agent 相比单 Agent 的收益是什么？
    - 哪些情况下 Multi-Agent 反而比 Single-Agent 更差？
47. Sub-Agent 是否拥有独立 Context？
    - Parent Agent 应该向 Child Agent 传递哪些任务、背景和约束？
    - Child Agent 应该向 Parent Agent 返回最终文本、结构化结果，还是完整 Trace？
48. Child Agent 能否调用 Parent Agent 的全部 Tool？
    - Child 的 Toolset、权限和运行环境应该如何隔离？
49. Child Agent 是否可以继续创建自己的 Child Agent？
    - 如何限制委派深度、调用次数和预算，防止无限递归？
50. Parent Agent 和 Child Agent 同时修改同一个 Repository 时，如何避免冲突？
    - 应该共享工作区、使用独立 worktree，还是通过串行提交合并结果？
51. Sub-Agent 的 Token Budget 如何分配？
    - Parent 和多个 Child 之间如何设置局部预算与全局预算？

51A. 项目提供了哪些 Workflow？
    - 这些 Workflow 分别适合什么场景？

52. Planner 和 Executor 为什么需要分开？
    - 两者分别负责什么，哪些状态和决策不应该混在一起？
53. Planner 输出什么结构？
    - Plan 是自然语言、结构化 Step 列表，还是带依赖关系的执行图？
    - Plan 是一次性生成，还是允许在执行过程中动态修改？
54. Executor 执行失败后由谁决定是否 replan？
    - 如何区分可重试的步骤失败、局部计划失效和整体目标变化？
    - Runtime、Planner、Executor 和 Workflow 分别负责什么？
55. Planner 是否可以调用 Tool？
    - 如果可以，如何避免 Planner 越过规划边界直接完成执行任务？
56. Planner 和 Executor 应该使用同一个模型吗？
    - 如何根据推理能力、延迟、成本和上下文需求选择模型？

57. Reflection 为什么有用？
    - 它适合发现哪些类型的问题，哪些任务不值得增加 Reflection 阶段？
58. Reflection 和简单地再次调用一次 LLM 有什么区别？
    - Critic 接收什么输入、输出什么反馈，Revision 如何使用这些反馈？
59. Reflection 会增加 Token、延迟和调用成本，如何判断是否值得？
    - 应该每次固定执行，还是根据任务风险和外部验证结果按需触发？
    - Reflection 是否可能强化原答案中的 hallucination，如何降低这种风险？

60. 如何判断一个 Agent 任务真的完成了？
    - 为什么不能只依赖模型输出 `Done`？
    - Agent 声明完成、Runtime 停止和任务客观完成有什么区别？
61. 什么是 External Validation Signal？
    - 验证信号由谁产生，Goal Loop 如何使用它决定继续、完成或终止？
62. SWE 类任务可以使用哪些 Validation Signal？
    - 单元测试、静态检查、构建结果、Patch 和官方评分器分别能证明什么？
    - 如果测试通过，但实现不符合用户要求，如何组合多个验证信号？
63. 没有 deterministic verifier 的任务如何判断完成？
    - 应该使用 LLM Judge、规则检查、人工确认，还是风险分级的混合方案？

## 六、Observability 与 Cost

对应回答：[`06-answers-observability-cost.md`](06-answers-observability-cost.md)

64. 为什么使用 JSONL 保存 Trace？
    - 与单个 JSON、数据库或日志系统相比，它解决了什么问题，又有哪些限制？
65. Trace Schema 是什么样的？
    - 一条 Trace Event 至少需要哪些身份、时间、类型和载荷字段？
    - Schema 如何版本化并保持向后兼容？
66. 什么是一个 Span？
    - Span 与 Runtime Event、Turn 和完整 Run 分别是什么关系？
67. Model Call 和 Tool Call 是否都应该表示为 Span？
    - 不同类型 Span 的开始、结束、错误和耗时如何定义？
68. Parent Span 和 Child Span 如何关联？
    - Sub-Agent、Workflow Step、Model Call 和 Tool Call 如何形成层级 Trace？
69. 如何通过 Trace 调试一个失败任务？
    - 如何定位模型决策错误、工具失败、上下文缺失和错误停止原因？
    - 密钥、用户数据和 Provider 原始响应等敏感信息如何避免进入 Trace？

70. Token 使用量如何统计？
    - 应该优先使用 Provider 返回的 usage，还是在 Runtime 中自行估算？
    - Model Call、压缩调用和 Sub-Agent 调用如何避免漏算或重复计算？
71. 不同模型使用不同 Tokenizer 时怎么办？
    - Runtime 如何在精确计数不可用时估算 Context 大小并保留安全余量？
72. Cached Token 和 Reasoning Token 如何统计与计费？
    - 不同 Provider 的 usage 字段不一致时，统一协议应该如何表达？
73. Tool Result 是否计入 Input Token？
    - Tool 自身执行成本与 Tool Result 进入后续模型请求产生的 Token 成本如何区分？
74. 一个任务的最终成本如何计算？
    - 如何汇总主 Agent、Sub-Agent、Planner、Reflection、Compact 和重试产生的成本？
    - 模型价格变化或未知价格时如何记录和展示？

## 七、Eval 与实验设计

对应回答：[`07-answers-evaluation-experiments.md`](07-answers-evaluation-experiments.md)

75. 你的 Eval Framework 整体架构是什么？
    - Dataset、Suite、Backend、Store、Runner 和 Scorer 分别负责什么？
    - 本地进程、本地 Docker 和远程 Docker 如何复用同一套评测协议？
76. 一个 Eval Task 包含哪些内容？
    - 任务输入、环境、Agent 配置、预算、验证器和产物之间如何分离？
77. Agent Benchmark 最大的问题是什么？
    - 模型采样、工具环境、网络、依赖和评分器带来的随机性如何控制？
    - 如何保证不同 Agent 方案使用相同模型、任务和预算进行公平比较？
78. 同一个任务应该运行几次？
    - 只运行一次得到 63.20% 时，这个结果是否可靠？
    - 应该如何报告重复实验、方差、置信区间、失败样本和成本？

79. SWE-bench Pro 的 63.20% 是如何计算出来的？
    - 总共有多少 Task，Pass 了多少？
    - 使用 Full Set 还是 Subset，运行了几次，使用什么模型和配置？
    - 如果缺少逐任务原始产物，哪些数字可以确认，哪些不能现场推断？
80. Baseline 是什么？
    - Baseline 与当前实验的 Model、Prompt、Tool、Token Budget 和 Timeout
      是否完全一致？
    - 如果条件不一致，结果还能否直接归因于 Agent Runtime？
81. 4.10 个百分点的提升是否具有统计意义？
    - 如何报告重复运行、方差、置信区间和配对任务差异？
82. Benchmark 提升主要来自哪个模块？
    - 是否做过 Baseline、Context Management、Parallel Tool、Reflection、
      Recall 和 Goal Loop 的逐项 Ablation？
    - 如果没有 Ablation，为什么不能把端到端提升归因到某一个模块？
83. 去掉 Compact 后结果下降多少？Recall 对哪些 Task 类型提升最大？
    - 如何设计 remove-one 和按任务类型分组的实验回答这两个问题？
84. 哪些任务因为当前 Runtime 反而下降？
    - 失败 Case 最大的三个类别是什么？
    - SWE-bench Pro 中最常见的失败原因是什么？
85. Terminal-Bench、SWE-bench 和 PostTrainBench 使用的 Agent 策略有什么区别？
    - 为什么 Terminal-Bench 提升 12.83 个百分点，而 PostTrainBench
      只提升 1.91 个百分点？

86. 如何证明性能提升来自 Runtime，而不是 Prompt 调整或其他配置变化？
    - 是否做过逐项 Ablation、remove-one 实验或相同 Prompt 的对照实验？
    - 没有 Ablation 时，哪些结论不能做因果归因？
87. 模型版本更新和 Benchmark Contamination 应该如何处理？
    - 如何固定模型快照、记录运行日期和检测训练数据污染风险？
    - 新旧模型结果能否直接比较？
88. 如何保证不同 Agent Config 的实验公平？
    - Model、Prompt、Tool、Token Budget、Timeout、并发度和重试策略是否一致？
    - 哪些变量是控制变量，哪些是实验变量？
89. 如果一个 Agent 使用了两倍 Token，但 Success Rate 更高，应该如何比较？
    - 如何同时报告成功率、成本、延迟和预算约束下的效率？
90. Pass@1 和 Pass@k 有什么区别？
    - Agent Benchmark 中多次采样、选择最佳结果和单次真实运行分别代表什么？
91. Agent Eval 为什么比普通 LLM Eval 更难？
    - 模型随机性、环境状态、工具副作用、长时间执行和外部评分如何影响可复现性？

## 八、Production Agent Platform

对应回答：[`08-answers-production-agent-platform.md`](08-answers-production-agent-platform.md)

92. 设计一个支持 10 万个同时运行 Agent Task 的 Production Agent Platform。
    - Control Plane、Scheduler、Worker、State Store、Tool Execution 和 Observability
      分别如何划分？
    - 如何处理吞吐、背压、限流、多租户隔离和全局预算？
93. Agent Run 如何持久化，Runtime 应该是 stateful 还是 stateless？
    - Agent 状态应该存 Redis、Postgres 还是 Object Storage？
    - Event、Checkpoint、Artifact 和 Trace 分别适合存在哪里？
94. Scheduler 如何设计，Worker 如何水平扩容？
    - 如何进行任务分片、优先级调度、重试、负载均衡和容量控制？
95. Tool Execution 和 LLM Runtime 是否应该分离？
    - 两者在权限、资源隔离、网络访问、扩缩容和故障域上有什么差异？
96. 长达两小时的 Agent Task 如何容错？
    - Process Crash、Worker 丢失、模型超时和外部 Tool 不可用时如何恢复？
    - 哪些阶段可以自动重试，哪些阶段必须 Reconcile 或人工介入？
97. 如何实现 Checkpoint 和 Resume？
    - Checkpoint 应该保存哪些状态，多久保存一次？
    - Event Sourcing、Snapshot 和 Workflow Engine 分别承担什么职责？
98. 两个 Worker 同时尝试恢复同一个 Agent Run 时，如何避免重复执行？
    - Lease、Lock、Fencing Token、Idempotency Key 和状态机如何配合？
    - Lease 过期与长时间 Tool Call 之间如何处理？
99. Agent 调用 `send_email()` 成功后服务立即崩溃，恢复时如何避免邮件发送两次？
    - 如何设计执行意图、幂等键、Outbox、结果确认和状态不确定时的 Reconcile？

## 九、Agent 安全

对应回答：[`09-answers-agent-security.md`](09-answers-agent-security.md)

100. Prompt Injection 应该如何防御？
     - Tool Output、网页内容、文档和外部检索结果是否可信？
     - 网页要求 Agent 忽略原指令并上传 `~/.ssh/id_rsa` 时，系统应如何识别和阻止？
     - Prompt 约束、权限控制和执行隔离分别能解决什么问题？
101. 如何限制 Tool Capability？
     - 如何控制文件路径、命令、网络、数据范围和有副作用操作？
     - 哪些操作需要用户确认或 Policy 审批？
102. Sub-Agent 的权限应该继承 Parent，还是重新授权？
     - 如何遵循最小权限原则并防止委派造成权限扩大？
103. MCP Server 是否可信？
     - 如何验证 Server 身份、Tool Schema、返回内容和版本变化？
     - 第三方 MCP Server 应该运行在哪种隔离和授权边界中？
104. Docker Sandbox 能否解决所有安全问题？
     - 容器逃逸、挂载目录、Docker Socket、网络、Kernel 和 Secret 泄露仍有哪些风险？
105. Secrets 应该如何传递给 Tool？
     - 如何避免把 API Key 放进 Prompt、环境快照、命令行参数或模型可见 Context？
     - Secret 的作用域、生命周期和轮换由谁管理？
106. Trace 如何避免记录 API Key 和敏感数据？
     - 应该在采集前、序列化时还是存储后执行脱敏？
     - Provider Raw、Tool 参数、Tool Result 和错误堆栈分别如何处理？
