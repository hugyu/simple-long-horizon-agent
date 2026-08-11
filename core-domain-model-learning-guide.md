# Simple Long Horizon Agent 核心数据模型学习指南

> 本文回答一个核心问题：一次 Agent 运行中，系统认为哪些信息是事实，这些事实如何流转、保存，又如何被投影成模型本轮真正看到的请求。
>
> 文中使用一条 Bash 工具调用作为贯穿示例。图片、并行工具、上下文压缩和 Provider 差异只在需要时做局部补充，不再反复创建新故事。

---

## 1. 先用一次真实运行建立全局认识

用户要求 Agent 执行：

```text
Use bash to run command: `printf 'hello-from-tool\n'`
```

这次运行的最小闭环是：

```text
用户任务
  → 第一次模型请求
  → 模型产生 ToolCallBlock(id="bash_1")
  → Runtime 执行 Bash
  → Runtime 产生 ToolResultBlock(tool_call_id="bash_1")
  → 第二次模型请求
  → 模型产生 final AssistantMessage
```

运行完成后，State 中有四条 Message：

| 消息索引 | 类型 | kind | sender → target | 关键内容 |
| ---: | --- | --- | --- | --- |
| 0 | UserMessage | task | user → bash_agent | 原始任务 |
| 1 | AssistantMessage | step | bash_agent → user | 说明文本 + `ToolCallBlock(id="bash_1")` |
| 2 | UserMessage | tool_result | tool → bash_agent | `ToolResultBlock(tool_call_id="bash_1")` |
| 3 | AssistantMessage | final | bash_agent → user | 最终答案 |

同一次运行的 Event Stream 则更完整：

```text
0  MessageEvent(task)
1  AgentStartEvent
2  TurnStartEvent
3  ModelRequestEvent
4  ModelResponseEvent(step)
5  MessageEvent(assistant tool call)
6  ToolExecutionStartEvent
7  ToolExecutionEndEvent
8  MessageEvent(tool result)
9  TurnEndEvent
10 TurnStartEvent
11 ModelRequestEvent
12 ModelResponseEvent(final)
13 MessageEvent(final)
14 TurnEndEvent
15 AgentEndEvent(reason="done")
```

这个例子先暴露了整个数据链：

```text
Content Block
  → Message
  → MessageEvent
  → State.events
  → StateSnapshot
  → 活跃上下文
  → ContextView
  → LLMMessage / LLMRequest
  → Provider Wire
  → LLMResponse
  → AssistantMessage
  → 新事件
```

后文会逐层拆解这条链路。

---

## 2. Content Block：一条消息内部有什么

### 2.1 Message 是信封，Content Block 是有序内容

一条消息不一定只有文本。模型可能同时产生思考、说明文本和多个工具调用；工具也可能同时返回文本与截图。

项目因此不把内容设计成一组互不相关的可选字段：

```text
text = ...
thinking = ...
tool_calls = ...
image = ...
```

而是使用一个有序序列：

```python
content = (
    ThinkingBlock(...),
    TextBlock(...),
    ToolCallBlock(...),
)
```

这个顺序是事实的一部分。如果 Provider 先产生思考，再说明即将做什么，最后发出工具调用，系统应当保留这个顺序，而不是把它们拆成几个无序列表。

### 2.2 五种内容块

| Content Block | 表达的事实 | 典型创建者 | 典型读取者 |
| --- | --- | --- | --- |
| TextBlock | 普通文本 | 用户、Runtime、模型、工具 | 模型、终端、Trace |
| ImageBlock | 已规范化的图片数据和 MIME 类型 | 输入解析或工具 | Adapter、Trace |
| ThinkingBlock | 模型思考内容及连续性元数据 | Provider Adapter | 后续模型请求、Trace |
| ToolCallBlock | 模型请求调用某工具 | Provider Adapter | Runtime 工具调度 |
| ToolResultBlock | 某次工具调用的结果 | Runtime 工具调度 | 下一轮模型、Trace |

Bash 示例中，第一次模型响应可以抽象为：

```python
AssistantMessage(
    kind="step",
    sender="bash_agent",
    target="user",
    content=(
        TextBlock("I'll run the requested command with bash."),
        ToolCallBlock(
            id="bash_1",
            name="bash",
            arguments={"command": "printf 'hello-from-tool\\n'"},
        ),
    ),
)
```

补充一个多模态局部例子：如果用户请求分析截图，一条 UserMessage 可以同时包含：

```python
content = (
    TextBlock("Please explain this error."),
    ImageBlock(data="...", mime_type="image/png"),
)
```

文本和图片属于同一次用户输入，因此应在同一条消息的有序 content 中。

### 2.3 共享内容模型不等于可以任意组合

| Message 类型 | 允许的关键内容 | 禁止的关键内容 |
| --- | --- | --- |
| UserMessage | 文本、图片；工具结果消息可包含 ToolResultBlock | ThinkingBlock、ToolCallBlock |
| RuntimeMessage | 文本、图片等运行时说明 | ThinkingBlock、ToolCallBlock、ToolResultBlock |
| AssistantMessage | 文本、图片、ThinkingBlock、ToolCallBlock | ToolResultBlock |

这些限制表达了不同参与者的能力边界：

- UserMessage 表达用户输入或环境反馈，不能伪装成模型工具调用；
- RuntimeMessage 表达运行时注入的说明，不能冒充模型行动或工具观察；
- AssistantMessage 表达模型输出和行动意图，不能伪造环境已经返回的结果。

ToolResultBlock 还有一层嵌套内容，其模型可见内容只允许文本或图片：

```python
ToolResultBlock(
    tool_call_id="shot_1",
    tool_name="screenshot",
    is_error=False,
    content=(
        TextBlock("Captured login page."),
        ImageBlock(data="...", mime_type="image/png"),
    ),
)
```

工具内部日志、子 Agent 事件和其他诊断信息不应伪装成模型可见内容。它们应进入 Event 或受控 sidecar。

### 2.4 为什么 Runtime 和 LLM 层共用同一组 Block

Runtime Message 和 LLMMessage 都使用同一种 Content Block 序列。这不是说两种 Message 完全相同，而是说“文本、图片、思考、工具调用和工具结果”的核心语义只定义一次。

```text
Runtime Message
│  role/content + sender/target/kind/sidecar
│
└─ Bridge 去掉运行路由字段
   ↓
LLMMessage
   role/content + provider extra
```

这样，Adapter 只需把同一个 ToolCallBlock 翻译成 OpenAI 的 `tool_calls` 或 Anthropic 的 `tool_use`，不需要让 Runtime 维护另一套 Provider 内容类型。完整边界转换在第 8 章统一说明。

---

## 3. Message：内容之外的运行语义

### 3.1 三种 Message

| Message 类型 | 谁产生 | 表达什么 | 模型侧 role |
| --- | --- | --- | --- |
| UserMessage | 用户，或代表环境反馈的工具调度层 | 任务、补充输入、工具结果 | user |
| RuntimeMessage | Runtime、Harness、Hook 或支撑能力 | 动态说明、摘要、上下文、运行指导 | system |
| AssistantMessage | 模型或 Agent | 文本输出、思考和工具调用 | assistant |

这里的 Runtime Message 容易与 Agent 的固定 system prompt 混淆：

- 固定 system prompt 属于 Agent 配置，每次模型请求都使用，但不作为普通对话消息进入 State；
- RuntimeMessage 是运行过程中动态产生的内容，会进入历史和 Trace。

大多数 Provider 没有 `runtime` role，所以 RuntimeMessage 在模型边界投影为 `system`。类型名称回答“谁产生它”，role 回答“模型如何接收它”。

### 3.2 role、kind、sender、target 是四个维度

用 Bash 调用消息举例：

```python
AssistantMessage(
    role="assistant",
    kind="step",
    sender="bash_agent",
    target="user",
    content=(
        TextBlock("I'll run it."),
        ToolCallBlock(id="bash_1", name="bash", arguments={...}),
    ),
)
```

| 字段 | 回答的问题 | 上例的含义 |
| --- | --- | --- |
| role | 模型把它当作什么角色 | assistant 输出 |
| kind | Runtime 把它当作什么运行阶段 | 非终结回合，应继续执行工具 |
| sender | 谁产生它 | bash_agent |
| target | 语义上发给谁 | user |

工具结果则是：

```text
role=user
kind=tool_result
sender=tool
target=bash_agent
```

它之所以是 UserMessage，不是因为人类用户输入了这段内容，而是因为它作为环境反馈进入下一轮模型上下文。

role 不能由 kind 替代。例如 `kind="summary"` 通常是 RuntimeMessage，但某些自定义压缩策略也可能选择其他续接角色。模型侧角色由 Message 类型确定，运行语义由 kind 确定。

### 3.3 MessageKind 表达任务阶段

| kind | 含义 | 典型后续行为 |
| --- | --- | --- |
| task | 初始任务或续接任务 | 保留为任务锚点 |
| message | 普通补充或输出 | 按调用者逻辑继续 |
| system | 动态运行说明 | 作为运行时指导进入上下文 |
| step | 非终结 Assistant 回合 | 通常执行工具并进入下一轮 |
| thought | 用于展示的探索或推理节点 | 由组合流程继续处理 |
| final | Agent 终结输出 | Runtime 正常停止 |
| tool_result | 工具执行反馈 | 进入下一次模型上下文 |
| summary | 被压缩历史的替代摘要 | 留在活跃上下文 |
| context | 委派或支撑能力注入的上下文 | 提供给目标 Agent |

kind 是受控集合。新增稳定运行语义时，应扩展统一协议，而不是让各模块自由发明字符串。

### 3.4 target 是路由语义，不是访问控制

当前 ContextView 使用一次运行的共享 transcript，主要根据 MessageKind 可见性过滤。`sender` 和 `target` 有助于表达路由、Trace 和组合流程，但不会自动隐藏共享 State 中的其他消息。

因此：

```text
target="writer"
≠ 只有 writer 才能读取
```

如果子 Agent 需要真正独立的上下文，应该使用独立 State，而不是依赖 target 在共享历史中实现隔离。这一点在第 7 章的 ContextView 中不再重复展开。

### 3.5 Sidecar 的边界

Sidecar 是少量非正文附加信息的逃生口，典型用途包括：

- `extra`：只供某个 Provider Adapter 读取的 namespaced hint；
- `raw`：调试所需的原始请求/响应快照；
- `details`：工具结果的非模型可见详情，例如子 Agent 事件；
- `compression`：压缩内部模型调用的元数据。

正常的模型可见内容应放在 content，稳定的运行活动应记录为 Event。Sidecar 不能变成第二套消息协议：如果一项数据被多个模块稳定读取、影响核心控制流，或需要统一校验，它应提升为明确字段、Content Block 或 Event。

Provider `extra` 和 `raw` 的完整流转集中在第 8 章说明。

---

## 4. Tool Call 与 Tool Result：用身份建立行动因果

### 4.1 工具交互是有编号的工单，不是相邻文本

Bash 示例中：

```text
ToolCallBlock.id             = "bash_1"
ToolResultBlock.tool_call_id = "bash_1"
```

这个 ID 建立了：

```text
模型请求执行 Bash
        ↕ bash_1
Runtime 返回这次 Bash 的观察
```

如果只依赖文本相邻或工具名称，当一轮同时调用两次 `read` 时，系统就无法稳定确定每个结果属于哪个路径。

### 4.2 并行调用的完整例子

假设模型同时请求：

```python
AssistantMessage(
    kind="step",
    content=(
        ToolCallBlock(
            id="c1",
            name="read",
            arguments={"path": "README.md"},
        ),
        ToolCallBlock(
            id="c2",
            name="search",
            arguments={"query": "StateSnapshot"},
        ),
    ),
)
```

实际执行时，`c2` 可能先失败，`c1` 后成功：

```text
0 ms    c1 开始
1 ms    c2 开始
51 ms   c2 失败
300 ms  c1 成功
```

ToolExecution Event 会保留真实执行时序。但是给下一轮模型的结果包会按原始 ToolCall 顺序组装：

```python
UserMessage(
    kind="tool_result",
    sender="tool",
    target="writer",
    content=(
        ToolResultBlock(
            tool_call_id="c1",
            tool_name="read",
            is_error=False,
            content=(TextBlock("README contents..."),),
        ),
        ToolResultBlock(
            tool_call_id="c2",
            tool_name="search",
            is_error=True,
            content=(TextBlock("search index unavailable"),),
        ),
    ),
)
```

这里同时保留了两种顺序：

- Event Stream 保留工具真实开始和完成的时间顺序；
- ToolResultBlock 序列保留模型原始调用顺序，避免并发速度改变模型看到的对应关系。

### 4.3 配对契约

- ToolCallBlock 必须有非空 ID 和工具名；
- ToolResultBlock 通过 `tool_call_id` 指回原调用，并保留工具名；
- 每个结果独立携带 `is_error`，一组并行调用可以部分成功、部分失败；
- 同一 Assistant 回合产生的多个工具结果被合并为一个 UserMessage；
- 结果在所有工具完成后按原 ToolCall 顺序组装；
- 压缩不能只保留调用或只保留结果，形成悬空工具对。

设计契约要求调用 ID 在一次交互中可识别。当前基础校验明确拒绝空 ID 和空工具名，但没有单独将“同一消息内的重复 ID”实现为通用验证错误。因此文档不应把“重复 ID 已被强制拒绝”写成当前代码事实。

### 4.4 为什么工具错误通常不让 Runtime 崩溃

参数错误、未知工具、超时或工具异常通常会被转换为：

```python
ToolResultBlock(
    tool_call_id="c2",
    tool_name="search",
    is_error=True,
    content=(TextBlock("...error..."),),
)
```

这个错误是环境观察。模型仍可以读取错误、修正参数或选择其他工具。只有明确的终止信号、外部 abort 或回合预算等控制条件，才会改变 Runtime 停止原因。

### 4.5 为什么 Runtime 使用“一个结果包”

一个 UserMessage 中包含多个 ToolResultBlock，自然表达“同一轮并行行动的一组反馈”。这是 Runtime 的规范形状，不要被 Provider Wire 形状反向决定：

```text
Runtime / LLMMessage:
  1 个 user 消息 + N 个 ToolResultBlock

Anthropic Wire:
  1 个 user 条目 + N 个 tool_result block

OpenAI Chat Wire:
  N 个 role="tool" 条目
```

Provider Wire 条目数可以不同，但 `tool_call_id` 和结果顺序不能丢失。

---

## 5. TokenUsage：一次模型调用的资源小票

### 5.1 Usage 属于整次调用

假设第二次模型请求携带：

```text
固定 system prompt
+ task Message
+ Assistant 工具调用
+ tool_result Message
```

Provider 返回：

```python
TokenUsage(
    input_tokens=1000,
    output_tokens=120,
    cache_read_tokens=300,
    cache_write_tokens=0,
)
```

它的意思是整次请求使用了这些资源，不是：

