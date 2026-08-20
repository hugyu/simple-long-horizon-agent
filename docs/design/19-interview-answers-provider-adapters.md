# 19. 面试专题回答：Provider-neutral 协议与 Adapter

本文覆盖 [`17-interview-question-checklist.md`](17-interview-question-checklist.md)
中第 2 个专题的全部问题。回答以当前 OpenAI Chat、OpenAI Responses、Anthropic
Messages 和 Fake Adapter 的代码与测试为事实依据。

## 1. OpenAI 与 Anthropic 的消息和工具调用协议有哪些结构差异？

主要差异不在“都能发文本”，而在 system prompt、工具调用、工具结果和 Thinking 的
组织方式。

OpenAI Chat 通常把 system prompt 作为一条 `role="system"` 的消息；工具调用放在
assistant 消息的 `tool_calls` 字段里；每个工具结果要拆成独立的 `role="tool"`
消息。Anthropic 则把 system prompt 放在顶层 `system` 字段，工具调用是 assistant
内容中的 `tool_use` block，多个工具结果可以放在同一条 user 消息的多个
`tool_result` block 里。

OpenAI Responses 又是第三种形状：system prompt 使用 `instructions`，对话、函数
调用和函数结果被展开成扁平的 input item。我的做法不是让 Runtime 理解这三套格式，
而是先统一成 `LLMMessage`、`ToolCallBlock` 和 `ToolResultBlock`，最后由各自的
Adapter 处理排列差异。

## 2. 统一 `Message` 的最小字段是什么？

如果说 Runtime 中的 `Message`，我认为最小语义包括 role、按顺序排列的 content、
sender、target 和 kind。role 决定模型如何接收这条消息；content 保存文本、图片、
Thinking、Tool Call 或 Tool Result；sender、target 和 kind 用来表达运行时来源、
目标和任务阶段。

进入模型边界后，`LLMMessage` 会收窄为 role、content 和可选 extra。Runtime 的
sender、target、kind 不直接发给 Provider，因为这些属于本地运行语义。

我没有把所有内容压成一个字符串。Tool Call ID、图片 MIME 类型和 Thinking 签名如果
丢失，下一轮重放时就无法保持原来的协议关系。

## 3. 多段内容、工具调用 ID、系统消息和推理内容如何映射？

多段内容在项目里统一表示为有顺序的 Content Block。Adapter 按原顺序读取
`TextBlock`、`ImageBlock`、`ThinkingBlock` 和 `ToolCallBlock`，再翻译成对应
Provider 的内容结构。

工具调用通过 `ToolCallBlock.id` 和 `ToolResultBlock.tool_call_id` 配对。OpenAI
Chat 把这个 ID 放到 `tool_calls[].id` 和后续 `role="tool"` 消息中；Anthropic
映射为 `tool_use.id` 与 `tool_result.tool_use_id`；OpenAI Responses 使用
`function_call.call_id` 和 `function_call_output.call_id`。

固定 system prompt 保存在 `LLMRequest.system_prompt`，普通运行时系统消息仍是
`LLMMessage(role="system")`。Thinking 则保存在 `ThinkingBlock` 中：Anthropic 会
保留 signature 和 redacted 状态，OpenAI Responses 会保留 reasoning item 的 ID 和
可重放信息，OpenAI Chat 会识别 `reasoning_content` 等字段。是否重放由 Provider
配置控制。

## 4. Provider 不支持并行工具调用时怎么办？

我会把“模型能否一次返回多个 Tool Call”和“Runtime 能否并行执行”分开看。统一协议
允许一条 AssistantMessage 带多个 Tool Call，但某个 Provider 如果只能生成一个，
Adapter 不需要伪造并行能力，Runtime 就按它实际返回的调用执行。

如果 Provider 支持多个调用但不接受某个并行开关，Adapter 只发送该接口支持的参数。
例如 OpenAI Chat 可以通过 request extra 设置 `parallel_tool_calls`，但这不是统一
协议的必填字段，也不会要求 Anthropic 使用同名参数。

真正执行时，是否并行由本地 `AgentTool.execution_mode` 决定，不由 Provider
直接控制。这样即使模型一次提出多个调用，我也可以因为工具有共享资源而强制顺序执行。

## 5. 流式响应如何转换成统一事件？

模型访问层定义了统一的 `StreamEvent`，包括文本增量、Thinking 增量、工具调用开始、
参数增量、工具调用完成、Token 用量更新和最终 `done`。最后一个 `done` 必须携带完整
`LLMResponse`，调用者不能只靠前面的增量自己猜最终结果。

当前真实 Adapter 的边界需要说清楚：OpenAI Chat、OpenAI Responses 和 Anthropic
现在使用阻塞式 SDK 调用，拿到完整响应后再映射成这些统一事件。比如一个完整 Tool Call
会产生 start 和 complete，一段完整文本通常产生一次 text delta。

所以项目已经有统一的流事件协议，但还没有把三家 Provider 的网络级流式接口真正接入。
如果继续实现真实 streaming，我会保持 `done.response` 与阻塞模式一致，并在参数增量
未形成合法 JSON 前不创建最终 ToolCallBlock。

## 6. Adapter 只做格式转换，还是负责重试、Token 统计和错误处理？

