# 05. Workflow 与完成判断

本文对应 [`question-checklist.md`](question-checklist.md) 中第五部分的问题。每道题包含可直接
口述的主回答；存在清单子问题时，保留追问原文和简短回答；最后记录整组问题对应的仓库实现。

## 46. 什么时候应该创建子 Agent？

### 口述主回答

当子任务相对独立、需要不同提示词或工具，或者可以并行探索时，我会考虑创建子 Agent。项目里
父 Agent 通过 `task` 工具选择已注册的子 Agent，子 Agent 使用普通 Runtime 独立完成任务，再把
结果返回父 Agent。

收益是上下文和职责更聚焦，也能隔离不同角色。代价是增加模型调用、上下文传递和结果合并成本；
如果任务强依赖共享状态、步骤很短或需要频繁往返，多 Agent 反而不如单 Agent 直接执行。

### 追问问题与回答

**追问：Sub-Agent 相比单 Agent 的收益是什么？**

它能隔离上下文、提示词和工具，让专门角色聚焦独立子任务，也便于并行探索和单独审计。

**追问：哪些情况下 Multi-Agent 反而比 Single-Agent 更差？**

任务很短、强依赖共享状态、需要频繁来回沟通，或委派和汇总成本高于任务本身时，多 Agent 会更差。

### 技术追问补充

- `task_tool(agents)` 在构造时校验子 Agent 列表，并按名称建立固定注册表；重复名称直接拒绝。
- 工具 Schema 中的 `subagent_type` 是注册名称枚举，父模型只能选择已登记的 Agent，不能动态导入。
- 每次委派调用选中 Agent 的普通 `agent.run()`，父 Runtime 不理解子 Agent 的内部 Turn。
- 当前没有自动判断“何时值得委派”的规则，是否调用 `task` 由父模型和 Prompt 决定。

## 47. 子 Agent 是否拥有独立上下文？

### 口述主回答

是的。`task` 工具会为选中的子 Agent 调用一次新的 `agent.run()`，因此它拥有独立的 State、
消息历史、压缩索引和事件流，不会把父 Agent 的完整对话直接复制过去。

父 Agent 传递的是明确子任务和可选背景说明。子 Agent 返回给父模型的是最终文本；完整子事件放在
工具结果的本地详情中，供 Trace 合并，不会全部塞回父 Agent 上下文。

### 追问问题与回答

**追问：Parent Agent 应该向 Child Agent 传递哪些任务、背景和约束？**

应传递自包含子任务、完成标准和必要背景，不直接复制父 Agent 的完整对话历史。

**追问：Child Agent 应该向 Parent Agent 返回最终文本、结构化结果，还是完整 Trace？**

当前默认返回最终文本；完整 Trace 作为本地详情保存。需要机器消费时，应为该工具定义明确的
结构化结果协议。

### 技术追问补充

- `task_tool` 的输入字段是 `subagent_type`、`task` 和可选 `context`；task 会成为子 State 的
  task Message，context 会在第一轮前记录为 `kind="context"` RuntimeMessage。
- 子 Agent 每次创建新 State，因此拥有独立 messages、events、Snapshot 和压缩上下文。
- 工具从子 State 中提取最后一条 `kind="final"` Message 文本作为父模型可见 ToolResult。
- 子 State 的完整事件列表保存在 `ToolResult.details["sub_events"]`，供 Trace 合并，不进入普通
  模型正文。

## 48. 子 Agent 能否调用父 Agent 的全部工具？

### 口述主回答

不会自动继承。每个子 Agent 在创建时已经绑定自己的工具集合、提示词和上下文策略，`task` 工具
只是从注册表选择并运行它。

因此工具权限应在子 Agent 组装阶段按最小权限配置。工作目录、MCP 连接和外部凭据也应按子任务
隔离，不能因为父 Agent 有权限就默认传给子 Agent。

### 追问问题与回答

**追问：Child 的 Toolset、权限和运行环境应该如何隔离？**

子 Agent 应独立绑定完成子任务所需的最小工具、凭据和工作目录，不自动继承父 Agent 的全部能力。

### 技术追问补充

- `task_tool` 接收的是已经组装完成的 Agent 对象，运行时直接使用该 Agent 自身的 tools、hooks 和
  `context_policy`，不会读取父 Agent 工具表。