```text
task 消息消耗 400
工具调用消耗 350
工具结果消耗 250
```

Provider 没有报告这种逐消息分配，项目不应伪造精确度。Usage 因此附着在这次调用产生的 AssistantMessage 上，与该输出的 `model` 标识一起成为调用级资源事实。

### 5.2 四个字段

| 字段 | 含义 |
| --- | --- |
| input_tokens | 本次新处理的、非缓存输入 |
| output_tokens | 本次 Assistant 输出 |
| cache_read_tokens | 从缓存读取的输入部分 |
| cache_write_tokens | 写入缓存的输入部分 |

项目定义两个服务于不同目的的总量：

```text
total_tokens
  = input_tokens + output_tokens
  = 1000 + 120
  = 1120

context_tokens
  = input_tokens + output_tokens
  + cache_read_tokens + cache_write_tokens
  = 1000 + 120 + 300 + 0
  = 1420
```

- `total_tokens` 把缓存单独保留，避免将缓存命中误当成新输入，更适合成本分析；
- `context_tokens` 表示这次调用占用的完整上下文窗口，因此包含缓存部分和本轮输出。

### 5.3 Adapter 为什么必须规范化 Provider Usage

某些 Provider 报告：

```text
prompt_tokens = 1300
cached_tokens = 300
```

其中 `prompt_tokens` 已经包含 300 个缓存 Token。如果直接保存成：

```text
input_tokens = 1300
cache_read_tokens = 300
```

再计算 `context_tokens` 就会重复统计缓存部分。Adapter 应在边界处规范化为：

```text
input_tokens = 1000
cache_read_tokens = 300
```

因此核心 Runtime 只读项目统一的 TokenUsage，不直接解释各 Provider usage 字段。

### 5.4 全零 Usage 为什么进入 Message 时变成 None

很多 Adapter 在 Provider 没有报告可信 usage 时，会先构造默认全零 TokenUsage。Bridge 把它包装成 AssistantMessage 时会解释为：

```text
usage=None
```

这表示“未知”，而不是声称一次真实模型调用确实消耗了零 Token。

### 5.5 Usage 如何帮助估算上下文

最近一条具有可信 usage 的 AssistantMessage，记录了它产生时整个请求的上下文总量。系统可将它作为历史前缀基线，只估算其后新增消息，不必对所有旧消息重新猜测 Token。

但如果压缩已经将旧内容移出活跃视图，压缩前 AssistantMessage 的 usage 仍然包含那些已退出的内容。它就不能继续作为新视图的基线。系统需要退回逐消息估算，直到压缩后又产生一条新的可信 usage AssistantMessage。

压缩前后的具体消息索引只在第 7 章完整演示，这里不重复。

---

## 6. Event 与 State：运行事实如何追加和投影

### 6.1 Message 是聊天记录，Event 是运行黑匣

Message 回答：

> 对话中正式出现了什么？

Event 回答：

> 运行过程中实际发生了什么？

因此，模型请求、工具开始、Hook 决策和 Agent 停止原因都应记录，但不应伪装成对话 Message 让模型看到。

| 类别 | Event | 记录的事实 |
| --- | --- | --- |
| 对话 | MessageEvent | 一条 Message 被正式写入运行历史 |
| Agent 生命周期 | AgentStart、AgentEnd | Agent 启动、固定 prompt、停止原因 |
| 回合生命周期 | TurnStart、TurnEnd | 一轮控制流程的边界及是否被工具终止 |
| 模型访问 | ModelRequest、ModelResponse | 可见消息数、请求投影、工具定义、输出类型、usage、模型和 Adapter |
| 上下文 | ContextCompression | 哪些消息退出活跃视图、替代消息位置和压缩策略 |
| 工具执行 | ToolExecutionStart、Update、End | 调用身份、增量更新、错误和终止信号 |
| Hook | HookFired | 扩展点是否触发、阻止或追加消息 |
| Goal | GoalStatus | 外层目标循环的状态、预算和原因 |

### 6.2 MessageEvent 把对话事实放进 Event Stream

每条进入 State 的 Message 都由 MessageEvent 承载：

```text
Message
  → MessageEvent(message=Message)
  → State.events
  → StateSnapshot.messages
```

因此消息顺序可以从 Event Stream 重建。但反过来不成立：只拿到 Message 列表，无法恢复模型请求时刻、工具执行耗时或 Agent 停止原因。

不是每个 Event 都产生 Message：

- ModelRequestEvent 记录模型将被调用，不向模型注入“请求事件”；
- ToolExecutionUpdateEvent 用于实时观察，不自动成为下一轮上下文；
- AgentEndEvent 记录停止原因，不伪装成 Assistant 最终答案；
- GoalStatusEvent 属于外层编排审计，不进入普通 Agent transcript。

### 6.3 State 是追加式事实容器

State 不是一个任意覆盖字段的全局变量：

| 部分 | 作用 | 事实地位 |
| --- | --- | --- |
| task | 本次运行的原始任务输入 | 运行级原始输入 |
| events | 按发生顺序追加的完整 Event Stream | 运行历史的主事实来源 |
| snapshot | 从 Event 派生的当前消息与活跃索引 | 可重建缓存，不是第二事实来源 |
| data | Harness 或实验的运行级元数据 | 扩展调用者 scratchpad，不是核心消息总线 |

默认 Agent 初始化的概念过程是：

```python
state = State(task=task)
state.send("task", "user", agent.name, task)
```

只执行：

```python
State(task="...")
```

不会自动生成任务 Message。默认 `Agent.run()` 的初始化器会显式记录 `kind="task"` 的 UserMessage；自定义初始化器必须自己建立所需 transcript。

### 6.4 State 如何统一为事件盖章

调用者创建 Event 时只提供领域字段。`State.record_event()` 负责：

```text
收到领域 Event
  → 写入 index
  → 写入 elapsed
  → 写入 UUID
  → 追加到 events
  → snapshot.apply(event)
```

三个时序字段解决不同问题：

| 字段 | 意义 |
| --- | --- |
| index | 本次运行内的追加顺序 |
| elapsed | 相对本次运行起点的单调时间 |
| UUID | 独立于当前位置、可跨文件引用的稳定身份 |

事件记录后不原地修改。某些嵌套操作只能在完成后把内部事件附加到父 State；这些事件可以保留它们真实的运行相对时间，但仍然按附加到父 State 的顺序获得新 index。

### 6.5 StateSnapshot 只是可重建的当前目录

Snapshot 缓存两项投影：

```text
messages
  = 所有 MessageEvent 中的 Message

active_context_indices
  = 当前仍属于活跃上下文的消息索引
```

Snapshot 只对两类 Event 改变这两项投影：

1. MessageEvent 把新 Message 追加到 messages；如果已有显式活跃索引，还把新消息索引追加进去；
2. ContextCompressionEvent 用新的索引列表重指向活跃上下文。

AgentStart、ModelRequest 和 ToolExecutionEnd 等事件仍在 events 中，但不改变消息投影。如果 Snapshot 丢失，可以从空 Snapshot 开始，顺序应用全部 Event 重建：

```text
State.events
  → 顺序 apply
  → StateSnapshot
```

反向则不成立：Snapshot 无法恢复所有非消息 Event。所以 events 是事实账本，Snapshot 只是快速目录。

### 6.6 data 是扩展 scratchpad，不是消息通道

合适的内容：

```python
state.data["benchmark_case_id"] = "case-17"
state.data["workspace"] = "C:/tmp/project"
state.data["model_patch"] = "..."
```

不合适的内容：

```python
state.data["tool_result"] = "README contents..."
state.data["final_answer"] = "..."
```

如果工具结果需要让下一轮模型看到，它必须进入 ToolResultBlock 和 MessageEvent。如果是 Agent 正式最终答案，它必须成为 `kind="final"` 的 AssistantMessage。核心 Runtime 不会自动读写 `data`。

---

## 7. 完整历史、活跃上下文与 ContextView

### 7.1 三层投影各自回答什么

```text
完整消息历史
  所有 MessageEvent 的投影
  回答：系统曾经记录过什么？
        ↓ 压缩重指向
活跃上下文
  完整消息列表的有序索引
  回答：压缩后当前保留什么？
        ↓ model_invisible_kinds
ContextView
  某个 Agent 本轮允许模型看到的不可变视图
  回答：本轮模型可以看到什么？
```

它们不是三份互相覆盖的历史，而是从完整事实逐层缩小的投影。

### 7.2 用一组索引完整演示压缩

假设完整消息历史为：

| 索引 | 内容 | kind |
| ---: | --- | --- |
| 0 | 原始任务 | task |
| 1 | Assistant 请求读取 README | step |
| 2 | 工具返回 README | tool_result |
| 3 | Assistant 初步分析 | message |
| 4 | Runtime 注入“面向初学者” | context |
| 5 | 用户补充“重点解释数据流” | message |

压缩器决定将消息 1、2、3 折叠成摘要。这里先不限定由哪一种策略生成，也不预设 replacement 的具体 Message 子类型；所有策略共同保证的是：原消息不被修改，而是追加一条 `kind="summary"` 的替代消息：

```text
Message 6
kind="summary"
sender="runtime"
content="Agent 已读取 README，并完成初步架构分析。"
```

然后记录 ContextCompressionEvent：

```text
compressed_message_indices = [1, 2, 3]
summary_message_index       = 6
active_context_indices      = [0, 6, 4, 5]
```

此时：

```text
完整历史：[0, 1, 2, 3, 4, 5, 6]
活跃上下文：[0, 6, 4, 5]
```

旧消息 1、2、3 仍可供 Trace、Recall 和审计读取，但不再参与当前模型上下文。

### 7.3 为什么活跃索引不一定递增

消息 6 在物理上最后追加，所以拥有高索引；但它在语义上替代消息 1、2、3，所以被插回消息 4 之前：

```text
物理追加顺序：0 → 1 → 2 → 3 → 4 → 5 → 6
当前语义顺序：0 → 6 → 4 → 5
```

读取者不能对 `active_context_indices` 自行排序。否则摘要会被错误移到最近消息之后，破坏对话语义。

未发生过压缩时：

```text
active_context_indices = None
```

它的意思不是“没有活跃消息”，而是“尚未压缩，所有 messages 默认活跃”。

### 7.4 ContextView 只做可见性投影

假设活跃上下文后来又追加了一条：

```text
Message 7
AssistantMessage(kind="thought", ...)
```

活跃索引变为：

```text
[0, 6, 4, 5, 7]
```

如果 Agent 的策略配置：

```python
ContextPolicy(model_invisible_kinds=("thought",))
```

ContextView 就过滤消息 7，本轮可见索引等价为：

```text
[0, 6, 4, 5]
```

三层对照如下：

| Message | 完整历史 | 活跃上下文 | ContextView |
| --- | ---: | ---: | ---: |
| 0 task | 是 | 是 | 是 |
| 1 tool call | 是 | 否 | 否 |
| 2 tool result | 是 | 否 | 否 |
| 3 初步分析 | 是 | 否 | 否 |
| 4 context | 是 | 是 | 是 |
| 5 用户补充 | 是 | 是 | 是 |
| 6 summary | 是 | 是 | 是 |
| 7 thought | 是 | 是 | 否 |

ContextView 是为某个 Agent、某一次模型请求构建的不可变快照。它还包含：

- `total_messages`：可见性过滤前的活跃消息数，不是完整历史消息数；
- `visible_messages`：过滤后的模型可见消息数；
- `estimated_chars` 和 `estimated_tokens`：可见内容的估算大小；
- `usage_known_messages`：可见 AssistantMessage 中拥有可信输出 usage 的数量。

上面的例子中：

```text
完整历史消息数       = 8
ContextView.total_messages = 5
ContextView.visible_messages = 4
```

因此，把 `total_messages` 称为“压缩前的全部消息数”不准确。正确语义是“压缩后、可见性过滤前的活跃消息数”。

### 7.5 压缩与隐藏不能混用

| 机制 | 回答的问题 | 对历史的影响 |
| --- | --- | --- |
| Compression | 内容太长，用什么替代旧内容 | 追加替代消息和压缩 Event，改变活跃索引，不删除原历史 |
| Visibility | 这种 kind 从语义上是否允许模型看到 | 只影响本轮 ContextView，不生成摘要，不改变活跃索引 |

如果希望 `task` 在压缩时保留原文，应该使用压缩策略的 `preserve_kinds`。如果把 `task` 放进 `model_invisible_kinds`，效果是模型根本看不到任务，而不是“保护任务不被压缩”。

当前压缩 Runtime 在把消息交给压缩策略前，也会排除 `model_invisible_kinds`。这是一个执行细节，不改变两者的语义边界：一个管上下文大小，一个管模型可见性。

### 7.6 压缩策略只做决定，公共 Runtime 负责安全执行

前面的索引例子只展示了“压缩之后发生什么”，还没有回答“摘要是谁写的”。项目把这两个问题分开：

```text
CompressionStrategy
  回答：压缩哪些稳定消息索引？用哪条 Message 替代？
        ↓ CompressionDecision
Compression Runtime
  负责：校验、追加 replacement、记录 Event、更新活跃索引
```

策略统一返回类似结构：

```python
CompressionDecision(
    compress_indices=(1, 2, 3),
    replacement=summary_message,
    label="...",
)
```

策略不直接修改 State。公共 Runtime 会统一完成：

1. 校验和对齐 ToolCall/ToolResult，避免形成悬空工具对；
2. 计算压缩前后的 Token 估算；
3. 用新的 MessageEvent 追加 replacement；
4. 记录 ContextCompressionEvent；
5. 将 replacement 插回被折叠内容的逻辑位置；
6. 更新 `active_context_indices`，但保留完整历史。

当前内置的三种摘要来源和一种组合方式如下：

| 策略 | 谁决定触发 | 谁写 replacement | 是否增加模型调用 | 主要用途 |
| --- | --- | --- | ---: | --- |
| ToolCompactStrategy | Runtime 根据 Token 阈值 | 固定规则 | 否 | 清理旧工具调用和大段工具输出 |
| SummarizeStrategy | Runtime 根据 Token 阈值 | 独立 compressor 配置 | 是 | 保存复杂目标、进度、事实和下一步 |
| AgentCompactStrategy | 主 Agent 调用 `compact` 工具 | 主 Agent 自己 | 不额外调用 | Agent 在阶段完成后主动收束上下文 |
| TieredStrategy | 依次询问内部阶段 | 由首个命中阶段决定 | 视命中阶段而定 | 组合廉价压缩和语义压缩 |

前三种最终都产生 CompressionDecision；TieredStrategy 自己不写摘要，只负责选择本次检查采用哪个阶段。

### 7.7 用同一组消息比较三种摘要策略

为了覆盖多个工具交换，把 7.2 的 README 任务继续运行到下面的活跃历史：

