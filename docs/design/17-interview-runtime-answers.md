# 17. Agent Runtime 面试问题与回答

本文保存第 14 篇问题地图中“4. P0：一次真实 Agent Run”部分的参考回答。
内容基于当前 `core.py`、`state.py`、LLM Adapter、运行示例和相关设计文档整理。

回答时应区分：

- 当前代码已经实现的 Runtime 事实；
- Provider Adapter 的协议转换和重试行为；
- Goal Loop 或评测层提供的外部完成检查；
- 尚未实现为完整跨进程恢复协议的生产化能力。

## 1. 从任务进入系统到最终停止，完整的数据流和控制流是什么？

**参考回答：**

> 一次运行可以分成初始化、回合循环、工具执行和结束四个阶段。
>
> 首先调用 `Agent.run(task)`。Runtime 会创建一个新的 `State`，并把原始任务写成一条
> `kind="task"` 的 `UserMessage`。这一步只完成状态初始化，还没有调用模型。
>
> 当调用方开始消费返回的 Event Stream 后，主循环才真正开始。每一轮的顺序大致是：
>
> ```text
> 检查 abort
> → TurnStartEvent
> → 请求前执行上下文压缩
> → 构建 ContextView
> → 记录 ModelRequestEvent
> → 调用 Agent.generate
> → 记录 ModelResponseEvent
> → 记录 AssistantMessage
> → 处理 ToolCall
> → 写入 ToolResult
> → TurnEndEvent
> → 判断是否继续或停止
> ```
>
> 如果模型返回工具调用，Runtime 不会在 `generate()` 内递归调用下一轮模型，而是先把
> Assistant 的工具调用完整记录下来，再执行工具。工具结果会按照 Tool Call ID 与原调用
> 配对，并被整理成一条 `tool_result` UserMessage，追加到 State。下一轮开始时，Runtime
> 会从 State 重新构建活跃上下文，因此上一轮的任务、Assistant 工具调用和工具结果就会
> 成为下一轮模型输入的一部分。
>
> 如果模型返回 `final`，或者达到最大回合数、外部中止、工具主动终止等条件，Runtime
> 会记录对应的 `AgentEndEvent`，整个 Run 结束。

以仓库中的 Bash Demo 为例：

```text
task Message
→ 第一轮模型返回 bash ToolCall
→ 执行 Bash
→ 写入 bash ToolResult
→ 第二轮模型读取工具结果
→ 返回 final Message
→ AgentEnd(reason="done")
```

## 2. 第一轮模型实际看到了哪些内容？

**参考回答：**

> 第一轮模型通常看到三类输入：Agent 的固定 system prompt、当前 State 中对该 Agent
> 可见的任务消息，以及本轮可用的工具定义。
>
> 默认初始化流程中，`Agent.run(task)` 会创建一条任务消息。第一轮请求前，Runtime 根据
> 当前 State 构建 `ContextView`，再将可见消息转换成供应商无关的 LLM Message，最后由
> Provider Adapter 转换成具体 API 请求。
>
> 因此第一轮的逻辑输入是：
>
> ```text
> 固定 system prompt
> + task Message
> + 工具定义
> ```
>
> 工具定义不一定作为普通对话 Message 存在，但会作为模型请求的一部分传给 Provider。
> 当前项目还会记录 `ModelRequestEvent`，其中包含：
>
> - 当前模型可见的消息数量；
> - 规范化的 LLM payload；
> - 工具定义；
> - Agent 名称和 Provider API；
> - ContextView 的统计信息。
>
> 因此不能简单地用最终的 `state.messages` 推断每一轮模型看到了什么。某一轮的准确
> 输入应以对应的 `ModelRequestEvent` 为准。

在 Bash 示例中：

```text
第一轮对话上下文：task Message
完整模型请求：system prompt + task Message + bash 工具定义
```

第二轮才会额外看到：

```text
Assistant ToolCall
+ ToolResult
```

## 3. 模型返回后先记录什么，再执行什么？

**参考回答：**