- Bash、Read、Edit 等工具的工作目录在构造时闭包绑定；父子是否共享目录取决于调用者如何组装。
- MCP 等有生命周期资源由各自 Session/Toolset 管理，不会因为父 Agent 已连接就自动传给 Child。
- 当前没有统一权限继承协议；最小权限依赖 Builder、Session、Hook 和外部运行环境共同落实。

## 49. 子 Agent 可以继续创建自己的子 Agent 吗？

### 口述主回答

可以，只要这个子 Agent 自己也绑定了 `task` 工具。但当前 Runtime 没有自动限制委派深度，也没有
统一统计整棵委派树的调用次数和总预算。

项目现有保护主要是每次子运行的 `max_turns`、可选软提醒和外部 abort。真正防止无限递归，需要
上层增加深度、调用次数和全局预算控制。

### 追问问题与回答

**追问：如何限制委派深度、调用次数和预算，防止无限递归？**

当前只能限制每次子运行的回合并传递 abort；深度、总调用次数和全局预算需要由外层 Workflow
显式维护和拒绝超限委派。

### 技术追问补充

- 只要 Child 自身 tools 中再次包含 `task`，它就可以继续委派；核心 Runtime 不区分委派层级。
- `task_tool(max_turns=...)` 为单次子运行设置硬上限；`soft_turn_limit` 只在指定 Turn 后注入一次
  剩余回合提醒，不增加额外模型 Turn。
- 父调用的 abort flag 会传入子 `agent.run()`，可中止当前委派链上的运行。
- 当前没有 `max_depth`、全局委派计数或跨 State Token 账本。

## 50. 父子 Agent 同时修改同一个仓库时如何避免冲突？

### 口述主回答

独立 State 只隔离对话和事件，不会自动隔离文件系统。如果父子 Agent 的工具绑定到同一个工作
目录，它们仍可能同时修改同一文件。

有写操作时，我会根据任务选择串行执行或独立 worktree。并行探索可以使用独立 worktree，最后由
一个明确的合并或最终执行阶段写回主工作区；简单强依赖修改则直接串行更可靠。

### 追问问题与回答

**追问：应该共享工作区、使用独立 worktree，还是通过串行提交合并结果？**

只读或强顺序任务可以共享并串行；并行写操作应使用独立 worktree，最后由一个明确阶段合并或在
主工作区重放最终修改。

### 技术追问补充

- 子 Agent 的独立 State 不包含文件系统快照；如果工具闭包绑定相同 cwd，父子仍操作同一目录。
- 通用 `task_tool` 不创建、重置或合并 git worktree。
- SWE 工作流的 PDR 组装会为并行尝试创建 detached worktree，并把最终 Agent 绑定到规范工作区。
- worktree 冲突检测和合并属于版本控制或 Workflow 层，核心 Runtime 没有自动合并协议。

## 51. 子 Agent 的 Token Budget 如何分配？

### 口述主回答

当前项目主要提供局部回合预算，没有完成父子之间的统一 Token 分配。`task` 工具可以限制每个
子 Agent 的最大回合，Workflow 也可以为不同步骤设置各自回合上限。

如果需要全局预算，我会先给父 Agent 预留决策和汇总额度，再按子任务风险分配局部预算，并由上层
统一统计。当前代码还没有跨多个独立 State 的全局预算账本。

### 追问问题与回答

**追问：Parent 和多个 Child 之间如何设置局部预算与全局预算？**

每个 Child 设置独立回合上限；父 Agent 保留决策和汇总预算；外层 Workflow 统一汇总所有子 State
的用量，并在总预算不足时拒绝新委派。

### 技术追问补充

- `task_tool` 当前只接受 `max_turns` 和 `soft_turn_limit`，没有单次子 Agent Token Budget 参数。
- 子 Agent 的 usage 保存在其独立 State；子事件会随 ToolResult details 返回，因此成本层可以递归
  统计已发生调用。
- Goal Loop 的 `token_budget` 只统计同一个连续 State 的 Assistant 输出，不自动扣减独立 Child
  State 用量。
- Workflow 可以通过各 `StepResult.state` 或子事件汇总用量，但当前没有统一“剩余全局预算”控制器。

## 52. Planner 和 Executor 为什么需要分开？

### 口述主回答

分开是为了让“决定做什么”和“实际改变环境”拥有不同职责。项目里的 Planner 默认没有工具，只
生成可检查的执行计划；Executor 接收原任务和计划，再使用真实工具完成任务。