| 索引 | 内容 | kind |
| ---: | --- | --- |
| 0 | 原始任务：分析项目并解释数据流 | task |
| 1 | Assistant 调用 `read` | step |
| 2 | `read` 返回 README | tool_result |
| 3 | Assistant 分析 README | message |
| 4 | Assistant 调用 `search` | step |
| 5 | `search` 返回大量匹配 | tool_result |
| 6 | Assistant 调用 `bash` 运行检查 | step |
| 7 | `bash` 返回测试日志 | tool_result |
| 8 | Assistant 汇总当前发现 | message |
| 9 | 用户补充“重点解释数据流” | message |

假设：

```text
active context       = 5200 tokens
compression threshold = 4000 tokens
```

下面三种策略面对的是同一组事实，但会选择不同的折叠范围和摘要作者。

#### 7.7.1 ToolCompactStrategy：只折叠旧工具交换

配置如下：

```python
ToolCompactStrategy(
    threshold_tokens=4000,
    keep_recent_exchanges=1,
    preview_chars=200,
)
```

它只识别完整工具交换：

```text
Exchange A = [1, 2]  read
Exchange B = [4, 5]  search
Exchange C = [6, 7]  bash
```

因为需要保留最近一个工具交换，所以本次决定为：

```text
compress_indices = [1, 2, 4, 5]
保留最近工具交换 = [6, 7]
普通消息 3、8、9 不属于它的处理目标
```

摘要完全由固定规则生成，只包含工具名和结果短预览：

```text
Compacted 2 older tool exchange(s):
- read: 'README says this project provides a minimal long-horizon...'
- search: 'StateSnapshot found in state.py and test_core.py...'
```

假设 replacement 获得新索引 10，新的活跃顺序为：

```text
[0, 10, 3, 6, 7, 8, 9]
```

完整历史仍然包含消息 0～10。索引 10 虽然最后追加，但被插回第一组旧工具交换的逻辑位置。

这种策略没有新模型请求、费用低且结果确定，也不会产生摘要幻觉；但它只理解工具交换结构，无法判断某条日志揭示了什么根因、哪些设计决策最重要，或者下一步应该做什么。

#### 7.7.2 SummarizeStrategy：让 compressor 理解旧工作

配置如下：

```python
SummarizeStrategy(
    compressor=summarizer,
    threshold_tokens=4000,
    keep_recent=4,
)
```

默认 `preserve_kinds` 为：

```text
task、system、summary、context
```

因此消息 0 原样保留。其余非保护消息形成 droppable 列表：

```text
[1, 2, 3, 4, 5, 6, 7, 8, 9]
```

保留最近四条 droppable 消息 `[6, 7, 8, 9]` 后，候选是 `[1, 2, 3, 4, 5]`。公共工具对齐逻辑确认其中的 `read` 和 `search` 调用/结果仍然完整，所以本次可折叠：

```text
compress_indices = [1, 2, 3, 4, 5]
```

compressor 收到旧消息和一条明确的压缩任务说明，要求尽可能保存：

```text
Goal              目标和约束
Done              已完成并验证的工作
State             当前进度
Facts & identifiers  精确路径、符号、命令、错误和结果
Open              未解决问题
Next              下一步动作
Tried & rejected  已尝试但失败的方法及原因
```

它可能生成：

```text
Goal:
- Explain how State, active context and ContextView form a data pipeline.

Done:
- Read README.md.
- Located StateSnapshot in state.py.
- Confirmed MessageEvent updates the snapshot.

Facts & identifiers:
- ContextView.total_messages counts active messages before visibility filtering.

Open:
- Verify how compression protects ToolCall/ToolResult pairs.

Next:
- Inspect compression/runtime.py and its unit tests.
```

摘要前还会加入 continuation preamble，大意是：

```text
这是先前会话压缩后保留下来的既有工作记忆。
其中的事实、决定和进度已经成立，请从这里继续，
不要把它当作新资料重新从头推导。
```

它解决的是“模型如何理解摘要的时间语义”：摘要不是用户新提交的参考材料，而是主 Agent 自己先前工作的延续。

这里的“独立 compressor Agent”不是一次完整子 Agent 运行。当前实现不会为压缩启动独立 State，再经历 AgentStart、多轮 Turn、工具执行和 AgentEnd；它使用独立 Agent 配置中的名称、system prompt、Provider 和 `generate()`，直接完成一次压缩模型调用：

```text
构建 compressor ContextView
  → ModelRequestEvent(compressor)
  → compressor.generate(messages)
  → ModelResponseEvent(compressor)
  → 取得摘要文本
```

压缩请求和响应作为 Trace Event 写回主 State，使观察者能够核对 compressor 看到了什么、使用哪个模型以及消耗多少 Token。replacement 的 sidecar 还会保存：

```python
{
    "compression": {
        "compressor": "...",
        "model": "...",
        "usage": {
            "input_tokens": ...,
            "output_tokens": ...,
            "cache_read_tokens": ...,
            "cache_write_tokens": ...,
        },
    },
    "raw": ...,
}
```

所以摘要正文服务于主 Agent 的工作连续性；Trace Event 和 sidecar 服务于压缩过程的审计。

#### 7.7.3 AgentCompactStrategy：主 Agent 主动提交阶段摘要

假设主 Agent 已经完成 README、搜索和检查阶段，它最清楚哪些事实应该带到下一阶段。它可以主动调用：

```python
compact(
    summary="""
    README and StateSnapshot were inspected.
    Confirmed that MessageEvent updates snapshot.messages.
    The next task is to inspect compression usage baselines.
    """,
    keep_recent=2,
)
```

这个摘要就是主 Agent 自己刚刚生成的工具参数，不需要再调用一个 compressor 模型。模型发出的 compact ToolCall 和 Runtime 返回的工具结果会继续追加到历史中。假设它们的索引是：

```text
10 AssistantMessage(step + compact ToolCall)
11 UserMessage(tool_result + compact_request details)
```

compact 工具执行时不会立即改变 State，只在 ToolResult details 中写入申请：

```python
{
    "compact_request": {
        "summary": "...",
        "keep_recent": 2,
    }
}
```

下一轮开始时，AgentCompactStrategy 才读取这项申请。默认保护消息 0，并保留最近两个非保护消息 10、11，因此它可以用主 Agent 的摘要折叠消息 1～9：

```text
保护：[0]
压缩：[1, 2, 3, 4, 5, 6, 7, 8, 9]
保留最近：[10, 11]
```

假设新摘要索引为 12，活跃顺序变成：

```text
[0, 12, 10, 11]
```

这表示：旧工作由主 Agent 自己写的 working memory 替代，而 compact 调用和确认结果暂时保留原文。

### 7.8 为什么 Agent compact 要等到下一轮开始

compact 工具只提交请求，真正压缩仍发生在统一的模型请求前阶段：

```text
当前工具回合
  模型调用 compact
  → Runtime 记录 ToolResult(details.compact_request)
  → 当前并行工具全部结束并组装结果

下一轮开始
  TurnStart
  → maybe_compress_context()
  → AgentCompactStrategy 读取请求
  → Compression Runtime 统一应用
  → build_context_view()
  → ModelRequest
```

如果 compact 工具在执行中途直接修改 State，一轮中的其他并行工具可能仍未完成，容易导致工具调用/结果被拆开、Snapshot 在回合中途改变，或者压缩绕过统一 Event 记录。请求与执行分离保证所有压缩都位于同一个安全边界。

### 7.9 high-water mark 如何保证 compact 请求只应用一次

上例中 compact 请求位于消息 11。下一轮开始时：

```text
request_index = 11
max(active_indices) = 11
```

请求仍是活跃视图中最新的消息，因此可以应用。压缩后追加摘要 12：

```text
active_context_indices = [0, 12, 10, 11]
```

再次检查时：

```text
request_index = 11
max(active_indices) = 12
11 != 12
```

较新的 summary 索引就是 high-water mark，证明请求已经被消费，不需要另外维护 `processed_request_ids`。

如果下一轮没有足够旧消息可折叠，策略本轮返回 no-op；一旦模型随后产生更新的消息，compact 请求也不再是最高索引，因此不会在很久以后用一份陈旧摘要去压缩后来才产生的历史。

### 7.10 TieredStrategy：每次检查采用首个可执行阶段

ContextPolicy 只持有一个 strategy：

```python
ContextPolicy(strategy=one_strategy)
```

如果希望表达多级策略，可以将组合对象作为这一个入口：

```python
TieredStrategy(
    stages=(
        agent_compact_strategy,
        ToolCompactStrategy(threshold_tokens=4000),
        SummarizeStrategy(
            compressor=summarizer,
            threshold_tokens=4000,
        ),
    )
)
```

每次压缩检查按顺序询问：

```text
1. 是否存在刚提交、仍有效的 agent compact 请求？
   有 → 使用它，本次不再检查后续阶段

2. 是否超阈值且存在可折叠的旧工具交换？
   有 → 使用 ToolCompact，本次不再检查 Summarize

3. 是否仍超阈值且存在可总结的旧消息？
   有 → 使用 Summarize
```

“先做廉价工具折叠，再在仍超预算时调用模型摘要”通常跨两次请求前检查发生，而不是同一次检查连续执行全部阶段：

```text
第 N 轮请求前：
  ToolCompact 命中
  → 折叠旧工具交换
  → 进入第 N 轮模型请求

第 N+1 轮请求前：
  如果仍超阈值，且 ToolCompact 已无旧工具交换可折叠
  → Summarize 才命中
```

需要区分两个概念：

- TieredStrategy 的多个阶段：本次检查只采用首个可执行阶段；
- 某一个阶段返回多个 CompressionDecision：公共 Runtime 可以在本次检查中依次应用这些决定。

例如 SummarizeStrategy 可能因为受保护的 task 将候选内容分成不同连续区间，从同一个阶段返回多个决定。这不等于 TieredStrategy 在同一次检查中同时执行 ToolCompact 和 Summarize。

### 7.11 如何选择策略

| 主要问题 | 建议策略 | 取舍 |
| --- | --- | --- |
| `read`、`bash`、`search` 输出大量膨胀 | ToolCompactStrategy | 最便宜、确定，但只保留工具名和短预览 |
| 长任务包含复杂决定、进度、失败尝试和下一步 | SummarizeStrategy | 能理解语义，但增加模型成本并可能遗漏事实 |
| Agent 能识别阶段完成点，并愿意主动整理 | AgentCompactStrategy + `compact` tool | 时机和摘要最贴近主 Agent，但依赖模型主动正确调用 |
| 希望主动整理、廉价清理和语义摘要共同工作 | TieredStrategy | 阶段顺序决定优先级，每次检查首个命中者获胜 |

无论选择哪种方式，最终都回到同一个不变量：

```text
选择旧消息索引
  → 追加 replacement summary
  → 记录 ContextCompressionEvent
  → 重指向 active_context_indices
  → 原始 Message 继续留在完整历史中
```

---

## 8. Runtime Message、LLMMessage 与 Provider Wire

### 8.1 三层是三种不同的语言

| 层次 | 保留什么 | 刻意不包含什么 | 所有者 |
| --- | --- | --- | --- |
| Runtime `Message` | role、content、sender、target、kind、sidecar；AssistantMessage 另有 usage/model | Provider SDK 对象 | Runtime / State |
| LLMMessage / LLMRequest | 模型角色、统一内容块、工具定义、system prompt、推理与请求参数 | sender、target、kind 等运行路由 | LLM 边界 |
| Provider Wire | 某家 API 真正要求的 messages/input/tools 等字段 | 项目内部抽象保证 | Provider Adapter |

这里的“Runtime Message”指 Runtime 层的 Message 联合类型，不是仅指具体的 RuntimeMessage 类。UserMessage 和 RuntimeMessage 没有 usage/model，这两个调用级字段只属于 AssistantMessage。

### 8.2 请求方向：从 ContextView 到具体 API

假设 ContextView 中有：

```python
UserMessage(
    role="user",
    kind="task",
    sender="user",
    target="writer",
    content=(TextBlock("分析 README"),),
)
```

Bridge 投影后得到：

```python
LLMMessage(
    role="user",
    content=(TextBlock("分析 README"),),
    extra={},
)
```

`sender`、`target` 和 `kind` 被移除，因为 Provider 不需要理解谁是 `writer`、target 是否为 `all`、或 kind 是否为 `summary`。

如果 Message.sidecar 包含：

```python
{
    "extra": {
        "anthropic.cache_breakpoint": True,
    },
    "details": {
        "debug_note": "injected by harness",
    },
}
```

Bridge 只会把 `sidecar["extra"]` 提升到 `LLMMessage.extra`。`details` 和 `raw` 不会整包发给 Provider。Anthropic Adapter 可以识别自己的 namespaced hint 并转换为 `cache_control`；OpenAI Adapter 可以忽略它不认识的 Anthropic hint。

工具也会从可执行 Runtime Tool 降级为纯数据 LLMTool：

```text
Runtime Tool
├─ name          → 保留
├─ description   → 保留
├─ parameters    → 保留
└─ execute       → 留在 Runtime，不发给 Provider
```

然后 Agent 的固定 system prompt、工具定义和请求参数组成：

```python
LLMRequest(
    provider=provider,
    system_prompt="你是代码架构分析助手。",
    messages=[...],
    tools=[...],
    reasoning="high",
    timeout_seconds=600,
    extra={},
)
```

Adapter 最后翻译成某家 Provider Wire。例如同一 ToolCallBlock：

```text
OpenAI Chat:
  assistant.tool_calls[].function

Anthropic:
  assistant content[].type = "tool_use"
```

固定 system prompt 在 OpenAI Chat 中可以成为领先的 system message，在 Anthropic 中则是顶层 `system` 字段。这些差异只属于 Adapter。

运行路由头可以在某些场景中显式加入文本：

```text
[researcher -> writer | context]
```

但普通模型调用默认 `with_header=False`，不会把 Runtime 路由元数据偷偷拼进模型正文。

### 8.3 响应方向：从 Provider 回到 State

假设 Provider 返回了思考、文本和 Bash 调用。Adapter 先转换为统一内容：

```python
content = (
    ThinkingBlock(text="I should run the requested command."),
    TextBlock(text="I'll run it."),
    ToolCallBlock(
        id="bash_1",
        name="bash",
        arguments={"command": "printf 'hello-from-tool\\n'"},
    ),
)
```

Provider 的停止字段也会规范化为项目统一的 StopReason：

```text
end_turn
tool_use
max_tokens
error
```

同时，Adapter 规范化 TokenUsage，读取 Provider 实际返回的模型标识，并保存边界快照：

```python
LLMResponse(
    content=content,
    stop_reason="tool_use",
    usage=TokenUsage(...),
    model="served-model-id",
    raw={
        "request": {...},
        "response": {...},
    },
)
```

`LLMResponse.content` 是规范化模型输出的事实来源。`response.text`、`response.thinking_blocks` 和 `response.tool_calls` 都是从同一个有序 content 序列中导出的便利视图，不是三份独立状态。

Bridge 再补回 Runtime 语义：

