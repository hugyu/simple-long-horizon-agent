# 21. 面试专题回答：并行 Tool Calling、超时与中止

本文覆盖 [`17-interview-question-checklist.md`](17-interview-question-checklist.md)
中第 4 个专题的全部问题。回答以当前同步 Runtime、线程池调度、协作式 abort 和工具级
超时实现为事实边界。

## 1. 哪些工具调用可以并行，谁负责判断依赖关系？

当前由工具定义者通过 `AgentTool.execution_mode` 声明是否允许并行。默认值是
`parallel`；如果某个工具依赖共享终端、编辑会话或其他不能安全并发的资源，就应声明为
`sequential`。

Runtime 不会从自然语言参数里自动推断调用依赖，也不会构建 Tool Call DAG。同一轮只要
有一个有效工具声明为 sequential，当前实现就会把整组调用降成单 worker 顺序执行。

这是一个保守选择：依赖关系由最了解副作用的人，也就是工具作者或组装 Agent 的代码负责。
代价是两个本来互不影响的调用，可能因为同组存在一个 sequential 工具而失去并行收益。

## 2. 模型一次返回多个 Tool Call 时如何调度？

Runtime 先按模型返回顺序收集所有 Tool Call，并为每个调用记录
`ToolExecutionStartEvent`。然后依次执行 `PRE_TOOL_USE` Hook；被 Hook 阻止的调用不会
进入线程池，但仍会生成错误结果。

剩余调用如果都允许并行，就提交到 `ThreadPoolExecutor`；worker 数取本轮有效调用数和
`max_concurrency` 的较小值，当前默认上限是 8。如果其中有 sequential 工具，worker 数
直接变成 1。

所有调用结束后，Runtime 把结果合并成一条 `tool_result` 用户消息，再进入下一轮模型
调用。模型不会在某一个并行工具刚完成时提前开始下一轮。

## 3. 完成顺序不确定，结果如何确定性写回？

工具执行完成事件按真实完成顺序记录，因为这能反映实际耗时和并发过程；但模型看到的结果
不会按完成顺序排列。

Runtime 用 `tool_call_id` 把每个结果存入字典，最后重新遍历模型最初返回的 Tool Call
列表，按原顺序构造 `ToolResultBlock`。所以即使第二个调用先完成，结果包里仍然先放第一
个调用的结果。

这样同时保留了两种信息：Event Stream 反映真实执行时间，Message 保持稳定的模型输入
顺序。测试和重放不会因为线程调度不同而改变 Tool Call 与结果的对应关系。

## 4. 两个并行工具同时修改文件或状态时如何处理冲突？

当前 Runtime 没有事务、文件锁、版本检查或自动冲突合并。如果两个 parallel 工具同时
修改同一个文件或共享对象，结果取决于具体工具实现，核心调度器不会替它们保证一致性。

当前正确做法是把这类工具声明为 sequential，或者让它们操作隔离的工作区，最后由一个
明确的合并步骤提交结果。对于文件编辑，还可以在工具边界加入 expected content、版本号
或 compare-and-swap 检查，让冲突变成结构化错误。

所以我不会说系统已经解决并行写冲突。它提供的是执行模式声明和确定性结果打包，资源级
一致性仍由工具和上层 Workflow 负责。

## 5. 单个工具超时后，其他工具继续还是全部取消？

当前是其他工具继续。单个工具超过 `timeout_seconds` 后，会返回
`is_error=True` 的结构化超时结果；同组其他 Future 不会因此自动取消。

Runtime 会等本轮所有调用都得到结果，再统一写入工具结果消息。这样模型下一轮可以同时
看到哪些调用成功、哪个调用超时，再决定重试、换方案或继续。

需要注意，Python 线程不能被安全地强制杀死。通用超时包装会停止等待，但底层线程可能仍在
运行，所以工具实现仍应支持 abort，或者使用能够真正终止子进程和网络请求的机制。Bash
工具自身使用 subprocess timeout，MCP 调用也有独立超时和中止轮询。

## 6. 中止信号如何跨 Agent Loop、子 Agent 和工具传播？

调用者向 `run()` 传入一个 `abort()` 函数。核心循环每轮开始前检查它，同时把同一个函数
传给所有 `AgentTool.execute`。长时间运行的工具需要主动检查这个信号，这是一种协作式
Cancellation。

Task 工具启动子 Agent 时，会继续把父调用收到的 abort flag 传给子 Agent；消费子事件时
也会再次检查。因此调用者发出中止后，父循环、子 Agent 和支持 abort 的工具可以沿同一
信号停止。

当前限制是 Runtime 不能强制停止任意 Python 线程。MCP 通过短间隔轮询做到较快响应，
但并不是所有工具都保证实时检查。真正需要强隔离的工具应运行在可终止的子进程或容器中。

## 7. Python 中使用线程、进程还是 `asyncio`？为什么？

