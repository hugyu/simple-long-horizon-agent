下面继续以“项目负责人 / 核心开发者”的第一人称回答。回答会区分统一语义、供应商特例和当前尚未实现的自动能力协商。

## 1. Provider Adapter 的统一接口是什么？

Provider 本身不是类继承体系，而是一个不可变配置对象，保存 `id`、`api`、`model`、`base_url`、密钥环境变量和默认推理参数。

Adapter 的统一函数签名是：

```python
AdapterFn = Callable[[LLMRequest], Iterator[StreamEvent]]
```

`LLMRequest` 包含统一消息、工具、system prompt、temperature、max tokens、reasoning 和请求扩展。Adapter 必须输出项目自有 StreamEvent，并且最后一条必须是携带 LLMResponse 的 `done`。

调用层只有三个主要入口：`register_adapter()` 注册、`iter_stream()` 流式消费、`complete()` 阻塞取得最终响应。Runtime 不直接选择 SDK，而是根据 `Provider.api` 从注册表选择 Adapter。

## 2. 为什么不能直接在 Runtime 中使用供应商返回对象？

因为供应商 SDK 对象不是项目能够控制的稳定协议。

OpenAI Chat 的工具调用位于 `choices[].message.tool_calls`，Responses 位于 `output[].type="function_call"`，Anthropic 则是 `content[].type="tool_use"`。如果 Runtime 直接读取这些字段，工具调度、停止判断和 State 都会绑定某一家 API。

SDK 对象还会带来序列化、版本升级、测试替换和跨 Provider 恢复问题。因此 Adapter 先把响应转换成项目自有 LLMResponse 和 Content Block，Runtime 只处理 AssistantMessage。

原始请求和响应仍保存在 `raw` 中作为调试证据，但 Runtime 不使用 raw 决定正常控制流。

## 3. 不同 Provider 的消息格式有哪些主要差异？

**OpenAI Chat Completions：**

使用 `messages` 数组；system prompt 通常是第一条 system message；工具定义采用 `{"type":"function","function":{...}}`；每个工具结果必须拆成独立的 `role="tool"` 消息；完成原因来自 `finish_reason`。

**OpenAI Responses：**

使用扁平 `input` item；固定提示使用 `instructions`；工具调用和结果分别是 `function_call` 与 `function_call_output`；工具定义是扁平 function 结构；输出由 reasoning、message 和 function_call 等 item 组成；长度截断通过 status 和 incomplete details 表达。

**Anthropic Messages：**

system prompt 是顶层 `system` 字段；消息正文是 Content Block；工具调用是 assistant 中的 `tool_use`；工具结果作为 user content 中的 `tool_result`；工具参数 Schema 使用 `input_schema`；Thinking、签名和 Prompt Cache 也有自己的结构。

多模态工具结果也不同。Anthropic 可以把图片放进 tool_result 内部，而 OpenAI Chat 和 Responses 需要把图片拆成相邻的 user message 或 input item。

## 4. Tool Call ID 如何统一管理？

Tool Call ID 是跨模型输出、工具执行和结果反馈的因果标识。

Adapter 从 Provider 响应中取得原始 ID：OpenAI Chat 使用 `tool_call.id`，Responses 优先使用 `call_id`，Anthropic 使用 `tool_use.id`。它们统一进入 `ToolCallBlock.id`。

Runtime 不修改这个 ID。工具执行事件和 ToolResultBlock 都使用同一个值，形成：

```text
ToolCallBlock.id
     ↕
ToolExecutionEvent.tool_call_id
     ↕
ToolResultBlock.tool_call_id
```

当前系统不会在 Provider ID 缟失时静默生成替代 ID。空 ID 属于非法协议状态，会在消息校验阶段明确失败，避免产生无法配对的结果。

## 5. OpenAI Responses 和 Chat Completions 的抽象差异是什么？

Chat Completions 的核心抽象是消息列表，一次响应通常从第一条 choice 中取得 assistant message。工具调用附着在 assistant message 上，后续结果使用 `role="tool"` 消息返回。

Responses 更接近一个统一 item 流。普通消息、reasoning、function call 和 function output 都是 input/output item；system prompt 使用 instructions；工具定义也不是 Chat 的嵌套结构。

Responses 还需要处理推理连续性。某些模型下一轮要求重新带上之前的 reasoning item、ID、summary 或 encrypted content。项目将通用摘要放进 ThinkingBlock，将 Responses 特有的连续性数据放进命名空间 extra，由 Responses Adapter 负责回放。

