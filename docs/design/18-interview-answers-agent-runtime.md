# 18. 面试专题回答：Agent Runtime 与核心抽象

本文覆盖 [`17-interview-question-checklist.md`](17-interview-question-checklist.md)
中第 1 个专题的全部问题。每个回答都按
[`16-interview-answer-guidelines.md`](16-interview-answer-guidelines.md) 使用候选人第一
人称口吻，并以当前代码和测试为事实边界。

## 1. 画一下 Runtime 的整体架构和一次 Turn 的完整数据流

我把 Runtime 设计成一个小型控制内核，核心只有 `Agent`、`State`、`Message`、
`ContextView` 和显式 `run()` 循环。模型访问、工具实现、压缩策略、Workflow 和
Trace 都在边界或外围，Runtime 只负责按固定顺序把它们串起来。

一次 Turn 的顺序是：先检查调用者是否要求中止，再记录 `TurnStartEvent`。接着在
模型请求前执行可选的上下文压缩，并从 State 的活跃消息构建 `ContextView`。Runtime
记录 `ModelRequestEvent` 后调用 `agent.generate()`，再把结果分别记录为模型响应事件
和一条 `AssistantMessage`。如果模型请求了工具，Runtime 就执行这些 Tool Call，把
结果合并成一条 `kind="tool_result"` 的用户消息。最后记录 `TurnEndEvent`。当模型
返回 `kind="final"` 时正常停止，否则进入下一轮。

这里的数据流可以概括为：

```text
用户任务
  → MessageEvent(task)
  → State / StateSnapshot
  → Active Context
  → ContextView
  → ModelRequestEvent
  → agent.generate()
  → ModelResponseEvent
  → Assistant MessageEvent
  → 可选 ToolExecution Events
  → ToolResult MessageEvent
  → 下一轮或 AgentEndEvent
```

## 2. 为什么选择显式 Agent Loop？它与工作流编排框架有什么区别？

我选择显式 Agent Loop，是因为项目首先要把“模型决策、工具执行、状态更新和停止”
这条最小链路讲清楚。循环直接写在 `core.run()` 里，顺序、退出条件和事件写入点都能
通过代码检查，不需要理解隐藏调度器或图执行引擎。

它和 Workflow 的职责不同。Agent Loop 负责一个 Agent 的逐轮运行；Workflow 负责
组合多次普通 Agent 运行，例如串行、并行、Planner-Executor 或 Goal Loop。复杂流程
可以建立在同一 Runtime 上，但不会反过来把核心循环变成一个通用编排框架。

代价是当前 Runtime 不提供成熟框架中的图持久化、分布式调度和节点级恢复。我接受这个
限制，因为项目目标是教学、实验和小团队任务，不是先构建一个生产级工作流平台。

## 3. `Message` 和 `Event` 的核心区别是什么？`State` 承担什么职责？

我这里最核心的区别是：`Message` 表示对话中出现的内容，部分消息会进入模型上下文；
`Event` 记录 Runtime 里实际发生的事情；`State` 则保存完整事件历史，并维护由这些
事件推导出的当前消息状态。

比如用户任务、模型输出和工具结果属于 Message，因为它们构成了本次会话，部分内容还要
发送给下一轮模型。模型什么时候开始调用、工具什么时候开始执行、上下文什么时候压缩、
Agent 为什么停止，这些不属于对话内容，所以记录为 Event。每条 Message 写入 State
时也会生成对应的 `MessageEvent`，因此消息历史可以从事件中重建。

`State.events` 是运行历史的主事实来源；`StateSnapshot.messages` 和
`active_context_indices` 是为了快速读取而维护的投影。State 还会统一为 Event 分配
递增序号、相对运行时间和 UUID，避免各个模块各自维护顺序。

## 4. 三种对象为什么需要分离，合并成一个结构有什么问题？

我把它们分开，是因为三者的消费者和生命周期不同。Message 要满足模型上下文和多
Agent 路由；Event 要覆盖模型请求、工具执行、Hook、压缩和停止等非对话事实；State
要拥有整个运行，并提供当前投影。

如果合并成一个万能结构，会出现两个方向的问题：要么为了 Trace 把工具开始、停止原因
等控制事实塞进模型上下文，污染模型输入；要么为了保持消息简洁而丢失 Runtime 的执行
过程，无法解释一次运行为什么停止、工具是否超时、压缩何时发生。