这样计划可以单独审计，也能限制规划阶段的副作用。代价是多一次模型调用，而且计划错误仍可能
传给执行阶段，所以 Executor 允许根据真实环境适当调整。

### 追问问题与回答

**追问：Planner 和 Executor 分别负责什么，哪些状态和决策不应该混在一起？**

Planner 负责把目标拆成有序步骤和假设，不应修改环境；Executor 负责读取真实反馈并执行，不应
擅自缩小或改变用户目标。

### 技术追问补充

- `make_planner_agent()` 默认不绑定工具，System Prompt 要求只输出计划、不执行任务或最终答案。
- `make_executor_agent()` 接受调用者提供的真实工具，System Prompt 允许遇到错误时适应并报告。
- `run_planner_executor()` 先用 `run_agent()` 运行 Planner，再把原任务和
  `plan_step.output` 组成 Executor 的新任务；两步使用独立 State。
- `WorkflowResult.steps` 同时保留 planner 与 executor 的任务、输出和完整 State，最终 output
  取 Executor 结果。

## 53. Planner 输出什么结构？

### 口述主回答

当前实现输出简短的自然语言编号计划，不是强类型步骤列表或依赖图。Workflow 直接把 Planner 的
最终文本和原任务一起交给 Executor。

这个计划是一次性生成的，Executor 可以在执行中适应失败，但不会修改一个正式计划对象。当前
Planner-Executor 也没有自动动态重规划。

### 追问问题与回答

**追问：Plan 是自然语言、结构化 Step 列表，还是带依赖关系的执行图？**

当前是自然语言编号计划，不是强类型步骤列表，也没有依赖图和节点状态。

**追问：Plan 是一次性生成，还是允许在执行过程中动态修改？**

当前一次生成后直接交给 Executor；Executor 可以适应实际情况，但不会修改一个正式 Plan 对象。

### 技术追问补充

- Planner System Prompt 要求输出简短、有序、可执行的编号步骤，并明确列出假设，不执行任务或
  输出最终答案。
- `run_agent()` 将 Planner 最终文本保存为 `plan_step.output`；当前没有 Plan dataclass、步骤 ID、
  依赖边或步骤状态枚举。
- `_executor_prompt()` 把原始任务和完整计划文本一起写入 Executor 的新任务。
- `run_planner_executor()` 只运行一次 Planner 和一次 Executor，没有计划更新或循环重规划阶段。

## 54. Executor 执行失败后由谁决定是否重新规划？

### 口述主回答

当前 Planner-Executor 不会自动重新规划。可修正的工具错误由 Executor 在自己的 Agent Loop 中
处理；如果整体计划失效，是否重新调用 Planner 应由外层 Workflow 决定。

Runtime 只负责模型、工具和停止控制，不理解计划语义。Planner 负责提出计划，Executor 负责执行
和报告事实，Workflow 才拥有重规划条件和循环次数。

### 追问问题与回答

**追问：如何区分可重试的步骤失败、局部计划失效和整体目标变化？**

参数或临时工具错误可以由 Executor 在当前循环中修正；局部步骤不适用时可调整执行方法；主要假设
或目标发生变化时才需要外层重新调用 Planner。

**追问：Runtime、Planner、Executor 和 Workflow 分别负责什么？**

Runtime 负责模型和工具循环；Planner 负责计划；Executor 负责执行和反馈；Workflow 负责是否以及
何时重新规划。

### 技术追问补充

- Executor System Prompt 明确允许步骤错误或不可执行时“合理适应并说明”，因此局部失败可在其
  普通 Agent Loop 中根据 Tool Result 自我修正。
- 核心 Runtime 不读取计划文本，也没有 step failure、plan invalid 或 replan 等事件类型。
- `run_planner_executor()` 返回 Executor 结果后直接结束，不检查失败类型，也不再次调用 Planner。
- 需要 replan 时只能由外层自定义 Workflow 组合新的 Planner 调用，当前没有通用重规划状态机。

## 55. Planner 是否可以调用工具？

### 口述主回答

技术上可以，因为 Planner 仍是普通 Agent；但项目提供的默认 Planner 不绑定工具，只负责规划。
这是为了避免规划阶段越过边界直接修改环境。

如果规划确实需要调查，我会只给只读工具，并要求输出计划而不是执行结果。真正有副作用的工具仍
应留给 Executor；仅靠提示词限制不是权限边界。

### 追问问题与回答

