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

这里的 `0` 到 `15` 是事件追加到 `State.events` 后得到的顺序索引，不是事件类型编号。最前面的三条事件可以读成：

```text
MessageEvent(task)  → 默认 initializer 先建立初始 transcript
AgentStartEvent     → 调用者开始消费惰性的 Runtime 事件流
TurnStartEvent      → Agent 的第一轮模型决策即将开始
```

`AgentStart/AgentEnd` 包围整次运行；`TurnStart/TurnEnd` 包围其中一次“模型决策 + 可选工具处理”。第一轮产生工具调用并写入结果，第二轮模型才能读取该结果并生成 final，因此这个示例有一个 Agent run、两个 Turn 和两次主模型调用。生命周期字段的完整定义见 [`03-domain-model-and-data-flow.md`](docs/design/03-domain-model-and-data-flow.md#72-agentturn-与-model-call-是三种粒度)，对应的 Runtime 控制顺序与特殊停止情况见 [`04-agent-runtime.md`](docs/design/04-agent-runtime.md#41-为什么-task-message-排在-agentstart-前面)。

### 1.1 `agent.run()` 到底怎样触发一次运行

最小调用方式是：

```python
agent = Agent(name="writer", generate=generate)

state, events = agent.run(
    "读取 README.md 并总结主要内容",
    max_turns=3,
)

# 迭代 events 才会拉动 Runtime。
for event in events:
    print(event)

# 消费完后，从同一个 State 读取事实和 transcript。
print(state.task)
print(state.messages)
print(state.events)
```

这段代码必须分成两个时刻理解：

```text
调用 agent.run(task)
  → 选择默认或自定义 init_state
  → 创建 State
  → 写入初始 task Message（默认行为）
  → 返回 (state, 惰性 events)

消费 events
  → AgentStartEvent
  → TurnStartEvent
  → ModelRequestEvent / 模型调用
  → ModelResponseEvent / Assistant MessageEvent
  → 可选 ToolExecution 与 tool_result MessageEvent
  → AgentEndEvent
```

所以只写下面两行时，通常还没有发生模型调用：

```python
state, events = agent.run("读取 README.md")
# events 尚未被消费，Runtime 还没有推进。
```

`for event in events` 和 `list(events)` 都会推进同一个生成器；前者适合实时打印或增量写 Trace，后者适合只关心最终 State。每个事件在 `yield` 前已经追加到 State，因此调用者不需要再维护第二份事件历史。这个“入口调用”和“事件消费”之间的边界，是理解 `AgentStartEvent` 为什么排在初始 `MessageEvent` 后面的关键。

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

### 4.6 details：同一结果包里的本地详情

工具执行得到的 `ToolResult` 不只有给模型看的 `content`，还可以携带不自动进入模型请求的 `details`：

```text
content  → ToolResultBlock → 下一轮模型
details  → Message sidecar → Trace / Viewer / 本地模块
```

例如 Read 工具截断大文件时，模型需要看到已返回的内容和续读提示；本地观察层则可能需要结构化的文件路径、总行数和截断原因。Runtime 会使用调用 ID 把两者重新放进同一个结果包：

```python
UserMessage(
    kind="tool_result",
    content=(
        ToolResultBlock(
            tool_call_id="read_1",
            tool_name="read",
            content=(
                TextBlock(
                    "...前 200 行...\n\n"
                    "[Showing lines 1-200 of 12000. "
                    "Use offset=201 to continue.]"
                ),
            ),
        ),
    ),
    sidecar={
        "details": {
            "read_1": {
                "path": "large.log",
                "total_lines": 12000,
                "start_line": 1,
                "truncation": {"truncated": True, "truncated_by": "lines"},
            },
        },
    },
)
```

为什么 `details` 下面还要有一层 `read_1`？因为一个结果消息可以同时包含 `read_1`、`search_1` 和 `task_1`，甚至可以包含两个同名 Read 调用。`tool_call_id` 是唯一能同时连接 ToolCall、ToolResultBlock 和本地 details 的因果身份；工具名、列表位置和并发完成顺序都不够可靠。

需要特别区分：`details` 是“默认不让模型看”，不是“不会保存”或“可以放秘密”。Trace 和 Viewer 仍可能读取它。完整的数据流、并行示例和边界约束见 [`06-tools-and-integrations.md`](docs/design/06-tools-and-integrations.md#31-content-与-details-是两条不同通道)。

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

#### “新增工具结果约 250 Token”是怎样算出的

工具本身不会报告“这条 ToolResult 占多少 Token”，Provider 也只会在下一次模型调用结束后报告整次请求 usage。因此，在新 ToolResult 已写入 State、下一次可信 usage 尚未返回的间隙，Runtime 使用字符启发式估算：

```text
estimated_tokens = ceil(estimated_model_visible_chars / 3.5)
```

假设新增结果为：

```python
UserMessage(
    sender="tool",
    target="writer",
    kind="tool_result",
    content=(
        ToolResultBlock(
            tool_call_id="call_1",
            tool_name="bash",
            content=(TextBlock("...840 个字符的命令输出..."),),
        ),
    ),
)
```

当前估算器会计算模型可见正文及少量结构元数据：

| 组成 | 字符数 |
| --- | ---: |
| ToolResultBlock 内部文本 | 840 |
| role=`user` | 4 |
| sender=`tool` | 4 |
| target=`writer` | 6 |
| kind=`tool_result` | 11 |
| tool_call_id=`call_1` | 6 |
| tool_name=`bash` | 4 |
| 合计 | 875 |

所以：

```text
ceil(875 / 3.5) = 250 Token
```

这里的 `250` 只属于这个模拟结果，不是每次工具调用的固定成本。输出越长、结果块越多，估算越大。一条消息包含多个 ToolResultBlock 时，会递归累计每个结果的模型可见文本或图片，并为每个结果增加 call id 和工具名长度。

不参与估算的内容包括：

- `ToolResult.details`；
- Message `sidecar` 中的 details、raw、compression 等本地证据；
- ToolExecution Event 和工具内部日志，除非它们另行成为模型可见 Message content。

图片没有可直接使用的文本长度，当前每个 ImageBlock 按 `7373` 个等价字符保守估算，单张图片正文约为：

```text
ceil(7373 / 3.5) = 2107 Token
```

消息路由字段等少量元数据还会在此基础上增加几个 Token。

#### 可靠基线与新增尾部怎样相加

若最近一次可信调用报告：

```text
input_tokens = 600
output_tokens = 100
cache_read_tokens = 0
cache_write_tokens = 0
context_tokens = 700
```

随后新增上面的工具结果，下一次请求前估算为：

```text
当前上下文 ≈ 最近可信前缀 700 + 新增 ToolResult 250 = 950
```

这表示 `700` 已经覆盖到产生该 AssistantMessage 为止的整个请求及输出，系统只估算它后面的消息，不能再把旧 task、Assistant 输出重复相加。下一次 Provider 返回新 usage 后，`950` 会被新的调用级事实替代。

压缩发生后则分两种阶段：

```text
压缩刚完成、尚无压缩后的可信 Assistant usage：
  旧基线失效
  task 80 + summary 180 + recent 120 + tool_result 300 ≈ 680

压缩后已经完成一次模型调用并取得可信 context_tokens=700：
  700 成为新前缀基线
  再新增 ToolResult 250 → 700 + 250 ≈ 950
```

第一种情况是对当前活跃消息逐条估算；第二种情况是“Provider 基线 + 基线后的短尾部估算”。字符比例对中文、代码、JSON 和日志都可能有误差，所以这些数字只用于预算判断，并由 safety buffer 吸收偏差，不能当作精确计费数据。

压缩前后的具体消息索引在第 7 章完整演示；稳定的估算契约见 [`07-context-and-long-horizon.md`](docs/design/07-context-and-long-horizon.md#31-新增工具结果怎样估算)。

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

#### 为什么 task 还要再写成 Message

看起来这里重复了两次：

```python
state = State(task="读取 README.md")
state.send("task", "user", "writer", "读取 README.md")
```

但它们服务于不同的问题：

```text
State.task
  回答：这份 State 最初是用什么运行输入创建的？
  用于：运行身份、RunTrace task/Header、组合 Trace 等运行级读取

UserMessage(kind="task")
  回答：模型对话中应该看到什么任务要求？
  进入：MessageEvent → Snapshot → active context
       → ContextView → LLMRequest
```

可以把 `Agent.run(task)` 想成在初始化时分叉：

```text
task="读取 README.md"
├── State.task
│   └── 运行级原始输入，不自动进入模型
└── UserMessage(kind="task")
    └── transcript 中的模型可见任务锚点
```

这不是“一份是真相、另一份是缓存”。`State.task` 与 task Message 都是事实，只是事实粒度不同：前者属于整份运行，后者属于可回放对话。Snapshot 才是可以从 Event 重建的缓存。

当前代码中，`run_trace_from_state()` 会直接读取 `state.task` 生成 RunTrace 的 task 字段；模型调用不会读取 `State.task`，而是沿 Message/ContextView 路径取得 task Message。Workflow 的 `StepResult.task` 与 Goal Loop 的 `objective` 通常由各自调用参数保存，不能笼统写成所有上层模块都直接读取 `State.task`。

续接运行时差异更明显：

```python
state, events = agent.run("读取 README.md")
# 消费 events 后：State.task 和第一条 task Message 都是原任务

state, events = agent.resume(state, "继续检查 StateSnapshot")
# State.task 不变，只新增第二条 kind="task" Message
```

最终形状是：

```text
State.task = "读取 README.md"

transcript:
0 UserMessage(kind="task", content="读取 README.md")
...
N UserMessage(kind="task", content="继续检查 StateSnapshot")
```

如果只保留 `State.task`，模型上下文中没有任务消息；如果只保留 task Message，RunTrace 等运行级消费者就要从可能包含多次 resume、路由和压缩的 transcript 中猜最初输入。完整契约见 [`03-domain-model-and-data-flow.md`](docs/design/03-domain-model-and-data-flow.md#81-为什么-task-还要写成-message)。

### 6.3.1 为什么 State(task="...") 不自动创建任务消息

直接写：

```python
state = State(task="分析项目")
```

此时：

```python
state.task == "分析项目"
state.events == []
state.messages == []
```

模型对话仍然是空的。因为 `State` 只是事实容器，它不知道任务应该发给哪个 Agent、由谁发送，也不知道是否要先加入 Skills 菜单、Runtime 说明或子 Agent context。由初始化器决定初始 transcript：

```python
def init_state(agent, task):
    state = State(task=task)
    state.send(
        kind="system",
        sender="runtime",
        target=agent.name,
        content="Available skills: docs-sync, ...",
    )
    state.send(
        kind="task",
        sender="user",
        target=agent.name,
        content=task,
    )
    return state
```

这个初始化结果的消息顺序是：

```text
RuntimeMessage(kind="system")  # 运行时指导
UserMessage(kind="task")       # 模型真正要执行的任务
```

如果自定义 initializer 只返回 `State(task=task)`，Runtime 不会自动补写 task Message。除非这是一个明确不需要普通任务消息的特殊流程，否则模型第一轮可能只能收到固定 system prompt，看不到用户任务。记忆为：

```text
State 构造器      → 创建事实容器
初始化器          → 决定初始 transcript
ContextView/Bridge → 把 transcript 投影给模型
```

### 6.3.2 默认初始化器、自定义初始化器与 `resume()`

`Agent.run()` 的初始化选择在一个地方完成：如果 Agent 配置了 `init_state`，就调用它；否则使用默认初始化器。默认实现可以近似写成：

```python
def _default_init_state(agent, task):
    state = State(task=task)
    state.send("task", "user", agent.name, task)
    return state
```

这里的 `state.send(...)` 同时完成两件事：构造模型可见的 `UserMessage(kind="task")`，并通过 `MessageEvent` 把它追加到 State。`State(task=task)` 只保存运行级输入，不知道消息应该发给谁，也不知道是否需要先注入 Skills 或其他上下文。

自定义 initializer 的类型约定是 `init_state(agent, task) -> State`。它必须返回一个已经符合本次运行需要的初始 State，例如：

```python
def init_state(agent, task):
    state = State(task=task)
    state.send(
        kind="system",
        sender="runtime",
        target=agent.name,
        content="可用能力：read_file、search、bash",
    )
    state.send(
        kind="task",
        sender="user",
        target=agent.name,
        content=task,
    )
    return state

agent = Agent(
    name="writer",
    generate=generate,
    init_state=init_state,
)
```

因此，Skills Agent 并没有另一套隐藏循环；它只是通过 initializer 在 `agent.run(task)` 初始化阶段把菜单或技能正文写入普通 Message。相反，下面的 initializer 是不完整的：

```python
def incomplete_init(agent, task):
    return State(task=task)
```

它返回的 State 中 `events` 和 `messages` 都为空，核心 Runtime 不会自动补写 task Message。除非流程明确不需要普通任务消息，否则模型第一轮可能只能看到固定 system prompt，看不到用户任务。

运行完成后，`resume(state, followup)` 会复用原 State，而不是创建副本：

```python
state, events = agent.run("读取 README.md")
for _ in events:
    pass

state, events = agent.resume(state, "继续检查 StateSnapshot")
for _ in events:
    pass
```

`resume()` 会追加一条新的 `UserMessage(kind="task")`，但不改写 `State.task`：

```text
State.task = "读取 README.md"
transcript task #1 = "读取 README.md"
transcript task #2 = "继续检查 StateSnapshot"
```

所以 `run()` 表示“从任务创建新 State 并开始一次运行”，`resume()` 表示“在同一事实账本上追加输入并继续运行”。恢复时应保持 Agent name 一致；跨进程恢复还需要调用者序列化并重建 State 与外部资源。

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

### 7.1 `transcript` 是什么

`transcript` 可以理解为 Agent 运行中的“对话记录”。它通常由 `MessageEvent` 投影得到，包含任务、Assistant 消息和工具结果：

```text
Event Stream = 完整运行账本
Transcript   = MessageEvent 提取出的对话记录
ContextView  = 本轮模型被允许看到的消息投影
```

例如，`AgentStartEvent`、`ModelRequestEvent`、`ToolExecutionStartEvent` 和 `ToolExecutionEndEvent` 都是运行事实，但不会自动成为 transcript；`MessageEvent(task)`、Assistant 的 ToolCall 消息和 ToolResult 消息才会进入对话记录。压缩或可见性过滤可能改变本轮 ContextView，却不会删除完整 transcript。

### 7.2 三层投影各自回答什么

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

### 7.3 用一组索引完整演示压缩

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

### 7.4 为什么活跃索引不一定递增

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

### 7.5 ContextView 只做可见性投影

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

### 7.6 压缩与隐藏不能混用

| 机制 | 回答的问题 | 对历史的影响 |
| --- | --- | --- |
| Compression | 内容太长，用什么替代旧内容 | 追加替代消息和压缩 Event，改变活跃索引，不删除原历史 |
| Visibility | 这种 kind 从语义上是否允许模型看到 | 只影响本轮 ContextView，不生成摘要，不改变活跃索引 |

如果希望 `task` 在压缩时保留原文，应该使用压缩策略的 `preserve_kinds`。如果把 `task` 放进 `model_invisible_kinds`，效果是模型根本看不到任务，而不是“保护任务不被压缩”。

当前压缩 Runtime 在把消息交给压缩策略前，也会排除 `model_invisible_kinds`。这是一个执行细节，不改变两者的语义边界：一个管上下文大小，一个管模型可见性。

#### 用同一组索引区分两种机制

假设运行中追加了以下消息：

```text
0 task: 请分析项目
1 assistant step: 请求读取 README
2 tool_result: README 内容
3 assistant step: 请求读取 architecture.md
4 tool_result: architecture.md 内容
5 assistant message: 当前阶段总结
6 user message: 继续分析最新部分
```

如果上下文超出预算，Compression 可以追加一条摘要 7，并将活跃视图重指向：

```text
完整 messages          = [0, 1, 2, 3, 4, 5, 6, 7]
active_context_indices = [0, 7, 6]
```

消息 1～5 没有被删除，仍可被 Trace、Recall 和审计读取；它们只是退出了当前活跃上下文。摘要 7 是新的 MessageEvent，压缩动作还会由 ContextCompressionEvent 记录。

如果不压缩，只配置可见性：

```python
ContextPolicy(model_invisible_kinds=("task",))
```

State 中仍然是同一组 `messages` 和 `active_context_indices`，但 ContextView 在构建本轮请求时过滤掉索引 0：

```text
模型实际看到 = [7 summary, 6 recent message]
```

此操作不会删除 task，不会改变活跃索引，不会生成摘要，也不会追加 ContextCompressionEvent。它只是让 task 不进入本次 LLMRequest。

因此两个配置的含义必须分开：

```text
preserve_kinds=("task", "system", "summary", "context")
  → 压缩时不要把这些 kind 替代掉

model_invisible_kinds=("task",)
  → 构建本轮模型输入时不要发送这些 kind
```

前者不保证消息一定对模型可见，后者也不表示消息受到压缩保护。当前实现的顺序是：压缩 Runtime 先从活跃消息中排除不可见 kind，再把剩余候选交给策略；这是执行顺序，不是把两种策略合并成一个概念。

#### 什么时候会故意隐藏 `task`

普通用户任务不应隐藏。默认的 `model_invisible_kinds` 是空元组，因此默认 `task` 会进入模型上下文。只有当 `task` 不是模型真正要执行的指令，或调用者明确进行可见性实验时，才可能配置：

```python
ContextPolicy(model_invisible_kinds=("task",))
```

常见例外包括：

| 场景 | 被隐藏的内容 | 目的 |
| --- | --- | --- |
| 运行元数据 | 评测编号、内部路径、调度标签 | 保留运行身份，但不把内部标签暴露给模型 |
| 子 Agent 委派 | 父 Agent 的宽泛总任务 | 让子 Agent 只处理明确的子任务 |
| 可见性实验 | 原始 task Message | 测试模型是否依赖该消息 |
| 特殊 Workflow/facade | 外层编排目标 | 只向模型提供当前阶段的 context 和任务 |

例如：

```text
State.task = "评测 case-17：参考答案位于私有目录"

RuntimeMessage(kind="context")
  请分析工作区中的测试失败。

UserMessage(kind="message")
  找出失败原因并提出修复方案。
```

此时隐藏原始 `task` 可能是有意的，但更清晰的设计是：在初始化阶段就把运行级元数据和模型任务分开，而不是把完整内部内容写入 task Message 后再用 Visibility 遮住。子 Agent 同样应优先使用“独立 State + 正确子任务”，而不是复制父任务后再隐藏。

还要注意：`model_invisible_kinds` 不是安全删除或访问控制。被隐藏的消息仍保存在 State、Trace 和可能的 Viewer 中；如果内容敏感，仍需在上游脱敏并限制轨迹访问。

### 7.7 压缩策略只做决定，公共 Runtime 负责安全执行

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

### 7.8 用同一组消息比较三种摘要策略

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

把这几类信息放回实际对象中，可以更清楚地看到各自边界。当前 SummarizeStrategy 的 replacement 是 `UserMessage`，不是 `RuntimeMessage`：

```python
UserMessage(
    kind="summary",
    sender="runtime",
    target="writer",
    content=(
        TextBlock(
            "[This session continues ...]\n\n"
            "此前已确认 State.events 是事实来源；"
            "下一步检查 ContextView。\n\n"
        ),
    ),
    sidecar={
        "compression": {
            "compressor": "compressor",
            "model": "compressor-model",
            "usage": {
                "input_tokens": 8000,
                "output_tokens": 300,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            },
        },
        "raw": {"request": {...}, "response": {...}},
    },
)
```

可以按三个问题记忆：

```text
摘要写了什么、下一轮模型读什么
  → summary Message.content

谁调用了压缩模型、实际模型和 usage 是什么
  → ModelRequestEvent / ModelResponseEvent
  → summary sidecar.compression 和 raw 保留就近调试证据

哪些消息退出活跃视图、摘要插到哪里
  → ContextCompressionEvent
```

对应的压缩动作可能是：

```python
ContextCompressionEvent(
    agent="writer",
    compressed_message_indices=[1, 2, 3, 4],
    summary_message_index=8,
    active_context_indices=[0, 8, 5, 6, 7],
    before_tokens=9000,
    after_tokens=1600,
    strategy="summarize",
    start_elapsed=12.4,
)
```

它表达索引替换和大小变化，不重复摘要正文。成本聚合使用 compressor 的 `ModelResponseEvent`，不能因为 sidecar 也保存 usage 就把同一次调用计算两遍。真实事件顺序是：

```text
ModelRequestEvent(compressor)
  → ModelResponseEvent(compressor)
  → MessageEvent(summary)
  → ContextCompressionEvent
```

##### 谁生成摘要，谁应用压缩

这里最容易混淆的是：`compressor` 生成了摘要文本，但它没有独立完成整个压缩动作。四个角色的边界是：

```text
compressor Agent / Provider
  → 理解旧消息，生成“摘要写了什么”

SummarizeStrategy
  → 选择“压缩哪些消息”
  → 准备 compressor 请求
  → 构造 CompressionDecision

compression.runtime
  → 执行 decision
  → 追加摘要 MessageEvent
  → 更新 active_context_indices
  → 生成 ContextCompressionEvent

State
  → 追加全部 Event
  → 统一补 index、elapsed、UUID
```

实际调用链是：

```text
core.run()
  → maybe_compress_context()
  → SummarizeStrategy
  → compressor.generate(compressor_messages)
  → CompressionDecision
  → _apply_decision()
  → State.events / StateSnapshot
```

假设压缩前活跃消息是：

```text
[0 task, 1 old call, 2 old result, 3 old call,
 4 old result, 5 old note, 6 recent, 7 recent]
```

默认保护 `task/system/summary/context`，保留最近两条普通消息后，Strategy 可能选择：

```text
compress_indices = [1, 2, 3, 4, 5]
```

它把这些消息和一条“请整理 Goal、Done、State、Facts、Open、Next”的指令交给 compressor。模型返回摘要文本后，Strategy 构造：

```python
CompressionDecision(
    compress_indices=(1, 2, 3, 4, 5),
    replacement=summary_message,
    label="summarize",
    trace_events=(request_event, response_event),
)
```

此时 `replacement`、`trace_events` 和 `CompressionDecision` 仍只是内存中的对象，还没有进入主 State。Runtime 应用它时才按以下顺序追加：

```text
1. ModelRequestEvent(agent="compressor")
2. ModelResponseEvent(agent="compressor", usage=...)
3. MessageEvent(UserMessage(kind="summary"))
4. ContextCompressionEvent(agent="writer", strategy="summarize")
```

因此需要区分：

| 事实 | 对象构造者 | 正式写入者 |
| --- | --- | --- |
| compressor 请求/响应事件 | `SummarizeStrategy` | `_apply_decision()` |
| 摘要消息事件 | `_apply_decision()` | `_apply_decision()` |
| 活跃索引变化事件 | `_apply_decision()` | `_apply_decision()` |
| index、elapsed、UUID | 调用者不决定 | `State.record_event_at()` |

`ContextCompressionEvent` 不能由 compressor 生成：compressor 不知道主 State 当前的活跃索引、摘要应插入的位置、压缩前后 Token，也不知道这次动作属于哪个主 Agent。

当前压缩器调用的是 `self.compressor.generate(...)`，不是 `run(compressor, compressor_state)`。所以它不会产生独立的 `AgentStartEvent`、`TurnStartEvent`、工具生命周期或 `AgentEndEvent`，只记录这次内部模型访问对应的 ModelRequest/Response Event。Trace 可以把这些事件派生为 compression Span，但 Span 仍是观察视图，不能反过来修改 Runtime。

如果把 `summary`、`compressed_indices`、`active_indices` 和 `should_apply` 全塞进 sidecar，正常 Runtime 就必须从附加字典恢复控制语义，sidecar 会成为第二套 Message/Event 协议。简化判断是：模型可见摘要进 `content`，运行投影变化进 Event，非正文的局部生成证据才进 sidecar。更完整的契约和非模型压缩对照见 [`07-context-and-long-horizon.md`](docs/design/07-context-and-long-horizon.md#52-模型摘要的三层事实)。

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

这里的 10、11 是 Message 索引，不是 Event 索引。一次 compact 工具调用还会产生 `ToolExecutionStartEvent`、`ToolExecutionEndEvent` 等执行事件，但它们不会投影到 `state.messages`，因此不占用 Message 索引。

compact 工具执行时不会立即改变 State，只在 ToolResult details 中写入申请：

```python
{
    "compact_request": {
        "summary": "...",
        "keep_recent": 2,
    }
}
```

完整的当前回合仍然是一次普通工具交互：

```text
AssistantMessage(kind="step", ToolCallBlock(name="compact"))
  → ToolExecutionStartEvent
  → ToolExecutionEndEvent
  → UserMessage(kind="tool_result")
       sidecar.details[call_id].compact_request = {...}
```

工具在调度线程中执行，不能直接修改共享 State。若它完成后立即压缩，而同一回合的其他并行工具仍未返回，就可能把 ToolCall 与 ToolResult 拆开，或让一组工具结果跨越压缩边界。因此它只提交申请，结果包写入完成后再等待下一轮的统一安全点。

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

完整历史和活跃上下文分别是：

```text
完整 messages          = [0, 1, 2, ..., 10, 11, 12]
active_context_indices = [0, 12, 10, 11]
```

这表示：旧工作由主 Agent 自己写的 working memory 替代，而 compact 调用和确认结果暂时保留原文。摘要 12 最后追加，所以物理索引最高；但它在逻辑上替代消息 1～9，因此被插回消息 10、11 前面。Message index 回答“什么时候写入完整历史”，active context 顺序回答“模型应该按什么顺序阅读”。

### 7.9 为什么 Agent compact 要等到下一轮开始

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

### 7.10 high-water mark 如何保证 compact 请求只应用一次

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

例如请求 11 本轮无法折叠，之后主 Agent 又追加消息 13：

```text
request_index = 11
max(active_indices) = 13
```

请求 11 从此失效，而不是排队等待以后执行。Agent compact 的语义因此是“立即下一轮尝试；成功则只消费一次；无法执行则丢弃”，避免在旧状态下写出的摘要覆盖后来出现的新事实。

最后与 `SummarizeStrategy` 对照：

```text
SummarizeStrategy
  旧消息 → compressor 模型 → ModelRequest/Response Event
  → summary Message → ContextCompressionEvent

AgentCompactStrategy
  主 Agent 的 compact 参数 → ToolResult.details.compact_request
  → 下一轮 Strategy → summary Message → ContextCompressionEvent
```

两者最终都由 `compression.runtime` 应用 `CompressionDecision`，并由 State 盖章保存；区别只在摘要文本的来源以及是否增加一次 compressor 模型调用。

### 7.11 TieredStrategy：每次检查采用首个可执行阶段

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

#### 用 5200 Token 的运行区分 Stage 与 Decision

假设第 N 轮请求前：

```text
active context ≈ 5200 tokens

0 task
1 assistant read call
2 read result
3 assistant search call
4 search result
5 普通分析
6 recent
7 recent
```

按优先级检查：

```text
AgentCompact
  → 没有最新 compact_request
  → 返回空，继续

ToolCompact
  → 5200 > 4000，且存在旧工具交换 1～4
  → 返回 CompressionDecision(label="tool-compact")
  → TieredStrategy 停止

Summarize
  → 本次不会调用
```

Runtime 应用 ToolCompact 后，即使当前估算仍有 4300 Token，也会进入第 N 轮模型请求：

```text
MessageEvent(tool summary)
  → ContextCompressionEvent(strategy="tool-compact")
  → build_context_view()
  → ModelRequestEvent
```

第 N+1 轮开始前才重新从第一个阶段检查。若 AgentCompact 仍无申请，ToolCompact 已没有旧工具对，而上下文仍超过 4000，Summarize 才会命中并调用 compressor。因此“先廉价折叠，再模型摘要”通常是逐轮降级，不是在同一次检查中把所有算法执行一遍。

这种单阶段选择有三个直接效果：一次请求前不会突然进行多种昂贵操作；每次上下文变化更容易预测；Trace 能清楚归因本轮采用的压缩机制。代价是一次压缩不保证立即降到阈值以下。

同一阶段返回多个 Decision 是另一件事。假设受保护的 task 把旧消息分隔开：

```text
0 old A
1 old B
2 task（受保护）
3 old C
4 old D
5 recent E
6 recent F
```

SummarizeStrategy 可以一次返回：

```text
Decision A: compress [0, 1] → summary 7
Decision B: compress [3, 4] → summary 8
```

Runtime 在同一次检查中依次应用 A、B，最终活跃顺序可能是：

```text
[7, 2, 8, 5, 6]
```

它没有在 A 与 B 之间重新从 AgentCompact 或 ToolCompact 开始选择。完整心智模型是：

```text
每轮请求前
  → TieredStrategy 按顺序选择首个非空 Stage
  → 停止检查后续 Stage
  → Runtime 依次应用该 Stage 返回的全部 Decision
  → 追加 replacement Message 和 ContextCompressionEvent
  → 构建 ContextView
  → 调用主模型
```

一句话：TieredStrategy 决定“本轮采用哪一种压缩方法”；CompressionDecision 决定“这种方法具体替换哪些消息”。

### 7.12 如何选择策略

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

#### 工具声明和执行为什么必须分开

假设 Runtime 注册了一个可执行工具：

```python
AgentTool(
    name="read",
    description="读取指定文件",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    execute=read_file,
    execution_mode="parallel",
    timeout_seconds=30,
)
```

它混合了两种信息：

```text
模型需要知道：name、description、parameters
  → 有什么工具、工具做什么、应该传什么参数

Runtime 才需要：execute、execution_mode、timeout_seconds
  → 调哪个本地函数、是否并行、单次等待多久
```

Bridge 投影给模型时只保留声明：

```python
LLMTool(
    name="read",
    description="读取指定文件",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
)
```

`execute` 是本地 Python callable，不能序列化进 HTTP，也不是 Provider 的权限。模型只能产生：

```python
ToolCallBlock(
    id="call-1",
    name="read",
    arguments={"path": "README.md"},
)
```

随后 Runtime 在本地注册表中按 `name` 查找 `AgentTool`，调用其 `execute`，再把结果包装成 `ToolResultBlock(tool_call_id="call-1")`。所以：

```text
LLMTool
  = 告诉模型“可以请求什么”

AgentTool.execute
  = Runtime 决定“请求如何在本地执行”
```

#### LLMRequest 和 Provider Wire 的完整边界

Runtime 先形成统一请求，仍不包含某家 SDK 的实际 JSON：

```python
LLMRequest(
    provider=provider,
    system_prompt="你是代码架构分析助手。",
    messages=[LLMMessage(role="user", content=(TextBlock("请分析 README.md"),))],
    tools=[read_llm_tool],
    reasoning="high",
    timeout_seconds=600,
    extra={},
)
```

Adapter 再翻译差异：同一工具参数 Schema 在 OpenAI Chat Wire 中通常位于 `function.parameters`，在 Anthropic Messages Wire 中位于 `input_schema`；固定 system prompt 可以成为首条 system message，也可以是顶层 `system` 字段；`reasoning="high"` 则翻译为各 Provider 自己的 reasoning/thinking 配置。

响应方向也先统一再执行：OpenAI `tool_calls[].function` 和 Anthropic `content[].type="tool_use"` 都规范化为同一个 `ToolCallBlock`。Provider 从始至终只收到工具声明，不会拿到本地 `execute`；工具执行权始终留在 Runtime。

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

### 8.3 缓存锚点：给 Provider 的可复用前缀提示

长任务中，每次模型请求经常重复携带一段稳定历史：

```text
system prompt + task + 已完成的代码分析 + 新增内容
                            ↑
                       稳定前缀
```

运行时可以在某条 `LLMMessage` 上携带供应商命名空间提示：

```python
LLMMessage(
    role="assistant",
    content="前面的代码分析已经完成……",
    extra={"anthropic.cache_breakpoint": True},
)
```

它不是模型需要理解的文本，也不是 State 中新增的 Message。数据流是：

```text
Message.sidecar["extra"]
    → Bridge 提升为 LLMMessage.extra
    → Anthropic Adapter 读取自己的命名空间
    → 最后一个 wire block 增加 cache_control
```

Anthropic 侧可能得到：

```json
{
  "type": "text",
  "text": "前面的代码分析已经完成……",
  "cache_control": {"type": "ephemeral"}
}
```

含义是“允许 Provider 把这里作为临时 Prompt Cache 边界”。例如：

```text
请求 1：system + task + 历史分析
请求 2：system + task + 历史分析 + 新工具结果
请求 3：system + task + 历史分析 + 新工具结果 + 新问题
```

如果 Provider 支持并实际命中，后续请求可能复用 `历史分析` 之前的输入，减少重复处理、延迟或成本。这里的“可能”很重要：锚点是请求提示，不是命中保证；缓存的写入、读取、过期和计费由 Provider 决定，项目只能从规范化的 `cache_write_tokens` 和 `cache_read_tokens` 判断发生了什么。

换成 OpenAI Adapter 时，`anthropic.cache_breakpoint` 通常被忽略，正文仍然发送，只是不生成 Anthropic 的 `cache_control`。因此它体现的是“核心协议统一、供应商差异下沉 Adapter”，而不是把 Anthropic 字段扩散到所有 Provider。

缓存锚点也不等于：

```text
Prompt Cache   = Provider 临时复用输入前缀
Compression    = Runtime 改变活跃上下文索引
Memory        = 跨请求或跨运行保存和取回信息
```

是否真的落到 wire，要看 Trace 的 raw request；是否真的产生缓存读写，要看 AssistantMessage/ModelResponse 中的 TokenUsage。仅看到 `extra` 不能声称已经节省了 Token。

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

### 8.4 响应方向：从 Provider 回到 State

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

#### 8.4.1 为什么要有 `LLMResponse`

OpenAI Chat、OpenAI Responses 和 Anthropic Messages 对同一件事使用不同的
字段和嵌套结构。例如，一个 Provider 把工具调用放在
`choices[].message.tool_calls`，另一个放在 `content[].type="tool_use"`。
如果 `core.py` 直接读取这些 SDK 对象，主循环就必须不断判断“当前是哪一家
Provider”，工具调度、停止判断和 Trace 也会被供应商格式污染。

Adapter 的边界职责是：

```text
Provider raw response
  → Adapter 解析、规范化
  → LLMResponse
  → Bridge 转成 AssistantMessage
  → Runtime 记录 Event 并更新 State
```

因此，Runtime 只依赖项目自己的 `ContentBlock`、`StopReason`、`TokenUsage`
和模型标识；Provider 专用字段留在 Adapter 或 `raw` 中。换 Provider 时，
变化集中在 Adapter，不需要重写 Agent 循环。

#### 8.4.2 `content` 为什么是唯一事实来源

`content` 是一个有顺序的 `ContentBlock` 元组，而不是把文本、思考和工具调用
分别存放的三个字段：

```python
response.content == (
    ThinkingBlock(text="先确认文件位置"),
    TextBlock(text="我先读取 README。"),
    ToolCallBlock(
        id="call-1",
        name="read",
        arguments={"path": "README.md"},
    ),
)
```

从它派生出的便利属性只是查询：

```text
response.text             → 拼接/提取 TextBlock
response.thinking_blocks  → 筛选 ThinkingBlock
response.tool_calls       → 筛选 ToolCallBlock
```

保留顺序很重要：模型可能先产生思考，再说明意图，随后请求工具；如果只保留
三个互相独立的列表，就无法准确重放、审计或构建下一轮请求。任何需要判断
“模型是否请求工具”的 Runtime 逻辑，都应读取规范化的 `content`（或其派生
查询），而不是读取某个 OpenAI/Anthropic 原始对象。

#### 8.4.3 其他响应字段各自回答什么

| 字段 | 它回答的问题 | 主要用途 |
| --- | --- | --- |
| `stop_reason` | Provider 为什么结束这次生成？ | Bridge 判断继续执行工具还是结束本轮 |
| `usage` | 本次调用报告了多少输入、输出及缓存 Token？ | `ModelResponseEvent`、成本和上下文分析 |
| `model` | 实际为本次响应服务的模型标识是什么？ | Trace、价格匹配和版本复盘 |
| `raw` | 实际发出的 Wire 请求和 Provider 原始响应是什么？ | Wire Debug 和 Adapter 排查 |

`model` 优先使用 Provider 返回的实际标识；请求使用别名时，响应可能返回带
日期或版本的服务标识。`usage` 中缓存读写 Token 要与普通输入 Token 分开，
避免重复计数。若 Provider 没有提供 usage，运行时应保留“未知”，不能把缺失
数据伪装成精确的零消耗。

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

### 8.5 当前“流式汇总”的准确理解

LLM 访问层暴露统一 `StreamEvent` 协议。当前源码中真实存在的
`StreamEvent.kind` 包括：

| kind | 作用 |
| --- | --- |
| `text_delta` | 一段新增普通文本 |
| `thinking_delta` | 一段新增思考内容 |
| `tool_call_start` | 工具调用开始，参数可以暂时为空 |
| `tool_call_delta` | 某个调用的 JSON 参数增量 |
| `tool_call_complete` | 参数已完整解析的工具调用 |
| `usage_update` | 本次调用的 Token 使用量更新 |
| `done` | 携带最终 `LLMResponse`，必须是最后一个事件 |

事件流最后必须产生：

```python
StreamEvent(
    kind="done",
    payload={"response": complete_llm_response},
)
```

`complete()` 消费这个事件流并返回最终 LLMResponse。

如果是一个真正逐片到达的 Provider 流，Adapter 内部可以按以下规则汇总：

```text
text_delta("我先") + text_delta("读取 README")
  → TextBlock("我先读取 README")

thinking_delta("需要先") + thinking_delta("读取文件")
  → ThinkingBlock("需要先读取文件")

tool_call_start(call-1)
  + tool_call_delta('{"path":')
  + tool_call_delta('"README.md"}')
  → tool_call_complete(call-1)
  → ToolCallBlock(arguments={"path": "README.md"})
```

实现上需要按工具 `id` 保存参数增量；收到 complete 时再解析 JSON，并把最终
的有序 blocks、stop reason、usage、model 和 raw 组成一个 `LLMResponse`。
不过，调用者不应自己从 delta 猜一个“第二份响应”：项目协议规定
`done.payload["response"]` 已经是 Adapter 组装好的最终结果，`complete()` 只
消费到 `done` 并返回它；没有合法 `done` 就应报错。

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

当前真实 OpenAI Chat、OpenAI Responses 和 Anthropic Adapter 主要执行阻塞式
SDK 调用：先拿到完整响应，再由公共 `emit_response()` 将完整 Block 回放成
统一事件。通常一个完整 `TextBlock` 对应一次 `text_delta`，一个完整
`ToolCallBlock` 对应 `tool_call_start` 后紧接 `tool_call_complete`；
`tool_call_delta` 虽在协议中存在，但这些 Adapter 已经拿到完整参数时不一定
会产生它。Fake Adapter 则可通过 `extra["chunk_size"]` 把文本拆成多个
`text_delta`，用于测试消费流的代码。

还要区分两层事件：

```text
StreamEvent                 # LLM 访问层的过程协议，通常不进入 State.events
  → done(LLMResponse)
  → AssistantMessage
  → ModelResponseEvent
  → MessageEvent
  → State.events / State.messages
```

所以 StreamEvent 适合实时 UI 或模型访问层观察；`LLMResponse` 是完整响应；
`AssistantMessage` 才是 Runtime transcript 中的消息；主 State 的事实仍由
`ModelResponseEvent` 和 `MessageEvent` 保存。

### 8.6 raw 是边界证据，不是核心协议

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

#### 8.6.1 SDK、Wire、raw 和 LLMResponse 的四层区别

SDK 是 Provider 提供的 Software Development Kit，也就是帮你发 HTTP 请求、做
认证、编码参数并解析响应的客户端库：

```python
client = OpenAI(api_key="...")
sdk_response = client.responses.create(model="...", input=[...])
```

`sdk_response` 是 Provider SDK 的专用 Python 对象，不是项目的 `LLMResponse`。
四层对象可以这样记：

```text
Provider HTTP JSON       # 网络边界上的真实字段
Provider SDK 对象        # SDK 对 JSON 的 Python 包装
LLMResponse              # Adapter 产出的项目统一运行协议
raw 快照                 # Adapter 保存的请求/响应调试证据
```

Adapter 的 `sdk_dump()` 优先调用 SDK 的 `model_dump()`，尽量得到由字典、列表和
基本值组成的快照；没有该能力时允许保留对象作为调试回退。因此 raw 适合
Wire Debug 和事后核对，但不能保证在所有第三方 SDK 版本下都是完整的 JSON，
更不能替代统一协议。

原始 SDK 对象不能直接放进 State：这样会让 State 依赖某个供应商的类、属性
路径和 SDK 版本，也会使 Fake Provider、Trace Reader 和重放难以工作。核心逻辑
应读取：

```python
if response.tool_calls:
    execute_tools()

if response.stop_reason == "end_turn":
    stop_agent()
```

而不是读取 `raw["response"]` 中某一家 Provider 的嵌套字段。完整的边界规则和
持久化影响见 [`05-model-access.md`](docs/design/05-model-access.md#111-provider-wire-sdk-对象raw-和-llmresponse-不是一回事)。

#### 8.6.2 OpenAI Responses 的推理连续性例子

某些 Responses 推理模型会在一次输出中返回 reasoning item 和 function call：

```json
{
  "output": [
    {
      "type": "reasoning",
      "id": "rs_abc",
      "summary": [{"type": "summary_text", "text": "先读取 README。"}],
      "encrypted_content": "encrypted-state-xyz"
    },
    {
      "type": "function_call",
      "call_id": "call_1",
      "name": "read",
      "arguments": "{\"path\":\"README.md\"}"
    }
  ]
}
```

Adapter 将可通用的部分转成：

```python
LLMResponse(
    content=(
        ThinkingBlock(text="先读取 README。", signature="rs_abc"),
        ToolCallBlock(
            id="call_1",
            name="read",
            arguments={"path": "README.md"},
        ),
    ),
    stop_reason="tool_use",
    raw={"response": {...}},
)
```

Bridge 只从 `raw["response"].output` 提取 Responses 专用的 `id`、summary 和
`encrypted_content`，放入：

```python
AssistantMessage.sidecar["extra"]["openai_responses.reasoning_items"]
```

下一轮 `message_to_llm_message()` 将它传给 OpenAI Responses Adapter。Adapter
按 `ThinkingBlock.signature` 找到对应 item，把 reasoning item 放在相关
`function_call` 前重新构造 wire 输入；没有 summary 或 encrypted content 的
空 item 会被跳过。这样既满足 Provider 的连续性要求，也不把
`reasoning_id`、加密内容等 OpenAI 专用概念污染所有 Message。

这条路径是“raw → 指定 Bridge → namespaced extra → 指定 Adapter”的受控特例，
不是 Runtime 的通用 raw 读取规则。Anthropic、OpenAI Chat 和 Fake Provider
可以忽略这个命名空间，仍使用相同的 `LLMResponse.content` 和 Runtime 事件链。

原始 SDK 对象也不能替代 LLMResponse 或 Message。Adapter 会尽量将 SDK 响应转换成可序列化快照，它的目的是保存证据，而不是让核心模块继续调用 Provider SDK 方法。

---

## 9. 用一次 Bash 调用把所有层串起来

这一章只追踪一个确定性例子：用户要求 Agent 执行
`printf 'hello-from-tool\n'`。不再引入新的抽象，只观察同一份数据怎样经过
初始化、两次模型决策、一次工具执行，最后变成可回放的 State 和 Event Stream。

先看完整账本。这里的数字是写入 `State.events` 后的事件索引，不是事件类型
编号：

```text
0  MessageEvent(task)
1  AgentStartEvent(agent="bash_agent")
2  TurnStartEvent(agent="bash_agent")
3  ModelRequestEvent(visible_count=1, llm_message_count=2)
4  ModelResponseEvent(output_kind="step", tool_call_count=1)
5  MessageEvent(assistant: ToolCallBlock(id="bash_1"))
6  ToolExecutionStartEvent(tool_call_id="bash_1")
7  ToolExecutionEndEvent(tool_call_id="bash_1", is_error=False)
8  MessageEvent(tool_result: ToolResultBlock(id="bash_1"))
9  TurnEndEvent(terminated=False)
10 TurnStartEvent(agent="bash_agent")
11 ModelRequestEvent(visible_count=3, llm_message_count=4)
12 ModelResponseEvent(output_kind="final", tool_call_count=0)
13 MessageEvent(assistant: TextBlock("Bash observation: hello-from-tool"))
14 TurnEndEvent(terminated=False)
15 AgentEndEvent(reason="done")
```

这个顺序体现了三条不同的事实线：

```text
MessageEvent       → 对话中出现了什么 Message
Model/Tool Event   → 模型访问和本地执行发生了什么
State.snapshot     → 当前有哪些完整 Message、哪些处于 active context
```

`Snapshot.messages` 可以从 MessageEvent 重建；`ModelRequestEvent` 和
`ModelResponseEvent` 保存每次模型调用的投影和结果摘要；工具执行事件不替代
工具结果消息，后者才是下一轮模型的环境观察。

### 9.1 初始化：先建立运行输入，再启动惰性循环

调用：

```python
state, events = agent.run(
    "Use bash to run command: printf 'hello-from-tool\\n'",
    max_turns=3,
)
```

默认初始化器做两件事：

```text
State(task=task)
  → 保存运行级原始输入
state.send("task", "user", "bash_agent", task)
  → 创建 UserMessage(kind="task")
  → 追加 MessageEvent(index=0)
  → Snapshot.messages = [task]
```

`agent.run()` 返回 `(state, events)`，但 `events` 是惰性迭代器。调用 `run()`
本身只完成 State 和初始任务消息的准备；只有执行 `for event in events` 或
`list(events)`，才会继续产生 `AgentStartEvent`、模型调用和工具执行事件。
因此 `MessageEvent(task)` 可以排在 `AgentStartEvent` 前面：它是运行输入的
初始化事实，不是 Agent 循环已经开始的证明。

### 9.2 Turn 1：从 active Message 到 Provider Wire

第一轮开始后，Runtime 依次完成：

```text
TurnStartEvent
  → active_context_messages() = [task]
  → ContextPolicy / ContextView 可见性处理
  → Bridge：Message → LLMMessage
  → LLMRequest：加入 system_prompt、bash LLMTool 和请求参数
  → ModelRequestEvent
  → Adapter：LLMRequest → Provider Wire
```

本例没有压缩或可见性过滤，所以模型对话中的可见 Message 只有一条：

```text
visible_count = 1
```

但是 `system_prompt` 是 Agent 配置，不是 State 中的普通 Message。Runtime
会把它作为请求的第一条 system 输入，因此完整 LLM payload 是：

```text
llm_message_count = 2
  1. system_prompt
  2. task UserMessage
```

这两个数字回答不同问题：`visible_count` 统计 ContextView 中可见的对话
Message；`llm_message_count` 统计送入 Adapter 的完整消息列表。ModelRequestEvent
同时记录 ContextView 统计、工具声明和可重建的 LLM 投影。

### 9.3 Turn 1 输出：模型只提出工具请求

Provider 的 OpenAI、Anthropic 或 Fake 结果都会先由 Adapter 规范化为统一响应：

```python
LLMResponse(
    content=(
        TextBlock("我先执行 Bash。"),
        ToolCallBlock(
            id="bash_1",
            name="bash",
            arguments={"command": "printf 'hello-from-tool\\n'"},
        ),
    ),
    stop_reason="tool_use",
    usage=...,
    model="served-model-id",
    raw={"request": {...}, "response": {...}},
)
```

Bridge 透传有序 `content`，补回 Runtime 的 `sender`、`target` 和 `kind`，得到：

```python
AssistantMessage(
    sender="bash_agent",
    target="user",
    kind="step",
    content=response.content,
    usage=response.usage,
    model=response.model,
    sidecar={"raw": response.raw},
)
```

Runtime 先写 `ModelResponseEvent(index=4)`，记录输出阶段、工具调用数量、
usage 和 model；随后写 `MessageEvent(index=5)`，这条 AssistantMessage 才正式
进入完整 transcript。`stop_reason="tool_use"` 表示“模型请求 Runtime 执行工具”，
不是“Agent 已经完成”。

### 9.4 Tool：执行事实与环境观察分开保存

Runtime 从 `AssistantMessage.tool_calls` 找到 `bash_1`，在本地工具注册表中
执行，不读取 Provider raw：

```text
ToolCallBlock(id="bash_1")
  → ToolExecutionStartEvent(index=6)
  → bash.execute(command)
  → ToolExecutionEndEvent(index=7, is_error=False)
  → ToolResultBlock(tool_call_id="bash_1", content="hello-from-tool")
  → UserMessage(kind="tool_result")
  → MessageEvent(index=8)
```

两种记录不能互相替代：

| 记录 | 主要回答的问题 | 下一轮是否直接作为模型输入 |
| --- | --- | --- |
| `ToolExecutionStart/EndEvent` | 本地函数何时开始、何时结束、是否失败或终止？ | 否 |
| `ToolResultBlock` | 工具向模型返回了什么观察？ | 是 |

结果消息按 `tool_call_id="bash_1"` 与 Assistant 的 ToolCall 配对；并行工具
时，多个结果仍可放在一个 `tool_result` Message 中，通过各自 call ID 关联。
工具执行和结果写入完成后，Runtime 才记录 `TurnEndEvent(index=9)`，所以
Turn 1 的边界包含完整的工具处理，而不只包含模型请求。

### 9.5 Turn 2：模型读取观察并正常结束

第二轮开始时，active context 中有三条可见 Message：

```text
1. task UserMessage
2. AssistantMessage(kind="step", ToolCallBlock("bash_1"))
3. UserMessage(kind="tool_result", ToolResultBlock("bash_1"))
```

于是：

```text
visible_count     = 3
llm_message_count = 4  # system_prompt + 上面三条 Message
```

Bridge 再次去掉 Runtime 路由头，Adapter 把规范请求翻译成 Provider Wire。
模型这次已经看到了工具结果，因此返回普通最终文本：

```python
LLMResponse(
    content=(TextBlock("Bash observation: hello-from-tool"),),
    stop_reason="end_turn",
    usage=...,
    model="served-model-id",
    raw={"request": {...}, "response": {...}},
)
```

`make_llm_agent()` 将 `end_turn` 映射为 `AssistantMessage(kind="final")`。Runtime
依次记录 `ModelResponseEvent(index=12)`、`MessageEvent(index=13)` 和
`TurnEndEvent(index=14)`；检测到本轮 final 后跳出控制循环，最后追加：

```text
AgentEndEvent(reason="done", index=15)
```

### 9.6 结束时怎样对账

本次运行的四条 Message 是：

```text
0 task UserMessage
1 assistant step（含 bash_1 ToolCall）
2 tool_result UserMessage（含 bash_1 ToolResult）
3 assistant final（Bash observation）
```

Message 的索引属于 `State.messages` / Snapshot；上面的 0～3 不是
`State.events` 的索引。完整 Event Stream 有 16 条（事件索引 0～15），其中
两次模型请求分别由 `ModelRequestEvent`/`ModelResponseEvent` 对记录，工具调用
由 `bash_1` 在 Assistant ToolCall、ToolExecution Event 和 ToolResultBlock 之间
保持因果关联。

最终可以从不同层得到不同观察：

```text
State.messages / Snapshot
  → 可回放的对话 transcript

State.events
  → 完整生命周期、模型访问和工具执行事实

ModelRequestEvent / ModelResponseEvent
  → 两次模型调用的输入投影、usage、model 和工具统计

AssistantMessage.sidecar["raw"]
  → Provider 边界调试证据（不参与普通控制流）
```

这就是一次工具调用的最小闭环：第一轮决定“调用 Bash”，Runtime 执行并写入
观察，第二轮根据观察给出 final。两轮共享同一个 State，但每层只读取自己
负责的事实；因此运行既能继续推进，也能在结束后独立重放和审计。

---

## 10. 用一个“修复代码并通过测试”的任务理解组合层

前九章讲的是一次 Agent Run 内部怎样产生 Message、Event、工具结果和最终
答案。本章只新增一个问题：当任务变长、需要不同专家或外部验收时，应该在
哪一层组合运行？

贯穿例子：

```text
修复认证模块的 bug，并运行测试确认修复有效。
```

先记住两个对象：

```text
Agent = 可反复使用的配置
  name / prompt / Provider / tools / policy / hooks / init_state

State = 某一次实际运行的事实账本
  task Message / Model Event / Tool Event / Tool Result / final / stop reason
```

同一个 `Agent` 可以产生很多互不干扰的 State：

```text
writer Agent
├── 第一次 run → State A
├── 第二次 run → State B
└── Goal Loop resume → 仍是 State A
```

### 10.1 先看全景：这些不是一条强制流水线

最容易产生的误解是把它画成：

```text
Builder → Session → Task Tool → Workflow → Goal Loop → Facade
```

这不是项目要求的调用顺序。它们是可以独立选择的不同维度：

```text
Builder / Flavor  → 决定一个 Agent 有什么能力
Session / Toolset  → 决定连接型资源活多久
Task Tool          → 模型临场决定要不要委派
Workflow           → 程序预先规定多个 Run 的关系
Goal Loop          → 外部检查决定是否继续同一目标
Workflow Facade    → 对外统一成普通 Agent.run() 入口
```

同一个“修复并验收”任务可能有不同配置：

```text
最小路径：Builder → Agent.run → State
需要 MCP：Session → Agent.run → State
需要临时专家：父 Agent → Task Tool → 子 Agent State
固定三阶段：Planner → Executor → Reviewer（Workflow Steps）
必须测试通过：Agent run → CompletionCheck → resume（Goal Loop）
外部只接受 Agent：Facade.run → 内部 Workflow
```

不要为了让流程超过一步就把所有层都启用。每增加一层，都应有明确的控制权、
资源生命周期或验收价值。

### 10.2 Builder 与 Flavor：先决定“一个 Agent 有什么”

Builder 的真实职责是把现有配置组装成普通 `Agent`，不执行模型：

```python
writer = make_agent(
    provider=provider,
    cwd="C:/repo",
    bash=True,
    read=True,
    general_purpose=True,
    system_prompt="分析并修复认证代码，完成后运行测试。",
)
```

得到的是配置对象：

```text
writer
├── system_prompt
├── Provider
├── Bash / Read
├── 可选 Task Tool
├── ContextPolicy
├── Hooks
└── init_state
```

此时没有模型请求，也没有运行 State。只有调用：

```python
state, events = writer.run("修复认证模块的 bug，并运行测试确认修复有效。")
for event in events:       # 消费惰性事件，才真正推进 Runtime
    pass
```

才会创建并推进一次 State。Builder 只组合 `Agent.run()` 已有的边界，不复制
`core.run()`、工具调度、ContextView 或停止逻辑。Skills 通过 `init_state`
注入菜单和上下文，仍使用同一套 Runtime。

Flavor 是命名好的 Builder 入口，例如 `bash`、`bash_task`、`bash_skills`；
`loop`、`pdr` 则是命名好的 Workflow/Facade 入口。Flavor 只是展开一组约定
参数，不是新的 Agent 类型、State 类型或 Runtime。

### 10.3 Session 与 Toolset：再决定资源“活多久”

Bash、Read 这类普通工具通常没有需要跨调用保持的连接，可以直接交给 Builder。
MCP Toolset 则可能拥有子进程、网络连接和握手状态：

```python
with agent_session(
    provider=provider,
    mcp_servers=[filesystem_server],
) as session:
    state, events = session.run("读取认证代码并运行测试")
    for event in events:   # 必须在 with 块内消费
        pass
```

实际时间线是：

```text
进入 with
  → 打开 Toolset / MCP 连接
  → 收集 AgentTool
  → 构建普通 Agent
  → Agent.run() 返回惰性 events
  → 在 with 内消费模型和工具事件
退出 with
  → 关闭连接和子进程
```

如果只在 `with` 内调用 `session.run()`，离开后才消费 events，惰性 Runtime
可能在 MCP 已关闭时才真正执行工具。Session 管的是资源所有权，不保存第二份
对话 State，也不改变 `agent.run()` 的 `(state, events)` 契约。

### 10.4 Task Tool：模型临场决定是否找专家

如果“是否需要代码阅读专家”取决于模型看到的当前代码，可以把子 Agent 注册
为 Task Tool 的枚举选项：

```python
task = task_tool([code_reader, test_runner])
```

父模型可能输出：

```python
ToolCallBlock(
    id="task_1",
    name="task",
    arguments={
        "subagent_type": "code_reader",
        "task": "定位认证失败的根因，阅读 auth.py 和相关测试。",
        "context": "只报告证据和建议，不修改文件。",
    },
)
```

Task Tool 的控制权在父模型：它可以不委派，也可以选择已注册的某一个子 Agent。
Runtime 不会根据模型字符串动态 import 任意模块。

Task Tool 的内部流程是：

```text
父 State
  → 记录 task ToolCall
Task Tool
  → 注册表查找 subagent_type
  → child_agent.run(task) 创建 Child State
  → 写入可选 context Message
  → 消费 Child events
  → 要求 Child 产生 kind="final"
  → ToolResult.content = 子 Agent final 文本
  → details["sub_events"] = 子 State.events
父 State
  → 记录 tool_result Message
  → 父模型下一轮读取结果
```

父、子 State 始终独立。父模型只看到 ToolResult 的文本；Trace 可以从
`details["sub_events"]` 派生子 Span。若子 Agent 因 `max_turns` 没有 final，
Task Tool 返回错误 ToolResult；这和 Workflow `run_agent()` 可回退最后一条
Assistant 文本的契约不同。

### 10.5 Workflow：程序决定固定阶段和数据传递

如果业务规则明确要求“先分析，再修改，最后测试验收”，就不应让模型临场决定
是否进入下一阶段，而应使用 Workflow：

```text
Analyzer
  → 输出：根因、涉及文件和风险
      ↓ 原始任务 + 分析输出
Executor
  → 输出：修改结果和测试命令
      ↓ 原始任务 + 修改结果
Tester / Reviewer
  → 输出：测试证据和剩余问题
```

每一步通过 `run_agent()` 消费惰性 events，并返回 `StepResult`：

```python
StepResult(
    name="tester",
    role="验收者",
    task="原始任务 + Executor 输出",
    output="pytest: 42 passed",
    state=tester_state,
)
```

整个流程返回：

```python
WorkflowResult(
    output="pytest: 42 passed",
    steps=[analyzer_step, executor_step, tester_step],
)
```

`output` 是给下一步或调用者的文本接口；`state` 是该步骤完整的 Message、
Event、usage、工具和停止事实。普通 Workflow 的 Step 通常拥有独立 State：

```text
Analyzer State ≠ Executor State ≠ Tester State
```

Workflow 只负责顺序、输入传递、并发、重试和结果选择，不把多个 State 强行
合并为一条共享 transcript。`max_turns` 时 `run_agent()` 可以回退最后一条
Assistant 文本给下一步，但仍保留真实 `AgentEndEvent(reason="max_turns")`，
不能把回退文本伪装成成功 final。

### 10.6 Goal Loop：测试验收才是完成条件

普通 Agent 在产生 `AssistantMessage(kind="final")` 后停止；这只说明模型认为
回答结束，不证明认证 Bug 真修好或测试真的通过。

Goal Loop 通过 `run_goal_loop(agent, objective, check=...)` 把外部检查加入控制流：

```text
第一次 agent.run(objective)
  → 模型修改代码并声称完成
  → CompletionCheck(state)
      ├─ pytest 全部通过 → status="complete"
      ├─ 未通过但可继续 → agent.resume(同一个 State, 反馈)
      ├─ 同一 blocker 连续达到阈值 → status="blocked"
      ├─ 回合或 Token 预算用完 → status="budget_exhausted"
      └─ abort / 墙钟截止 → status="aborted"
```

未通过时继续的是同一个 State：

```text
原始任务
→ 之前的读写和测试结果
→ 模型上一次 final
→ 外部 pytest 反馈
→ 后续修改和验证
```

Goal Loop 每轮追加 `GoalStatusEvent`，保留 objective、状态、回合和 Token 计数。
`complete` 必须由 `CompletionCheck` 报告；`blocked` 和 `budget_exhausted` 都
不是成功。也就是说：

```text
模型 final = 候选完成
外部 check 通过 = 可接受完成
```

### 10.7 Workflow Facade：统一入口，不隐藏内部事实

某些外部运行器只接受普通接口：

```python
state, events = agent.run(task)
```

但 `loop` 或 `pdr` flavor 内部可能执行多次 Agent Run、并行 Worker、Distiller
和 Finalizer。这时可以返回一个 Workflow Facade：

```text
facade.run(task)
  → 外层普通 core.run()
  → facade.generate(...)
  → 内部 Workflow
  → WorkflowResult
  → 外层 AssistantMessage(kind="final")
```

对外它像普通 Agent；对内每个 Worker、Goal resume 和 Distiller 仍然有自己的
State。`compose_trace_state()` 只在最终导出时派生一个组合展示视图：

```text
独立 Step State / 子 Trace
  → compose_trace_state()
  → 组合 Trace / Viewer 树
```

Facade 不创建新的 Message/Event 协议，不把内部 State 偷偷合并成共享 Runtime，
也不能用外层 final 隐藏内部失败。内部 `StepResult`、停止原因和 Goal 状态仍是
事实，外层只是统一调用入口和组合视图。

### 10.8 把“修复并验收”映射到正确层次

| 需求 | 优先使用 | 为什么 |
| --- | --- | --- |
| 给一个 Agent 增加 Bash/Read 或自定义无状态能力 | Builder | 只改变能力集合 |
| 为常见能力组合提供易懂名称 | Flavor | 命名配置，不增加运行语义 |
| 工具需要 MCP 连接、子进程或握手状态 | Session + Toolset | 资源覆盖完整惰性运行 |
| 是否找专家取决于模型当前观察 | Task Tool | 父模型拥有动态委派权 |
| 必须按固定阶段分析→修改→测试 | Sequential / Planner-Executor | 程序顺序稳定、容易审计 |
| 需要独立代码审查 | Reflection | Critic 结果是显式协议 |
| 多个候选或代码分片并行 | Parallel + Aggregator | State 隔离、结果顺序稳定 |
| 完成必须由 pytest/外部状态确认 | Goal Loop | final 只是候选，check 才是完成权威 |
| 外部只接受 `Agent.run()` | Workflow Facade | 统一入口但保留内部事实 |

### 10.9 最后只记住四条边界

```text
Agent     = 配置；State = 一次运行事实
Builder   = 装配能力；Session = 管资源寿命
Task Tool = 模型动态委派；Workflow = 程序固定编排
Goal Loop = 外部验收；Facade = 统一外部入口
```

无论选择哪种组合，底层都回到同一个 `Agent.run()` / `core.run()`：

```text
普通 Agent Run
  → Message / Event / State

多个独立 Run
  → StepResult / WorkflowResult

外部观察
  → Trace / CompletionCheck / Eval
```

组合层的价值是让控制权、资源所有权和完成条件明确，而不是把简单循环藏在
更大的框架名词后面。


## 11. 从事件账本到 Trace：一次运行如何被观察、回放和计费

前面已经建立了两个基础：

- 第 6 章说明 `Message` 是对话事实，`Event` 是运行事实，`State.events` 是追加式事实账本；
- 第 10 章说明父 Agent、Task Tool 子 Agent 和 Workflow Step 通常各自拥有独立 State。

这一章不再创造新的运行事实，而是回答：这些事实怎样变成可阅读、可分析和可持久化的观察结果？

最核心的不变量是：

> Runtime 负责产生事实，Event Stream 负责保存事实，Trace 层负责从事实派生观察视图，Viewer 只负责展示这些视图。

把本章后面的几个组件放在同一张图里：

```text
State.events = 不可替代的运行事实账本
       ↓
观察层从账本派生不同视角
├─ ModelTurn              模型视角：当时输入和输出是什么
├─ RunCost                成本视角：usage 和价格如何聚合
├─ Span                   时间视角：操作持续多久、嵌套在哪里
├─ Trajectory v5          持久化视角：怎样追加到磁盘并恢复
├─ IncrementalTraceWriter 实时写入视角：新增事件怎样落盘
└─ Viewer                 人类查看视角：如何展示这些结果
```

它们共享同一条单向边界：

```text
Runtime 产生 Event
Writer   保存 Event
Reader   读取 Event
Trace    派生视图
Viewer   展示视图
```

观察层的筛选、展开、成本计算或 Span 合并都不会反向修改 State，也不会替代
`agent.run()` / `resume()`。如果需要继续运行，必须回到拥有 Agent 配置、工具注册、
Snapshot 和 ContextPolicy 的 Runtime State。

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

可以把 `RunTrace` 理解成：从一次 Agent 运行的 `State` 中截取一份“只读观察报告”，再按需要生成 Span、ModelTurn 和成本视图。它不是新的运行容器，也不是第二个 Agent。

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

构造时复制的是列表目录，而不是重新执行运行：

```python
trace.events = list(state.events)
trace.messages = list(state.messages)
```

如果 Runtime 仍在运行，先创建的 Trace 只代表创建瞬间的快照；State 后续追加的事件不会自动回写到这份 Trace。要观察最新状态，应重新构造 Trace，或使用 `IncrementalTraceWriter` 做增量写入。

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

#### Header、Event Body 与 JSONL 的分工

Header 回答“这是什么文件”：

```text
schema / trace_id / producer / task / meta
```

后续 Event Body 回答“运行中发生了什么”：

```text
index / elapsed / uuid / kind / 事件专用字段
```

其中 `index` 是追加顺序，`elapsed` 是相对运行时间，`uuid` 用于跨文件或子 Trace 引用，`kind` 决定 Reader 如何解释这一行；`schema` 描述整个文件的版本契约，不能和 `kind` 混为一谈。

使用 JSONL 的核心原因是追加和恢复：新事件只需追加一行，Viewer 可以读取已经完成的前缀，进程中断时可以恢复完整尾行之前的内容，Reader 还可以边读边重建 transcript、活跃上下文、Span、ModelTurn 和 RunCost。

因此 Header 不重复保存这些派生数组：

```text
messages / spans / model_turns / cost
```

它们都应从 Event Stream 重新计算。内存中的 `ModelRequestEvent.llm_payload` 是当时的请求投影，但 v5 写盘时会由 `event_record()` 移除，避免每一轮重复保存不断增长的历史；需要精确核对 Provider Wire 时，再读取外置 raw pool。

`IncrementalTraceWriter` 的典型生命周期是：首次 flush 写 Header，后续只追加新增 Event，停止时执行最终 flush；没有新增事件则不写入。它观察 State 并写文件，不推进 Runtime，也不能替代 `agent.run()` 或 `resume()`。

#### Reader 如何从文件恢复观察结果

Reader 不是把整个 JSONL 文件当成一份新的 Runtime State，而是按顺序处理：

```text
读取 Header
  → 校验 schema、trace_id 和运行元数据
读取一行 Event
  → 校验 index / kind / JSON 结构
  → 追加事件并更新 transcript、Context、Span、成本聚合
遇到 raw_ref
  → 需要 Wire Debug 时再读取相邻 raw pool
```

如果写入过程在最后一行中断，Reader 只恢复此前完整写入的行，不会把半行数据当作真实 Event。恢复后的 transcript、活跃上下文和 Span 都是从事件重新派生的观察结果；它不能替代拥有 Agent 配置、工具注册和 ContextPolicy 的原始 State，因此不能仅凭 Trajectory 直接继续 `resume()`。

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

可以用一个完整的模拟数据把这句话展开：

```text
父事件：
0.0 AgentStart(parent)
0.5 TurnStart(parent)
1.0 ModelRequest(parent)
2.0 ModelResponse(parent, ToolCall(task_1))
2.1 ToolExecutionStart(task_1)
9.3 ToolExecutionEnd(task_1)
9.4 MessageEvent(tool_result, details[task_1].sub_events)
9.5 TurnEnd(parent)

父 Span：
parent.agent_run 0.0 - 10.0
└── parent.turn 0.5 - 9.5
    ├── parent.model_call 1.0 - 2.0
    └── parent.tool_call 2.1 - 9.3
```

子 Agent 的事件有自己的相对时间轴：

```text
0.0 AgentStart(child)
0.5 TurnStart(child)
1.0 ModelRequest(child)
2.0 ModelResponse(child, ToolCall(read_1))
2.1 ToolStart(read_1)
4.0 ToolEnd(read_1)
4.5 TurnEnd(child)
5.0 TurnStart(child)
5.5 ModelRequest(child)
6.0 ModelResponse(child, final)
6.5 TurnEnd(child)
7.0 AgentEnd(child)
```

合并器先把子事件独立转换为 Span，再使用：

```text
merged_start = child.start + parent_tool.start
merged_end   = child.end + parent_tool.start
```

因此子 `agent_run` 从 `0.0 - 7.0` 变成 `2.1 - 9.1`，完整落在父 Tool 的 `2.1 - 9.3` 区间内。实际 Task Tool 会在消费子运行并组装结果后才结束，所以正常数据通常保持这种嵌套。仍需知道当前合并器只做时间平移，不裁剪或缩放异常数据；这些时间适合展示相对结构，不能当作经过跨时钟校准的绝对时间。

合并后的 parent-child 关系是：

```text
parent.tool_call(task_1)
└── child.agent_run
    ├── child.turn
    │   ├── child.model_call
    │   └── child.tool_call
    └── child.turn
        └── child.model_call
```

只有子 Agent 的根 Span 改挂到父 Tool Span；子树内部的 `parent_id` 保持原样。父 State 和子 State、两边的 Event index、ContextCompressionEvent 和生命周期事件都没有被合并或修改。Span 最终只是 Viewer 使用的派生树，不能再用它反向控制 Runtime，例如“Span 超过 10 秒就自动停止 Agent”；真正的停止必须由 Runtime 写入 abort、工具终止或其他结构化 Event。完整规则见 [`09-trace-and-observability.md`](docs/design/09-trace-and-observability.md#61-普通-span-怎样从-event-配对)。

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
