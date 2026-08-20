# 05. Workflow 与完成判断

本文对应 [`question-checklist.md`](question-checklist.md) 中第五部分的问题。每道题包含可直接
口述的主回答，以及精简的技术追问补充。

## 46. 什么时候应该创建子 Agent？

### 口述主回答

当子任务相对独立、需要不同提示词或工具，或者可以并行探索时，我会考虑创建子 Agent。项目里
父 Agent 通过 `task` 工具选择已注册的子 Agent，子 Agent 使用普通 Runtime 独立完成任务，再把
结果返回父 Agent。

收益是上下文和职责更聚焦，也能隔离不同角色。代价是增加模型调用、上下文传递和结果合并成本；
如果任务强依赖共享状态、步骤很短或需要频繁往返，多 Agent 反而不如单 Agent 直接执行。

### 技术追问补充

- 适合委派的是检索、独立分析、多文件调查或专门角色任务。
- 不适合的是强顺序依赖、共享可变资源或委派成本高于任务本身的场景。
- 子 Agent 类型来自预先注册列表，模型不能动态导入任意 Agent。

## 47. 子 Agent 是否拥有独立上下文？

### 口述主回答

是的。`task` 工具会为选中的子 Agent 调用一次新的 `agent.run()`，因此它拥有独立的 State、
消息历史、压缩索引和事件流，不会把父 Agent 的完整对话直接复制过去。

父 Agent 传递的是明确子任务和可选背景说明。子 Agent 返回给父模型的是最终文本；完整子事件放在
工具结果的本地详情中，供 Trace 合并，不会全部塞回父 Agent 上下文。

### 技术追问补充

- 子任务通过 `task` 参数传递，背景和约束通过可选 context Message 传递。
- 父模型默认只看到子 Agent 最终文本，不看到完整内部轨迹。
- 当前标准结果以文本为主；需要结构化结果时，应设计明确工具结果协议。

## 48. 子 Agent 能否调用父 Agent 的全部工具？

### 口述主回答

不会自动继承。每个子 Agent 在创建时已经绑定自己的工具集合、提示词和上下文策略，`task` 工具
只是从注册表选择并运行它。

因此工具权限应在子 Agent 组装阶段按最小权限配置。工作目录、MCP 连接和外部凭据也应按子任务
隔离，不能因为父 Agent 有权限就默认传给子 Agent。

### 技术追问补充

- 子 Agent 的工具来自自身 `Agent.tools`，与父 Agent 工具表相互独立。
- 当前核心没有统一权限继承模型，隔离由 Builder、Session 和运行环境负责。
- 高风险工具还可以通过执行前 Hook 阻止调用。

## 49. 子 Agent 可以继续创建自己的子 Agent 吗？

### 口述主回答

可以，只要这个子 Agent 自己也绑定了 `task` 工具。但当前 Runtime 没有自动限制委派深度，也没有
统一统计整棵委派树的调用次数和总预算。

项目现有保护主要是每次子运行的 `max_turns`、可选软提醒和外部 abort。真正防止无限递归，需要
上层增加深度、调用次数和全局预算控制。

### 技术追问补充

- `task_tool` 为每次委派设置局部硬回合上限，也可以在接近上限时注入提醒。
- 当前没有内建 `max_depth`、全局子任务计数或跨 State Token 账本。
- 生产化方案应由 Workflow 传递委派深度和预算上下文，并在入口拒绝超限调用。

## 50. 父子 Agent 同时修改同一个仓库时如何避免冲突？

### 口述主回答

独立 State 只隔离对话和事件，不会自动隔离文件系统。如果父子 Agent 的工具绑定到同一个工作
目录，它们仍可能同时修改同一文件。

有写操作时，我会根据任务选择串行执行或独立 worktree。并行探索可以使用独立 worktree，最后由
一个明确的合并或最终执行阶段写回主工作区；简单强依赖修改则直接串行更可靠。

### 技术追问补充

- 通用 `task` 工具不会自动创建 worktree。
- PDR 等调用者可以为不同尝试绑定独立工作区，SWE 评测已有这种组装方式。
- 合并冲突属于 Workflow 或版本控制层职责，不由核心 Runtime 自动解决。

## 51. 子 Agent 的 Token Budget 如何分配？

### 口述主回答

当前项目主要提供局部回合预算，没有完成父子之间的统一 Token 分配。`task` 工具可以限制每个
子 Agent 的最大回合，Workflow 也可以为不同步骤设置各自回合上限。

如果需要全局预算，我会先给父 Agent 预留决策和汇总额度，再按子任务风险分配局部预算，并由上层
统一统计。当前代码还没有跨多个独立 State 的全局预算账本。

### 技术追问补充