**追问：如果 Planner 可以调用 Tool，如何避免它越过规划边界直接完成执行任务？**

只给必要的只读调查工具，并通过工具集合和执行前 Hook 禁止写操作；不能只依赖 Prompt 要求。

### 技术追问补充

- `make_planner_agent()` 的接口没有 `tools` 参数，内部调用 `make_role_agent()` 时工具集合为空。
- 核心 `Agent` 本身支持绑定任意 AgentTool，因此调用者仍可手动构造有工具 Planner，Runtime
  不识别 Planner 角色并禁止工具。
- Planner Prompt 只是一层行为约束；真正的权限边界来自实际工具列表、Hook 和运行环境。
- 默认 Executor Builder 显式接受 `tools=`，体现副作用能力属于执行阶段。

## 56. Planner 和 Executor 应该使用同一个模型吗？

### 口述主回答

不要求相同。它们是独立 Agent，可以分别配置模型。Planner 更看重任务分解和长程推理，Executor
更看重工具调用稳定性、延迟和成本。

小任务使用同一模型最简单；复杂任务可以给 Planner 更强的推理模型，给 Executor 使用更便宜但
工具能力稳定的模型。当前项目不会自动选择模型，这属于组装和实验配置。

### 追问问题与回答

**追问：如何根据推理能力、延迟、成本和上下文需求选择模型？**

Planner 优先考虑任务分解和长上下文推理；Executor 优先考虑工具调用稳定性、延迟和成本，最终用
相同任务和预算下的评测决定是否值得拆分。

### 技术追问补充

- `make_planner_agent(provider=...)` 和 `make_executor_agent(provider=...)` 分别接收 Provider，
  两者没有共享模型约束。
- Planner 和 Executor 是独立 Agent，分别保存 system prompt、工具、请求参数和模型超时。
- `run_planner_executor()` 分别提供 `planner_max_turns` 与 `executor_max_turns`，但不自动做模型
  路由或成本优化。
- 每一步 State 都保留模型和 usage，可在 Eval 中比较同模型与异构模型方案的成功率和成本。

## 57. Reflection 为什么有用？

### 口述主回答

Reflection 的价值是让独立 Critic 按原任务检查草稿，再把具体问题反馈给 Generator 修订。它适合
发现遗漏要求、逻辑矛盾、论证不完整和结构问题。

对于简单任务、低风险回答，或者已经有可靠测试和规则验证的任务，Reflection 可能只是增加成本。
它不能替代外部验证，因为 Critic 仍然是模型。

### 追问问题与回答

**追问：Reflection 适合发现哪些问题，哪些任务不值得增加 Reflection 阶段？**

它适合发现遗漏要求、逻辑矛盾和结构问题；简单低风险任务，或已有快速确定性验证的任务通常不值得
额外增加模型批评。

### 技术追问补充

- `run_reflection()` 先运行 Generator 生成初稿，再在最多 `max_rounds` 个循环中运行 Critic；
  未批准时再运行 Generator 修订。
- Critic 输入包含原任务和当前完整草稿；默认 `make_critic_agent()` 不绑定工具。
- Critic Prompt 要求输出具体、可执行且按重要性排序的反馈；满意时输出批准标记。
- Reflection 达到轮数上限时返回最新草稿，不会伪造批准；批准仍是模型判断，不是外部验证。

## 58. Reflection 和简单地再次调用一次模型有什么区别？

### 口述主回答

区别在于角色、输入和停止条件都被显式化。Critic 同时看到原任务和当前草稿，输出可执行的修改
意见；Generator 再看到原任务、旧草稿和批评，返回完整修订版本。

Workflow 记录每次草稿、批评和修订，并用批准标记或轮数预算停止。简单再调用一次模型通常没有
这种明确反馈通道，也难以说明第二次调用为什么会更好。

### 追问问题与回答

**追问：Critic 接收什么输入、输出什么反馈，Revision 如何使用这些反馈？**

Critic 接收原任务和当前草稿，输出具体修改意见；Revision 同时接收原任务、旧草稿和反馈，并返回
完整修订答案。

### 技术追问补充

- 初稿 Prompt 只包含原任务；批评 Prompt 包含原任务和当前草稿；修订 Prompt 包含原任务、旧草稿
  和 Critic 反馈。
- 每个 Generator、Critic 和 Revision 都通过 `run_agent()` 形成独立 StepResult 和 State，
  `WorkflowResult.steps` 按实际执行顺序保留全部步骤。
