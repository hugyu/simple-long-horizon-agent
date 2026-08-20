# 面试专题回答：Workflow 与完成判断

本文覆盖 [`question-checklist.md`](question-checklist.md) 中第 6 个专题。这里把普通
Agent Loop、多个 Agent 运行的 Workflow，以及外部 Goal Loop 的完成判定严格分开。

## 1. Planner-Executor、Reflection、Parallel、Goal Loop 分别适合什么任务？

Planner-Executor 适合先形成可检查计划、再由有工具权限的 Agent 执行；Reflection 适合有
明确质量标准、草稿可以根据具体反馈改进的任务；Parallel 适合彼此独立的子任务或需要多个
候选的场景；Goal Loop 适合模型可能过早声明结束、需要外部证据反复验收的长任务。

它们不是普遍更优。简单任务增加角色只会增加成本和信息损失；Reflection 的 critic 可能给出
错误反馈；Parallel 会争用资源；Goal Loop 的 verifier 若定义不好，会把 Agent 引向错误目标。

## 2. 它们是独立 Runtime，还是同一个 Runtime 上的策略？

它们复用同一个 Runtime。每个 Workflow step 都通过普通 `Agent.run()` 执行，Workflow 只负责
决定运行哪些 Agent、给每一步什么任务、怎样传递输出和何时结束。

所以 Planner、Executor、Critic 和 Worker 都不是特殊 Runtime。这个边界避免为每种编排复制
模型调用、工具调度、State 和 Trace 逻辑，也让每一步仍可单独测试和审计。

## 3. Workflow 状态放在哪里？

每个 Agent step 的运行状态保存在自己的 `State`，并由 `StepResult.state` 保留。Workflow
自身的有序步骤和最终输出保存在 `WorkflowResult`。固定 Workflow 当前主要通过普通 Python
局部变量维护阶段控制，没有通用持久化 Workflow State Machine。

Goal Loop 更特殊：它复用同一个 Agent State 继续 `resume()`，并把 active、complete、blocked、
budget_exhausted 或 aborted 写成 `GoalStatusEvent`。因此它的完成生命周期可以从事件检查，但
当前固定 Workflow 还不支持进程崩溃后的通用节点级恢复。

## 4. Reflection 为什么可能有效？什么时候会产生反作用？

它可能有效，是因为生成和批评被分成独立模型调用，Critic 能把遗漏转成具体反馈，Generator
再基于原任务、旧草稿和反馈完整修订。当前实现用 approval marker 或最大轮数决定停止。

反作用也很明确：Critic 可能误判正确答案、提出互相冲突的修改，Generator 可能为迎合 marker
而退化；同时每轮都增加 Token 和延迟。项目测试证明控制流能按批准标记停止，但没有公开消融
证明它在某个 Benchmark 上稳定提升，因此我只把它描述为可实验策略。

## 5. Goal Loop 与普通 Agent Loop 的区别是什么？

普通 Agent Loop 管一段模型—工具交互，模型返回 `kind="final"` 就以 Runtime 的 done 原因结束。
Goal Loop 是外层控制：一段运行结束后调用 `completion_check(state)`，只有检查通过才把目标标为
complete；否则向同一个 State 追加 continuation prompt 并 `resume()`。

因此 final 是“这段 Agent 运行结束”，不是“业务目标客观完成”。Goal Loop 还统一计算累计
turn、Token、wall-clock 和 abort 预算，并记录目标状态事件。

## 6. Agent 如何知道任务已经完成？完成信号由谁产生？

项目支持不同强度的信号。最弱的是模型调用 `update_goal(status="complete")`；更强的是执行固定
命令、重新执行模型给出的 `verify_command`，或者让独立 Judge 阅读 transcript。最终停止决定在
Goal Loop 的 `completion_check`，不是工具或模型单方面决定。

选择哪种检查取决于任务。代码修复优先使用测试或构建命令；结构化数据用 Schema 和业务规则；
开放写作可能只能使用 rubric 或 Judge，但要承认它仍有模型误差。

## 7. 什么是“模型声明完成”，什么是“任务实际完成”？

模型声明完成是一次可审计的候选结论，例如调用 `update_goal` 并给出验证命令。任务实际完成则
需要与目标同源的外部证据，例如测试退出码为零、产物通过 Schema、官方 scorer 判定通过。

二者分开是因为模型只能根据它看到的上下文判断，可能遗漏失败、误读输出或为了结束而乐观。
声明应该触发验收，而不是替代验收。

## 8. 仅依靠 LLM 输出 `done` 有什么问题？Runtime 为什么不能直接相信？

自由文本 `done` 容易误解析，也没有携带验证证据；即使改成结构化 tool call，模型仍可能对
环境状态判断错误。核心 Runtime 也不了解具体任务的成功定义，不能内置“测试通过”或“文档
足够好”这样的业务规则。

所以 Runtime 可以接受 final 来结束当前内层运行，客观完成由外层 Goal Loop 或 Eval verifier
负责。这样通用控制流和任务语义不会混在一起。

## 9. 外部验证信号有哪些类型？

当前代码实际支持 shell command verifier、模型提交并重新执行的 `verify_command`、独立 Judge
和自定义 `CompletionCheck`。在评测层还有 suite 内 evaluate hook、后续 Judge run 和官方
harness。

一般可以归为确定性程序检查、环境状态检查、结构化规则和模型 Judge。越接近任务真实验收
标准越好；模型 Judge 适合难以程序化的开放结果，但不应被描述成客观无误。

## 10. 验证器本身不可靠怎么办？

先区分 verifier 的误报、漏报和基础设施失败。确定性测试要固定环境并保存 stdout、stderr 和
退出码；模型 Judge 要固定版本和 rubric，保留原始判决，必要时做多 Judge 或人工抽检。