```text
LLMResponse
+ 当前 Agent 名称
+ 目标
+ 由 stop_reason 导出的 kind
= AssistantMessage
```

例如：

```python
AssistantMessage(
    role="assistant",
    sender="bash_agent",
    target="user",
    kind="step",  # tool_use 尚未终结
    content=response.content,
    usage=response.usage,
    model=response.model,
    sidecar={"raw": response.raw},
)
```

如果 `stop_reason="end_turn"`，`make_llm_agent()` 将 kind 设为 `final`；其他非终结响应则为 `step`。

Runtime 随后依次记录：

```text
ModelResponseEvent
  → 这次模型访问的输出类型、工具调用数、usage 和 model

MessageEvent(AssistantMessage)
  → 这条消息正式进入 transcript
```

### 8.4 当前“流式汇总”的准确理解

LLM 访问层暴露统一 StreamEvent 协议，事件流最后必须产生：

```python
StreamEvent(
    kind="done",
    payload={"response": complete_llm_response},
)
```

`complete()` 消费这个事件流并返回最终 LLMResponse。

但当前 OpenAI Chat、Anthropic 等真实 Adapter 的具体实现是：

```text
执行一次阻塞式 SDK 请求
  → 获得完整 Provider 响应
  → 规范化成 Content Block
  → 发出统一 StreamEvent
  → 最后发出 done + LLMResponse
```

因此更准确的说法是：

> Adapter 将 Provider 响应规范化为统一 StreamEvent，并以携带完整 LLMResponse 的 done 事件结束。

不应把当前实现描述成“已经逐个消费所有 Provider 网络流片段”。

### 8.5 raw 是边界证据，不是核心协议

`LLMResponse.raw` 通常包含：

```python
{
    "request": provider_request_snapshot,
    "response": provider_response_snapshot,
}
```

它用于回答：

- 最终向 Provider 发送了哪些字段？
- system prompt、工具 Schema 和缓存 hint 是否正确落到 Wire？
- Provider 实际返回了哪个模型版本？
- 是否存在统一类型未覆盖的 Provider 专用字段？

正常控制流不能这样实现：

```python
if raw["response"]["choices"][0]["message"]["tool_calls"]:
    execute_tools()
```

否则 Runtime 就会被 OpenAI Chat 数据结构绑定。正常逻辑应读取：

```text
模型文本       → TextBlock
思考内容       → ThinkingBlock
工具请求       → ToolCallBlock
停止原因       → LLMResponse.stop_reason
Token 使用      → TokenUsage
实际模型       → LLMResponse.model
Provider 调试  → raw
```

当前 Bridge 对 OpenAI Responses 还有一个受控特例：它会从 raw response 中提取下一轮推理连续性需要的 Provider 元数据，放入 namespaced `extra`。这不意味着 raw 变成通用运行协议；它仍然是一个边界层明确控制的 Provider 特例。

原始 SDK 对象也不能替代 LLMResponse 或 Message。Adapter 会尽量将 SDK 响应转换成可序列化快照，它的目的是保存证据，而不是让核心模块继续调用 Provider SDK 方法。

---

## 9. 再把一次工具调用完整串起来

现在回到开头的 Bash 主例子，不再引入新概念。

### 9.1 初始化

```text
Agent.run(task)
  → State(task=task)
  → 记录 UserMessage(kind="task")
  → MessageEvent 进入 events
  → Snapshot.messages = [task]
```

需要注意，`Agent.run()` 返回的运行事件是惰性生成器。调用后已经存在初始任务 Message，但只有开始消费 events，Runtime 才继续模型和工具流程。

### 9.2 第一次模型请求

```text
State.active_context_messages()
  → [task]

build_context_view()
  → 本轮可见 Message

Bridge
  → LLMMessage，去掉 sender/target/kind

LLMRequest
  → 加入 Agent.system_prompt、bash 工具定义和请求参数

Adapter
  → Provider Wire
```

Runtime 在调用前记录 ModelRequestEvent，它会保存 ContextView 统计、工具定义和请求投影。

在这个确定性例子中：

```text
visible_count     = 1  # 一条 task Message
llm_message_count = 2  # task + 固定 system prompt
```

固定 system prompt 参与请求，但不是 State 中的普通 Message，所以两个计数不同。

### 9.3 模型返回工具调用

```text
Provider response
  → Adapter 规范化
  → LLMResponse(
        content=[TextBlock, ToolCallBlock("bash_1")],
        stop_reason="tool_use",
        usage=...,
        model=...,
        raw=...,
     )
  → Bridge 补回运行语义
  → AssistantMessage(kind="step")
```

Runtime 先记录 ModelResponseEvent，再通过 MessageEvent 将 AssistantMessage 写入 State。

### 9.4 Runtime 执行工具并返回观察

```text
ToolCallBlock(id="bash_1")
  → ToolExecutionStartEvent
  → bash.execute(...)
  → ToolExecutionEndEvent
  → ToolResultBlock(tool_call_id="bash_1")
  → UserMessage(kind="tool_result")
  → MessageEvent
```

ToolExecution Event 是给观察者的执行事实；ToolResultBlock 是给下一轮模型的环境观察。两者都必要，但不能互相替代。

### 9.5 第二次模型请求和正常停止

此时模型可见对话是：

```text
task
+ Assistant tool call
+ tool_result
```

再加上固定 system prompt，第二次 ModelRequestEvent 的核心计数是：

```text
visible_count     = 3
llm_message_count = 4
```

模型返回 `stop_reason="end_turn"`，Bridge 生成：

```python
AssistantMessage(
    kind="final",
    sender="bash_agent",
    target="user",
    content=(TextBlock("Bash observation: hello-from-tool"),),
    usage=...,
    model=...,
)
```

Runtime 记录 ModelResponseEvent、MessageEvent、TurnEndEvent 和：

```text
AgentEndEvent(reason="done")
```

此时系统同时拥有：

- 四条可回放的 Message；
- 十六条完整运行 Event；
- 两次模型请求的投影和 usage；
- 一对通过 `bash_1` 稳定关联的 ToolCall/ToolResult；
- 可从 Event Stream 重建的 Message Snapshot；
- 不污染核心 State 的 Provider raw 边界证据。

---

## 10. 从一个 Agent 到多 Agent 系统：Builder、Session、委派与 Workflow

前九章解释的是一条 Agent 运行内部的数据和控制流。本章把粒度提升一层，回答三个新问题：

```text
一个 Agent 应该具备哪些能力？
这些能力依赖的外部资源应该存活多久？
多个 Agent 运行之间怎样传递任务、结果和完成状态？
```

可以先把组合体系放进一条主线：

```text
Builder / Flavor
  组装一个 Agent 的 prompt、tools、policy 和 hooks
        ↓
Session / Toolset
  保证 MCP 等外部资源覆盖完整惰性运行
        ↓
Task Tool
  让父模型在推理过程中动态委派子 Agent
        ↓
Workflow
  由程序规定多个独立 Agent 运行的协作关系
        ↓
Goal Loop
  在模型输出 final 后，用外部事实验证是否真正完成
        ↓
Workflow Facade
  让复杂 Workflow 对外仍保持 Agent.run(task) 入口
```

这些层次不引入第二套 Message、State 或 Runtime。它们只是决定如何创建普通 Agent、如何托管资源，以及如何组织多次普通运行。

### 10.1 先分清六个层次

| 层次 | 回答的问题 | 是否拥有独立对话 State |
| --- | --- | --- |
| Builder | 一个 Agent 有什么能力 | 只创建 Agent，不运行 State |
| Flavor | 哪组常用 Builder 配置有一个稳定名称 | 取决于该 flavor 构建普通 Agent 还是 Facade |
| Session | Toolset、连接和子进程活多久 | 不创建第二套对话 State |
| Task Tool | 父模型是否在当前推理中动态委派 | 子 Agent 拥有独立 State |
| Workflow | 多个运行以什么程序关系协作 | 普通 Step 通常各有独立 State |
| Goal Loop | 候选答案是否通过外部验收 | 多次 `resume` 继续同一个 State |

最重要的边界是：

```text
Builder 管配置
Session 管资源生命周期
State 管一次运行事实
Workflow 管多次运行关系
Goal Loop 管外部完成条件
```

不要因为这些对象都能“运行 Agent”，就把它们理解成同一层抽象。

### 10.2 Builder：组装能力，不执行任务

假设要构建一个能分析 State 与 Compression 的 Agent：

```python
agent = make_agent(
    provider=provider,
    cwd="C:/repo",
    bash=True,
    read=True,
    general_purpose=True,
    tools=[custom_search_tool],
)
```

Builder 可能组装出：

```text
Agent
├── name / role
├── system_prompt
├── Provider
├── Bash Tool
├── Read Tool
├── Task Tool
├── custom_search_tool
├── ContextPolicy
├── Hooks
└── init_state
```

调用 `make_agent()` 不会自动产生 State，也不会自动请求模型。真正运行仍由调用者显式开始：

```python
state, events = agent.run(
    "分析 State 与 Compression，并验证文档是否准确。"
)
```

因此 Builder 的数据流是：

```text
Provider + prompt + 能力开关 + 自定义工具
  → 组装 Agent
  → 调用者决定何时 Agent.run()
  → 仍进入同一个 core.run()
```

Builder 可以配置 Provider、system prompt、工具、ContextPolicy、Hooks 和 `init_state`，但不应该复制：

```text
Agent.run()
core.run()
ToolCall 调度
ToolResult 组装
State.record_event()
ContextView 构建
停止条件判断
```

例如 Skills 通过 `init_state` 在任务开始时注入菜单和方法，之后仍然进入同一套 Runtime；它不是一条专用 Skills 循环。

#### Flavor 是命名好的 Builder 配置

项目当前的简单 Agent flavor 包括：

```text
bash
bash_task
bash_task_read
bash_skills
```

Workflow flavor 包括：

```text
loop
pdr
```

Flavor 的本质是：

```text
稳定名称
  → 一组约定的构建参数
  → 普通 Agent 或 Workflow Facade
```

它不是新的 Message 类型、Runtime 类型或 Agent 继承体系。`bash_task` 只是“已约定装配 Bash 和 Task 能力”的入口；`pdr` 则构建一个对外像 Agent、内部运行 Workflow 的 Facade。

### 10.3 Session 与 Toolset：资源生命周期不是对话生命周期

普通 Bash、Read 工具通常已经可以直接执行，不需要维持长连接：

```python
agent = make_agent(provider=provider, bash=True, read=True)
```

MCP 等能力则通常依赖有生命周期的资源：

```text
连接 MCP Server
  → 初始化 MCP Session
  → 发现工具
  → 在整个 Agent 运行期间保持连接
  → 运行结束后关闭
```

因此使用 AgentSession：

```python
with agent_session(
    provider=provider,
    mcp_servers=[server_config],
) as session:
    state, events = session.run(
        "搜索项目文档并解释 State 与 Compression。"
    )
    for event in events:
        print(event.kind)
```

完整顺序是：

```text
进入 with
  → 创建 ExitStack
  → 收集 Bash、Read 等静态 Tool
  → 逐个打开 Toolset
  → 从 Toolset 收集 AgentTool
  → 使用全部 Tool 构建 Agent
  → Agent.run() 返回 State 和惰性 events
  → 在 with 内消费完整 Event Stream
退出 with
  → 清除 session.agent
  → ExitStack 逆序关闭 Toolset
```

#### 为什么 events 必须在 Session 内消费

下面的写法是错误的：

```python
with agent_session(...) as session:
    state, events = session.run("分析项目")

# Session 已退出，MCP 连接已经关闭
for event in events:
    ...
```

`Agent.run()` 返回的是惰性生成器。离开 `with` 时，真正的模型请求和工具执行可能尚未发生。之后模型再请求 MCP 工具，Runtime 面对的就是已关闭连接。

这里存在两个不同生命周期：

```text
State 生命周期
  = 一次 Agent 运行的消息与事件事实

Session 生命周期
  = 支撑工具执行的连接、Toolset 和子进程
```

Session 不保存第二份 State，不改变 `agent.run()` 的返回契约，也不会把 MCP 内部事件直接塞进模型 transcript。MCP 工具的模型可见输出仍然经过统一链路：

```text
MCP Tool 执行
  → ToolResult
  → ToolResultBlock
  → UserMessage(kind="tool_result")
  → 下一轮模型上下文
```

### 10.4 Task Tool：由父模型动态决定是否委派

继续使用同一个任务：

```text
分析 State 与 Compression，并验证文档是否准确。
```

父 Agent 认为自己需要一个专门代码阅读者，于是产生：

```python
ToolCallBlock(
    id="task_1",
    name="task",
    arguments={
        "subagent_type": "code_reader",
        "task": (
            "阅读 state.py 和 compression/runtime.py，"
            "说明压缩如何修改 active_context_indices。"
        ),
        "context": "重点核对 ToolCall/ToolResult 配对保护。",
    },
)
```

Task Tool 内部持有受控注册表：

```python
{
    "code_reader": code_reader_agent,
    "test_runner": test_runner_agent,
}
```

模型只能选择已经注册的 `subagent_type`，不能把任意模块路径、类名或 import 字符串变成可执行代码。

#### Task Tool 内部数据流

```text
父 Agent
  → ToolCallBlock(task_1)
Task Tool
  → 根据 subagent_type 查找已注册 Agent
  → child_agent.run(task) 创建独立 Child State
  → 在 Child State 中记录可选 context Message
  → 消费完整 Child Event Stream
  → 查找 child kind="final" Message
  → 将 final 文本包装为 ToolResult.content
  → 将 Child State 的完整事件放入 details["sub_events"]
父 State
  → 记录一个普通 tool_result Message
  → 父模型下一轮读取子 Agent 结果文本
```

父子事实被明确分开：

```text
Parent State
├── 父任务
├── 父模型请求
├── task ToolCall(task_1)
└── ToolResult：子 Agent 最终文本

Child State
├── 子任务
├── 委派 context
├── 子模型请求
├── 子工具执行
├── 子压缩事件
└── 子 final
```

父模型直接看到的是简化后的结果文本，例如：

```text
StateSnapshot.apply 只处理 MessageEvent 和 ContextCompressionEvent；
压缩通过追加 replacement 和重指向 active_context_indices 改变活跃视图。
```

观察者则可以从：

```python
ToolResult.details["sub_events"]
```

检查子 Agent 的完整执行过程。这再次体现了前文的边界：模型可见内容保持简洁，Event/sidecar 为审计保留完整证据。

#### 为什么父子 Agent 必须使用独立 State

如果父子并发追加同一个 State，会产生：

- 父任务与子任务混在同一 transcript；
- 父子 Event index 相互交错；
- 子 Agent 压缩可能修改父 Agent 的活跃索引；
- 父子 ToolCall/ToolResult 容易跨运行错配；
- 子 Agent 的 TurnEnd、AgentEnd 和 `max_turns` 污染父生命周期。

独立 State 让父运行和子运行都能单独回放。父 State 只把子 Agent 当作一次普通 Tool 观察。

