# 09. Agent 安全

本文对应 [`question-checklist.md`](question-checklist.md) 中第九部分的问题。每道题包含可直接
口述的主回答，以及精简的技术追问补充。

## 100. Prompt Injection 应该如何防御？

### 口述主回答

Prompt Injection 不能只靠系统提示防御。网页、文档、工具结果和检索内容都应视为不可信数据，
模型可以参考其中的事实，但不能因为内容要求“忽略原指令”就获得新的权限。

真正的防线在模型之外：Agent 只获得最小工具集合，工具执行前由确定性策略校验目标、参数和数据
范围，高风险操作需要审批，运行环境限制文件与网络访问。即使模型被诱导，攻击影响也应被权限
边界截断。

### 技术追问补充

- “上传 `~/.ssh/id_rsa`”应同时被文件访问策略、网络策略和敏感操作审批阻止。
- Prompt 约束负责降低模型误判，权限控制决定能做什么，沙箱限制成功执行后的影响范围。
- 工具返回内容在下一轮仍是不可信输入，不能因为来自 Tool 就提升信任级别。
- 当前项目没有通用 Prompt Injection 检测和策略引擎，Bash/Read 也不是完整安全边界。

## 101. 如何限制 Tool Capability？

### 口述主回答

我会从“是否暴露工具”和“执行时是否允许”两层限制。Agent 组装时只绑定当前任务需要的工具；
执行时再校验文件路径、命令类型、网络目标、数据范围和调用身份，不能把模型生成的参数直接当作
授权结果。

删除数据、发送消息、发布代码、转账、修改权限和访问 Secret 等有副作用操作，应由宿主策略或
用户审批后执行。超时和 JSON Schema 只能改善可靠性，不能替代权限控制。

### 技术追问补充

- 当前项目支持显式工具列表和 `PRE_TOOL_USE` Hook，可在执行前阻止调用。
- Read/Edit 当前允许绝对路径，Bash 也能执行普通 Shell 命令，因此必须依赖外层目录和容器限制。
- 网络访问应使用目标白名单或默认关闭，而不是让模型自行决定。
- 高风险审批要绑定具体工具、参数和资源，批准内容变化后必须重新审批。

## 102. 子 Agent 的权限应该继承父 Agent 吗？

### 口述主回答

不应该完整继承，而应根据子任务重新授权，并且只能是父 Agent 权限的子集。父 Agent 委派“只读
分析”时，子 Agent 不应该因为父 Agent 能修改仓库就自动获得写权限。

项目当前的子 Agent 本来就拥有独立工具集合，这是合适的边界。生产化时还应传递短期能力令牌，
限制资源、工具、预算和委派深度，防止通过多层委派扩大权限。

### 技术追问补充

- 子 Agent 的工具来自自身配置，不会自动复制父 Agent 工具表。
- 委派上下文只传任务所需数据，不传父 Agent 的全部消息、Secret 和外部连接。
- 权限检查应在工具执行端完成，不能只依赖父 Agent 的 Prompt 描述。

## 103. MCP Server 是否可信？

### 口述主回答

不能默认可信。连接配置、工具 Schema、返回内容和服务端实现都可能被篡改或发生版本变化。Client
应验证服务身份和授权范围，只连接允许的地址，并把 MCP 返回内容继续当作不可信数据。

项目当前会发现工具、添加名称前缀并转换 Schema，但没有服务签名、版本锁定和完整工具白名单。
第三方 MCP Server 应运行在独立进程或容器中，使用受限网络和短期凭据。

### 技术追问补充

- HTTP 连接应使用 TLS，并验证服务身份；访问令牌需要校验受众和授权范围。
- 工具 Schema 变化应触发重新审核或版本更新，不能运行中静默扩大能力。
- 当前 `MCPToolset` 默认包装连接时发现的全部工具，最小权限需要在绑定 Agent 前过滤。
- MCP 返回的资源、文本和错误可能包含间接 Prompt Injection。

## 104. Docker Sandbox 能否解决所有安全问题？

### 口述主回答