Runtime 不理解这两种 API 的差异，它只看到相同的 ToolCallBlock、ThinkingBlock 和 StopReason。

## 6. Anthropic Tool Use 与 OpenAI Tool Call 如何映射？

入站时，两者都转换成：

```python
ToolCallBlock(
    id=...,
    name=...,
    arguments={...},
)
```

Anthropic 的 `tool_use.input` 本身就是对象；OpenAI 的 function arguments 通常是 JSON 字符串，需要先解析。如果 JSON 非法，Adapter 保留原始字符串并触发有限的模型纠正请求。

出站工具结果统一保存为 UserMessage 中的 ToolResultBlock。Anthropic Adapter 将多个结果保留在同一条 user message 中；OpenAI Chat Adapter 将其拆成多个 `role="tool"` 条目；Responses Adapter 将其拆成多个 `function_call_output` item。

因此 Runtime 只维护一种自然的结果包结构，Wire 差异由 Adapter 处理。

## 7. System Prompt、工具定义和多模态内容如何统一表达？

System Prompt 是 LLMRequest 的独立字段，不属于普通 Runtime transcript。Adapter 分别将它转换成 OpenAI Chat 的 system message、Responses 的 instructions 或 Anthropic 的顶层 system。

工具统一表示为：

```python
LLMTool(
    name=...,
    description=...,
    parameters={...},  # JSON Schema
)
```

本地 `execute` 函数、超时和执行模式不会进入 LLMTool，它们只保留在 Runtime 的 AgentTool 中。

多模态内容统一为有序 Content Block，包括 TextBlock、ImageBlock、ThinkingBlock、ToolCallBlock 和 ToolResultBlock。Adapter 再将 ImageBlock 转换为各 Provider 的 image URL、base64 source 或 input image 结构。

## 8. Streaming Event 如何映射成项目自有事件？

这里需要区分 LLM StreamEvent 和 Runtime Event。

Provider 的流式数据先由 Adapter 转换成项目自有的 LLM StreamEvent：

- `text_delta`
- `thinking_delta`
- `tool_call_start`
- `tool_call_delta`
- `tool_call_complete`
- `usage_update`
- `done`

`done` 携带规范化 LLMResponse。Bridge 再把 LLMResponse 转成 AssistantMessage，Runtime 记录 ModelResponseEvent 和 MessageEvent。

当前三个真实 Adapter 仍使用阻塞 SDK 调用，取得完整响应后再发出一次性标准 StreamEvent，而不是逐 Token 实时转发。Token 级增量目前也不会逐条写入 Runtime Event Stream。这是当前实现边界，不应描述成已经完成端到端实时 Streaming。

## 9. Token Usage 和成本统计由哪一层负责？

Token 规范化由 Provider Adapter 负责，因为只有 Adapter 知道供应商字段的真实含义。

统一 TokenUsage 包含四个桶：

```text
input_tokens
output_tokens
cache_read_tokens
cache_write_tokens
```

OpenAI 报告的 input 通常已经包含 cached tokens，所以 Adapter 会减去缓存部分，转换成项目的可加和口径。Anthropic 原生分开报告输入、Cache Read 和 Cache Creation，可以直接映射。

Runtime 只把 usage 和 model 记录在 AssistantMessage 与 ModelResponseEvent 上。美元成本由下游 `RunCost + PriceBook` 计算，不进入核心循环。这样价格表变化时，可以重新计算成本而不修改历史运行事实。

## 10. Provider 不返回 Token Usage 时怎么办？

Adapter 返回全零 TokenUsage，Bridge 会把它视为“未知”，而不是权威的零消耗，并在 AssistantMessage 上保存为 `usage=None`。

上下文管理会退化到字符数和图片固定权重等启发式估算，用于控制上下文预算，但不能冒充 Provider 精确 Token。

成本层也不会把缺失 Usage 解释成真实零成本。没有 Usage 就无法精确定价；如果模型有 Usage 但价格表没有对应模型，则 Token 仍会统计，同时把模型列入 `unpriced_models`，提示总成本只是下界。

## 11. OpenAI-compatible 服务并不完全兼容时怎么处理？

我不会把 OpenAI-compatible 理解成完全兼容。配置时必须明确选择实际 Wire 协议，例如 `openai-chat`，并通过 `base_url` 连接对应端点。

对常见差异，Adapter采用受控兼容：字段读取有合理缺省；推理内容可以识别不同响应字段；`Provider.replay_reasoning` 可以关闭某些端点不接受的推理回放；request extra 可以传递该端点明确支持的参数；raw 请求和响应用于核对实际 Wire。