#### Task Tool 与 Workflow 的输出回退不同

当前 Task Tool 要求子 Agent 真正产生 `kind="final"`。如果子 Agent 因 `max_turns` 停止且没有 final，它返回错误 ToolResult：

```text
Sub-agent 'code_reader' produced no final message
```

这与后面的 Workflow `run_agent()` 不同：Workflow 为了让截断步骤仍能给下一阶段提供参考，可以回退到最后一条 Assistant 文本。不能把这两种契约混写。

未知子类型同样形成可恢复的错误 ToolResult：

```text
Unknown subagent_type 'unknown_worker'.
Available: ['code_reader', 'test_runner']
```

父模型可以读取错误后修正选择，而 Runtime 不会动态导入未知模块。

### 10.5 Task Tool 与 Workflow 的本质区别

两者都会运行其他 Agent，但控制权不同：

| 问题 | Task Tool | Workflow |
| --- | --- | --- |
| 谁决定是否运行下一个 Agent | 父模型在当前推理中决定 | Python 程序或 Workflow 协议决定 |
| 子运行如何进入父视角 | ToolResult | StepResult.output 成为下一步输入 |
| 是否属于父 Agent 的一次工具回合 | 是 | 否，通常是多个并列或串联运行 |
| 适合场景 | 是否委派取决于当前观察 | 阶段顺序是稳定业务规则 |

例如：

```text
“遇到不熟悉模块时，模型可以选择委派 code_reader”
  → Task Tool

“必须先规划，再执行，最后审查”
  → Workflow
```

### 10.6 StepResult 与 WorkflowResult：输出和事实必须同时保留

一次 Workflow Step 的结果不是一个裸字符串：

```python
StepResult(
    name="reviewer",
    role="critic",
    task="审查 State 与 Compression 文档",
    output="发现 ContextView.total_messages 的表述需要修正。",
    state=reviewer_state,
)
```

其中：

```text
output
  = 给下一步使用的文本接口

state
  = 本步骤完整的 Message、Event、工具调用、TokenUsage、
    压缩历史和停止原因
```

整个 Workflow 返回：

```python
WorkflowResult(
    output="最终审查和修订结果",
    steps=[planner_step, writer_step, reviewer_step],
)
```

其结构是：

```text
WorkflowResult
├── output：整个流程的最终对外输出
└── steps：按逻辑顺序排列
    ├── StepResult(state_planner)
    ├── StepResult(state_writer)
    └── StepResult(state_reviewer)
```

WorkflowResult 不会在运行时把多个 State 强行合并成一条共享 transcript。每一步的 State 仍然是自己的事实来源。

#### `run_agent()` 为什么必须消费惰性 Event Stream

Workflow 辅助函数会完成：

```text
agent.run(task)
  → 得到 state 和惰性 events
  → 记录可选 context Message
  → 消费 events，真正推进 Runtime
  → 从完成后的 State 提取输出
  → 返回 StepResult
```

如果不消费 events，StepResult 只会得到初始化状态，模型和工具流程还没有执行。

#### `max_turns` 时为什么仍可能有 StepResult.output

Workflow 输出提取顺序是：

```text
1. 当前 Agent 本段最新的 kind="final" Message
2. 否则，当前 Agent 最后一条有文本的 AssistantMessage
3. 否则，空字符串
```

假设 Agent 在回合耗尽前最后输出：

```text
已经定位到 state.py:64，但尚未完成修改和验证。
```

即使 State 最后记录：

```text
AgentEndEvent(reason="max_turns")
```

Workflow 仍可以得到：

```text
step.output = "已经定位到 state.py:64，但尚未完成修改和验证。"
```

这只是给下一阶段提供最后可用信息，不代表系统伪造了一条 final，也不代表该步骤成功完成。停止原因仍必须从 State Event 判断。

### 10.7 六种 Workflow 如何组织独立运行

下面都使用同一个总任务：

```text
分析 State 与 Compression，修正文档并完成验证。
```

#### 10.7.1 Sequential Chain：固定流水线

```text
Analyzer
  输入：原始任务
  输出：架构分析
        ↓
Writer
  输入：原始任务 + Analyzer 输出
  输出：修订稿
        ↓
Polisher
  输入：原始任务 + Writer 输出
  输出：最终稿
```

后一步默认同时接收原始任务和前一步输出，而不只是接收上一步文本。否则目标可能逐层漂移：分析只保留模块名，写作者继续丢失“解释为什么”的要求，最后润色器只会润色一个不完整结果。

每个阶段仍然有独立 State：

```text
Analyzer State ≠ Writer State ≠ Polisher State
```

#### 10.7.2 Planner / Executor：计划与环境修改分离

```text
Planner
  工具权限较少
  输出：读取哪些文件、修改哪里、运行哪些检查
        ↓
Executor
  拥有 Read/Edit/Bash
  输入：原始任务 + 计划
  实际修改文件并运行验证
```

它的价值不仅是“多调用一个模型”，而是明确权限和审计边界：Planner 决定做什么，Executor 才能改变环境。

#### 10.7.3 Reflection：生成、批评、修订

```text
Generator 产生学习指南草稿
  → Critic 检查事实和可理解性
     ├─ 输出约定的 APPROVED → 返回当前草稿
     └─ 未批准 → Generator 根据批评修订 → 再次 Critic
```

`APPROVED` 是协议，不是模糊语气。Critic prompt、Workflow 参数和 `is_approved()` 必须检查同一个 marker。

如果达到 `max_rounds` 仍未批准，Workflow 返回最新草稿，但不会伪造“Critic 已批准”。因此：

```text
最新草稿 ≠ 已通过审查
```

完整 steps 仍保留 draft、critique、revision 等每一次 State。

#### 10.7.4 Routing：从注册表中选择一个专家

```python
routes = [
    Route("domain", domain_agent, "检查 Message、State 与 Event"),
    Route("context", context_agent, "检查 Compression 与 ContextView"),
]
```

Router 输出 route name，系统再从注册 Route 中解析：

```text
Router Step
  → select_route()
  → 注册表查找
  → 只运行被选中的 Specialist Step
```

无法解析时可以使用显式 default；没有 default 时，只返回 Router Step 和原始输出。模型输出的任意模块名不会被动态 import。

#### 10.7.5 Parallel：独立候选或任务分片

Ensemble 模式让多个 Worker 接收同一任务：

```text
Worker A：从领域类型分析
Worker B：从 Runtime 时序分析
Worker C：从教学可理解性分析
```

Map 模式让 Worker 接收不同子任务：

```text
Worker A → messages.py
Worker B → state.py
Worker C → compression/runtime.py
```

每个 Worker 都有独立 State。即使完成时间是：

```text
C → A → B
```

WorkflowResult.steps 仍按声明顺序稳定排列：

```text
[A, B, C]
```

真实执行时序保存在各 State Event 中；结果数组顺序服务于稳定程序语义。可选 Aggregator 最后读取 Worker 输出并生成汇总，但不能反向修改 Worker State。

#### 10.7.6 PDR：多轮并行探索和发现提炼

当前 PDR 更准确的结构是：

```text
第 1 轮
  多个 Attempt 并行分析
        ↓
  Distiller 提炼 findings brief

第 2 轮
  原始任务 + findings brief
        ↓
  多个 Attempt 再次并行分析
        ↓
  Distiller 更新 findings brief

最后
  Finalizer 根据原始任务和累计 brief 生成结果
```

跨轮传递的是显式 `brief`，不是让所有 Worker 共享一个不断增长的 State。

如果配置外部 CompletionCheck，每轮 Attempt 完成后会按声明顺序检查各自 State；第一个被外部验证为完成的 Attempt 可以直接成为结果，跳过剩余轮次、Distiller 和 Finalizer。

### 10.8 Goal Loop：模型 final 只是候选完成

普通 Runtime 的停止条件是：

```text
AssistantMessage(kind="final")
  → AgentEndEvent(reason="done")
```

这只证明模型认为本轮回答可以结束，不能证明：

- 测试真的通过；
- 文件真的存在；
- 服务真的启动；
- 外部 API 真的更新；
- 验收条件已经满足。

对于“修正文档并保证检查通过”这样的可验证目标，Goal Loop 增加外部 CompletionCheck：

```text
Agent 首次 run
  → 模型产生候选 final
  → CompletionCheck(state)
     ├─ 外部验证通过 → complete
     ├─ 未通过但可继续 → resume 同一个 State
     ├─ 同一 blocker 连续达到阈值 → blocked
     ├─ 回合/Token 预算耗尽 → budget_exhausted
     └─ 调用者取消或墙钟时间到期 → aborted
```

#### 为什么继续使用同一个 State

验证未通过时：

```python
agent.resume(
    state,
    continuation_feedback,
)
```

同一个 State 持续保留：

```text
原始任务
→ 之前的工具调用和修改结果
→ 模型上一次 final
→ 外部验证反馈
→ 后续模型输出
→ GoalStatusEvent
```

模型因此从真实工作进度继续，而不是重新创建 State 后从头开始。

Goal Loop 中的多个 StepResult 表示同一 State 的不同 run/resume 片段。它们的 `state` 指向同一个持续增长的 State；输出提取通过 `after_message_index` 限定在当前续接片段，避免误用先前 segment 的 final。这与普通 Workflow 每个 Step 通常拥有独立 State 不同。

#### Goal 状态

| 状态 | 含义 | 是否成功 |
| --- | --- | ---: |
| active | 当前检查未通过，仍可继续 | 否 |
| complete | 外部检查确认完成 | 是 |
| blocked | 同一外部阻塞连续达到阈值 | 否 |
| budget_exhausted | Goal 回合或输出 Token 预算耗尽 | 否 |
| aborted | 调用者取消或墙钟时间到期 | 否 |

当前默认阻塞判定要求同一个 blocker reason 连续出现至少三次。`blocked` 表示仍有预算但外部条件持续阻止推进；`budget_exhausted` 表示任务可能还能继续，但允许资源已经用完。二者都不能伪装成成功。

### 10.9 状态、顺序与并发所有权

默认所有权可以记成：

```text
父 Agent             → Parent State
Task Tool 子 Agent   → Child State
Planner              → Planner State
Executor             → Executor State
Parallel Worker A    → State A
Parallel Worker B    → State B
Goal Loop run/resume → 同一个持续 State
```

不要让多个并发 Worker 同时追加一个共享 `state.events`。否则会出现：

- Event index 由线程调度竞争；
- Message 顺序不稳定；
- 一个 Worker 的压缩改变另一个 Worker 的活跃上下文；
- ToolCall/ToolResult 可能跨 Worker 错配；
- 单个步骤无法独立重放。

上一步结果应通过明确输入进入下一步，而不是修改旧 State：

```text
Planner State
  └─ output = 计划

Executor State
  └─ task/context = 原始任务 + Planner output
```

旧 State 继续忠实表达 Planner 当时发生的事实，不会被 Executor 反向改写。

### 10.10 Workflow Facade：统一入口，不统一事实账本

外部运行器通常只认识：

```python
state, events = agent.run(task)
```

但 `pdr` 或 `loop` flavor 内部可能实际执行多个 Worker、Distiller、Finalizer 或多次 Goal resume。Workflow Facade 将它们包装成看起来像普通 Agent 的对象：

```text
外层 facade_agent.run(task)
  → 外层 core.run()
  → facade.generate(visible)
  → 内部执行 Workflow
  → 得到 WorkflowResult
  → 返回一个 kind="final" 的 AssistantMessage
```

外层 State 主要记录：

```text
外层 task
→ 外层 Agent/Turn 生命周期
→ 一次 facade generate
→ 外层 final
```

内部仍然保存：

```text
Worker State
Distiller State
Finalizer State
WorkflowResult.steps
```

所以 Facade 只统一调用外形，不会把内部步骤变成真正共享的 Runtime transcript。

#### `compose_trace_state()` 实际做什么

内部步骤可以先各自写出子 Trace。最终导出时，Facade 根据 overview 派生一个新的轻量 State：

```text
Facade Trace
├── step-00：Worker → 指向完整子 Trace
├── step-01：Distiller → 指向完整子 Trace
└── step-02：Finalizer → 指向完整子 Trace
```

这个组合 State 使用合成的 ToolCall/ToolResult 节点表达树状关系，并引用子 Trace；它不是把所有内部 Event 原样塞进外层运行 State，也不是内部步骤运行时共同写入的事实账本。

更准确的关系是：

```text
多个独立 Step State / 子 Trace
  → compose_trace_state()
  → 便于统一展示的派生树
```

内部 Step State 仍是完整事实来源，Facade Trace 是可重建的组合视图。

### 10.11 如何选择组合方式

| 实际需求 | 推荐方式 | 原因 |
| --- | --- | --- |
| 给一个 Agent 增加 Bash、Read 等无状态能力 | Builder 参数 | 只改变单个 Agent 的能力集合 |
| 使用常见能力组合 | Flavor | 减少重复配置，不增加新运行语义 |
| 工具依赖 MCP 连接或子进程 | Session + Toolset | 资源必须覆盖完整惰性运行 |
| 是否委派取决于模型当前推理 | Task Tool | 父模型动态决定是否调用子 Agent |
| 阶段顺序是固定业务协议 | Sequential / Planner-Executor | Python 顺序清晰且可审计 |
| 需要独立质量审查 | Reflection | Critic 显式批准或要求修订 |
| 一个任务只交给一个专家 | Routing | 从受控 Route 中安全选择 |
| 需要多个候选或分片 | Parallel | Worker 独立并发，结果顺序稳定 |
| 需要多轮并行探索和提炼 | PDR | Attempt、brief、再探索、Finalizer |
| 完成必须由测试或外部状态确认 | Goal Loop | 模型 final 只作为候选结果 |
| 外部接口只接受普通 Agent | Workflow Facade | 保持 `agent.run()` 入口一致 |

最后可以把组合层压缩成下面几句话：

```text
Builder
= 一个 Agent 有什么

Session
= 支撑能力的资源活多久

Task Tool
= 模型是否动态委派子 Agent

Workflow
= 多个独立 Agent 按什么程序关系协作

StepResult / WorkflowResult
= 如何保存单步事实和整体结果

Goal Loop
= 候选 final 如何通过外部事实变成 complete

Workflow Facade
= 如何让复杂 Workflow 对外保持普通 Agent 入口
```

---

## 11. 从事件账本到 Trace：一次运行如何被观察、回放和计费

前面已经建立了两个基础：

- 第 6 章说明 `Message` 是对话事实，`Event` 是运行事实，`State.events` 是追加式事实账本；
- 第 10 章说明父 Agent、Task Tool 子 Agent 和 Workflow Step 通常各自拥有独立 State。

这一章不再创造新的运行事实，而是回答：这些事实怎样变成可阅读、可分析和可持久化的观察结果？

最核心的不变量是：

> Runtime 负责产生事实，Event Stream 负责保存事实，Trace 层负责从事实派生观察视图，Viewer 只负责展示这些视图。