> 模型返回后，Runtime 先记录模型响应事实，再记录 Assistant Message，最后才根据其中
> 的 ToolCall 执行工具。
>
> 具体顺序是：
>
> ```text
> generate()
> → ModelResponseEvent
> → MessageEvent(AssistantMessage)
> → ToolExecutionStartEvent
> → PRE_TOOL_USE Hook
> → 执行工具
> → ToolExecutionUpdateEvent
> → ToolExecutionEndEvent
> → ToolResult MessageEvent
> ```
>
> 这样做有两个原因。
>
> 第一，模型产生的工具调用本身是一个独立事实。即使工具后来失败、被 Hook 阻止或
> 执行超时，也应该保留模型当时做出的决定。
>
> 第二，工具执行和模型响应是两个不同生命周期。把 Assistant Message 先记录下来，
> 可以清楚区分：
>
> ```text
> 模型决定做什么
> → Runtime 是否允许执行
> → 工具实际发生了什么
> ```
>
> 工具结果则在工具执行完成后追加到 State，并作为下一轮 ContextView 的输入。

当前 `core.run()` 的关键顺序是：

```python
output = agent.generate(visible)

yield state.record_event(ModelResponseEvent(...))
yield state.record(output)

if output_tool_calls:
    yield from dispatch_tool_calls(...)
```

## 4. 工具调用如何进入 State，并在下一轮成为模型输入？

**参考回答：**

> 工具调用通过两个 ID 建立因果关系：
>
> ```text
> ToolCallBlock.id
> ToolResultBlock.tool_call_id
> ```
>
> 假设模型返回：
>
> ```python
> ToolCallBlock(
>     id="bash_1",
>     name="bash",
>     arguments={...},
> )
> ```
>
> Runtime 会先把这条 Assistant Message 写入 State，然后执行对应工具。工具结束后产生：
>
> ```python
> ToolResultBlock(
>     tool_call_id="bash_1",
>     tool_name="bash",
>     ...
> )
> ```
>
> 多个工具调用时，Runtime 可以并行执行，但最终会按照原始 Tool Call 顺序组装结果，
> 避免并发完成顺序改变模型看到的语义。
>
> 结果会被包装成一条 `kind="tool_result"` 的 UserMessage，并通过 `State.record()` 写入
> State。下一轮开始时：
>
> ```text
> State
> → active_context_messages()
> → build_context_view()
> → messages_to_llm_messages()
> → Provider Request
> ```
>
> 因此下一轮模型能看到：
>
> ```text
> 原始 task
> + 上一轮 Assistant ToolCall
> + 对应的 ToolResult
> ```
>
> 工具调用不是直接作为 Python 返回值传给下一次模型调用，而是先进入运行事实，再从
> State 投影成下一轮上下文。这使工具行动可以被观察、压缩、审计和恢复。

## 5. 模型输出 `final`、工具调用、空响应或非法响应时分别发生什么？

### 5.1 输出 `final`

**参考回答：**

> 在当前 LLM Agent 适配层中，如果 Provider 返回 `stop_reason="end_turn"`，通常会被
> 转换成 `kind="final"` 的 AssistantMessage。
>
> Runtime 记录这个 Message 后，如果它属于当前 Agent，就把本轮标记为正常完成：
>
> ```text
> TurnEndEvent
> → AgentEndEvent(reason="done")
> ```
>
> 但这里的 `done` 只表示当前 Agent 认为自己完成了，不一定表示外部目标已经客观完成。
> 对于代码任务或评测任务，还可以由 Goal Loop、测试或评分器继续验证。

### 5.2 输出工具调用

**参考回答：**

> 如果 AssistantMessage 中包含 `ToolCallBlock`，Runtime 会先记录模型响应，再执行工具。
> 工具结果写回 State 后进入下一轮。工具调用本身不会直接结束当前 Run，除非工具结果
> 带有 `terminate=True`，明确要求 Runtime 停止。

### 5.3 空响应

**参考回答：**