- 修订 Prompt 明确要求返回完整新版答案，而不是差异或局部修改。
- `is_approved()` 当前以不区分大小写的字符串包含判断批准标记；它是 Workflow 停止协议，不证明
  结果客观正确。

## 59. 如何判断 Reflection 是否值得？

### 口述主回答

我会根据错误代价、任务复杂度和是否已有外部验证来决定。高风险、开放式、容易遗漏要求的任务更
值得 Reflection；有快速确定性测试时，应优先运行测试，而不是固定增加模型批评阶段。

当前 `run_reflection` 一旦被调用就按批准标记或轮数上限执行，没有自动风险判断。Reflection 也
可能让两个模型共同强化错误，所以重要事实仍要回到工具和外部证据验证。

### 追问问题与回答

**追问：Reflection 应该每次固定执行，还是根据任务风险和外部验证结果按需触发？**

应该按需触发。高风险、开放式或外部检查失败的任务更值得使用；简单任务不应固定增加批评阶段。

**追问：Reflection 是否可能强化原答案中的 hallucination，如何降低这种风险？**

可能。Critic 仍是模型，可能接受错误前提；应让批评基于原任务和可核对证据，并优先使用工具或
外部验证纠正事实。

### 技术追问补充

- `run_reflection()` 被调用后必定先运行一次 Generator；每轮至少运行一次 Critic，未批准时再运行
  一次 Generator 修订，因此成本随 `max_rounds` 线性增加。
- 当前函数没有风险评分、测试结果判断或自动触发条件；是否调用 Reflection 由外层调用者决定。
- 默认 Critic 无工具，只看到任务和当前草稿，因此无法独立核验文件、命令或外部事实。
- 停止条件是 Critic 输出中出现批准标记或轮数耗尽；批准标记不是客观正确性证明。

## 60. 如何判断 Agent 任务真的完成了？

### 口述主回答

我会区分三层：模型输出 final，只代表它声明当前回答结束；Runtime 停止，只说明循环因为 done、
预算、工具终止或 abort 结束；任务客观完成，需要测试、命令或其他外部检查通过。

项目用 Goal Loop 把第三层放在 Runtime 外面。Agent 每次给出候选结果后，完成检查读取当前 State
和环境；不通过就继续同一会话，直到验证成功、阻塞、预算耗尽或被中止。

### 追问问题与回答

**追问：为什么不能只依赖模型输出 `Done`？**

模型可能遗漏要求、误判工具结果或没有真正执行验证，所以 Done 只能作为声明，不能作为完成证据。

**追问：Agent 声明完成、Runtime 停止和任务客观完成有什么区别？**

Agent 声明是模型判断；Runtime 停止是控制流状态；客观完成是外部检查确认目标已满足。

### 技术追问补充

- 核心 Runtime 收到当前 Agent 的 `kind="final"` 后记录 `AgentEndEvent(reason="done")`，只结束
  当前内层运行。
- Goal 模式可由 Agent 调用 `update_goal` 声明 complete/blocked；`model_declared_check()` 从工具
  结果 details 中读取该声明。
- `run_goal_loop()` 在每段 run/resume 后调用独立 `CompletionCheck`；未通过时在同一 State 上
  追加继续任务。
- Goal 终态包括 complete、blocked、budget_exhausted 和 aborted；相同 blocker 连续三次才进入
  blocked。

## 61. 什么是外部验证信号？

### 口述主回答

外部验证信号是由模型自述之外的检查产生的完成证据，例如命令退出码、测试结果、规则检查、独立
Judge 或人工确认。它回答的是“目标是否真的满足”，而不是“模型是否说完成”。

Goal Loop 每完成一段运行就调用检查器：通过则结束为 complete；未通过则追加继续提示并 resume；
同时还受回合、Token、墙钟时间和 abort 限制。

### 追问问题与回答

**追问：验证信号由谁产生，Goal Loop 如何使用它决定继续、完成或终止？**

验证信号由命令、测试、规则、Judge 或其他外部检查器产生；Goal Loop 根据检查结果完成、继续、
标记阻塞，或在预算和 abort 条件下终止。

### 技术追问补充

- `CompletionCheck` 是 `Callable[[State], CompletionResult]`，结果字段为 `done`、`blocked` 和
  `reason`；检查器实现位于 Workflow 边界。