可以先把各层放在一起：

```text
State.events  不可变的运行事实账本
    ↓ 封装
RunTrace      一次运行的身份、事件和消息
    ├─ spans()         时间与父子关系视图
    ├─ model_turns()   每次模型调用的输入输出视图
    ├─ run_cost()      Token 与费用聚合视图
    └─ event_stream()  trajectory.v5 JSONL 持久化
                              ↓
                         Trace Viewer
```

这很像银行系统：Event Stream 是原始流水，Span 和 Cost 是根据流水生成的报表，Viewer 是读取报表的管理后台。报表可以重新生成，但不能反过来修改已经发生的流水。

### 11.1 为什么终端输出不能充当事实账本

终端可能只显示：

```text
Running bash...
patch failed
Finished.
```

这适合人即时阅读，却无法可靠回答：

- 哪个 `tool_call_id` 对应这次失败；
- 工具失败后是否允许 Agent 继续；
- 哪段错误文本真正进入了下一轮模型上下文；
- 模型当时看到了哪些历史、system prompt 和工具定义；
- 是否发生过压缩或子 Agent 委派；
- 最终为何停止，消耗了多少 Token。

同一次失败在 Event Stream 中至少有两类互补事实：

```text
ToolExecutionEndEvent(
    tool_call_id="call_03",
    tool_name="bash",
    is_error=True,
    terminate=False,
)

MessageEvent(
    UserMessage(
        kind="tool_result",
        sender="tool",
        content=[
            ToolResultBlock(
                tool_call_id="call_03",
                tool_name="bash",
                is_error=True,
                content=[TextBlock("patch: missing.patch: No such file")],
            )
        ],
    )
)
```

第一条回答“工具执行发生了什么”，第二条回答“模型下一轮会收到什么环境反馈”。终端中的一句 `patch failed` 不能替代任何一条。

### 11.2 用一次完整运行建立观察样本

下面使用 Trace Viewer 的 fake/demo 轨迹作为教学样本，不把它描述成真实生产调用。任务是：

```text
Investigate the failing wc-line counter regression and ship a fix with a new test.
```

简化后的控制流是：

```text
任务进入 State
  → 主 Agent 请求模型
  → 模型调用 bash，工具成功返回
  → 主 Agent 通过 task 工具委派 search_agent
  → 子 Agent 在独立 State 中完成搜索
  → 主 Agent 再次调用 bash，但工具失败
  → Runtime 压缩活跃上下文
  → 模型产生 final
  → AgentEnd(reason="done")
```

相应事件账本可以简化成：

```text
0  MessageEvent(task)
1  AgentStartEvent
2  TurnStartEvent
3  ModelRequestEvent
4  ModelResponseEvent
5  MessageEvent(assistant + bash ToolCall)
6  ToolExecutionStartEvent(call_01)
7  ToolExecutionEndEvent(call_01)
8  MessageEvent(tool_result)
9  TurnEndEvent

10 TurnStartEvent
11 ModelRequestEvent
12 ModelResponseEvent
13 MessageEvent(assistant + task ToolCall)
14 ToolExecutionStartEvent(call_02)
15 ToolExecutionEndEvent(call_02)
16 MessageEvent(tool_result + sub_events)
17 TurnEndEvent

18 TurnStartEvent
19 ModelRequestEvent
20 ModelResponseEvent
21 MessageEvent(assistant + bash ToolCall)
22 ToolExecutionStartEvent(call_03)
23 ToolExecutionEndEvent(call_03, is_error=true)
24 MessageEvent(tool_result, is_error=true)
25 TurnEndEvent

26 ContextCompressionEvent

27 TurnStartEvent
28 ModelRequestEvent
29 ModelResponseEvent(output_kind="final")
30 MessageEvent(final)
31 TurnEndEvent(terminated=true)
32 AgentEndEvent(reason="done")
```

这 33 条事件共同描述一次运行，但只有其中的 `MessageEvent` 会投影成 transcript。模型请求、工具开始、压缩和停止原因仍然值得观察，却不会被伪装成对话内容再次发给模型。

后面的 Span、ModelTurn 和 RunCost 都从这组事实出发。

### 11.3 RunTrace：封装一次运行，不复制一套 Runtime

运行结束或观察开始时，可以从 State 构造：

```python
trace = run_trace_from_state(
    state=state,
    trace_id="demo.observatory.001",
    producer="demo:trace-viewer",
    meta={
        "model": "demo/observatory-mini",
        "provider": "fake",
    },
)
```

得到的 `RunTrace` 主要保存：

| 字段 | 含义 | 来源 |
| --- | --- | --- |
| `trace_id` | 跨文件引用的一次运行身份 | 导出调用者 |
| `producer` | 谁生成这份 Trace | Harness、Demo 或 Workflow |
| `task` | 便于阅读的任务文本摘要 | `state.task` |
| `events` | 完整运行事件 | `state.events` 的列表快照 |
| `messages` | 完整消息历史 | `state.messages` 的列表快照 |
| `meta` | 模型、运行标签等扩展元数据 | 导出调用者 |

`RunTrace` 不会重新执行 Agent，也不是另一份可以继续 `resume()` 的 State。它只是提供统一观察入口：

```python
trace.spans()
trace.merged_spans()
trace.model_turns()
trace.run_cost()
```

因此关系不是：

```text
State 与 RunTrace 各自维护一套可变运行状态
```

而是：

```text
State 产生并保存运行事实
  → RunTrace 封装一次事实快照
  → 不同纯函数按需要派生观察结果
```

### 11.4 Trajectory v5：怎样把事实账本持久化

规范磁盘格式是 JSONL：第一行是 Header，后续一行一个 Event。

```json
{"schema":"simple-long-horizon-agent.trajectory.v5","type":"trajectory","trace_id":"demo.observatory.001","producer":"demo:trace-viewer","task":"Investigate the failing wc-line counter regression...","meta":{"provider":"fake"}}
{"index":0,"elapsed":0.0,"uuid":"evt-0","kind":"message","message":{"role":"user","kind":"task"}}
{"index":1,"elapsed":0.1,"uuid":"evt-1","kind":"agent_start","agent":"obs_agent"}
{"index":2,"elapsed":0.2,"uuid":"evt-2","kind":"turn_start","agent":"obs_agent"}
```

几个身份字段不能混用：

| 字段 | 回答的问题 |
| --- | --- |
| `index` | 这条 Event 在本次运行中排第几 |
| `elapsed` | 它在运行开始后多久发生 |
| `uuid` | 其他文件或对象如何唯一引用它 |
| `kind` | Reader 应按哪种事件协议解释它 |
| `schema` | 整个文件遵循哪个持久化契约 |

使用 JSONL 的原因与追加式 State 相呼应：

- 新事件可以逐行追加，而不必重写整个历史；
- Viewer 可以读取已经完成的行并实时刷新；
- 进程中断时仍能恢复已完整写入的前缀；
- Header 与 Event Body 的职责清楚；
- Reader 可以重新派生 transcript、Span 和成本。

Header 不重复保存完整 `messages`、`spans`、`model_turns` 或 `cost` 数组，因为它们都可以从后续事件重新计算。

内存中的 `ModelRequestEvent` 会携带精确的 `llm_payload`。写入 v5 时，`event_record()` 会移除这个可重建字段，避免每轮重复保存不断增长的模型上下文；持久化 Reader 需要结合 MessageEvent、Agent registry 或 Provider raw 重建相应视图。

### 11.5 Provider raw 为什么放在 sidecar 文件

第 8 章已经说明 raw 是 Provider 边界证据，不是核心协议。Trace 持久化还要解决一个文件大小问题。

假设每次 Provider 请求都带上累积历史：

```text
请求 1：10 KB
请求 2：20 KB
请求 3：30 KB
...
请求 N：N × 10 KB
```

如果每轮都内嵌 raw，请求历史会被反复复制，总量接近平方增长。项目因此把主轨迹中的 raw 替换为引用：

```json
{"sidecar":{"raw":{"raw_ref":0}}}
```

实际 Provider 请求/响应写入相邻文件：

```text
trajectory.jsonl
trajectory.jsonl.raw.jsonl
```

两者职责是：

```text
主 trajectory  = 供应商无关的规范运行事实
raw pool         = 仅在 Wire Debug 时读取的 Provider 原始证据
```

最终整文件导出会对相同 raw blob 做内容去重；增量 Writer 更重视追加过程中 `raw_ref` 的稳定，维护持续增长的 pool。两种写法的优化方式可以不同，但引用语义必须一致。

即使 raw sidecar 丢失，Reader 仍应能查看 transcript、工具错误、基础 Span、Token usage 等规范事实；受影响的应只是 Provider Wire 调试，而不是对 Agent 是否成功的判断。

### 11.6 Span：把时间顺序派生成操作树

Event Stream 按时间记录：

```text
AgentStart → TurnStart → ModelRequest → ModelResponse
           → ToolStart → ToolEnd → TurnEnd → AgentEnd
```

人更容易阅读的却是：

```text
agent_run
├── turn 1
│   ├── model_call 1
│   └── tool_call bash
├── turn 2
│   ├── model_call 2
│   └── tool_call task
└── turn 3
    └── model_call 3
```

`spans_from_events()` 使用开始/结束事件构造不可变 `Span`：

```python
Span(
    id="demo.observatory.001.call1",
    parent_id="demo.observatory.001.turn1",
    kind="model_call",
    start=0.75,
    end=1.00,
    input=[...],
    output={"kind": "step", "model": "demo/observatory-mini", ...},
    attributes={"agent": "obs_agent", "api": "fake", ...},
)
```

Span 回答“何时开始和结束、属于哪个父操作、输入输出是什么”，但它不是第二份事实日志。Span 提取失败不能反向修改 State，也不能把已经成功的 Agent 宣告为失败。

#### 子 Agent Span 合并不等于 State 合并

第 10 章已经说明 Task Tool 子 Agent 使用独立 State。父 State 只在工具结果 sidecar 中保存子事件：

```text
父 State
└── ToolResult details[call_02].sub_events
    └── 子 Agent State.events 的序列化副本
```

`trace.merged_spans()` 可以在展示时把它派生成：

```text
父 tool_call task
└── 子 agent_run
    ├── 子 turn
    ├── 子 model_call
    └── 子 tool_call
```

合并过程为子 Span 重设展示层 `parent_id`，并将子运行相对时间平移到父工具调用附近。它不把子事件直接追加进父 `State.events`，也不共享两边的消息、压缩索引或生命周期。

### 11.7 ModelTurn：还原模型当时真正看到了什么

`ModelTurn` 不是“最终 transcript 中的一条 AssistantMessage”，而是一对事件事实：

```text
ModelRequestEvent
  + 随后同一 Agent 的 Assistant MessageEvent
  = ModelTurn
```

例如第一次模型调用可派生成：

```python
ModelTurn(
    step_id="demo.observatory.001.model1",
    agent="obs_agent",
    input_messages=[
        {"role": "system", "content": "You are obs_agent."},
        {"role": "user", "content": "Investigate ..."},
    ],
    output_message={
        "role": "assistant",
        "kind": "step",
        "content": [
            {"kind": "text", "text": "I will inspect the regression."},
            {"kind": "tool_call", "id": "call_01", "name": "bash"},
        ],
    },
    tools=[{"name": "bash"}, {"name": "task"}],
    meta={
        "visible_count": 1,
        "model_message_count": 2,
        "request_event_index": 3,
        "message_event_index": 5,
        "api": "fake",
    },
)
```

这里必须依赖请求发生时的投影，因为：

```text
完整消息历史
≠ 压缩后的活跃上下文
≠ 可见性过滤后的 ContextView
≠ 加入固定 system prompt 后的本轮模型输入
```

最终 transcript 无法告诉你某条旧消息在第三轮请求时是否已经被压缩，也无法凭空恢复固定 prompt 和当时的工具定义。因此训练样本构造、单轮重放和压缩效果分析都应从 `ModelRequestEvent` 出发，不能从最终消息列表猜测。

Fake Provider 调用也会生成 `ModelTurn`，只是明确标记 `api="fake"`。下游可以按用途过滤，但 Runtime 不应隐藏一次真实发生过的调用。

### 11.8 RunCost：Token 是调用事实，美元是价格投影

每次可信的 `ModelResponseEvent` 保存：

```text
model
api
TokenUsage(
    input_tokens,
    output_tokens,
    cache_read_tokens,
    cache_write_tokens,
)
```

`RunCost.from_run()` 再把这些调用按模型聚合，并结合 `PriceBook` 计算美元成本：

```text
ModelResponseEvent usage ─┐
                          ├─ 按 model 聚合 → Token 总量
子 Agent sub_events usage ┘
                                      +
                                  PriceBook
                                      ↓
                                  RunCost
```

这里有三个重要边界：

1. usage 是运行时报告的资源事实，价格表是以后仍可能更新的元数据；
2. 四项全零 usage 表示未知或不可信，不计作一次真实的零 Token 调用；
3. 模型有 usage 但价格表没有对应项时，Token 仍保留，并进入 `unpriced_models`。

因此未知模型的美元小计为 0 表示“无法定价”，不是“该调用免费”。调用方应把总价理解成已知价格范围内的结果，而不是静默接受一个虚假的精确成本。

Task Tool 的子 Agent 成本也能通过 `details[*].sub_events` 递归聚合，但计算结果不会写回父 State，更不会改变 Agent 的成功、失败或停止原因。

### 11.9 IncrementalTraceWriter：实时观察，但不控制 Runtime

长任务如果只在结束后导出 Trace，运行过程中就不可观察。`IncrementalTraceWriter` 因此定期读取 State 的浅快照：

```text
Agent 消费惰性 Event Stream并驱动 Runtime
                  ↓ 追加
              State.events
                  ↓ 定期读取
       IncrementalTraceWriter
          ├─ 首次原子写 Header
          ├─ 只追加新增 Event 行
          └─ 同步追加 raw pool
                  ↓
             Viewer 轮询读取
```

一次典型生命周期是：

```python
writer.start()
try:
    events = list(agent_events)  # 真正驱动 Runtime
finally:
    writer.stop()               # 停止线程并做最终 flush
```

Writer 保存已经写入的事件数量。State 没有增长时不写文件；首次写入会清理同名旧 raw sidecar，避免新运行错误引用旧 blob；`stop()` 默认执行最终 flush，避免遗漏尾部事件。

边界必须保持为：

```text
Agent / 调用者驱动 Runtime
Writer 观察 State 并写文件
Viewer 读取文件
```

而不是：

```text
Writer 决定 Agent 是否继续
Viewer 用自己的 Tab 或筛选状态控制 Runtime
```

写入失败通过 `on_error` 报告观察层问题。它不能补造 `AgentEnd(reason="failed")`，因为 Trace 文件失败和 Agent 行为失败是两件不同的事实。

增量阶段的 Header 是首次写入时的快照。最终规范导出可以整文件原子重写，并把 `meta.in_progress` 从 `true` 更新为 `false`，明确区分运行中快照与完成轨迹。

