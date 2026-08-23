# 02. 模型适配器：OpenAI / Anthropic

本文对应 [`question-checklist.md`](question-checklist.md) 中第二部分的问题。每道题先给第一轮
口述主回答，再单独记录追问答案。

## 18. OpenAI 和 Anthropic 的工具调用协议有哪些差异？

### 口述主回答

两家都能表达工具声明、工具调用和工具结果，但接口报文差异很大。项目里实际先把模型返回的
调用统一成包含调用编号、工具名和参数的 `ToolCallBlock`，把执行结果统一成能够关联原调用的
`ToolResultBlock`，所以运行时只处理一套工具语义。

OpenAI Chat 把工具调用放在助手消息的专门字段中，参数以 JSON 字符串返回，工具结果通常需要
拆成独立的工具角色消息。Anthropic 则把工具调用和普通文本一样放在内容块中，参数直接是结构化
对象；工具结果放在用户消息的结果块中，一条消息可以携带多个结果。适配器负责这些格式转换，
同时保证调用编号和结果的对应关系不变。

### 技术追问补充

假设模型调用 `read(path="README.md")`，调用编号为 `call_1`。

**OpenAI Chat** 的调用参数是 JSON 字符串，结果使用独立的工具角色消息：

```json
{"role":"assistant","tool_calls":[
  {"id":"call_1","type":"function",
   "function":{"name":"read","arguments":"{\"path\":\"README.md\"}"}}
]}
{"role":"tool","tool_call_id":"call_1","content":"README content"}
```

**OpenAI Responses** 使用扁平输入项，调用和结果分别是独立条目：

```json
{"type":"function_call","call_id":"call_1",
 "name":"read","arguments":"{\"path\":\"README.md\"}"}
{"type":"function_call_output","call_id":"call_1",
 "output":"README content"}
```

**Anthropic** 把调用放在助手内容块中，参数直接是对象；结果放在用户内容块中：

```json
{"role":"assistant","content":[
  {"type":"tool_use","id":"call_1",
   "name":"read","input":{"path":"README.md"}}
]}
{"role":"user","content":[
  {"type":"tool_result","tool_use_id":"call_1",
   "content":"README content"}
]}
```

**并行调用**时，假设还有一个 `call_2`。Anthropic 可以把两个 `tool_result` 放在同一条
用户消息中；OpenAI Chat 要拆成两条工具角色消息。无论报文怎样排列，`call_1` 和 `call_2`
都必须保留，Runtime 才能把每个结果关联回原调用。

## 19. 你的适配器抽象是什么样的？

### 口述主回答

适配器的核心职责是接收一次统一的模型请求，返回统一的模型响应。代码里的接口形状是：
输入 `LLMRequest`，输出一组模型流事件，最后一个事件必须携带完整的 `LLMResponse`。
运行时不直接调用 OpenAI 或 Anthropic 的客户端。

实际链路是：运行消息先转换成模型可读消息，本地工具只把名称、说明和参数结构暴露给模型；
注册表再根据模型配置选择具体适配器。适配器负责生成实际接口报文并调用模型服务，响应回来后
统一整理文本、思考内容、工具调用、停止原因和用量，最后再包装成能够写入运行状态的助手消息。

### 技术追问补充

- **三层边界**：运行时负责回合、状态、工具执行和停止；统一模型协议描述一次调用的输入输出；
  供应商适配器负责客户端调用和接口字段转换。
- **组装位置**：`make_llm_agent()` 把模型配置、系统提示和工具绑定到 Agent，在运行消息与
  统一模型协议之间转换。
- **新增模型服务**：新增并注册适配器，验证请求报文、响应转换和工具调用编号；核心 Agent
  Loop 不应修改。
- **当前流式边界**：真实适配器目前先执行阻塞式客户端请求，再把完整响应转换成统一事件；
  已统一消费接口，但还没有实现网络层逐 Token 转发。

## 20. 如何统一不同模型服务的消息、工具调用、工具结果和响应语义？

### 口述主回答

我统一的是 Runtime 真正依赖的稳定语义，而不是各家 API 的报文格式。项目把模型输入统一成
`LLMRequest`，消息使用有序 Content Block 表达文本、图片、Thinking、工具调用和工具结果；响应则
统一成内容、停止原因、Token 用量和实际模型标识。具体的 system prompt、工具字段和推理参数怎样
落到 OpenAI 或 Anthropic 报文，由各自 Adapter 负责。

工具调用中最重要的不变量是调用编号不能变化。不同 Provider 返回的调用都会转换成
`ToolCallBlock(id, name, arguments)`，Runtime 执行后再用同一个 id 生成 `ToolResultBlock`，所以
并行结果也能正确配对。供应商独有能力放在带命名空间的 `extra`，原始请求和响应保存在 `raw`；
Runtime 只读取统一字段，不依赖某家 SDK 对象。

### 技术追问补充

