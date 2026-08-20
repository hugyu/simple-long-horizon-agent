# 面试专题回答：MCP 与子 Agent 委派

本文覆盖 [`question-checklist.md`](question-checklist.md) 中第 5 个专题。回答以当前
MCP client、Task Tool 和对应测试为事实边界；项目目前接入的是 MCP Tools，不把
MCP Resources 或 Prompts 描述成已有能力。

## 1. MCP 在架构中处于什么位置？

我把 MCP 放在工具集成边界，而不是 Agent Runtime 核心。Runtime 只认识统一的
`AgentTool`：名称、描述、参数 Schema、执行函数、超时和执行模式。MCP client 负责
连接外部 Server、发现工具，再把每个 MCP Tool 包装成同样的 `AgentTool`。

这样核心循环不需要知道 stdio、HTTP、异步 SDK 或 MCP content 类型。模型提出的仍是
普通 Tool Call，执行结果仍以 `ToolResult` 回到 State。目前代码只接入了 MCP Tools；
Resources 和 Prompts 尚未成为 Runtime 能力。

## 2. MCP Tool 与本地 Tool 是否使用同一个抽象？

是的，进入 Runtime 后使用同一个 `AgentTool` 抽象。本地 Bash、Read 和 MCP Tool
都由同一调度器执行，也共享 Tool Call ID、超时、abort、错误结果和 Trace 事件语义。

差异被留在适配层：本地工具直接执行 Python 函数；MCP Tool 把同步调用提交给后台
`asyncio` loop 中的 `ClientSession.call_tool()`。MCP Server 的原始工具名用于线上
调用，模型可见名称默认加 Server 前缀，避免多个 Server 之间静默重名。

## 3. MCP Server 不可信时有哪些风险？

主要风险包括恶意或误导性的工具描述和 Schema、返回内容中的 Prompt Injection、超长或
敏感结果、Server 进程自身的文件和网络权限，以及工具产生的外部副作用。JSON Schema
只能约束参数形状，不能把不可信 Server 变成安全执行环境。

当前项目提供超时、abort、名称隔离、内容类型转换和 Hook gate，但不是完整安全沙箱。
真正部署时我会把不可信 Server 放入最小权限的进程或容器，限制网络、文件系统、凭证和
输出大小；高风险操作还要经过宿主审批。返回内容应按不可信数据处理，不能因为来自工具
就获得 system prompt 的可信级别。

## 4. 如何处理 MCP 连接断开、协议错误和 Schema 变化？

连接建立是原子的：握手或工具发现失败时，`open()` 会清理半启动的线程、event loop 和
stdio 子进程。调用期间的异常会转成 `is_error=True` 的 Tool Result；如果连接已经死亡，
结果还会带 `terminate=True`，避免 Agent 继续消耗回合调用一个不可恢复的连接。单次超时
但连接仍存活时则允许模型下一轮决定是否重试。

Schema 在当前实现中只在 `open()` 时发现并固定到该连接生命周期，没有热更新或版本协商。
Server 改 Schema 后，需要重连并重新构建 Agent tools。明显不完整的 input schema 会补成
最小 object schema，但这不是兼容任意破坏性变更；生产化时还应记录 Server 版本或 Schema
摘要，并在变化时做兼容检查。

## 5. 子 Agent 与普通 Tool Call 的本质区别是什么？

从父 Agent 的协议看，子 Agent 就是一次普通 Tool Call；从执行语义看，它内部又运行了一套
完整 Agent Loop。普通 Tool 通常完成一个确定的外部操作，子 Agent 则可以多轮调用模型和
工具，拥有自己的 State、上下文和停止过程。

这种设计让父循环保持不变：父模型调用 `task`，Task Tool 从注册表选择子 Agent，完整消费
子运行，再把最终文本作为 Tool Result 返回。子运行事件放在非模型可见的 details 中，供
Trace 合并，而不是把整个子 transcript 塞回父模型上下文。

## 6. 子 Agent 拥有哪些上下文、工具和权限？

子 Agent 拥有独立 `State`，不会自动继承父 Agent 的完整消息历史。它看到的是父 Agent
明确提交的 `task`、可选 `context` 和 Task Tool 配置的默认上下文。它能使用哪些工具、
system prompt 和 Context Policy，在构建这个已注册子 Agent 时就确定了。