- 子运行支持 `max_turns` 和软回合提醒。
- Goal Loop 的 Token 预算针对同一个连续 State，不会自动汇总所有子 Agent。
- 全局预算需要从子事件或各 `StepResult.state` 汇总后由 Workflow 控制。

## 52. Planner 和 Executor 为什么需要分开？

### 口述主回答

分开是为了让“决定做什么”和“实际改变环境”拥有不同职责。项目里的 Planner 默认没有工具，只
生成可检查的执行计划；Executor 接收原任务和计划，再使用真实工具完成任务。

这样计划可以单独审计，也能限制规划阶段的副作用。代价是多一次模型调用，而且计划错误仍可能
传给执行阶段，所以 Executor 允许根据真实环境适当调整。

### 技术追问补充

- Planner 的 State 和 Executor 的 State 独立，计划作为文本进入 Executor 任务。
- Planner 不应修改环境；Executor 不应悄悄改变用户目标。
- 工作流结果保留两个步骤的完整 State，便于检查计划和执行差异。

## 53. Planner 输出什么结构？

### 口述主回答

当前实现输出简短的自然语言编号计划，不是强类型步骤列表或依赖图。Workflow 直接把 Planner 的
最终文本和原任务一起交给 Executor。

这个计划是一次性生成的，Executor 可以在执行中适应失败，但不会修改一个正式计划对象。当前
Planner-Executor 也没有自动动态重规划。

### 技术追问补充

- 当前计划只是 `StepResult.output` 文本，没有步骤状态机。
- 适合固定、线性的任务；复杂依赖图需要新增结构化协议和调度逻辑。
- 计划需要保留假设、顺序和可执行步骤，但不应直接写最终答案。

## 54. Executor 执行失败后由谁决定是否重新规划？

### 口述主回答

当前 Planner-Executor 不会自动重新规划。可修正的工具错误由 Executor 在自己的 Agent Loop 中
处理；如果整体计划失效，是否重新调用 Planner 应由外层 Workflow 决定。

Runtime 只负责模型、工具和停止控制，不理解计划语义。Planner 负责提出计划，Executor 负责执行
和报告事实，Workflow 才拥有重规划条件和循环次数。

### 技术追问补充

- 参数错误或临时工具失败可以由 Executor 直接重试或换方法。
- 局部步骤失效时 Executor 可适应，但目标或主要假设变化应重新规划。
- 当前仓库没有通用 replan 状态机，需要组合 Reflection 或自定义 Workflow。

## 55. Planner 是否可以调用工具？

### 口述主回答

技术上可以，因为 Planner 仍是普通 Agent；但项目提供的默认 Planner 不绑定工具，只负责规划。
这是为了避免规划阶段越过边界直接修改环境。

如果规划确实需要调查，我会只给只读工具，并要求输出计划而不是执行结果。真正有副作用的工具仍
应留给 Executor；仅靠提示词限制不是权限边界。

### 技术追问补充

- `make_planner_agent()` 默认创建无工具 Planner。
- 自定义 Planner 可以绑定工具，Runtime 不会禁止。
- 只读调查与写操作应通过工具集合和 Hook 做实际隔离。

## 56. Planner 和 Executor 应该使用同一个模型吗？

### 口述主回答

不要求相同。它们是独立 Agent，可以分别配置模型。Planner 更看重任务分解和长程推理，Executor
更看重工具调用稳定性、延迟和成本。

小任务使用同一模型最简单；复杂任务可以给 Planner 更强的推理模型，给 Executor 使用更便宜但
工具能力稳定的模型。当前项目不会自动选择模型，这属于组装和实验配置。

### 技术追问补充

- 两个 Agent 分别持有自己的 Provider、Prompt、工具和回合预算。
- 选择时应同时比较成功率、调用成本、延迟和上下文窗口。
- 是否值得拆模型需要通过相同任务和预算下的 Eval 验证。

## 57. Reflection 为什么有用？

### 口述主回答

Reflection 的价值是让独立 Critic 按原任务检查草稿，再把具体问题反馈给 Generator 修订。它适合
发现遗漏要求、逻辑矛盾、论证不完整和结构问题。

对于简单任务、低风险回答，或者已经有可靠测试和规则验证的任务，Reflection 可能只是增加成本。
它不能替代外部验证，因为 Critic 仍然是模型。

### 技术追问补充

- 当前流程是草稿、批评、完整修订，直到批准或达到轮数上限。
- Critic 默认无工具，只根据任务和草稿给反馈。
- 适合有明确质量标准但缺少直接确定性检查的任务。

## 58. Reflection 和简单地再次调用一次模型有什么区别？

### 口述主回答