- Goal Loop 每次先检查 abort，再调用 CompletionCheck；done 立即返回 complete，blocked 会累计
  相同原因，未完成则检查预算后 resume。
- 回合与输出 Token 耗尽返回 budget_exhausted，墙钟截止和调用者 abort 返回 aborted。
- 每次继续和终止都会追加 `GoalStatusEvent`，记录 objective、状态、回合、Token 和原因。

## 62. SWE 类任务可以使用哪些验证信号？

### 口述主回答

单元测试证明被覆盖行为是否通过，静态检查发现类型或格式问题，构建结果证明项目能够编译打包，
Patch 只能证明产生了修改，官方评分器才最接近 Benchmark 的最终成功定义。任何单一信号都可能
覆盖不完整。

如果测试通过但不符合用户要求，我会把用户验收条件、针对性复现、回归测试和必要的语义审查组合
起来。项目支持固定命令检查，也支持重新执行模型声明的验证命令或使用独立 Judge。

### 追问问题与回答

**追问：单元测试、静态检查、构建结果、Patch 和官方评分器分别能证明什么？**

单测证明覆盖行为，静态检查发现类型和格式问题，构建证明可编译打包，Patch 只证明产生修改，
官方评分器最接近 Benchmark 最终标准。

**追问：如果测试通过，但实现不符合用户要求，如何组合多个验证信号？**

同时检查用户验收条件、针对性复现、回归测试、构建和必要的语义审查，不能把单个测试通过当作
全部要求满足。

### 技术追问补充

- `command_verifier_check(command)` 直接执行固定命令，退出码为 0 才返回 done。
- `executed_completion_check()` 先读取 Agent 的 complete 声明和 `verify_command`，再重新执行该
  非空命令；命令失败或缺失时保持未完成。
- `default_check(verifier=...)` 允许外部 verifier 否决模型的 complete 声明，调用者也可以实现
  组合多个信号的自定义 CompletionCheck。
- SWE 官方 scorer 位于 Eval/Host 边界，不是 Goal Loop 每轮默认调用的检查器。

## 63. 没有确定性验证器时如何判断完成？

### 口述主回答

没有确定性验证器时，我会采用风险分级的混合方案，而不是只让同一个模型自评。低风险任务可以用
规则检查加独立 Judge；高风险或不可逆操作需要人工确认，并保留证据和不确定性说明。

项目当前提供 Judge Agent 检查，但它仍可能被有说服力的错误文本误导，所以只能作为近似信号。
Goal Loop 的检查接口是可插拔的，调用者可以组合规则、Judge 或外部人工流程。

### 追问问题与回答

**追问：应该使用 LLM Judge、规则检查、人工确认，还是风险分级的混合方案？**

采用风险分级混合方案：先做确定性规则，再用独立 Judge 处理语义；高风险、不可逆或证据不足时
转人工确认。

### 技术追问补充

- `judge_agent_check()` 调用独立 Judge Agent，并要求返回 `{"done": bool, "reason": str}` JSON；
  解析失败时默认 done=false。
- `verified_completion_check()` 先要求执行 Agent 声明 complete，再让 Judge 作为 verifier 否决或
  放行。
- 当前 Judge 实现实际读取的是从 State 提取的最终输出文本，不是完整 Tool Trace，因此无法可靠
  核对所有执行证据。
- 当前没有内建人工审批状态或任务队列；高风险人工确认需要由外层系统实现，并在确认前保持未完成。

## 核对依据

- [`tools/task.py`](../../src/simple_long_horizon_agent/tools/task.py)
- [`workflow/base.py`](../../src/simple_long_horizon_agent/workflow/base.py)
- [`workflow/planner_executor.py`](../../src/simple_long_horizon_agent/workflow/planner_executor.py)
- [`workflow/reflection.py`](../../src/simple_long_horizon_agent/workflow/reflection.py)
- [`workflow/goal_loop.py`](../../src/simple_long_horizon_agent/workflow/goal_loop.py)
- [`workflow/goal_checks.py`](../../src/simple_long_horizon_agent/workflow/goal_checks.py)
- [`workflow/pdr.py`](../../src/simple_long_horizon_agent/workflow/pdr.py)
- [`08-agent-composition-and-workflows.md`](../design/08-agent-composition-and-workflows.md)
- [`test_workflow.py`](../../tests/unit/test_workflow.py)
- [`test_goal_loop.py`](../../tests/unit/test_goal_loop.py)