权限也不会因为“子 Agent”身份自动收窄。当前安全边界取决于分配给它的工具和外部运行
环境，因此调用者必须按最小权限组装。父级 abort 会传入子运行，Task Tool 还为子运行设置
独立的 hard `max_turns`，可选 soft turn reminder 只提醒，不增加预算。

## 7. 主 Agent 如何描述子任务并判断结果是否可用？

父模型通过 `subagent_type`、非空 `task` 和可选 `context` 描述委派。可选类型来自显式注册表，
未知类型会成为结构化工具错误，不支持动态 import 任意 Agent。

当前 Task Tool 对“可用”的最低判断是：子 Agent 必须产生 `kind="final"` 的消息；否则返回
`is_error=True`。它不会自动验证结果事实是否正确。父 Agent 可以结合任务环境继续检查，
或者由上层 Workflow 的 verifier 判断。需要客观质量门槛时，应该给子任务定义结构化产物
和外部检查，而不是把子 Agent 的自信文本当成证据。

## 8. 子 Agent 返回完整 Trace、摘要还是结构化结果？

模型可见通道返回子 Agent 最终消息的文本，因此默认是简洁结果，而不是完整 Trace。完整
子事件列表保存在 `ToolResult.details["sub_events"]`，父 State 随后按 Tool Call ID 把它放到
工具结果消息的 sidecar 中，Trace 层可以据此合并父子 Span。

当前没有通用的结构化子任务结果 Schema；如果某类委派需要补丁、引用或置信度，我会为该
专用工具定义明确返回协议，而不是把所有信息塞进自由文本。完整 Trace 不应直接回灌模型，
否则会迅速扩大父上下文。

## 9. 子 Agent 失败后由谁重试？

当前没有 Task Tool 内部的自动重试。未知类型、没有 final、异常或中止会作为错误 Tool
Result 返回父 Agent，由父模型决定修改任务、换一个子 Agent 或继续自己处理。

这是为了避免对子 Agent 内部可能发生的副作用做盲目重放。若要自动重试，我会先区分可重试
的模型或基础设施错误与业务失败，并为写操作提供幂等键和外部状态查询；否则一次“重试子
Agent”可能把它已经执行成功的修改再做一遍。

## 10. 如何避免递归委派或 Agent 数量失控？

当前主要依靠静态能力组装和预算：只有显式给某个子 Agent 配置 Task Tool，它才有继续委派
的能力；每次子运行有 `max_turns`，父级 abort 也会传播。普通 `task_tool()` 本身没有通用
递归深度、全局子 Agent 数量或跨树 Token 预算，所以我不会说项目已经完整解决递归失控。

如果进一步工程化，我会在调用链携带 delegation depth、全局 run budget 和稳定 parent run
ID，在创建子运行前原子扣减配额；达到上限直接返回结构化拒绝。对默认 Agent，我更倾向让
执行型子 Agent 不再拥有 Task Tool，从能力上切断无意义递归。

## 11. 子 Agent 是否真的提高成功率？如何做消融实验？

现有代码和单元测试只能证明委派、独立 State、abort 传播和 Trace 合并按协议工作，不能证明
子 Agent 一定提高成功率。README 的 SWE-bench Pro 配置提到 chained workflow 和 `task`
工具，但仓库没有公开一组只改变 Task Tool 的组件级 Ablation，所以我不会把端到端分数提升
归因给子 Agent。

合理实验应固定模型版本、任务集合、prompt、总 Token/时间预算和并发，比较单 Agent、相同
预算下的子 Agent 版本，并至少记录成功率、成本、墙钟时间和失败类型。最好对随机任务做多次
重复或配对统计；如果只跑一次，结论只能是该次配置结果，不能证明稳定因果收益。

## 核对依据

- [`tools/task.py`](../src/simple_long_horizon_agent/tools/task.py)
- [`mcp/client.py`](../src/simple_long_horizon_agent/mcp/client.py)
- [`mcp/content.py`](../src/simple_long_horizon_agent/mcp/content.py)
- [`06-tools-and-integrations.md`](../docs/design/06-tools-and-integrations.md)
- [`tests/unit/test_core.py`](../tests/unit/test_core.py)
- [`tests/unit/test_mcp.py`](../tests/unit/test_mcp.py)