我把职责拆成两层。具体 Adapter 负责请求和响应格式转换，包括 system prompt 放在哪、
工具如何编码、停止原因如何映射，以及 Provider usage 如何规范化。它也负责发现明显的
协议错误，例如响应没有任何 choice 或事件流没有最终 `done`。

重试不放进每个 Adapter，而是放在统一模型访问层。`complete_with_retry()` 处理限流和
部分服务端错误，使用有上限的指数退避；`complete_with_tool_call_retry()` 处理模型输出
了未知工具或非法 JSON 参数的情况，会追加纠正说明后重新请求。

Token 统计由 Adapter 读取 Provider 的 usage，再转换成统一 `TokenUsage`。例如 OpenAI
报告的 cached token 通常已经包含在 input 总量里，Adapter 会先减出来；Anthropic 的
缓存字段本来就是独立计数，直接映射即可。

## 7. 如何避免统一协议退化成“最小公分母”？

我的原则是统一共同语义，不强行抹平所有 Provider 特性。文本、图片、Thinking、Tool
Call、Tool Result、usage 和 stop reason 这些跨 Provider 都有稳定含义，所以进入公共
协议。

某家 Provider 独有、但又确实需要保留的能力，会放在受控 extra 或 sidecar 中。例如
Anthropic 的 cache breakpoint、OpenAI Responses 的 reasoning item 和 encrypted
content，都有明确命名空间，不会污染其他 Adapter。

当某个扩展被多家 Provider 稳定支持，而且多个模块都开始依赖它时，我会把它提升为正式
字段。反过来，如果只是把任意字典原样透传，统一协议就失去了边界价值，所以当前 Adapter
只读取自己明确支持的 extra 键。

## 8. OpenAI-compatible 是否真的兼容？遇到过哪些协议不一致？

我不会把 OpenAI-compatible 理解成完全兼容。当前实现复用 OpenAI Chat SDK 和
`base_url` 接入这类服务，但代码里仍保留了兼容差异的处理。

一个明确例子是 reasoning 字段。有的服务返回 `reasoning_content`，有的网关返回
`reasoning`，但下一轮重放时又可能要求标准的 `reasoning_content`。当前 Adapter 会
识别这两种输入字段，并记录来源；对外重放时使用当前实现约定的标准字段。

另一个风险是工具参数、停止原因、图片支持和额外请求参数。项目通过无网络 stub 测试
OpenAI wire 形状，但没有声明所有 OpenAI-compatible 服务都通过了完整兼容矩阵。接入
新服务时，我会至少验证多轮 Tool Call、Thinking 重放、usage、超时和错误响应，而不是
只测试一次文本请求成功。

## 9. Provider 原始响应是否保存？为什么不能直接进入核心 State？

当前会保存原始请求和响应快照。`LLMResponse.raw` 保存实际跨模型边界的数据，转换成
`AssistantMessage` 后会放到 `sidecar["raw"]`，Trace 层还可以把它外置到单独的 raw
记录中，避免主轨迹因为重复历史而快速膨胀。

但 Provider SDK 对象不能直接成为核心 State 的协议。它们可能不可序列化、版本不稳定，
而且不同 Provider 的字段完全不同。如果 Runtime 直接读取这些对象，切换模型服务就会
迫使核心循环和上下文代码一起修改。

所以核心 State 保存项目自己的 Message、Event 和 Content Block；raw 只作为调试证据
放在受控 sidecar 中。正常运行逻辑不能依赖 raw 才能理解 Tool Call、usage 或停止原因，
这些稳定语义必须先被 Adapter 提升为正式字段。

## 10. 如何测试 Adapter 的行为一致性？

我主要用无网络 stub 测试真实 Adapter 的请求和响应转换。测试会捕获传给 SDK 的完整
参数，然后断言 system prompt、消息顺序、工具 Schema、Tool Call ID、工具结果形状、
Thinking、停止原因和 usage 是否正确。

一致性不是要求三家的 wire 完全相同，而是要求经过转换后语义相同。比如同一个包含两个
Tool Result 的统一消息，Anthropic 应生成一条带两个 result block 的 user 消息，
OpenAI Chat 应拆成两条 `role="tool"` 消息，但两个调用 ID 和结果内容必须保持一致。

我还测试了多模态工具结果、Thinking 重放、缓存 Token 归一化、未知 extra 的忽略规则、
缺少 API key 的错误以及非法 Tool Call 重试。当前不足是实时端到端测试覆盖有限，而且
OpenAI-compatible 服务没有系统化兼容矩阵；这部分不能只靠 stub，需要按目标服务增加
小规模 live smoke test。

## 核对依据

- [`llm/types.py`](../../src/simple_long_horizon_agent/llm/types.py)
- [`llm/bridge.py`](../../src/simple_long_horizon_agent/llm/bridge.py)
- [`llm/adapters/`](../../src/simple_long_horizon_agent/llm/adapters/)
- [`llm/retry.py`](../../src/simple_long_horizon_agent/llm/retry.py)
- [`05-model-access.md`](05-model-access.md)
- [`tests/unit/test_real_adapters.py`](../../tests/unit/test_real_adapters.py)
- [`tests/unit/test_llm_retry.py`](../../tests/unit/test_llm_retry.py)