> 空响应不能直接被解释成成功。它首先要在 Provider Adapter 和消息协议边界被标准化。
> 如果 Adapter 能够把它转换成合法的 AssistantMessage，Runtime 会按照该消息的 `kind`
> 和内容继续处理；如果无法形成合法的项目消息，则应产生明确的异常或失败结果，而不
> 是伪装成 `final`。
>
> 当前核心 Runtime 的职责是消费合法的项目 Message，不在主循环中猜测每种 Provider 空
> 响应的具体含义。不同 Provider 的响应差异应由 Adapter 处理。

### 5.4 非法响应

**参考回答：**

> 非法响应应在模型访问边界尽早暴露。比如非法内容块、无法解析的工具参数或不符合项目
> 协议的响应，不能直接写入 State。
>
> 当前项目对模型自己产生的结构性非法工具调用提供了有限的重问修复路径；Provider
> 层的暂时性限流或服务错误也可以在进入 Runtime State 前按策略重试。无法恢复的协议
> 错误则应保留明确失败信息或终止本次运行。
>
> 关键原则是：不能把解析失败的响应伪装成正常 Assistant Message，也不能让核心 Runtime
> 依赖某个 Provider 的原始错误格式。

这里不要把“空响应自动重试”说成当前 Runtime 的统一行为。更准确的说法是：

> 具体重试和响应标准化由 Provider Adapter / LLM 访问层负责，核心 Runtime 只处理规范化
> 后的项目对象。

## 6. 一次 Turn 和一次完整 Run 的边界是什么？

**参考回答：**

> 一次 Turn 是一次模型决策及其直接引发的工具处理；一次 Run 则是从 AgentStart 到
> AgentEnd 的完整运行。
>
> 一次 Turn 通常是：
>
> ```text
> TurnStartEvent
> → 压缩和上下文构建
> → ModelRequestEvent
> → ModelResponseEvent
> → Assistant Message
> → 可选工具执行
> → ToolResult Message
> → TurnEndEvent
> ```
>
> 一次 Run 可以包含多个 Turn：
>
> ```text
> AgentStart
> → Turn 1
> → Turn 2
> → Turn 3
> → AgentEnd
> ```
>
> 在 Bash 示例中：
>
> ```text
> Turn 1：模型请求执行 Bash
> Turn 2：模型读取 Bash 结果并返回 final
> ```
>
> `Model Call` 是 Turn 的一部分，但不等于整个 Turn，因为 Turn 还包括请求前压缩、
> Context 构建、Assistant Message 记录、工具调度和停止判断。
>
> `AgentStart` / `AgentEnd` 描述整个 Run 的生命周期，`TurnStart` / `TurnEnd` 描述
> 单轮控制流程，二者不能混用。

## 7. `Agent.run()` 为什么返回惰性 Event Stream？什么时候真正开始调用模型？

**参考回答：**

> `Agent.run()` 返回：
>
> ```python
> state, events = agent.run(task)
> ```
>
> 调用 `run()` 时，Runtime 只完成初始化：
>
> 1. 创建 State；
> 2. 写入 task Message；
> 3. 创建主循环生成器；
> 4. 返回 State 和 Event Iterator。
>
> 真正的模型调用、工具执行和后续事件记录，要等调用方开始消费 `events` 时才发生：
>
> ```python
> for event in events:
>     ...
> ```
>
> 如果调用方不消费迭代器：

> - 不会调用模型；
> - 不会执行工具；
> - 不会产生 AgentStart、TurnStart、ModelRequest 等后续事件；
> - State 只保留初始化阶段的任务事实。
>
> 这样设计把运行推进权交给调用方。调用方可以实时展示事件、增量写入 Trace、检查
> abort、进行外部协调，并在资源 Session 生命周期内消费完整运行。
>
> 代价是调用方必须正确消费 Event Stream，不能把“已经拿到 Iterator”误认为 Agent 已经
> 运行。

当前示例中，调用 `agent.run(task)` 后：

```text
state.events  == 1
state.messages == 1
```

消费完成后，才会出现完整的模型、工具和结束事件。

## 8. `resume()` 和重新启动一个 Agent 有什么区别？

**参考回答：**