当前普通 Tool Calling 使用线程池。主要原因是核心 Runtime 和工具协议都是同步接口，而
文件、Shell、网络和子 Agent 等常见工具大多是 I/O 型任务，用线程接入简单，也能避免为
整个教学 Runtime 引入 async 调用链。

当前没有使用多进程执行普通工具。进程适合 CPU 密集或需要强隔离的任务，但会增加对象
序列化、进程生命周期和状态传递复杂度。

MCP 是例外：它的 SDK 是异步的，所以 `MCPConnection` 在专用后台线程里维护一个长期
`asyncio` event loop，再把同步工具调用提交到这个 loop。这样异步资源不会迫使核心
Runtime 改成 async。

## 8. 阻塞型工具如何接入异步 Runtime？

当前核心 Runtime 本身是同步的，所以阻塞型工具可以直接通过线程池执行，不存在“阻塞
async event loop”的问题。

如果以后把 Runtime 改成异步，我会保留同一工具语义，但把同步阻塞工具放进
`asyncio.to_thread()` 或受控 executor；原生异步工具则直接 await。超时用
`asyncio.timeout()`，Cancellation 还要继续传给工具内部资源。

项目现有 MCP 走的是反方向：把异步 SDK 适配成同步 AgentTool。它通过
`run_coroutine_threadsafe` 把调用提交到后台 loop，并用短间隔轮询 timeout 和 abort。

## 9. 工具调用是否支持重试？如何避免副作用重复执行？

当前 Runtime 没有通用的自动 Tool Retry。工具失败或超时后，结果会作为
`is_error=True` 的 Tool Result 返回给模型，模型可以在下一轮修改参数后主动重试。

这是有意的边界。读取类工具通常可以安全重试，但写文件、提交任务、调用支付或外部变更
API 的工具不能在核心层一概重试。是否重试必须结合工具的幂等性、错误类型和外部状态。

当前项目也没有通用幂等键协议。对于有副作用的工具，我会要求调用方提供稳定 operation
ID，工具端保存执行状态，并把“可重试错误”和“结果未知”分开。只有确认前一次未生效时
才自动重试。

## 10. 如何处理“工具实际成功，但响应因超时丢失”？

当前通用 Runtime 不能可靠解决这个 exactly-once 问题。它只能看到等待超时，因而向模型
返回超时错误；如果底层操作实际上已经成功，模型再次调用可能造成重复副作用。

生产化处理需要工具协议和外部系统共同支持。调用前生成稳定的幂等键或 operation ID；
超时后先调用查询接口确认状态；如果外部系统返回已成功，就恢复原结果，如果明确未执行
才允许重试。

如果外部系统既不支持幂等键也不能查询状态，我会把结果标记为 unknown，而不是自动重试。
这时需要补偿操作或人工确认。当前项目没有这套状态机，所以回答时必须明确这是后续方案。

## 11. 如何限制并发数和资源消耗？

当前每组 Tool Call 有 `max_concurrency`，默认最多 8 个 worker；如果调用数更少，就只
创建需要的 worker。只要有 sequential 工具，整组并发数降到 1。每个工具还可以配置
自己的 `timeout_seconds`。

具体工具还会做自己的资源限制，例如 Bash 限制超时和输出字符数，Read 限制读取范围，
Recall 限制索引数和返回字符数。评测场景可以再通过 Docker 限制 CPU、内存、网络和总
执行时间。

当前不足是 `max_concurrency` 还不是 Agent 的公开配置项，也没有跨多轮或多 Agent 的
全局 semaphore、CPU 配额和内存配额。大规模并发需要把限制提升到 Session 或运行调度
层，而不是每轮各自创建线程池。

## 12. 并行执行一定更快吗？什么时候会降低成功率？

不一定。只有调用彼此独立，而且主要等待 I/O 时，并行才容易缩短墙钟时间。调用很短时，
线程调度开销可能比收益更大；多个调用争用同一个 API、磁盘或终端时，还可能触发限流和
资源竞争。

并行也可能降低任务成功率。模型可能把有先后依赖的操作误放在同一轮；两个工具可能同时
修改同一文件；并行返回的大量结果也会一次性增加上下文负担。当前 Runtime 不推断这些
语义依赖。

因此我把并行看成工具能力，而不是默认优化目标。读取不同文件、查询互不相关的信息适合
并行；编辑同一工作区、依赖前一步输出的操作更适合顺序执行或交给 Workflow 明确分阶段。

## 核对依据

- [`core.py`](../../src/simple_long_horizon_agent/core.py)
- [`tools/__init__.py`](../../src/simple_long_horizon_agent/tools/__init__.py)
- [`tools/task.py`](../../src/simple_long_horizon_agent/tools/task.py)
- [`tools/bash.py`](../../src/simple_long_horizon_agent/tools/bash.py)
- [`mcp/client.py`](../../src/simple_long_horizon_agent/mcp/client.py)
- [`06-tools-and-integrations.md`](06-tools-and-integrations.md)
- [`tests/unit/test_core.py`](../../tests/unit/test_core.py)
- [`tests/unit/test_mcp.py`](../../tests/unit/test_mcp.py)
