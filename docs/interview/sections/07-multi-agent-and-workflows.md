下面继续以“项目负责人 / 核心开发者”的第一人称回答。这里会区分模型动态决定的委派、代码确定的 Workflow，以及外部验证驱动的 Goal Loop；实验收益仍按整体配置口径表述。

## 1. 动态委派和确定性编排有什么区别？

> 动态委派由模型在普通 Agent Loop 中决定。父 Agent 看到 Task Tool 后，根据当前上下文选择是否委派、选择哪个 subagent type，并生成子任务和 context。
>
> 确定性编排由代码预先决定执行结构，例如 Planner 一定先于 Executor，Reflection 一定按照生成、批评、修订运行，Parallel 一定先并发再聚合。
>
> 动态委派适合任务结构无法提前确定的场景，灵活但不可预测；确定性编排适合阶段边界和责任已经清楚的流程，可审计性和预算控制更强。
>
> 两者最终都复用同一个 `Agent.run()`，没有第二套子 Agent Runtime。

## 2. 什么任务适合多 Agent，什么任务单 Agent 更好？

> 多 Agent 适合以下情况：
>
> - 子任务可以独立定义和验收。
> - 不同阶段需要不同 Prompt、工具或权限。
> - 多个候选可以并行探索。
> - 需要独立 Critic、Judge 或外部验证。
> - 子任务中间过程很长，不值得污染主上下文。
>
> 单 Agent 更适合任务较短、步骤强耦合、需要持续共享同一工作状态，或者委派成本高于任务本身的场景。
>
> 我不会因为任务步骤多就默认使用多 Agent。拆分必须带来角色专注、上下文隔离、并行收益或独立验证，否则只会增加 Token 和协调成本。

## 3. Planner 和 Executor 之间传递什么？

> 当前 Planner/Executor 传递的是文本计划，不是共享 State 或结构化 Plan 对象。
>
> Planner 接收原始任务并输出有序计划。Executor 接收：
>
> ```text
> 原始任务
> +
> Planner 的完整输出
> ```
>
> Planner 和 Executor 各自拥有独立 State。WorkflowResult 同时保留两个 StepResult，所以可以检查 Planner 计划了什么，以及 Executor 实际做了什么。
>
> 当前计划协议容易理解，但缺少机器可校验的 step ID、依赖关系和状态字段。复杂生产流程可以把计划升级为结构化 Schema。

## 4. 计划应该什么时候更新？

> 当执行证据证明原计划的假设已经失效时就应该更新，例如文件结构与预期不一致、依赖不可用、测试暴露新的问题，或者外部 Goal Check 失败。
>
> 当前基础 `run_planner_executor` 是一次规划、一次执行。Executor 被允许根据环境自行调整，但不会再次调用 Planner，也不会修改原 Plan Step。
>
> 如果需要显式重规划，可以在外层加入 Reflection、Goal Loop，或者实现 `plan → execute → check → replan` 工作流。更新计划必须由新证据触发，而不是每执行一步都重新规划。

## 5. 反思机制如何避免只增加 Token、没有实际收益？

> Reflection 必须有明确质量标准、独立 Critic、有限轮数和停止协议。当前 Critic 只有在输出约定的 approval marker 后才停止，否则 Generator 根据具体反馈生成完整修订版。
>
> `max_rounds` 保证反思不会无限继续；预算耗尽时返回最新草稿，不会伪造批准。
>
> 对代码任务，Critic 反馈最好建立在测试、静态检查或工具证据上，而不是只评价文本风格。如果外部检查已经通过，也没有必要继续反思。
>
> 当前基础 Reflection 主要依赖模型 Critic，不能保证每轮都提升质量。是否值得使用需要通过成功率增量与额外 Token、延迟对比验证。

## 6. Router 的决策依据是什么？

> Router 接收原始任务，以及每个 Route 的名称和 description；description 为空时会使用 Specialist 的 role。
>
> Router 被要求只返回一个注册名称。解析时先做忽略大小写和标点的精确匹配，再按照名称长度从长到短做包含匹配，避免 `sql_writer` 被错误解析为 `sql`。
>
> Router 不会动态导入模块，也不能选择注册集合之外的 Agent。当前决策主要依赖文本语义，没有独立置信度或能力探测机制。

## 7. 路由错误后如何纠正？

> 如果 Router 输出无法解析，可以使用显式 default route；没有 default 时，Workflow 只返回 Router Step，让调用者看到错误，而不会随机选择 Specialist。
>
> 如果成功解析但 Specialist 不适合，当前基础 `run_routing` 不会自动回到 Router 重选。调用者可以重新运行 Router、切换 default，或者用 Goal Check 发现失败后触发新的路由流程。
>
> 更强实现可以让 Router 返回结构化 choice、confidence 和 reason，并在低置信度或 Specialist 失败时尝试候选 Route，但必须设置最大重路由次数。