- **两次投影隔离了三套语义**：`message_to_llm_message()` 负责 Runtime Message 到模型消息的
  投影，保留 role 和 content，去掉 sender、target、kind 等运行时路由字段；
  `llm_response_to_assistant_message()` 再把规范响应包装回带路由和 step/final 语义的
  AssistantMessage。Adapter 位于两次投影之间，只处理 Provider 协议。
- **统一内容不是纯文本字符串**：`LLMMessage.content` 和 `LLMResponse.content` 都是有序
  `ContentBlock` 元组，可以包含 `TextBlock`、`ImageBlock`、`ThinkingBlock`、
  `ToolCallBlock` 和 `ToolResultBlock`。`text`、`thinking_blocks`、`tool_calls` 只是从有序内容
  派生出的读取视图，不是另一份可能失真的状态。
- **一次工具调用的规范化往返**：

  ```text
  Provider 响应
    OpenAI tool_calls / Responses function_call / Anthropic tool_use
        ↓ Adapter 解析
  ToolCallBlock(id="call_1", name="read", arguments={"path": "README.md"})
        ↓ Runtime 本地执行并按 id 配对
  ToolResultBlock(
      tool_call_id="call_1",
      tool_name="read",
      content=(TextBlock("README content"),),
      is_error=False,
  )
        ↓ 下一轮 Adapter 投影
    OpenAI Chat role=tool + tool_call_id="call_1"
    OpenAI Responses function_call_output + call_id="call_1"
    Anthropic tool_result + tool_use_id="call_1"
  ```

  多个并行结果在 Runtime 中可以位于同一个 UserMessage；Anthropic 保持为一条用户消息中的多个
  `tool_result`，OpenAI Chat 则拆成多条 `role="tool"` 消息。形状可以变化，但调用 ID 和结果内容
  不能变化。现有 Adapter 测试会核对两个并行结果拆分后仍分别对应原来的 `a`、`b`。
- **停止原因直接连接 Runtime 语义**：例如 OpenAI Chat 的 `stop`、`tool_calls`、`length` 分别
  映射为 `end_turn`、`tool_use`、`max_tokens`；Anthropic 的 `end_turn`、`tool_use`、
  `max_tokens` 做同类映射。`make_llm_agent()` 只有在 `stop_reason == "end_turn"` 时把消息标为
  `final`，其他情况标为 `step`；核心循环再从消息中的 Tool Call 判断是否实际调度工具。
- **Token 用量统一了计数口径，而不只是字段名**：`TokenUsage` 固定为 `input_tokens`、
  `output_tokens`、`cache_read_tokens`、`cache_write_tokens`。OpenAI 的输入 Token 已包含缓存命中，
  Adapter 会先减掉 cached tokens，避免计算 Context 时重复计数；Anthropic 原生把缓存读写单列，
  可以直接映射。
- **异常也要转换成可恢复语义**：OpenAI 返回的工具参数是 JSON 字符串。若模型生成非法 JSON，
  Adapter 不让 SDK 对象或解析异常直接穿透 Runtime，而是把原字符串放入 `_raw_arguments`，由统一
  重试层要求模型修正。供应商限流同样在模型访问层处理，不污染 Agent Loop。
- **单家能力使用受控逃生口**：请求级 `LLMRequest.extra` 和消息级 `LLMMessage.extra` 承载尚未
  进入公共协议的选项；例如 Anthropic 缓存断点和 OpenAI Responses 推理续接数据使用各自命名
  空间。Adapter 只读取自己的键，未知键忽略，因此同一 Transcript 仍可切换 Provider。
- **`raw` 是证据，不是控制面**：`LLMResponse.raw` 保存传给 SDK 的完整请求参数和 SDK 响应快照，
  用于检查 system prompt、reasoning、cache control 等字段最终如何落到 Provider 报文。工具调度、
  停止判断和成本聚合仍只依赖规范字段，避免业务逻辑重新绑定某家 SDK 对象。
- **当前边界**：真实 Adapter 目前使用阻塞式 SDK 调用，再把完整响应转换成统一 StreamEvent，尚未
  做网络层逐 Token 转发；未知 Provider stop reason 当前回退为 `end_turn`，因此新增 Adapter
  时必须用测试明确覆盖停止原因映射，不能只验证文本能够返回。

## 核对依据

- [`llm/types.py`](../../src/simple_long_horizon_agent/llm/types.py)
- [`llm/bridge.py`](../../src/simple_long_horizon_agent/llm/bridge.py)
- [`llm/stream.py`](../../src/simple_long_horizon_agent/llm/stream.py)
- [`llm_agent.py`](../../src/simple_long_horizon_agent/llm_agent.py)
- [`openai_chat.py`](../../src/simple_long_horizon_agent/llm/adapters/openai_chat.py)
- [`openai_responses.py`](../../src/simple_long_horizon_agent/llm/adapters/openai_responses.py)
- [`anthropic_messages.py`](../../src/simple_long_horizon_agent/llm/adapters/anthropic_messages.py)
- [`05-model-access.md`](../design/05-model-access.md)
- [`test_real_adapters.py`](../../tests/unit/test_real_adapters.py)