### 11.10 Viewer、容器与远程观察的单向关系

Viewer 是多个派生视图的前端，而不是另一个 Runtime。它可以展示：

| 面板 | 主要依据 |
| --- | --- |
| Transcript | MessageEvent |
| Event Stream | 全部 Event |
| Timeline / Span | 生命周期开始和结束事件 |
| Tool | ToolCall、ToolResult 和 ToolExecution Event |
| Compression | ContextCompressionEvent |
| Model | ModelRequest 与 Assistant Message 配对 |
| Agent Registry | AgentStart 与 compressor 请求中的 prompt |
| Wire Debug | raw sidecar |
| Cost | ModelResponse usage + PriceBook |

Viewer 自己的筛选条件、展开节点和当前 Tab 只属于 UI 状态，不应回写 Agent State。

在容器或远程 Worker 中也不需要让 Viewer 反向连接并控制 Agent：

```text
Worker
  → 写入 out/trajectory.jsonl
  → bind mount 或 Store
  → 必要时同步到 Viewer 可扫描的本地目录
  → Viewer 轮询读取
```

这是 `Worker → Store → Viewer` 的单向观察链。远程 Store 只负责搬运 bytes；如果 Viewer 只支持本地扫描，就先把对象同步到约定目录，而不是把观察需求变成 Worker 的入站控制协议。

### 11.11 完整记录不等于无限保存：Trace 的隐私边界

Trace 可能包含：

```text
用户任务和文件内容
命令输出与错误
Provider 原始请求/响应
工具 details
子 Agent 历史
评测过程与产物路径
```

主轨迹、raw sidecar、artifact root、meta 和 details 提供了结构化隔离点，但不会自动完成全部安全治理。实际运行和存储所有者仍需决定：

- 哪些密钥和环境变量不得进入模型请求；
- 哪些字段写盘前需要最小化或脱敏；
- 谁能读取 raw pool；
- Trace 和 artifact 保留多久、如何清理；
- 私有评测答案是否被禁止进入模型可见内容。

“事件足以审计”是数据结构能力；“数据可以向谁保存多久”是运行治理策略。两者不能混为一谈。

### 11.12 用这组不变量检查是否真正理解

1. `State.events` 是运行事实来源，RunTrace 是一次运行的观察封装。
2. Transcript 只来自 MessageEvent，不等于完整 Event Stream。
3. Span、ModelTurn 和 RunCost 都是可重新计算的派生视图。
4. 每轮模型输入应来自当时的 ModelRequest，而不是从最终 transcript 猜测。
5. Provider raw 外置只能改变存储位置，不能改变规范事件语义。
6. 子 Agent 可以合并到 Span 展示树，但父子 State 仍然独立。
7. Incremental Writer 只观察并写文件，不驱动 Agent。
8. Trace 写入失败是观察层失败，不是 Agent 行为失败。
9. 未定价模型必须保留 Token 并显式标记，不能伪装成免费。
10. Schema、Writer、Reader、Fixture 和 Viewer 必须同步演进。

最后可以用一张问答表记住整章：

```text
运行中发生了什么？       Event Stream
哪些内容成为对话？       MessageEvent → transcript
怎样封装一次运行？       RunTrace
各操作持续了多久？       Span
某轮模型看到了什么？     ModelTurn
使用了多少资源？         TokenUsage → RunCost
怎样写到磁盘？           trajectory.v5 JSONL
怎样核对 Provider 边界？ raw sidecar
怎样在运行中观察？       IncrementalTraceWriter + Viewer
```

---

## 12. 从 Trace 到 Eval：如何用外部事实判断 Agent 是否真的完成任务

第 10 章说明 Goal Loop 为什么不能把模型的 `final` 直接当成目标完成；第 11 章说明一次运行如何形成可审计的 Trace。本章继续向外走一层，回答：

> 怎样在一批真实任务上运行 Agent，并用与 Agent 自我声明无关的评分事实判断效果？

最准确的概括不是“所有任务都交给官方评分器”，而是：

> 项目让 Agent 在真实或受控隔离环境中产生可验证产物，将 Agent 可见任务与评分私有数据分开，再根据 Benchmark 特性选择环境内评分、独立 Judge 或官方 Harness。

可以先记住这条端到端链路：

```text
Benchmark instance
  ├─ task_input()  → Agent 可见任务
  └─ eval_inputs() → 隐藏 rubric / 测试 / 评分信息
          ↓
Suite + Backend + Artifact Store
          ↓
Agent 在真实工作区行动
          ↓
result.json      trajectory.jsonl
任务产物          过程证据
    ↓                 ↓
评分器 / Judge       失败分析
    ↓
Eval Result
```

这里最重要的边界是：

```text
模型说“完成了”     = 候选结论
result.json 中有产物 = 可以进入评分
评分器确认通过       = 评测成功事实
trajectory.jsonl     = 解释过程，不自动等于分数
```

### 12.1 Unit Test、Smoke Run 和 Benchmark Eval 不是一回事

项目使用的反馈信号可以分成三层：

| 层次 | 回答的问题 | 示例 | 通常成本 |
| --- | --- | --- | ---: |
| Unit Test | 类型、协议和纯逻辑是否正确 | ToolCall/Result 配对、State 回放 | 低 |
| Smoke Run | 关键链路能否端到端跑通 | Fake Provider 调 Bash 并产生 Trace | 中低 |
| Benchmark Eval | Agent 能否在一批真实任务上完成目标 | SWE-bench、ProgramBench、Terminal-Bench | 高 |

三者不能互相替代。

```text
单元测试通过
≠ Agent 能解决真实 Issue

Fake Provider 跑通
≠ 真实模型在长任务上有效

某个 Benchmark 分数较高
≠ 所有内部协议都没有回归
```

例如 SWE-bench 并不评价最终回答是否“像修复说明”，而是评价实际补丁：

```text
Git 仓库 + Issue
  → Agent 阅读、修改并测试
  → 提取 model_patch
  → 在评分环境应用补丁
  → 运行 Benchmark 测试
  → resolved / unresolved
```

即使 AssistantMessage 写着：

```text
The issue has been fixed and all tests pass.
```

只要官方测试没有通过，这个实例仍然是 unresolved。

### 12.2 三个正交边界：Suite、Backend 与 Artifact Store

通用评测框架没有把 Benchmark、Docker 和文件传输写成一个巨大 Runner，而是拆成三类责任：

```text
Suite          = 评什么
Backend        = 在哪里运行
Artifact Store = 输入和产物放在哪里
```

#### Suite：定义任务语义

一个 Suite 的 host half 主要提供：

```python
class Suite(Protocol):
    name: str
    container_module: str

    def launch_spec(self, instance) -> LaunchSpec: ...
    def task_input(self, instance) -> dict: ...
    def eval_inputs(self, instance) -> Mapping | None: ...
```

它回答：

- 使用哪个镜像、工作目录和资源约束；
- 原始数据中哪些字段可以交给 Agent；
- 哪些评分私有字段走隔离通道；
- 容器内应加载哪段 suite-specific 任务逻辑。

Suite 还有一个随 wheel 发布的 container half，负责：

```text
build_task()     把清洗后的 instance 变成 Agent 任务
prepare()        可选的工作区准备
extract_result() 从工作区提取补丁、回答或代码包
evaluate()       可选的环境内评分
```

host half 和 container half 是部署边界两边的不同职责，不是重复实现。

#### Backend：定义运行地点

同一个 Suite 可以使用：

| Backend | 用途 |
| --- | --- |
| `LocalProcessBackend` | 本地快速调试，同进程运行 container half |
| `LocalDockerBackend` | 在本机隔离容器中运行 |
| `RemoteDockerBackend` | 由远程 Docker daemon 执行 |
| `FakeBackend` | 不启动真实 Agent，测试编排与 Store 契约 |

切换 Backend 应改变执行环境，而不改变 Benchmark 的任务含义。

#### Artifact Store：定义数据去哪里

Store 用统一的 `put/get` 协议传递输入、结果和实时 Trace。常见实现是：

```text
LocalDirStore  本机目录或 bind mount
HostHttpStore  Worker 能访问 Host 时通过 HTTP 传输
```

因此 Runner 不需要分别理解“任务输入传输”“结果上传”和“Trace sink”。对它而言，这些都只是不同 key 的 bytes。

### 12.3 用一个实例理解 Generic Runner 的数据流

假设原始 SWE-bench instance 是：

```json
{
  "instance_id": "repo__project-123",
  "problem_statement": "Fix parse_config when the file is empty.",
  "repo": "repo/project",
  "base_commit": "abc123",
  "patch": "<gold patch>",
  "test_patch": "<hidden tests>"
}
```

`run_suite_instance()` 的主流程可以简化成：

```text
1. Suite.launch_spec(instance)
   → 解析镜像、workdir、资源和网络策略

2. Suite.task_input(instance)
   → 删除 gold/private 字段
   → Store.put("input/instance.json")

3. Suite.eval_inputs(instance)
   → 若采用环境内评分，写入 "input/eval.json"
   → 若返回 None，表示稍后由独立 Judge 或官方 Harness 评分

4. Backend.run(RunSpec, Store binding)
   → container half 构造任务并运行 Agent

5. extract_result(workspace, instance)
   → 写出 "out/result.json"

6. Runtime 同时周期输出
   → "out/trajectory.jsonl"

7. Store.collect_outputs()
   → Host 获得产物和 Trace
```

标准实例目录是：

```text
<run_root>/<run_id>/<instance_id>/
├── input/
│   ├── instance.json
│   └── eval.json              # 仅需要环境内评分时存在
└── out/
    ├── result.json
    ├── trajectory.jsonl
    └── trajectory.jsonl.raw.jsonl
```

这几个文件回答不同问题：

| 文件 | 回答的问题 |
| --- | --- |
| `instance.json` | Agent 被允许解决什么任务 |
| `eval.json` | 评分器还需要哪些私有信息 |
| `result.json` | Agent 最终产生了什么可评分产物 |
| `trajectory.jsonl` | Agent 是怎样得到该产物的 |
| raw sidecar | 实际 Provider 请求/响应是什么 |

Runner 的终止状态也不能直接等同于 Benchmark 成功。容器正常退出只说明运行过程完成；`result.json` 中的补丁仍可能无法通过评分测试。

### 12.4 为什么必须隔离 Agent 输入与评分数据

同一个原始 instance 被拆成两条通道：

```text
原始 Benchmark row
├─ task_input(instance)
│    → input/instance.json
│    → Agent 可以读取
│
└─ eval_inputs(instance)
     → input/eval.json
     → 只交给 evaluate / grader
```

例如专业问答任务可能包含：

```json
{
  "prompt": "Explain the treatment decision.",
  "rubrics": ["must mention A", "must reject B"],
  "human_scores": [8, 3]
}
```

Agent 可见输入应只有 prompt；rubric 和参考分数只能进入 Judge。否则评测会退化成：

```text
Agent 读取评分标准或 gold
→ 复述目标答案
→ 获得高分
```

这测到的是数据泄漏能力，不是任务解决能力。

源码中还有一个明确的例外：`provider="oracle"` 的可信基础设施检查可以拿到未清洗记录，用参考信息验证环境和评分链路。Oracle 不是普通候选 Agent，也不能与真实模型分数混合报告。

### 12.5 三种评分拓扑

项目没有强迫所有 Benchmark 使用同一种 Judge。评分地点取决于任务性质。

#### 环境内 `evaluate()`

当 Suite 的 `eval_inputs()` 返回私有评分数据时，Generic Runner 会在任务环境中调用 container half 的 `evaluate()`，并把 verdict 合并进 `result.json`：

```text
Agent 产出 answer
  → extract_result()
  → evaluate(workspace, instance, context["eval"])
  → result.json = answer + score + rubric details
```

OneMillion-Bench 默认采用这种方式：候选模型回答专业问题，Judge 模型根据隐藏 rubrics 给出逐项判定和加权分数。

#### 后续独立 Judge Run

如果生成和评分需要不同 Agent、模型或运行配置，可以让候选 Run 先生成 `result.json`，再启动另一个普通 Suite Run 读取该结果：

```text
Candidate Run
  → candidate/result.json
  → Judge Suite task_input
  → Judge Run
  → judge/result.json
```

它仍然复用 Suite、Backend 和 Store，不需要把 Judge 逻辑塞进候选 Agent 的 State。

#### 官方 Harness / Verifier

有成熟官方评分器时，项目优先保留其权威语义。例如 SWE-bench：

```text
每实例 result.json["model_patch"]
  → 收集为官方 predictions.jsonl
  → 官方 SWE-bench Harness
  → resolved / unresolved
```

ProgramBench 则把 Agent 重建的工作区打包，再交给官方 evaluator 编译并运行分支测试。

Terminal-Bench 的集成边界不同：Harbor 自己管理任务环境、verifier、产物下载和 job 级聚合。SAL 提供安装式 Agent 和内部 Trace，但 Harbor 的 `result.json` 才是分数事实来源。

所以统一点不是“都使用官方 Harness”，而是：

```text
评分依据位于 Agent 自我声明之外
并且评分私有数据不进入普通 Agent 输入
```

### 12.6 `result.json` 与 `trajectory.jsonl` 为什么必须分开

第 11 章的 Trace 能回答：

- 模型看到了什么；
- 调用了哪些工具；
- 哪个命令失败；
- 子 Agent 做了什么；
- 消耗了多少 Token。

但 Trace 不自动回答“补丁是否通过官方测试”。相反，评分器可能只需要 `result.json` 中的一项产物：

```json
{
  "model_patch": "diff --git a/... b/..."
}
```

可以把两者理解成：

```text
result.json
= 交给评分器的答案卷

trajectory.jsonl
= 解释答题过程的监控录像
```

答案卷决定分数，录像帮助定位为什么得分或失败。录像中看到 Agent 自信地宣称完成，不能覆盖评分器的失败；评分器判定失败，也不应删除 Trace 中已经发生的工具、模型和成本事实。

这种分离还允许：

- 用新版评分器重新计算旧产物；
- 不重新运行昂贵 Agent 就做失败分类；
- 比较成功与失败实例的 Tool/Compression/Token 模式；
- 对同一个产物应用不同但显式版本化的分析指标。

### 12.7 当前仓库接入了哪些 Benchmark

需要区分“存在运行 Adapter”和“README 已发布最终成绩”。当前代码提供的主要接入包括：

| Benchmark | 主要测试能力 | 评分方式 |
| --- | --- | --- |
| SWE-bench Verified / Multilingual / Pro | 真实仓库的软件修复 | 官方 SWE-bench Harness；部分变体可做环境内等价评分 |
| ProgramBench | 观察黑盒程序并重建代码 | 官方 ProgramBench evaluator |
| OneMillion-Bench | 专业领域问答 | 隐藏 rubric + Judge 模型 |
| Terminal-Bench / Harbor | 隔离终端中的操作任务 | Harbor verifier 与官方 job 结果 |

