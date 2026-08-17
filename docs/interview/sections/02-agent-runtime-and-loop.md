下面继续以“项目负责人 / 核心开发者”的第一人称回答。回答严格区分当前已经实现的能力、设计边界和生产化后才应补充的能力。

## 1. Agent Loop 的核心状态机是什么？

核心状态可以概括为：初始化、回合开始、上下文准备、模型决策、工具执行、回合结束和运行结束。

每轮固定执行：检查 Abort → 记录 TurnStart → 尝试压缩 → 构建 ContextView → 记录模型请求 → 调用 `generate` → 记录模型响应和 Assistant Message → 可选执行工具 → 记录工具结果 → TurnEnd → 判断是否继续。

终态有四种：`done`、`max_turns`、`tool_terminate` 和 `abort`。模型输出 final 只是 `done` 的触发条件；如果外层使用 Goal Loop，还需要外部检查才能认定业务目标完成。

```text
Initialized
    ↓
AgentStarted
    ↓
TurnStarted
    ↓
ContextPrepared
    ↓
ModelRequested
    ↓
ModelResponded
    ├─ ToolCall → ToolsExecuting → ToolResultsRecorded ┐
    └─ No Tool ────────────────────────────────────────┤
                                                       ↓
                                                   TurnEnded
                                  ┌────────────────────┴───────────────────┐
                                  ↓                                        ↓
                              NextTurn                         done/max_turns/abort
```

## 2. 为什么使用生成器，而不是普通循环、异步队列或事件总线？

实际上内部仍然是一个普通 `for` 循环，只是把它包装成同步生成器。生成器让我在保持控制流从上到下可读的同时，把中间 Event 实时暴露给调用者。

如果只是普通函数返回最终 State，调用者无法自然观察中间过程，也很难在长任务中实时落盘或中止。异步队列需要额外处理后台任务、队列关闭、取消传播和事件顺序；事件总线则会把一条清楚的调用链拆成多个订阅者，增加隐式控制流。

当前模型决策本身是顺序依赖的，真正适合并行的是单轮中的独立工具调用，所以核心保持同步生成器，工具边界内部使用线程池并发。代价是它不适合作为海量并发服务的最终调度模型。

## 3. 生成器具体解决了什么问题？

第一是惰性执行。`agent.run(task)` 只创建 State 和 Event Iterator，真正的模型调用发生在调用者消费 Iterator 时。

第二是流式观察。每个 Event 在 `yield` 之前已经写入 State，调用者可以边运行边打印、持久化或更新 Viewer。

第三是控制权清楚。谁消费 Iterator，谁决定运行何时继续，并可以通过 Abort Flag 请求停止。

第四是测试简单。单元测试可以逐个消费事件，检查每一步的顺序和 State 投影，而不需要启动后台服务。

## 4. 一轮 Agent Turn 的输入、输出是什么？

`generate` 的直接输入是 `list[Message]`，它来自当前 State 的活跃上下文，再经过可见性策略过滤。固定 system prompt、模型参数和工具定义在 LLM 边界组装成 `LLMRequest`。

`generate` 的直接输出是一条 Message，通常是 AssistantMessage，可以同时包含文本、ThinkingBlock 和多个 ToolCallBlock。

从 Runtime 角度看，一个完整 Turn 还包括这条模型输出引发的工具执行和 ToolResult Message。因此 Turn 不等于一次模型调用，而是“一次模型决策及其直接产生的行动和观察”。

## 5. Message、Event、State 分别承担什么职责？

Message 是模型语义，保存任务、Assistant 输出、工具调用和工具结果等可能进入模型上下文的内容。

Event 是运行事实，记录 Agent 生命周期、模型请求与响应、工具开始与结束、Hook、压缩和停止原因。

State 是一次运行的事实容器。它持有追加式 Event Stream、由事件派生的 StateSnapshot，以及少量运行级元数据。State 不负责替模型做决策，只负责统一记录和投影。

## 6. 为什么同时需要 Message 和 Event？边界是什么？

因为模型需要看到的内容和人类需要审计的事实不是同一集合。

工具结果需要成为 Message，模型下一轮必须看到；但工具耗时、开始时间、Hook 是否阻止、Agent 为什么停止，不应该全部塞进对话文本。

判断标准是：需要参与模型决策的内容进入 Message；描述运行中已经发生的活动进入 Event。Message 通过 MessageEvent 进入 State，因此它既是对话事实，也可以被完整追踪。

## 7. State 是可变对象还是不可变快照？

State 是受控可变的追加式聚合对象，但 Message 和 Event 是冻结的不可变值。

`state.events` 持续追加，不能原地改写历史；StateSnapshot 是可变的派生缓存，用于快速得到 messages 和 active context indices。如果 Snapshot 丢失或怀疑不一致，可以通过重新回放 Event Stream 重建。

所以准确说法是：不可变事实、追加式 State、可重建的可变投影。`state.messages` 每次还会返回列表浅拷贝，避免并发读取时看到长度变化。