当前 Goal Loop 会把 verifier 返回的 `done=False` 当作否决，但没有通用的 verifier quorum 或
置信度校准。如果 verifier 本身异常，合理做法是记录“验证不可用”，不要把它等价为任务成功；
是否重试应由外层策略结合幂等性和预算决定。

## 11. Agent 声称代码修好，但测试未通过，Runtime 如何处理？

外层检查应拒绝完成。`executed_completion_check()` 只有在模型声明 complete、提供非空命令且
该命令实际返回零时才给出 `done=True`；测试非零时返回未完成，Goal Loop 继续同一 State。

这不是把内层 `AgentEndEvent(reason="done")` 改写掉。那条事件仍表示上一段模型运行正常结束；
随后新增的 `GoalStatusEvent(status="active")` 和 continuation message 表示业务目标尚未通过。

## 12. 验证失败后如何记录、反馈并决定继续还是终止？

Completion Check 返回 `done=False` 和 reason，Goal Loop 记录 active 的 `GoalStatusEvent`，再向
同一会话追加 continuation prompt，明确提醒完成尚未被证明，让 Agent 查看失败并继续。每段
输出和 State 都保留在步骤结果中。

继续受多重边界约束：caller abort 或 wall-clock 到达是 aborted；累计 turn 或 Token 到达是
budget_exhausted；相同 blocker 连续达到三次才是 blocked；检查通过才是 complete。

## 13. 如何防止 Agent 为了通过验证器而投机？

验证器必须尽量检查真实目标，而不是容易操纵的代理指标。隐藏测试和 gold 数据不能进入模型
可见 task；测试应在干净环境运行；评分脚本和产物要分权保存；对高风险任务还要限制模型修改
验证器、测试或运行配置的权限。

当前 Eval 框架分离 `task_input` 与 `eval_inputs`，SWE-bench 还用独立官方 harness 评分。但项目
不是完整对抗安全平台；如果 Agent 能修改测试命令本身，仅重新执行它提供的 command 仍可能被
投机，所以 production 需要宿主拥有的固定 verifier。

## 14. 没有明确验证器的开放任务如何停止？

我会使用分层信号：模型给结构化完成声明，再由 rubric Judge 或调用者审核，同时保留最大 turn、
Token 和 wall-clock 预算。若只有模型声明，就应把结果标为 declared complete，而不要包装成
客观验证成功。

当前 `model_declared_check` 支持这种弱语义，`verified_completion_check` 可以增加 Judge gate。
开放任务无法完全消除主观性，重点是让判定来源和不确定性可见。

## 15. 最大 Turn 数是兜底还是正常退出？

它是失控保护和预算边界，不是任务成功条件。普通 Runtime 达到上限记录
`AgentEndEvent(reason="max_turns")`；Goal Loop 达到累计上限返回 `budget_exhausted`。

上层仍可读取最后一个 Assistant 输出用于诊断或后续步骤，但不能因为有输出就把状态改成
complete。把截断输出和成功状态分开，是避免评测虚高的重要条件。

## 16. 达到最大 Turn 时应标记为什么？

应标记为预算耗尽，而不是完成。内层 Runtime 的精确原因是 `max_turns`，Goal Loop 的外部状态
是 `budget_exhausted`。如果调用者主动中止或 wall-clock abort，则是 `aborted`，两者也不应混用。

这个区分能让 Eval 判断究竟是能力失败、预算不足还是人工取消，而不是把所有非成功都压成一个
error。

## 17. 最大 Turn、预算耗尽和验证成功的优先级是什么？

当前 Goal Loop 每段结束后先检查外部 abort 和 wall-clock，再运行完成检查；检查通过就 complete。
未完成时再处理稳定 blocker、turn/Token 预算，最后才继续。因此一次已经产生结果的 segment
仍有机会被验证成功，不会仅因为刚好用完最后一个 turn 就丢掉成功证据。

但 caller abort 和 wall-clock 是更高优先级的外部控制，会报告 aborted。Token/turn 用尽则报告
budget_exhausted，永远不会自动转成 complete。

## 核心场景题

我会把模型的 `done` 当成候选完成，而不是最终状态。模型调用 `update_goal(status="complete",
verify_command="...")` 后，外层 `executed_completion_check` 在宿主控制下重新执行验证命令。
测试失败就返回 `done=False` 和失败原因；Goal Loop 保留上一段的 `AgentEndEvent`，追加 active 的
`GoalStatusEvent`，再向同一个 State 加一条 continuation message，让模型看到“尚未证明完成”并
根据测试输出继续修改。

每次继续都会累计 turn、Token 和 wall-clock。只有后续测试返回零才记录 complete；如果累计
turn 或 Token 到上限则是 budget_exhausted，调用者中止则是 aborted，相同阻塞原因连续三轮才是
blocked。当前代码已经覆盖 verifier veto、失败后 resume 和预算状态；对于真正不可信代码，验证
命令还应由宿主固定，并放在隔离环境中执行。

## 核对依据

- [`workflow/goal_loop.py`](../src/simple_long_horizon_agent/workflow/goal_loop.py)
- [`workflow/goal_checks.py`](../src/simple_long_horizon_agent/workflow/goal_checks.py)
- [`workflow/`](../src/simple_long_horizon_agent/workflow/)
- [`08-agent-composition-and-workflows.md`](../docs/design/08-agent-composition-and-workflows.md)
- [`tests/unit/test_goal_loop.py`](../tests/unit/test_goal_loop.py)
- [`tests/unit/test_workflow.py`](../tests/unit/test_workflow.py)