> `run()` 和 `resume()` 的核心区别是是否创建新的 State。
>
> `run(task)` 表示创建一次新的运行：
>
> ```text
> 新建 State
> → 写入初始 task Message
> → 从空的运行历史开始
> ```
>
> `resume(state, followup)` 则是在已有 State 上追加一条新的 follow-up task Message，再
> 继续运行同一个 State：
>
> ```text
> 原有 State
> + follow-up task Message
> → 继续产生新的 Event
> → 继续使用已有消息和上下文
> ```
>
> 因此 `resume()` 会保留之前的 Message、Event、上下文压缩事实、同一条运行轨迹和原始
> 的 `state.task`。
>
> `resume()` 适合在同一个会话中继续任务，或者在保持上下文的情况下更换模型配置后继续
> 运行。但它不是完整的跨进程恢复协议。如果进程已经退出，还需要调用方序列化并重建
> State、Agent 配置、工具注册和外部资源。
>
> 恢复时通常要保持 Agent name 一致，因为上下文可见性、消息路由和子 Agent 语义依赖
> Agent 身份。

代码示例：

```python
state, events = agent.run("读取 README.md")
for _ in events:
    pass

state, events = agent.resume(state, "继续检查 StateSnapshot")
for _ in events:
    pass
```

这两次运行共享同一个 `state.events`，但不是重新初始化一个新会话。

## 9. Agent 为什么停止？谁判断任务真的完成？

**参考回答：**

> 当前 Runtime 有四类主要停止原因：
>
> | 原因 | 触发条件 | 是否代表任务成功 |
> | --- | --- | --- |
> | `done` | 当前 Agent 输出 `kind="final"` | 只代表 Agent 声称完成 |
> | `max_turns` | 回合预算耗尽 | 不代表成功 |
> | `tool_terminate` | 工具明确要求终止 | 不等于模型完成 |
> | `abort` | 外部 abort flag 触发 | 不代表成功 |
>
> Runtime 负责判断运行控制层面的停止条件：
>
> - 当前 Agent 是否返回了 `final`；
> - 是否超过最大回合数；
> - 工具是否返回 `terminate=True`；
> - 外部调用者是否触发 abort。
>
> 但 Runtime 不应该仅凭模型的 `final` 判断外部目标一定完成。对于需要客观验证的任务，
> 完成判断由外部 Goal Loop、测试程序、工作区检查器或 Benchmark Scorer 负责。
>
> 例如软件修复任务中，模型可能返回：
>
> ```text
> final = "问题已经修复"
> ```
>
> 但最终是否成功，要看测试是否通过、目标文件是否正确修改、Patch 是否符合任务要求，
> 以及官方评分器是否接受结果。
>
> Goal Loop 的逻辑是：
>
> ```text
> Agent 返回候选结果
> → 外部 Goal Check
> → 通过：Complete
> → 不通过：把反馈追加回同一个 State 并 resume
> → 达到预算或阻塞阈值：停止
> ```
>
> 所以需要区分：
>
> ```text
> AgentEnd(reason="done")
> ≠
> 外部目标检查通过
> ```

## 10. 一句话总结整条链路

```text
任务 → State/task Message
→ 消费 Event Stream
→ 构建 ContextView
→ ModelRequest
→ ModelResponse
→ Assistant Message
→ ToolCall
→ ToolResult 写回 State
→ 下一轮模型输入
→ final / abort / max_turns / tool_terminate
→ AgentEnd
→ 可选的外部 Goal Check 验证真正完成
```

## 11. 参考材料

- [用一个真实运行看懂 Agent](00-running-example.md)
- [Agent 核心运行机制](04-agent-runtime.md)
- [核心概念与数据流](03-domain-model-and-data-flow.md)
- [模型访问边界](05-model-access.md)
- [Agent 组合与工作流](08-agent-composition-and-workflows.md)
- [轨迹与可观测性](09-trace-and-observability.md)
- [评测与运行体系](10-evaluation-and-operations.md)
- [`core.py`](../../src/simple_long_horizon_agent/core.py)
- [`state.py`](../../src/simple_long_horizon_agent/state.py)
- [`llm_agent.py`](../../src/simple_long_horizon_agent/llm_agent.py)