区别在于角色、输入和停止条件都被显式化。Critic 同时看到原任务和当前草稿，输出可执行的修改
意见；Generator 再看到原任务、旧草稿和批评，返回完整修订版本。

Workflow 记录每次草稿、批评和修订，并用批准标记或轮数预算停止。简单再调用一次模型通常没有
这种明确反馈通道，也难以说明第二次调用为什么会更好。

### 技术追问补充

- Generator 和 Critic 每次都是独立普通 Agent 运行，各自保留 State。
- 修订提示要求返回完整答案，不是只返回差异。
- 批准标记是 Workflow 协议，不代表任务已通过外部验证。

## 59. 如何判断 Reflection 是否值得？

### 口述主回答

我会根据错误代价、任务复杂度和是否已有外部验证来决定。高风险、开放式、容易遗漏要求的任务更
值得 Reflection；有快速确定性测试时，应优先运行测试，而不是固定增加模型批评阶段。

当前 `run_reflection` 一旦被调用就按批准标记或轮数上限执行，没有自动风险判断。Reflection 也
可能让两个模型共同强化错误，所以重要事实仍要回到工具和外部证据验证。

### 技术追问补充

- 成本包括一次初稿，以及每轮 Critic 和可能的 Generator 修订调用。
- 可以根据测试失败、低置信度或高风险标签在外层按需触发。
- 当前批准判断依赖模型输出标记，不能作为客观正确性的证明。

## 60. 如何判断 Agent 任务真的完成了？

### 口述主回答

我会区分三层：模型输出 final，只代表它声明当前回答结束；Runtime 停止，只说明循环因为 done、
预算、工具终止或 abort 结束；任务客观完成，需要测试、命令或其他外部检查通过。

项目用 Goal Loop 把第三层放在 Runtime 外面。Agent 每次给出候选结果后，完成检查读取当前 State
和环境；不通过就继续同一会话，直到验证成功、阻塞、预算耗尽或被中止。

### 技术追问补充

- `kind="final"` 对应内层 `AgentEnd(reason="done")`，不是业务成功状态。
- Goal 状态包括 complete、blocked、budget_exhausted 和 aborted。
- 同一阻塞原因连续出现达到阈值时才标记 blocked，避免一次失败就误判无法推进。

## 61. 什么是外部验证信号？

### 口述主回答

外部验证信号是由模型自述之外的检查产生的完成证据，例如命令退出码、测试结果、规则检查、独立
Judge 或人工确认。它回答的是“目标是否真的满足”，而不是“模型是否说完成”。

Goal Loop 每完成一段运行就调用检查器：通过则结束为 complete；未通过则追加继续提示并 resume；
同时还受回合、Token、墙钟时间和 abort 限制。

### 技术追问补充

- `CompletionCheck` 接收当前 State，返回 done、blocked 和 reason。
- 检查器可以读取工作区或执行命令，不要求由核心 Runtime 实现。
- 每轮 Goal 状态会记录成事件，便于审计为什么继续或停止。

## 62. SWE 类任务可以使用哪些验证信号？

### 口述主回答

单元测试证明被覆盖行为是否通过，静态检查发现类型或格式问题，构建结果证明项目能够编译打包，
Patch 只能证明产生了修改，官方评分器才最接近 Benchmark 的最终成功定义。任何单一信号都可能
覆盖不完整。

如果测试通过但不符合用户要求，我会把用户验收条件、针对性复现、回归测试和必要的语义审查组合
起来。项目支持固定命令检查，也支持重新执行模型声明的验证命令或使用独立 Judge。

### 技术追问补充

- `command_verifier_check` 以固定命令退出码作为信号。
- `executed_completion_check` 会重新运行 Agent 声明的非空验证命令。
- Judge 只能读取提供给它的证据，可靠性低于真实执行结果。
- 官方 scorer 适合最终评测，不一定适合每个中间回合频繁调用。

## 63. 没有确定性验证器时如何判断完成？

### 口述主回答

没有确定性验证器时，我会采用风险分级的混合方案，而不是只让同一个模型自评。低风险任务可以用
规则检查加独立 Judge；高风险或不可逆操作需要人工确认，并保留证据和不确定性说明。

项目当前提供 Judge Agent 检查，但它仍可能被有说服力的错误文本误导，所以只能作为近似信号。
Goal Loop 的检查接口是可插拔的，调用者可以组合规则、Judge 或外部人工流程。

### 技术追问补充

- Judge 应独立于执行 Agent，并同时看到原目标和可核对证据。
- 规则可以先检查格式、引用、必填项和已执行步骤，再让 Judge 处理语义。
- 高风险场景应由上层保持未完成并转人工确认，不能把模型置信度当成完成证明。

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