## 8. 工具调用结果如何重新进入模型上下文？

模型先返回包含 ToolCallBlock 的 AssistantMessage，Runtime 会先把这条消息写入 State，然后执行工具。

每个工具产生 ToolResult。Runtime 将同一轮所有结果组装成一条 `UserMessage(kind="tool_result")`，其中每个 ToolResultBlock 通过 `tool_call_id` 指回原调用。

下一轮开始时，Runtime 从更新后的 State 重新构建 ContextView，因此模型会看到“原始任务 → Assistant 工具调用 → User 工具结果”，再基于真实环境反馈继续决策。工具不会直接递归调用模型。

## 9. 如何识别模型已经完成任务？

LLM Adapter 返回统一的 `stop_reason`。`make_llm_agent` 将 `end_turn` 映射为 `AssistantMessage(kind="final")`，其他可继续状态映射为 `step`。

Runtime 只在 final 的 sender 等于当前 Agent 时设置完成状态。它不会通过搜索“完成了”“done”等自然语言判断，也不会被历史中其他 Agent 的 final 误停。

需要强调：这里判断的是“当前 Agent 认为回答完成”。对于代码修复等可验证任务，外层 Goal Loop 还要运行测试或检查工作区，才能认定业务目标完成。

## 10. 有哪些停止条件？

当前核心 Runtime 有四种停止原因。

- `done`：当前 Agent 输出自己的 final。
- `max_turns`：回合预算耗尽但没有 final。
- `tool_terminate`：工具返回 `terminate=true`。
- `abort`：调用者的 Abort Flag 生效。

`max_turns` 和 `abort` 都不能伪装成正常完成。工具终止也只代表环境要求停止，不自动等于任务成功。

## 11. 如何防止 Agent 无限循环？

第一层是 `max_turns`，每次运行都有显式回合上限，默认值是 10。

第二层是外部 Abort Flag，可以承接墙钟时间、用户中止或上层预算控制。工具也会收到 Abort 函数，但工具必须协作式检查，Python 线程无法保证被强制杀死。

第三层是工具超时、Provider 有限重试和 Goal Loop 的 Token、回合及 blocked 判断。

当前核心没有通用的“重复行为检测器”。如果模型在预算内重复同一无效动作，最终仍由 max turns 截断；生产化可以在 Hook 或 Goal Loop 中增加重复调用指纹和停滞检测。

## 12. 模型只输出文本、不调用工具怎么办？

是否结束不取决于有没有工具，而取决于响应的 stop reason。

如果模型输出纯文本并以 `end_turn` 结束，它会被映射为 final，Runtime 正常以 `done` 停止。

如果模型输出纯文本但 stop reason 是 `max_tokens` 等可继续状态，它会被记录为 step。当前轮正常结束，下一轮再次调用模型；如果始终不产生 final，最终以 `max_turns` 截断。

## 13. 如何处理模型连续调用无效工具？

结构性错误先在 LLM 边界处理。未知工具名或无法解析的 JSON 参数最多进行三次模型调用，也就是首次调用加两次纠正请求。纠正请求会明确列出错误原因和可用工具。

如果仍然无效，响应不会让整个 Agent 崩溃。未知工具、参数校验失败、工具异常和超时都会转成 `is_error=true` 的 ToolResult，进入下一轮供模型自我修正。

如果模型继续重复，`max_turns` 最终停止运行。当前没有无限重试，也不会静默把错误调用改成另一个工具。

## 14. Agent 中断后能否恢复？恢复点是什么？

当前支持两种不同程度的恢复。

同一进程和同一 State 上，可以通过 `Agent.resume(state, followup)` 追加任务并继续运行。此前的 Message、Event、压缩投影和 Trace 都会保留。

StateSnapshot 也可以从 Event Stream 重建。但跨进程恢复还不是完整的生产级 Checkpoint 协议：调用者需要持久化 State，并重新创建同名 Agent、工具注册、ContextPolicy、工作区和外部连接。

最安全的恢复点是一个完整边界之后，例如 ToolResult Message 已经写入，或者一个 Turn 已经结束。正在执行中的外部副作用不能仅靠 Event Stream 自动恢复。

## 15. 同一个任务重放能得到一致结果吗？

必须区分状态重放和执行重放。

给定相同 Event Stream，StateSnapshot、消息历史和活跃上下文投影是确定性的。并行工具即使完成顺序不同，模型看到的结果也按原始 ToolCall 顺序组装。

但重新调用真实模型、Shell 或外部服务不是确定性的，因为模型、环境、时间和网络都可能变化。使用 Fake Model、Fake Tool 和固定输入时，可以实现确定性测试。

如果要求精确执行重放，需要保存模型响应、工具输出、环境版本和副作用结果，并在重放时使用记录值，而不是重新调用外部系统。

## 16. 如何处理流式模型输出和工具调用？