另外，State 不能只是“最后一条万能消息”。它还需要保存追加顺序、完整历史和活跃上下文
索引。把这些职责塞进 Message，会让一条对话记录同时承担容器、事件和视图三个角色，
后续压缩和恢复都很难保持不变量。

## 5. Event 是否是 append-only？哪些 Event 允许修改或丢弃？

当前 Runtime 的标准写入路径是 append-only。Event 使用不可变 dataclass，进入
`State.record_event()` 后，State 会补上 index、elapsed 和 UUID，再追加到
`state.events`。Runtime 本身没有修改旧 Event 的操作。

压缩也不会删除旧的 `MessageEvent`。它会新增一条替代消息和
`ContextCompressionEvent`，再更新 Snapshot 中的 `active_context_indices`。因此旧
消息只是退出当前活跃上下文，仍然保留在完整历史里。

当前实现没有定义“某类 Event 可以就地修改或按策略丢弃”。Trace 导出可以做脱敏、
外置 raw 数据或生成派生视图，但那是存储和展示层的处理，不等于修改 State 中已经发生
的事实。需要说明的是，`state.events` 当前仍是公开的 Python list，所以 append-only
更多是 Runtime 契约，不是容器层面的强制防篡改；生产化版本还需要封装写入口或使用不可变
日志存储。

## 6. State 是可变对象、事件重放结果，还是持久化状态快照？

当前 `State` 是一个进程内可变容器，但它的主要变更方式是追加 Event。它内部的
`StateSnapshot` 是 Event 重放得到的派生缓存，不是独立事实来源。

具体来说，`record_event()` 会先把 Event 追加到 `state.events`，再调用
`snapshot.apply()` 更新消息列表或活跃索引。已有 Event 和 Message 都是不可变值，
可变的是 State 持有的事件列表和派生 Snapshot。

当前核心没有把 State 定义成数据库持久化快照，也没有完整的跨进程序列化协议。已有
`rebuild_snapshot()` 可以从 Event 重建内存投影，但“把 State 保存到外部存储再恢复”
仍需要调用者或更高层补齐。

## 7. 如果 State 可以由 Event 恢复，为什么还需要持久化 State？

从当前实现看，真正需要持久化的是足以恢复运行事实的数据，核心是 Event Stream。
Snapshot 本身可以重建，所以没有必要把它当作唯一事实来源重复保存。

但工程上仍可能保存 State 快照，主要是为了恢复速度和运行级元数据。长任务如果有几十万
条 Event，每次都从头重放成本会比较高；另外 `State.task` 和扩展用的 `State.data`
并不都由核心 Event 自动重建。

当前项目没有实现这套持久化方案，所以我不会说已经具备基于快照的故障恢复。如果继续
工程化，我会把 Event 日志作为事实源，周期性保存 Snapshot，同时记录数据版本和最后
一个 Event 的 index；恢复时先加载 Snapshot，再校验并重放后续增量事件。

## 8. Tool Call 属于 Message 还是 Event？是否需要两种表示？

在我的设计里，工具调用请求首先属于 Message。模型输出的
`AssistantMessage.content` 中会带有 `ToolCallBlock`，这样下一轮模型仍能看到自己
调用了什么工具、传了什么参数。

真正开始执行工具后，过程就属于 Event。Runtime 会记录工具开始、过程更新和结束事件；
执行完成后，再把模型需要看到的结果包装成 `ToolResultBlock`，放进一条用户消息。

所以这里确实需要两种表示，但不是重复记录。Message 回答“模型请求了什么、下一轮会
看到什么”，Event 回答“Runtime 实际怎么执行、是否报错、是否要求终止”。两边通过
稳定的 `tool_call_id` 关联起来。

## 9. LLM Response 属于 Message 还是 Event？流式增量、规范化响应和最终消息如何记录？

规范化后的模型输出最终会成为 `AssistantMessage`，因为文本、Thinking 和 Tool Call
都可能需要进入后续上下文。与此同时，Runtime 会记录 `ModelResponseEvent`，保存这次
调用的输出类型、目标、Tool Call 数量、Token 用量、实际模型和 Adapter 类型。