如果服务只是在少量字段上兼容，可以继续使用现有 Adapter。如果消息、工具或停止语义已经实质不同，就应该新建 Adapter，而不是在 Runtime 中增加 endpoint 判断。

当前没有自动探测兼容能力的握手协议。配置错误或未知 API 会明确失败，不会静默回退。

## 12. 如何区分 Provider 通用能力和供应商特有能力？

判断标准是这项信息是否会被多个 Provider 使用，以及它是否影响 Runtime 的通用行为。

文本、图片、Thinking、工具调用、工具结果、StopReason、TokenUsage 和 Reasoning Effort 属于通用能力，进入项目自有类型。

Anthropic 的 cache breakpoint、OpenAI Responses 的 encrypted reasoning content、previous response ID 等属于供应商特例，放在命名空间 `extra` 中，仅由对应 Adapter 读取。

SDK 原始字段进入 raw，用于调试和审计，不自动提升成核心协议。只有当一个特例变成稳定、跨 Provider 且被多个模块读取的能力时，才考虑将它提升为正式字段。

## 13. 模型不支持并行工具调用时，Runtime 怎么降级？

模型是否能够一次生成多个 ToolCall，与 Runtime 是否能够并行执行工具是两个问题。

如果模型不支持并行 Tool Call，它通常每轮只产生一个调用，Runtime 自然按多轮串行执行，不需要特殊分支。OpenAI Chat 还可以通过 request extra 设置 `parallel_tool_calls=False`。

如果模型产生多个调用，Runtime 根据本地 AgentTool 的 `execution_mode` 决定执行方式。只要其中一个有效工具声明为 `sequential`，本组调用就使用单 Worker 顺序执行；否则可以并行执行。

当前项目没有模型能力注册表，也不会自动探测“这个模型是否支持并行工具调用”。该能力目前由 Provider 配置、请求参数和模型实际输出共同决定。

## 14. Provider Adapter 会不会成为“最低公共能力”抽象？

这是 Adapter 设计最大的风险，所以我没有只保留纯文本、普通消息和简单函数调用。

通用 Content Block 保留了图片、Thinking、多个工具调用、工具结果身份、缓存 Usage 和 Reasoning Effort。供应商专有能力通过 namespaced extra 保留，raw 又保存了完整边界证据。

因此统一层只统一 Runtime 真正需要稳定理解的语义，不试图统一供应商 API 的每个字段。这样既避免核心被某一家 Provider 污染，也不要求丢弃供应商高级能力。

代价是部分特性只能在特定 Adapter 中生效，但这种局部差异比把大量条件判断扩散到核心更可控。

## 15. 新接入一个 Provider，需要修改哪些地方？

正常情况下不需要修改 Runtime、State、Message、工具调度或 Workflow。

接入步骤是：

1. 为新的 Wire 协议增加 Adapter 模块。
2. 实现 `LLMRequest -> Iterator[StreamEvent]`。
3. 将统一消息、工具、图片和推理参数转换成 Provider Wire。
4. 将响应转换为 Content Block、统一 StopReason 和 TokenUsage。
5. 保证最后产生携带 LLMResponse 的 `done`。
6. 使用 `register_adapter(api, stream)` 注册。
7. 必要时扩展 ApiKind、协议默认值、环境配置和可选依赖。
8. 增加 Wire 转换、工具结果、Usage、错误和多模态测试。

如果新 Provider 带来项目尚未支持的稳定模态，例如音频，就需要有意识地扩展 Content Block，并同步更新所有相关 Adapter 和 Trace，而不是把音频字典藏在某个 Adapter 中。

## 16. 如何证明不是大量条件分支完成适配？

第一，核心 Runtime 没有导入 OpenAI 或 Anthropic SDK，也不读取 choices、output、tool_use 等供应商字段。

第二，Provider 选择只发生在 LLM 注册表，Runtime 不包含 `if provider == ...`。

第三，Bridge 只负责 Runtime Message 与统一 LLM 类型之间的投影，不负责 Wire 翻译。

第四，每个 Adapter 只处理自己的消息格式、停止原因和 Usage；真正相同的逻辑，例如参数解析、OpenAI Usage 归一化和统一响应尾部，才进入共享 Adapter Spine。

第五，相同的 ToolCallBlock 和 ToolResultBlock 会通过测试转换成 Anthropic、OpenAI Chat 和 Responses 三种不同 Wire，再还原成相同 Runtime 语义。

我的判断标准是：接入一个新 Provider 时，如果需要修改 `core.run()` 或工具调度器，说明现有抽象仍然泄漏了供应商差异。