LLM 层定义了独立的 StreamEvent，包括文本增量、思考增量、工具调用开始、参数增量、工具调用完成、usage 更新和最终 done。

当前 `make_llm_agent` 使用阻塞的 `complete_with_tool_call_retry`，它会先消费完整 Stream，取得最终 LLMResponse，再转换成 AssistantMessage。因此核心 Agent Runtime 当前记录的是完成后的规范响应，不会逐 Token 向外 yield。

工具只有在最终 done 到达、参数完成解析后才会执行，避免对尚未完成的 JSON 参数产生副作用。Runtime Event 和 LLM StreamEvent 是两套不同协议，不能混用。

## 17. 为什么说运行机制是“显式”的？

因为核心行为都能在 `run()` 中按顺序直接读到：什么时候压缩、什么时候构建上下文、什么时候调用模型、什么时候记录响应、什么时候执行工具、什么时候停止。

没有隐藏的节点调度器、自动插件发现、后台 Agent 守护线程或由订阅关系驱动的控制流。外围能力只能通过 initializer、ContextPolicy、Tool、Hook 或 Workflow 接入。

同时每个关键决定都会留下结构化 Event，所以“显式”既指代码控制流，也指运行证据。

## 18. 只看一个核心函数，应该看什么？

我会让面试官先看 `core.py` 中的 `run()`。它包含完整生命周期：AgentStart、Session Hook、Turn 循环、Compression、ContextView、ModelRequest、generate、ModelResponse、Message 写入、工具调度和四种退出原因。

看完 `run()` 后，第二个函数是 `dispatch_tool_calls()`，它解释并行执行、Hook gate、错误转换、结果排序和 ToolResult Message 如何形成。

## 19. Agent Loop 伪代码

```python
def run(agent, state, max_turns, abort):
    record(AgentStart)

    fire_hook(SESSION_START)

    end_reason = "max_turns"

    for _ in range(max_turns):
        if abort():
            end_reason = "abort"
            break

        record(TurnStart)

        apply_compression_if_needed(state)
        context = build_context_view(state)

        record(ModelRequest(context))
        output = agent.generate(context.messages)
        record(ModelResponse(output))
        record(MessageEvent(output))

        final_emitted = (
            output.sender == agent.name
            and output.kind == "final"
        )

        if output.has_tool_calls:
            results = dispatch_tool_calls(output.tool_calls)
            record_tool_events(results)
            record(tool_result_message(results))

            if any(result.terminate for result in results):
                record(TurnEnd(terminated=True))
                end_reason = "tool_terminate"
                break

        record(TurnEnd)

        if final_emitted:
            end_reason = "done"
            break

    fire_hook(SESSION_END)
    record(AgentEnd(reason=end_reason))
```

## 20. 模型返回两个工具调用时，State 何时更新？

首先，模型的完整 AssistantMessage 会先进入 State，所以工具执行前已经保留两个 ToolCallBlock。

Runtime 随后按调用顺序为两个调用分别记录 ToolExecutionStartEvent，再执行 PRE_TOOL_USE Hook，然后将可并行工具提交线程池。

工具执行结束时，Update 和 End Event 可以按照实际完成顺序进入 State。但所有工具结束后，Runtime 才会生成一条统一的 ToolResult UserMessage；其中结果按照模型原始 ToolCall 顺序排列，而不是线程完成顺序。

因此模型不会看到半批结果，下一轮只会看到完整且顺序稳定的结果包。

## 21. 工具执行成功，但结果尚未写入历史时进程崩溃，会怎样？

当前实现不能保证这类窗口的 exactly-once 恢复。

如果工具已经产生外部副作用，但 ToolResult Message 尚未写入 State，恢复后只能看到 ToolExecutionStartEvent，或者最多看到不包含结果正文的 ToolExecutionEndEvent。系统无法仅凭现有事件判断副作用是否已经成功提交。

正确做法是将这次调用标记为 in-doubt：对于只读或幂等工具，可以安全重试；对于写操作，应先查询外部系统或通过 idempotency key 对账，不能直接重放。

生产化需要增加持久化调用日志、幂等键、准备/提交状态或工具级 reconcile。当前项目提供可观察边界，但没有宣称解决任意外部副作用的一次且仅一次执行。

## 22. Event 是事实、控制命令，还是二者兼有？

Event 的定位是已经发生的不可变事实，不是外部控制命令。

真正的控制输入包括 Abort Flag、HookDecision、CompressionDecision、ToolResult 的 terminate 和 Runtime 的直接分支逻辑。Runtime 根据这些输入作出决定，然后记录相应 Event。

ContextCompressionEvent 在回放时会更新 StateSnapshot，看起来具有“驱动投影”的作用，但它表达的是“压缩已经发生并形成了这个活跃索引”，不是“请执行一次压缩”的命令。

因此更准确的说法是：Event 是具备重放语义的事实。它可以重建状态和解释控制结果，但不能由 Viewer 或外部消费者随意注入来操纵 Runtime。