模型访问层还定义了 `StreamEvent`，包括 `text_delta`、`thinking_delta`、
`tool_call_start`、`tool_call_delta`、`tool_call_complete`、`usage_update` 和
`done`。最后一个 `done` 事件携带完整的 `LLMResponse`，再由转换层包装成 Runtime
使用的 `AssistantMessage`。

需要说明的是，当前 OpenAI Chat、OpenAI Responses 和 Anthropic Adapter 都先做
阻塞式 SDK 请求，再把完整响应映射成统一的 StreamEvent；并不是真正把网络返回的
Token 增量逐片转发。Fake Adapter 可以生成多段文本增量，用来验证流式消费协议。主
State 记录的是最终规范化响应和模型调用事件，不会逐条保存这些流事件。

## 10. 模型调用、工具执行、上下文管理和停止控制的边界在哪里？

模型调用的边界在 `llm/` 和 `llm_agent.py`。这一层先把 Runtime 中的消息转换成
统一的 `LLMRequest`，再由 Adapter 生成不同 Provider 的实际请求格式；响应回来后，
再转换成 `AssistantMessage`。核心循环不直接构造 OpenAI 或 Anthropic 的请求。

工具执行的边界在 `AgentTool.execute`。模型只看到工具名称、描述和 JSON Schema，
Runtime 才持有真正的执行函数、并行方式和超时配置。上下文管理由 `ContextPolicy`、
压缩策略和压缩执行层负责；策略只提出压缩建议，真正修改活跃上下文的动作仍由 Runtime
记录并应用。

停止控制最终归 `core.run()` 所有。模型只能通过 `kind="final"` 表达正常完成，工具
可以返回 `terminate=True`，调用者可以提供 abort，回合预算由 `max_turns` 限制；
Runtime 把这些信号统一映射为 `AgentEndEvent.reason`。

## 11. Agent Loop 有哪些退出条件？如何避免无限循环？

当前有四类退出原因：模型产生属于当前 Agent 的 `kind="final"` 消息时是 `done`；
任一工具返回 `terminate=True` 时是 `tool_terminate`；调用者的 abort flag 生效时是
`abort`；用完 `max_turns` 仍未完成时是 `max_turns`。

避免无限循环的硬边界是 `max_turns`，因为 `run()` 直接使用有限 range 驱动。工具和
调用者还可以提前中止。`max_turns=0` 时不会调用模型，会直接记录
`AgentEndEvent(reason="max_turns")`。

当前普通 Agent Loop 没有 Token 总预算或墙钟时间的内建参数；评测和 Goal Loop 等
更高层可以提供 abort 或额外预算控制。达到 `max_turns` 只表示运行被截断，不会被标记
为任务成功。

## 12. Agent 崩溃后如何 resume？

当前实现支持的是同一进程内、同一 `State` 上的会话续接。调用
`agent.resume(state, followup)` 后，Runtime 会追加一条新的任务消息，再继续运行
相同的核心循环。旧消息和旧 Event 都保留，新的 AgentStart、Turn 和结束事件继续
向后追加。

它不是完整的崩溃恢复。跨进程恢复需要调用者先持久化并反序列化 Event、Message、
`State.task` 和必要的扩展数据，再重建 Snapshot、Agent 配置、工具连接和外部资源。
当前仓库还没有提供通用的 State 序列化协议、Checkpoint 管理器或自动恢复协调器。

如果要实现可靠恢复，我会采用 Event 日志加周期 Snapshot：加载最近 Snapshot，校验
schema 和最后 Event index，再重放增量 Event；然后根据最后一个完整生命周期边界判断
从下一次模型调用、工具结果确认还是人工介入点继续。

## 13. 为避免重复模型调用或副作用工具，resume 前必须持久化什么？

至少要持久化稳定的运行 ID、完整 Event 顺序、每次模型请求的身份与规范化结果、每个
Tool Call 的 ID、参数、执行状态和结果，以及最后一次已提交的 State/Snapshot 位置。
对有副作用的工具还需要幂等键或外部操作 ID，不能只靠“最后一个 Event 看起来执行过”
来推断。

关键原则是先记录执行意图，再执行副作用，最后记录结果。恢复时，如果只看到工具开始
事件，却没有可信的结束事件和结果，就不能直接重跑；要先向外部系统查询操作是否已经
成功，或者把这次调用标记成状态不确定，交给人工确认。