ProgramBench 的典型任务不是阅读源代码后补全函数，而是：

```text
只提供文档和已编译 ./executable
  → Agent 运行程序观察行为
  → 在受控网络条件下重建实现
  → 打包整个 workspace
  → 官方 evaluator 验证行为是否一致
```

Adapter 存在说明项目具备运行和评分该类任务的代码路径，不等于仓库已经公布它的最终榜单成绩。

### 12.8 如何正确阅读 README 的三项公开结果

当前 README 发布了三项端到端结果：

| Benchmark | Agent 模型 | 项目分数 | 列出的 Baseline | 绝对差 | README 相对提升 | 成本/任务 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| SWE-bench Pro | GPT-5.4 xHigh | 63.20% | 59.10% | +4.10 个百分点 | +6.94% | $7.7823 |
| Terminal-Bench 2.1 | GPT-5.3-Codex xHigh | 77.53% | 64.70% | +12.83 个百分点 | +19.83% | $0.5667 |
| PostTrainBench | GPT-5.5 xHigh | 45.88% | 43.97% | +1.91 个百分点 | +4.34% | 未发布 |

必须区分绝对百分点和相对提升。以 SWE-bench Pro 为例：

```text
绝对差 = 63.20% - 59.10%
       = 4.10 个百分点

相对提升 = (63.20 - 59.10) / 59.10
         ≈ 6.94%
```

因此准确说法是：

> SWE-bench Pro 分数高 4.10 个百分点，相对 Baseline 提升约 6.94%。

不能说“提高了 6.94 个百分点”。

README 将这里的 Baseline 定义为相同模型、相同任务预算下的对照结果，目的是尽量分离“基础模型更强”和“Agent 方案更有效”。但这些数值链接到外部榜单；当前 checkout 没有随仓库提交与每项结果一一配对的 baseline artifacts。因此可以确认 README 的公开声明，不能仅凭本地文件独立核对所有对照运行参数。

### 12.9 三项结果分别能说到什么程度

#### SWE-bench Pro：63.20% resolved

这一指标表示通过官方判定的实例比例。README 说明它使用类似 ChainSWE 的 chained workflow，并增加 `task` 工具。仓库中的 chain runner 确实支持：

```text
bash / loop / pdr flavor
Task Tool
显式 chain state
handoff 或 summarize compression
context window 和 turn budget
完整 trajectory
官方评分入口
```

当前操作文档给出了默认的 SWE-bench Pro chain 配置，但 README 对应的 63.20% 没有在本 checkout 中附带逐实例 manifest、结果目录和完整参数快照。因此安全表述是：

> README 报告 GPT-5.4 xHigh 的 chained SAL 方案在 SWE-bench Pro 上达到 63.20%，比列出的 59.10% Baseline 高 4.10 个百分点。

不能据此说“Task Tool 单独贡献 4.10 个百分点”，因为没有对应的 on/off 消融证据。

#### Terminal-Bench 2.1：77.53%

Harbor 管理数据集、任务环境、verifier 和最终 job 结果。仓库同时提供 `bash` 与 `bash_task` 两个 Terminal-Bench 2.1 启动器，默认 xHigh、150 turns、10 个并发 trial、3 倍 timeout multiplier。

README 的成绩行没有把 77.53% 明确绑定到其中某个启动器，也没有提交对应 Harbor job 目录。因此安全表述是：

> README 报告 GPT-5.3-Codex xHigh 的 SAL 配置取得 77.53% 官方分数，比列出的 64.70% Baseline 高 12.83 个百分点。

不能把全部提升归因于子 Agent 委派。

#### PostTrainBench：45.88%

这里要区分两个模型角色：

```text
GPT-5.5 xHigh  = 执行后训练任务的 Agent 模型
Qwen3-4B-Base  = 被后训练并接受评估的目标模型
```

README 说明最终加权平均来自 AIME 2025、BFCL、GSM8K 和 HumanEval，并采用 PostTrainBench Lite 的 normalized reward 方法。

但当前仓库没有对应的本地 Suite、逐子任务结果、训练配置或成本产物。这一项在当前 checkout 中主要是 README 的发布声明，不能像 SWE-bench Adapter 那样从本地源码一路核对到运行入口。

### 12.10 Baseline、Git baseline、Fake 和 Oracle 不要混用

项目中至少有四个容易都被叫作“基线”的概念：

| 名称 | 真正含义 | 能否当成公开性能对照 |
| --- | --- | ---: |
| README performance Baseline | 相同模型与任务预算的外部对照成绩 | 是，但需核对实验配对 |
| pre-agent Git baseline | Agent 修改前的工作区状态，用来提取 diff | 否 |
| Fake Provider | 确定性测试 Runner、Store 和 Trace 链路 | 否 |
| Oracle run | 用 gold 验证环境和评分路径 | 否 |

例如 SWE-bench 文档中的：

```text
diff against the pre-agent baseline
```

只是：

```text
Agent 修改后 Git 状态 - Agent 修改前 Git 状态 = model_patch
```

它和结果表里的 59.10% 完全不是同一个概念。

### 12.11 当前证据能够证明什么，不能证明什么

当前仓库和 README 合起来可以支持以下结论：

- 项目不仅有教学 Runtime，也实现了真实 Benchmark Adapter；
- Suite、Backend 和 Store 将任务语义、运行地点和传输分开；
- Agent 输入与评分私有数据存在明确隔离通道；
- SWE-bench、ProgramBench 和 Harbor 保留外部/官方评分路径；
- README 发布的三项结果都高于表中 Baseline；
- `result.json` 与 Trace 能支持后续评分和失败分析。

但这些材料不能单独证明：

- Task Tool、compression、handoff 或 Workflow 各自贡献多少；
- 提升在多次随机运行中是否稳定；
- 差异是否具有统计显著性；
- 每种任务类型都获得相同提升；
- 当前方案优于排行榜中的所有 Agent；
- README 的每项结果都能从当前 checkout 完整独立复现。

原因是 `evals/out/*` 默认被 Git 忽略，当前工作区只保留布局说明，没有三项发布结果对应的完整运行目录。仍缺少的典型实验材料包括：

```text
精确运行 manifest 和代码版本
逐实例预测与官方评分结果
完整 Agent / model / budget 参数
重复运行次数、方差和置信区间
paired task 差异
成功/失败任务分类
组件 on/off 消融
统一成本明细
PostTrainBench 本地配置和子项分数
```

所以当前公开材料更接近“端到端结果摘要”，还不是“仓库内可完整复算的实验报告”。

### 12.12 怎样设计更有说服力的下一轮实验

如果要回答“某个设计是否有效”，最小可信实验不只是再跑一个总分，而应建立配对对照：

```text
同一任务集合
同一模型与 reasoning effort
同一 turn / Token / 时间预算
同一 Backend、镜像和评分器版本
唯一变量：Task Tool on/off
```

每个任务同时保存：

```text
candidate result.json
official eval result
trajectory.jsonl
model / prompt / flavor / budget manifest
Token 和美元成本
```

然后至少报告：

- 总分与绝对百分点差；
- paired win/loss/unchanged 数量；
- 多次运行的均值和波动；
- 成本变化；
- 从 Trace 得到的失败类型；
- 新增能力是否引入回归任务。

只有这样才能把：

```text
“整套配置在这个 Benchmark 上更好”
```

进一步收窄为：

```text
“在其他条件相同的情况下，这个组件带来了多少可重复改善”
```

### 12.13 最后用一条事实链记住评测系统

```text
Benchmark Dataset
  → Suite 分离 task_input 与 eval_inputs
  → Backend 在本地、容器或远程环境运行
  → Store 传递 instance、result 和 trajectory
  → Agent 生成可评分产物
  → Judge / Official Harness 产生评分事实
  → Trace 解释成功、失败和成本
  → 多实例结果聚合成 Benchmark 指标
```

对应问题是：

```text
评什么？             Suite
在哪里运行？         Backend
数据放在哪里？       Artifact Store
Agent 能看到什么？   task_input / instance.json
评分秘密放在哪里？   eval_inputs / eval.json
Agent 交了什么？     result.json
过程发生了什么？     trajectory.jsonl
是否真的完成？       evaluate / Judge / Official Harness
结果能证明什么？     由 manifest、重复实验和消融证据决定
```

---

## 13. 最后只记住这张责任图

```text
Content Block
  → 一条消息里具体有什么

Message
  → 谁向谁表达了什么，处于什么运行阶段

ToolCall / ToolResult
  → 哪个环境观察属于哪个模型行动

TokenUsage
  → 一次模型调用使用了多少资源

Event
  → 整个运行过程中实际发生了什么

State.events
  → 追加式保存完整运行事实

StateSnapshot
  → 从 Event 快速读取完整消息和活跃索引

完整历史
  → 系统曾经记录过什么

活跃上下文
  → 压缩后当前保留什么

ContextView
  → 某个 Agent 本轮允许模型看到什么

LLMMessage / LLMRequest
  → 如何用供应商无关语言表达本次模型调用

Provider Wire
  → 某家 API 最终接收了什么

LLMResponse
  → Provider 响应规范化后的调用级事实

raw
  → 实际跨过 Provider 边界的调试证据，不是核心控制协议

RunTrace
  → 把一次 State 的 Event 与 Message 封装成可观察运行

Span / ModelTurn / RunCost
  → 从事实账本派生时间、模型调用和成本视图

Trajectory v5 / raw sidecar
  → 持久化规范事件，并外置 Provider 调试证据

IncrementalTraceWriter / Viewer
  → 实时写入和展示观察结果，但不控制 Runtime

Suite
  → 定义任务语义、Agent 可见输入和评分私有输入

Backend / Artifact Store
  → 决定任务在哪里运行，以及输入和产物如何传递

result.json / trajectory.jsonl
  → 分别保存可评分产物与运行过程证据

Judge / Official Harness
  → 用 Agent 自我声明之外的事实产生评分结果

Builder / Flavor
  → 一个 Agent 具备哪些能力

Session / Toolset
  → 支撑这些能力的外部资源存活多久

Task Tool
  → 父模型如何动态委派一个独立子运行

StepResult / WorkflowResult
  → 多次独立运行如何保留输出和完整 State

Goal Loop
  → 模型 final 如何经过外部检查成为 complete

Workflow Facade
  → 如何统一调用入口，同时保留内部独立事实
```

整套设计最核心的思想是：

> 先用项目自己的不可变类型保存完整运行事实，再根据当前目的构建可重建的视图和边界投影。压缩、Provider 差异、Trace 和评测都不应反过来改写已经发生的领域事实。

---

## 14. 阅读源码时的对照路径

```text
scripts/run_bash_agent_demo.py
  → agents/starter.py
  → core.Agent.run()
  → core.run()
  → messages.py / protocols.py / state.py
  → context_view.py / compression/runtime.py
  → llm_agent.py / llm/bridge.py / llm/types.py
  → llm/adapters/*
  → tools
  → tools/task.py
  → workflow/*
  → agents/flavors.py
  → trace/run_trace.py
  → trace/spans.py / training.py
  → model_metadata.py
  → trace/live.py / Trace Viewer
  → evals/protocols.py / runner.py
  → evals/backends/* / stores/*
  → evals/<suite> host half
  → evals/suites/<suite> container half
  → official scorer / Judge
```

建议在源码中重点核对：

- `messages.py`：Content Block、Message、TokenUsage、合法放置校验；
- `protocols.py`：完整 Event 集合与事件字段；
- `state.py`：追加事件、Snapshot 投影和活跃索引；
- `core.py`：模型请求、模型响应、工具调度和停止顺序；
- `context_view.py`：可见性投影与 Token 估算；
- `compression/runtime.py`：压缩决策应用、工具对保护和活跃索引重指向；
- `llm/bridge.py`：Runtime Message 与 LLMMessage/AssistantMessage 的双向转换；
- `llm/types.py`：LLMMessage、LLMRequest、LLMResponse 和 StreamEvent；
- `llm/adapters/*`：OpenAI、Anthropic 等 Provider Wire 差异。
- `agents/starter.py`：Builder、AgentSession 和 Toolset 生命周期；
- `tools/task.py`：子 Agent 注册、独立 State、final 提取和子事件 sidecar；
- `workflow/base.py`：StepResult、WorkflowResult、`run_agent()` 和输出回退；
- `workflow/sequential.py`、`planner_executor.py`、`reflection.py`、`routing.py`、`parallel.py`、`pdr.py`：六种 Workflow 的控制与数据传递；
- `workflow/goal_loop.py`：同一 State 的 run/resume、外部检查和 GoalStatusEvent；
- `agents/flavors.py` 与 `workflow/trace.py`：Workflow Facade 和派生组合 Trace。
- `trace/run_trace.py`：RunTrace、Trajectory v5 Header、Event 序列化和 raw 外置；
- `trace/spans.py`：Span 配对、父子结构和子 Agent Span 合并；
- `trace/training.py`：ModelRequest 与 Assistant Message 配成 ModelTurn；
- `model_metadata.py`：主 Agent 与子 Agent usage 的递归聚合和价格投影；
- `trace/live.py`：增量 Writer、最终规范写入和 raw sidecar 生命周期；
- `tests/unit/test_trace_fixture_golden.py`、`test_live_trace.py`：完整演示轨迹和持久化契约。
- `src/simple_long_horizon_agent/evals/protocols.py`：Suite、Backend、Store、RunSpec 和固定 artifact key；
- `src/simple_long_horizon_agent/evals/runner.py`：清洗输入、隔离 eval data、启动 Backend 和收集产物；
- `evals/backends/*` 与 `evals/stores/*`：执行地点和 bytes 生命周期；
- `evals/swebench/suite.py`、`programbench/suite.py`、`onemillion/suite.py`：三种不同任务与评分拓扑；
- `evals/suites/*/container.py`：Agent 实际运行环境中的任务构造与结果提取；
- `evals/swebench/evaluate_predictions.py`、ProgramBench evaluator 和 Harbor result：外部评分事实来源；
- `runs/run_bench.py` 与 `runs/_benches/*`：统一 CLI、批量运行和评分入口。

阅读时始终用两条线检查自己是否理解：

```text
业务数据流：
Message 如何转成工具行动、环境观察和新 Message？

运行控制流：
Runtime 何时请求模型、执行工具、进入下一轮或停止？

组合控制流：
谁创建 Agent、谁托管资源、哪些运行共享或隔离 State、
输出怎样进入下一步、谁有权确认真正完成？

观察派生流：
哪些是不可变事件事实，哪些是 Span、ModelTurn、Cost 或 Viewer 派生结果，
持久化和观察失败是否会错误地反向影响 Runtime？

评测证据流：
Agent 看到了哪些输入、评分秘密在哪里、什么产物进入哪个评分器、
最终结论是本地可复算事实、README 发布声明，还是仍需消融验证的推断？
```

能同时说清这五条线，才是真正理解了这套设计。