## 8. 并行 Agent 的结果如何合并？

> 每个 Worker 拥有独立 State。它们可以处理同一个任务形成 Ensemble，也可以分别处理不同子任务形成 Map。
>
> Worker 完成后，结果会按照 Worker 声明顺序排列，不按线程完成顺序排列。
>
> 如果配置 Aggregator，它会接收原始任务和所有带来源标签的 Worker 输出，负责综合成最终答案。如果没有 Aggregator，Workflow 只返回按 Worker 名称标记的结果拼接，不假装已经完成裁决。
>
> 对会修改工作区的 Worker，应提供独立 Worktree 或容器，不能因为 State 独立就假设文件系统也独立。

## 9. 多个 Agent 给出冲突结论时谁做裁决？

> 配置了 Aggregator 时，由 Aggregator 做语义裁决；PDR 中由 Distiller 归纳分歧和证据，最终由 Finalizer 形成答案。
>
> 但模型裁决不等于客观正确。涉及代码、计算或环境事实时，应优先使用测试、命令、官方评分器或其他确定性 Completion Check。
>
> 如果没有 Aggregator 或外部 Check，Workflow 只能保留冲突结果交给调用者，不能用多数票自动替代事实验证。

## 10. PDR 的完整含义和执行流程是什么？

> PDR 是 `Parallel-Distill-Refine`。
>
> 每一轮包含：
>
> ```text
> 多个 Worker 并行尝试
>     ↓
> Distiller 汇总有效假设、进展和失败路径
>     ↓
> 形成紧凑 Findings Brief
>     ↓
> 下一轮 Worker 在原始任务 + Brief 上继续探索
> ```
>
> 运行若干轮后，Finalizer 根据原始任务和累计 Brief 生成最终结果。Finalizer 默认使用第一个 Worker，也可以单独指定。
>
> PDR 还支持 Completion Check。某轮中如果第一个可验证成功的 Attempt 已经完成任务，就立即返回，跳过剩余轮次、Distillation 和 Finalizer。
>
> 每个 Attempt、Distiller 和 Finalizer 都是独立 StepResult，拥有自己的 State 和 Trace。

## 11. Goal Loop 与普通 Agent Loop 的区别是什么？

> 普通 Agent Loop 在当前 Agent 输出 final、达到 max turns、工具 terminate 或外部 abort 时结束。final 只说明模型认为本段回答结束。
>
> Goal Loop 位于普通 Loop 外层。它先运行 Agent，再调用独立 CompletionCheck；如果检查不通过，就在同一个 State 上追加 continuation prompt，并通过 `resume()` 继续。
>
> Goal Loop 负责可验证完成、跨段预算、连续阻塞判断和 GoalStatusEvent；内部模型调用、工具执行和上下文处理仍由普通 Runtime 完成。

## 12. Goal 如何判断已经完成？

> 完成权由可插拔 CompletionCheck 决定，而不是固定依赖模型的 final。
>
> 当前支持的方式包括：
>
> - 模型通过 `update_goal` 声明 complete 或 blocked。
> - 固定验证命令必须退出码为 0。
> - 重新执行模型提供的 `verify_command`。
> - 独立 Judge Agent 返回结构化判断。
> - 模型声明通过后，再由外部 Verifier 或 Judge 做 veto。
>
> 对代码任务，我更信任可重复执行的测试命令，而不是第二个模型阅读总结。只有 CompletionCheck 返回 `done=True`，Goal 才进入 complete。

## 13. 如何防止 Goal 不断自我扩张？

> Objective 在 Goal Loop 开始时固定，并被写入每个 GoalStatusEvent。Continuation Prompt 会要求 Agent从原始 Objective 推导可检查要求，不能把目标替换成更容易或更宽泛的问题。
>
> CompletionCheck 也应该只检查原始 Objective 的接受标准，不奖励额外功能。
>
> 回合、输出 Token 和墙钟时间预算进一步限制无边界探索。相同 blocker 连续三次出现时，Goal 会以 blocked 结束。
>
> 当前没有自动的需求范围 Diff 或形式化 Goal Schema，因此模型仍可能过度实现。生产化可以把 Objective 拆成固定 requirement ID，并要求 Check 逐项返回状态。

## 14. 子 Agent 的 Token 和时间预算由谁分配？