这些是我会采用的生产化方案，不是当前项目已经完成的能力。当前 Runtime 虽然记录了
模型请求、模型响应和工具执行的开始、结束事件，但没有事务日志和幂等账本，也没有解决
“工具已经成功，但结果事件还没写入时进程崩溃”的 exactly-once 问题。

## 14. 你所说的“轻量级”如何衡量？

我这里的“轻量级”主要是结构指标，不是没有测量依据的性能宣传。核心 Agent Loop 集中
在 `core.py`，Agent 本身只是运行配置，State 和协议使用显式 dataclass；模型接入、
工具、上下文压缩、Workflow、Trace 和 Eval 都通过清楚的模块边界连接。

另一个衡量方式是可替换性：`generate` 只是
`Callable[[list[Message]], Message]`，所以不接真实模型也能用确定性函数测试完整循环；
工具是普通 `AgentTool` 值；Workflow 复用普通 Agent，而不是引入第二套 Runtime。

我不会把“轻量级”说成已经做过包体积、吞吐或内存基准。当前能证明的是代码路径短、
依赖方向受约束、核心行为有单元测试，并且最小 demo 不需要外部服务。

## 15. 如果重新设计一次，你会保留和删除哪些抽象？

我会保留 `Message`、`Event`、`State`、`ContextView` 和显式 Agent Loop。这几个抽象分别对应
模型可见语义、运行事实、事实所有权、当前视图和控制流，实际解决的是不同问题；Tool
Call/Result 用 ID 配对、完整历史与活跃上下文分离这两条不变量我也会保留。

我会继续收紧两个地方。第一，`State.data` 是有意留给评测和实验代码的开放扩展点，但长期
容易变成无类型旁路，我会把稳定用途逐步提升为明确协议。第二，Runtime Message 的
sidecar 虽然使用了受控的 TypedDict，仍要防止 Provider 原始响应、工具详情和压缩
元数据不断扩张成第二套消息协议。

我不会删除 Event 或把所有逻辑收回一个 Agent 类。真正可能删除的是没有跨模块消费者、
只为一次功能存在的抽象；目前核心这五个对象都已经被运行、压缩、Trace 或测试独立使用。

## 16. 压力追问：只用数据结构和伪代码实现最小 Agent Loop

我会先保留四个数据结构：`Message` 表示用户、助手和工具结果；`State` 保存追加消息和
事件；`Tool` 保存声明与执行函数；`Agent` 保存生成函数和工具表。最小循环不需要
Planner 或 Reflection。

```python
def run(agent, task, max_turns, abort):
    state = State(task=task)
    state.append_message(UserMessage(kind="task", content=task))
    state.append_event(AgentStart(agent=agent.name))

    for _ in range(max_turns):
        if abort():
            state.append_event(AgentEnd(reason="abort"))
            return state

        state.append_event(TurnStart(agent=agent.name))
        visible = build_context(state)
        state.append_event(ModelRequest(messages=visible))

        reply = agent.generate(visible)
        state.append_event(ModelResponse(reply=reply))
        state.append_message(reply)

        results = []
        for call in reply.tool_calls:
            state.append_event(ToolStart(call_id=call.id))
            result = execute(agent.tools[call.name], call.arguments)
            state.append_event(ToolEnd(call_id=call.id, error=result.is_error))
            results.append((call.id, result))

        if results:
            state.append_message(tool_result_message(results))

        state.append_event(TurnEnd(agent=agent.name))

        if reply.kind == "final":
            state.append_event(AgentEnd(reason="done"))
            return state

    state.append_event(AgentEnd(reason="max_turns"))
    return state
```

这个版本先保证三条不变量：状态只通过追加事实推进；每个 Tool Call 都有可配对结果；
只有 Runtime 统一决定退出。并行、压缩、Hook、Trace 导出和持久化都可以在这个顺序
稳定后再加。

## 核对依据

- [`core.py`](../../src/simple_long_horizon_agent/core.py)
- [`messages.py`](../../src/simple_long_horizon_agent/messages.py)
- [`protocols.py`](../../src/simple_long_horizon_agent/protocols.py)
- [`state.py`](../../src/simple_long_horizon_agent/state.py)
- [`04-agent-runtime.md`](04-agent-runtime.md)
- [`tests/unit/test_core.py`](../../tests/unit/test_core.py)