不能。容器共享宿主内核，错误的 Capability、特权模式、可写宿主挂载、开放网络或 Docker Socket
都会削弱隔离。尤其是 Docker Daemon 权限很高，把 Socket 挂进容器基本等于把宿主控制权交给容器。

我会采用非特权用户、最小 Capability、只读根文件系统、受限挂载、默认关闭网络，并启用
seccomp、AppArmor 或同类策略。更高风险代码还需要虚拟机或更强隔离，不能只靠 Docker。

### 技术追问补充

- Docker Socket 不应暴露给 Agent 容器；远程 Daemon 应通过 SSH 或双向 TLS 保护。
- `host` 网络、`SYS_ADMIN` 和 `seccomp=unconfined` 都会扩大攻击面，只能按套件需要显式开启。
- Secret、宿主目录和内核漏洞仍可能造成容器外影响。
- 当前 Eval 支持网络、Capability、Security Opt 和挂载配置，但不是自动安全策略。

## 105. Secrets 应该如何传递给 Tool？

### 口述主回答

Secret 不应进入 Prompt、模型上下文或命令行参数。生产环境中应由 Secret Manager 或凭据代理在
工具执行时注入短期、最小权限凭据，并限制只能访问指定服务和资源。

项目当前主要通过环境变量读取模型凭据，评测也只转发明确允许的变量；但环境变量仍可能被命令或
错误信息打印出来，所以还需要进程隔离、输出过滤、日志脱敏和定期轮换，不能把环境变量本身当作
完整安全方案。

### 技术追问补充

- 凭据应按 Agent、Tool、任务和运行环境限定作用域，并设置短有效期。
- 不在命令行传 Secret，避免被进程列表、Shell 历史和 Trace 记录。
- 工具只获得实际调用需要的凭据，子 Agent 和其他工具不应共享同一全局 Token。
- 当前项目没有统一 Secret Manager、动态凭据签发和轮换机制。

## 106. Trace 如何避免记录 API Key 和敏感数据？

### 口述主回答

第一原则是在采集前最小化：Secret 不进入 Prompt，工具结果不返回无关敏感内容，错误也不回显完整
凭据。序列化时再做字段级脱敏，存储层使用访问控制、加密和保留周期；数据写入后再清理只能作为
补救。

Provider raw、工具参数、工具结果和异常堆栈都可能包含敏感信息，应分别设置采集开关和脱敏规则。
项目当前会把 raw 外置以控制体积，但外置不等于脱敏，也没有覆盖全部数据类型的自动清洗。

### 技术追问补充

- Provider raw 默认只应对受控调试人员开放，并设置较短保留期。
- 工具参数和结果按字段、路径或数据分类脱敏，不能只用字符串正则处理所有内容。
- 错误堆栈保留错误类型和定位信息，但移除请求头、凭据和用户私有内容。
- Trace Writer 失败或脱敏失败时，高敏场景应拒绝写入，而不是保存未处理原文。

## 核对依据

- [`hooks.py`](../../src/simple_long_horizon_agent/hooks.py)
- [`tools/read.py`](../../src/simple_long_horizon_agent/tools/read.py)
- [`tools/edit.py`](../../src/simple_long_horizon_agent/tools/edit.py)
- [`tools/bash.py`](../../src/simple_long_horizon_agent/tools/bash.py)
- [`mcp/client.py`](../../src/simple_long_horizon_agent/mcp/client.py)
- [`llm/env.py`](../../src/simple_long_horizon_agent/llm/env.py)
- [`trace/run_trace.py`](../../src/simple_long_horizon_agent/trace/run_trace.py)
- [`src/simple_long_horizon_agent/evals/protocols.py`](../../src/simple_long_horizon_agent/evals/protocols.py)
- [`01-product-goals-and-scope.md`](../design/01-product-goals-and-scope.md)
- [`06-tools-and-integrations.md`](../design/06-tools-and-integrations.md)
- [`09-trace-and-observability.md`](../design/09-trace-and-observability.md)
- [OWASP Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
- [MCP Security Best Practices](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices)
- [Docker Engine Security](https://docs.docker.com/engine/security/)
- [OpenAI Agent Safety](https://developers.openai.com/api/docs/guides/agent-builder-safety)