> 当前预算主要由调用者或 Workflow 配置，而不是由父模型动态分配。
>
> Task Tool 配置子 Agent 的 hard `max_turns` 和可选 soft turn limit。Workflow 分别配置 Planner、Executor、Critic、Worker、Distiller、Aggregator 和 Finalizer 的 max turns，以及最大并发数。
>
> Provider Request 自身还有超时，外部 Abort 可以承接总墙钟限制。Goal Loop 额外支持 continuation 次数、累计输出 Token 和 wall clock budget。
>
> 当前 Task Tool 没有每次调用独立的 Token Budget 参数，也没有全局动态预算调度器。要实现成本感知委派，需要由父流程预先分配或在工具层增加 Budget Context。

## 15. 主 Agent 能否中止子 Agent？

> 可以通过共享 Abort Flag 请求中止。Task Tool 将父运行的 Abort 传给子 Agent，子 Runtime 在回合开始检查，子工具也会收到同一个 Abort。
>
> Parallel Workflow 中的 Worker 同样共享外部 Abort。
>
> 但这是协作式中止，不保证立即杀死正在运行的 Python 线程、Bash 子进程或远端 MCP 操作。具体取消能力仍取决于工具实现和宿主进程隔离。

## 16. 多 Agent 是否真的提高成功率？代价是什么？

> 多 Agent 在以下情况下可能提高成功率：独立候选能够覆盖不同思路，Specialist 的 Prompt 和工具更聚焦，Critic 能发现 Generator 的盲点，外部 Judge 能阻止错误完成。
>
> 但它不是免费的能力放大器。代价包括更多模型调用、Token、延迟、并发资源、状态和 Trace 体积，以及信息传递丢失、错误路由、错误聚合和共享工作区冲突。
>
> 是否有效必须在相同模型、任务和总预算下比较。只比较“多 Agent 使用了更多计算后的分数”不能证明编排本身更优。

## 17. 哪些工作流在 Benchmark 中产生了收益？

> 当前能够确定的是，SWE-bench Pro 的 63.20% 配置使用了类似 ChainSWE 的链式运行，并叠加 Task Tool；相对同模型同任务预算 Baseline 提升 6.94%。
>
> 但这个结果只能证明整套配置有效，不能把提升分别归因于 Chain、Task Tool、Handoff 或其他组件。
>
> Terminal-Bench 仓库提供 Bash 与 Bash+Task 两种运行入口，SWE-bench Chain 也支持 bash、loop、PDR、Task Tool、Compression 和 Handoff 等实验变量，但当前公开简历结果没有完整消融表。
>
> 因此我不会声称 Reflection、PDR、Routing 或 Goal Loop 已分别贡献了多少分。要回答这个问题，需要固定模型和总预算做单变量对照及多次重复实验。

## 18. 如何测试工作流逻辑而不调用真实模型？

> Workflow 接收预先构造的 Agent，因此测试可以使用确定性的程序化 `generate` 函数，不需要 Provider 和网络。
>
> 可以验证：
>
> - Chain 是否把原始任务和上一步输出传给下一步。
> - Planner 输出是否完整进入 Executor。
> - Reflection 是否在 approval marker 时停止，并在轮数上限时返回最新草稿。
> - Router 是否选择正确注册 Route，以及 default 行为。
> - Parallel 是否保持声明顺序并调用 Aggregator。
> - PDR 是否正确传递 Brief，以及 Early Check 是否短路。
> - Goal Loop 是否正确处理 complete、blocked、budget 和 abort。
>
> CompletionCheck、Fake Tool 和 Abort Flag 都可以注入，因此编排控制流可以完全确定性测试。

## 19. 为什么需要这么多工作流，而不是一个通用 Loop？

> 因为这些模式解决的是不同控制问题。
>
> Chain 解决固定阶段传递；Planner/Executor 解决决策与执行分权；Reflection 解决独立质量审查；Routing 解决专家选择；Parallel 解决多候选或分片；PDR 解决跨轮复用并行探索结果；Goal Loop 解决外部完成验证。
>
> 把它们都塞进核心 Agent Loop，会让每个普通 Agent 都理解 Planner、Router、Judge 和并行聚合。做成通用图引擎又会隐藏项目最想展示的直接控制流。
>
> 当前选择是核心只有一个 Runtime，每种 Workflow 用短小普通 Python 函数表达自己的结构。

## 20. 工作流应该写死在代码中还是配置化？

> 控制语义应该保留在类型明确、可测试的代码中；模型、角色、Prompt、预算、Route、并发度和策略选择可以配置化。
>
> 当前 Workflow 是普通 Python 函数，Benchmark 可以通过 `agent_flavor` 和运行参数选择 bash、loop、PDR、Task Tool、Compression 或 Handoff。
>
> 如果未来出现大量稳定且重复的业务流程，可以增加声明式配置，但配置最终仍应映射到已验证的 Workflow 构造器，而不是允许任意字符串动态导入和执行。
>
> 我的原则是：配置描述“选择什么和参数是多少”，代码负责“控制流到底意味着什么”。
