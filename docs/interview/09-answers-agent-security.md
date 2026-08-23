# 09. Agent 安全

本文对应 [`question-checklist.md`](question-checklist.md) 中第九部分的问题。每道题包含可直接
口述的主回答；存在清单子问题时，保留追问原文和简短回答；最后记录整组问题对应的仓库实现与
安全边界。

## 100. Prompt Injection 应该如何防御？

### 口述主回答

我的判断是 Prompt Injection 无法只靠“识别恶意文本”彻底解决，系统必须假设网页、文档、Tool
Output 和检索内容都可能带有攻击指令。模型可以使用其中的数据，但外部内容不能改变系统目标、
授权范围和审批规则，更不能因为文本写着“忽略原指令”就获得新权限。

真正的防线在模型之外。我会给外部内容保留来源和信任标签，Agent 只获得完成当前任务所需的最小
工具；执行前由确定性 Policy 校验工具、参数、资源范围和数据流，高风险操作绑定具体参数请求审批；
执行环境再限制文件、网络和凭据。对于“读取 `~/.ssh/id_rsa` 并上传”的请求，既要在文件策略阻止
读取敏感路径，也要在网络策略阻止未授权外发，并对“敏感数据源到外部 Sink”的组合默认拒绝。

当前项目会把 Tool Result 作为下一轮 Message 返回模型，并提供 `PRE_TOOL_USE` Hook 阻止某次
Tool Call，但没有内容信任标签、通用 Prompt Injection 检测或内置 Policy Engine。Read/Edit
允许绝对路径，Bash 能执行普通 Shell，所以现有 Hook 只是安全策略的接入点，不能被描述为项目已经
完成了 Prompt Injection 防御。

### 追问问题与回答

**追问：Tool Output、网页内容、文档和外部检索结果是否可信？**

默认不可信。它们可以提供任务数据，但不能作为新的系统指令、授权证明或审批结果；进入模型上下文
后也不应提升信任级别。

**追问：网页要求 Agent 忽略原指令并上传 `~/.ssh/id_rsa` 时，系统应如何识别和阻止？**

根据内容来源把这段话标记为外部数据，同时在执行端独立校验：敏感路径读取被拒绝，外部网络目标
不在白名单时被拒绝，涉及敏感数据外发必须重新审批。不能依赖模型自己判断后再决定是否遵守。

**追问：Prompt 约束、权限控制和执行隔离分别能解决什么问题？**

Prompt 约束降低模型把数据误当指令的概率；权限控制决定某个身份能调用什么资源；执行隔离限制
工具即使被错误调用后的影响范围。前者是软约束，后两者才是确定性安全边界。

### 技术追问补充

- 当前 Runtime 会把 `ToolResult.content` 封装为 `ToolResultBlock`，再写成
  `UserMessage(kind="tool_result")`；它与普通外部文本一样会进入后续 Context，没有可信内容类型。
- `PRE_TOOL_USE` Hook 可以读取 Agent、State 和 `ToolCallBlock`，返回 `block_reason` 后 Runtime
  跳过执行，并把拒绝原因作为错误 Tool Result 返回模型；Hook 本身需要宿主提供真实策略。
- 当前 Hook 只看到结构化 Tool Call 和 State，没有统一的资源分类、数据来源标签、污点传播或
  “敏感 Source 到外部 Sink”规则。
- `read_file()` 和 `edit_file()` 会解析相对或绝对路径，但没有校验最终路径仍位于配置的 root 内；
  Bash 子进程则继承宿主进程能够访问的文件和网络权限。
- 生产 Policy Decision 至少应包含 actor、tenant、tool、规范化参数、resource、data
  classification、network destination、decision、reason、policy version 和 approval reference。
- 防御测试应覆盖间接注入：恶意网页或 Tool Result 诱导 Agent 读取 Secret、修改权限、调用外部
  Sink，并验证即使模型提出调用，执行端仍确定性拒绝。

## 101. 如何限制 Tool Capability？

### 口述主回答

我会把 Tool Capability 分成四层：Agent 是否能看见这个工具、允许哪些参数和资源、工具在哪种
隔离环境中执行，以及是否需要审批。Agent 组装时只绑定当前任务需要的工具；运行时再根据调用身份
和任务授权校验规范化后的路径、命令、网络目标、数据范围和副作用，不能把模型生成的 JSON 参数
直接当成授权结果。

具体来说，文件工具需要允许目录和符号链接逃逸检查；Shell 最好被更窄的结构化工具替代，必须使用
时限制工作目录、命令、用户和资源；网络默认关闭或只允许明确域名、IP、协议和端口；业务工具还要
把 tenant、row、账户和金额范围带入授权。删除、发送消息、发布、转账、权限变更、Secret 访问和
其他不可逆操作，应由 Policy 或用户对“工具加参数加资源”做审批，参数变化后原审批失效。

当前项目的 Agent 显式持有 `tools` 元组，Runtime 也支持 `PRE_TOOL_USE` Hook，这提供了暴露面和
执行前拦截点。但 JSON Schema 主要约束输入形状，Read/Edit 没有 root containment，Bash 没有命令
白名单，项目也没有网络、数据行级授权和审批状态机，因此还不是完整 Capability System。

### 追问问题与回答

**追问：如何控制文件路径、命令、网络、数据范围和有副作用操作？**

文件路径先规范化并限制在允许根目录；命令使用结构化 allowlist 或受限执行器；网络使用默认拒绝
和目标白名单；数据查询强制 tenant 与字段范围；副作用操作使用幂等键、Policy 和审批，所有检查在
工具执行端再次完成。

**追问：哪些操作需要用户确认或 Policy 审批？**

不可逆、高价值、对外可见或跨越信任边界的操作需要审批，例如删除数据、发送邮件、发布代码、
转账、修改权限、访问 Secret、扩大网络范围。审批应绑定准确参数、资源和有效期，而不是一次批准
后允许任意后续调用。

### 技术追问补充

- `Agent.tools` 在构造时显式绑定，并在一次 `run()` 开始时按名称建立快照；未绑定工具不会被模型
  看到，也不会进入 Runtime 的 `tool_by_name`。
- `AgentTool.parameters` 会作为 JSON Schema 发送给模型，但 Runtime 没有通用 Schema Validator；
  具体参数和业务约束仍主要由各工具实现检查。
- `PRE_TOOL_USE` 的拒绝发生在线程池执行前，被拒绝调用不会运行；决定会记录为
  `HookFiredEvent`，便于审计。
- Read/Edit 对绝对路径直接 `resolve()`，没有 `path.is_relative_to(root)` 一类 containment
  检查；符号链接解析后同样可能指向工作区外。
- Bash 以 `bash -lc` 执行模型提供的命令，支持 Timeout、输出截断和可选 `exec_prefix`，但这些
  可靠性机制不等于命令授权或安全沙箱。
- 审批记录至少需要 request hash、tool、arguments、resource、actor、approver、policy version、
  expiry 和 single-use 状态；执行前重新计算 hash，避免批准后参数被替换。

## 102. 子 Agent 的权限应该继承父 Agent 吗？

### 口述主回答

Sub-Agent 不应该完整继承 Parent 权限，而应根据委派任务重新授权。有效权限应该是 Parent 可委派
范围、Child 固有角色权限和当前任务 Policy 的交集。例如 Parent 能修改仓库，但它只委派“分析
日志”，Child 就只获得对应日志的只读权限，不能顺带获得写文件、网络外发或继续创建高权限子
Agent 的能力。

委派时我只传完成子任务所需的最小上下文，并签发短期 Capability Token，绑定 parent run、child、
工具、资源范围、预算、过期时间和委派深度。真正的授权检查仍在 Tool Executor，不能因为 Parent
Prompt 里写了“允许”就相信这次委派。

当前项目的 `task_tool()` 从显式注册表选择一个 Sub-Agent，被选 Agent 使用自己构造时绑定的工具，
不会自动复制 Parent 工具；它也创建独立 `State`，只传 task、可选 default context 和 call context。
这是合适的静态边界，但当前没有动态权限求交、短期令牌、敏感 Context 过滤或递归委派深度控制。

### 追问问题与回答

**追问：如何遵循最小权限原则并防止委派造成权限扩大？**

Child 权限取 Parent 可委派范围、Child 角色和任务策略的交集；上下文、工具、资源和预算分别最小化，
禁止 Child 自行扩大权限。每次 Tool 执行都校验绑定此次委派的短期令牌，继续委派时再次做同样求交。

### 技术追问补充

- `task_tool(agents)` 构造 `subagent_type` enum，只能选择注册表中的 Agent；未知名称返回 Tool
  Error，不会动态加载任意 Agent。
- Child 通过 `agent.run(task)` 创建独立 State；Parent 的完整 transcript、State.data 和工具连接
  不会自动复制给 Child。
- `default_context` 与模型生成的 call-level `context` 会作为 Runtime Message 写入 Child State；
  当前没有敏感字段过滤，因此 Parent 仍可能主动把 Secret 或恶意内容委派给 Child。
- Child 的 `sub_events` 会放入 Parent Tool Result 的非模型可见 details，随后可能进入 Trace；
  权限隔离不代表审计数据天然不含敏感信息。
- `max_turns` 和可选 `soft_turn_limit` 限制一次 Child 运行回合，但不是 Token、成本、工具次数、
  网络范围或递归深度预算。
- 当前默认 general-purpose Child 会由 Agent Starter 绑定 Bash；它的工具集合虽然独立于 Parent，
  但并不天然更窄，最小权限仍取决于组装时实际传入的工具与执行环境。
- 如果 Child 自身绑定了 `task` 工具，它仍可继续委派；生产系统需要在 Capability Token 中携带
  delegation depth 和 downstream scope，不能只依赖静态 Agent 配置。

## 103. MCP Server 是否可信？

### 口述主回答

MCP Server 不能默认可信，它同时影响工具声明、执行逻辑和返回内容。接入前我会验证 Server 身份、
传输安全和授权受众，只允许配置中的明确地址或可执行文件；首次发现工具时记录 allowlist、Schema
哈希和版本，之后工具新增、描述改变、参数扩大或授权范围变化都触发重新审核，而不是运行中静默
接受。

调用期间，Client 仍要把 Tool 参数发送到外部信任域，因此每个 Server 只能获得完成其职责需要的
短期凭据和数据。Server 返回的文本、资源、错误和 structured content 继续按不可信输入处理，不能
因为走 MCP 协议就被当成系统指令。第三方 stdio Server 应运行在受限子进程或容器中，HTTP Server
使用受限出站网络、服务身份校验和最小 Scope。

当前项目支持 stdio 和 Streamable HTTP，连接后调用 `list_tools()`，把发现到的全部工具转换成
`AgentTool`，添加名称前缀并拒绝重复模型可见名称。配置解析会校验字段类型和 Timeout，但没有强制
TLS、Server allowlist、OAuth Scope 校验、工具白名单、Schema pinning 或签名验证，所以当前 MCP
集成解决的是协议适配和生命周期，不是零信任接入。

### 追问问题与回答

**追问：如何验证 Server 身份、Tool Schema、返回内容和版本变化？**

Server 身份通过受信 URL、TLS 和认证配置验证；Tool inventory 与 Schema 采用 allowlist、版本和
哈希审查；返回内容保持不可信并经过大小、类型与内容策略；任何新增工具或权限扩大的 Schema Diff
都要求重新批准。

**追问：第三方 MCP Server 应该运行在哪种隔离和授权边界中？**

stdio Server 运行在独立低权限进程、容器或更强沙箱中，使用清理后的环境、受限文件和网络；
HTTP Server 位于明确网络边界，只获得短期最小 Scope Token。它不应与 Runtime 共用全部 Secret、
宿主目录或高权限身份。

### 技术追问补充

- `MCPServerConfig` 保存 stdio command/args/env/cwd 或 HTTP url/headers，并校验必填字段、名称字符
  和正数 Timeout；它不限制 URL Scheme、Host、命令路径或 Header 内容。
- stdio 配置的 `env=None` 会把空值交给 SDK，当前代码没有显式构造最小环境；第三方进程不应默认
  继承 Runtime 的全部环境变量和 Secret。
- HTTP 工厂把配置中的 URL 和 Headers 直接交给 `streamablehttp_client()`，当前没有证书 Pinning、
  Host allowlist 或 Token Audience 检查。
- `MCPConnection.open()` 完成 initialize 和 `list_tools()` 后保存工具列表；`MCPToolset.tools()`
  默认包装该列表中的全部工具，没有内建 include/exclude allowlist。
- `_normalize_parameters()` 只补齐缺少的 object/properties 外壳，不验证 Schema 是否恶意扩大数据
  范围，也不比较前后版本。
- MCP 内容会转换为 TextBlock、ImageBlock 或文本占位符；`structuredContent` 进入 Tool Result
  details。协议转换保持内容，不提供注入检测或数据脱敏。
- Server 连接失效时工具结果可能设置 `terminate=True`，Timeout 和 Abort 负责可靠性，不等于身份
  验证与授权。

## 104. Docker Sandbox 能否解决所有安全问题？

### 口述主回答

不能。Docker 能提供进程、文件系统和资源隔离，但容器仍共享宿主 Kernel，隔离强度取决于启动
配置。高权限 Capability、root 用户、可写宿主挂载、开放网络、宽松 seccomp，或者把 Docker
Socket 暴露给容器，都会让 Agent 影响宿主或其他任务；镜像、依赖和 Kernel 漏洞也仍然存在。

生产上我会默认使用非 root 用户、只读根文件系统、临时可写目录、drop all Capabilities 后按需
添加、no-new-privileges、seccomp/AppArmor、CPU/内存/PID 限制，以及默认关闭或严格代理的网络。
宿主挂载只暴露单任务目录并尽量只读，绝不挂 Docker Socket。执行未知代码或多租户高风险任务时，
还要考虑 rootless 容器、用户命名空间、gVisor、微虚拟机或独立虚拟机。

当前 Eval 的 `LaunchSpec` 可以传 `network_mode`、`cap_add`、`security_opt` 和资源限制，但
`LocalDockerBackend` 默认用户是 root，网络未设置时沿用 Docker 默认网络，也没有自动配置
read-only rootfs、cap_drop、no-new-privileges 或 PID limit。它是可配置执行 Backend，不是 hardened
Sandbox。

### 追问问题与回答

**追问：容器逃逸、挂载目录、Docker Socket、网络、Kernel 和 Secret 泄露仍有哪些风险？**

容器逃逸和 Kernel 漏洞可能越过隔离；可写挂载可篡改宿主文件；Docker Socket 可控制 Daemon 和
其他容器；开放网络可用于横向移动和数据外泄；注入容器的 Secret 可能被命令、依赖或日志读取。
这些风险需要独立权限、网络和凭据边界，不能由容器名称或镜像隔离替代。

### 技术追问补充

- `_create_kwargs()` 把 LaunchSpec、环境变量和 Store Binding 直接转换为 docker-py 参数；默认包含
  user、volumes 和 cap_add，但没有设置 privileged、read_only、cap_drop、pids_limit 或
  no-new-privileges 的安全默认值。
- `LocalDockerBackend(user="root")` 是当前默认值；Suite 可以请求额外 Capability，ProgramBench
  等场景还可能显式使用 host network 或 `SYS_ADMIN`，这些是评测兼容配置，不是安全基线。
- `security_opt` 可传入 `seccomp=unconfined` 等值。该选项有时用于兼容旧 Daemon，但会扩大攻击面，
  只能针对受控任务显式启用。
- `ContainerBinding.mounts` 可以包含只读或读写挂载；持久 Memory 当前使用读写宿主目录，因此隔离
  取决于调用者传入的 namespace 和 mount 范围。
- Provider 凭据通过 `environment` 注入整个容器，容器内 Bash 和其他进程可读取；容器隔离不能
  替代按 Tool 分发短期 Secret。
- 远程 Docker Daemon 的访问本身是高权限控制面，连接认证、网络保护和 Daemon 账号隔离属于部署
  责任，当前 Backend 不自动建立多租户安全边界。

## 105. Secrets 应该如何传递给 Tool？

### 口述主回答

Secret 不应该进入 Prompt、模型可见 Context、命令行参数、普通配置文件或 Trace。生产上我会让
Tool Executor 根据经过授权的 Tool Call 向 Secret Manager 或凭据 Broker 申请短期凭据，只注入给
这一个工具进程，并把 Scope 限制到目标服务、资源、动作和租户；Agent 和模型只看到凭据引用或
执行结果，不看到 Secret 本身。

Secret 的 Owner 是平台身份与凭据层，不是模型。它负责签发、有效期、轮换、吊销和审计；Policy
决定某个 Run 是否可以申请某类凭据；Tool Executor 负责在执行结束后清理。环境变量比把 Key 放进
Prompt 更好，但仍可能被 Shell、子进程、错误转储和调试日志读取，所以高风险场景更适合文件
Descriptor、内存注入或本地凭据代理。

当前项目的 Provider 通过 `Provider.api_key_env` 从进程环境读取 Key；Eval 通过明确的
`provider_env` 将模型环境传入整个容器，MCP 配置也允许 env 和 HTTP headers。项目没有 Secret
Manager、动态凭据、自动轮换或输出脱敏，因此现有环境变量方案是配置边界，不是完整 Secret
Isolation。

### 追问问题与回答

**追问：如何避免把 API Key 放进 Prompt、环境快照、命令行参数或模型可见 Context？**

Prompt 和 Tool 参数只传凭据引用，不传值；Secret 在执行端按需解析并通过不出现在 argv 和模型
Context 的通道注入；环境和诊断快照使用 allowlist，日志、错误和 Trace 在写入前删除凭据字段。

**追问：Secret 的作用域、生命周期和轮换由谁管理？**

由 Secret Manager、身份系统和凭据 Broker 管理。平台 Policy 根据 tenant、Agent、Tool 和资源
签发短期最小权限凭据，Executor 使用并清理，轮换和吊销不由模型或 Tool Prompt 决定。

### 技术追问补充

- `provider_from_env()` 读取模型、Token、Base URL 和 API Kind；`resolve_api_key()` 在 Adapter
  调用前从 `os.environ` 取出凭据，未配置时失败。
- Model config 支持 inline `auth_token`，但会发出 Warning 并把值写入私有环境变量；这避免直接
  放入 Provider 对象，不代表 Secret 已离开进程环境。
- `container_provider_env()` 只收集调用者列出的 passthrough names 以及 NO_PROXY，但生成的
  `RunSpec.provider_env` 最终作为整个容器环境，而不是只对模型 Adapter 可见。
- MCP stdio config 可以携带 env，HTTP config 可以携带 headers；当前 JSON 配置格式没有
  secret-reference 类型，直接写 Token 会产生静态配置泄露风险。
- 生产执行请求应只携带 `credential_ref`、required scopes、audience 和 TTL；Broker 校验 Run
  identity 与 Policy 后返回短期凭据或代理调用，避免 Runtime 长期持有业务 Secret。
- Secret 绝不能放入 Tool Result details。details 不会自动发给模型，但会进入 State、Trace 和
  Viewer，仍属于需要保护的数据。
- 轮换要支持旧 Token 立即吊销和进行中任务续签；审计记录 Secret ID 和使用身份，不记录 Secret
  Value。

## 106. Trace 如何避免记录 API Key 和敏感数据？

### 口述主回答

Trace 安全要做三层，但优先级不同。第一层是在采集前最小化：Secret 不进入 Prompt，默认不采集
完整 Provider Raw，高风险 Tool Result 只返回必要摘要。第二层是在序列化边界做结构化脱敏，根据
字段类型、数据分类和 Tool Schema 处理请求、结果、Header 与错误。第三层才是存储侧的租户隔离、
加密、访问控制、审计和保留周期；数据写入后再扫描删除只能作为补救。

不同数据不能用同一条正则处理。Provider Raw 默认关闭或单独加密并限制调试访问；Tool 参数按
字段与资源类型脱敏；Tool Result 将敏感大内容替换成受控 Artifact 引用；错误保留类型、状态码和
定位信息，但删除请求 Header、Token 和用户数据。脱敏失败时，高敏环境应 fail closed，不写未处理
内容。

当前项目的 Trajectory v5 会丢弃可从 Event 重建的 `llm_payload`，并把 Message sidecar 中的
Provider raw 外置到相邻 `*.raw.jsonl`，主要是为控制体积。`json_safe()` 会继续序列化普通字段，
项目没有通用字段脱敏、Secret Scanner、加密或访问控制，因此 raw_ref 和 sidecar 分离都不能被
描述为安全脱敏。

### 追问问题与回答

**追问：应该在采集前、序列化时还是存储后执行脱敏？**

三处都需要，但先采集最少数据，再在序列化前做确定性脱敏，最后用存储权限和保留策略兜底。只在
存储后清理意味着敏感数据已经进入磁盘、备份和日志，不能作为主要方案。

**追问：Provider Raw、Tool 参数、Tool Result 和错误堆栈分别如何处理？**

Provider Raw 默认关闭或进入独立高权限存储；Tool 参数按 Schema 字段和资源引用脱敏；Tool Result
只保留模型需要的摘要并把大敏感内容外置；错误保留诊断结构但移除 Header、凭据、请求正文和用户
私有数据。

### 技术追问补充

- `event_record()` 会从每条 Event 删除 `llm_payload`，但 `MessageEvent.message`、Tool Call 参数、
  Tool Result content/details、system prompt 和错误文本仍可能进入事件流。
- `split_raw_from_record()` 与 live writer 只把符合 request/response 形状的 `raw` Blob 替换成
  `raw_ref` 并写入 sidecar；它不删除 Token、Header、Prompt 或响应内容。
- `json_safe()` 会递归序列化 Dataclass、Mapping、list 和基本类型，不按字段名执行 Secret
  Redaction；未知对象还会使用 `repr()`，其中也可能包含敏感数据。
- `ToolResult.details` 不进入普通模型请求，但会保存在 Message sidecar；Task Tool 的
  `sub_events`、MCP `structuredContent` 和本地工具诊断都可能随 Trace 持久化。
- Incremental writer 的 `on_error` 只报告写入错误；当前没有“脱敏失败则拒绝落盘”的前置阶段，
  也没有区分普通 Trace 与高敏 Trace 的策略。
- Provider Raw、Tool 输入输出和错误应定义独立 retention class；raw sidecar 通常比规范 Event
  更敏感，应拥有更短保留期、更小读者集合和独立删除路径。
- 脱敏规则需要版本化并保留命中统计，但审计日志只记录规则和字段位置，不应再次保存原始 Secret。

## 核对依据

- [`hooks.py`](../../src/simple_long_horizon_agent/hooks.py)
- [`tools/read.py`](../../src/simple_long_horizon_agent/tools/read.py)
- [`tools/edit.py`](../../src/simple_long_horizon_agent/tools/edit.py)
- [`tools/bash.py`](../../src/simple_long_horizon_agent/tools/bash.py)
- [`tools/task.py`](../../src/simple_long_horizon_agent/tools/task.py)
- [`agents/starter.py`](../../src/simple_long_horizon_agent/agents/starter.py)
- [`agents/toolsets.py`](../../src/simple_long_horizon_agent/agents/toolsets.py)
- [`mcp/config.py`](../../src/simple_long_horizon_agent/mcp/config.py)
- [`mcp/config_file.py`](../../src/simple_long_horizon_agent/mcp/config_file.py)
- [`mcp/client.py`](../../src/simple_long_horizon_agent/mcp/client.py)
- [`llm/config.py`](../../src/simple_long_horizon_agent/llm/config.py)
- [`llm/env.py`](../../src/simple_long_horizon_agent/llm/env.py)
- [`trace/jsonl.py`](../../src/simple_long_horizon_agent/trace/jsonl.py)
- [`trace/live.py`](../../src/simple_long_horizon_agent/trace/live.py)
- [`trace/run_trace.py`](../../src/simple_long_horizon_agent/trace/run_trace.py)
- [`src/simple_long_horizon_agent/evals/protocols.py`](../../src/simple_long_horizon_agent/evals/protocols.py)
- [`src/simple_long_horizon_agent/evals/backends/docker_local.py`](../../src/simple_long_horizon_agent/evals/backends/docker_local.py)
- [`01-product-goals-and-scope.md`](../design/01-product-goals-and-scope.md)
- [`06-tools-and-integrations.md`](../design/06-tools-and-integrations.md)
- [`09-trace-and-observability.md`](../design/09-trace-and-observability.md)
- [OWASP Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
- [MCP Security Best Practices](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices)
- [Docker Engine Security](https://docs.docker.com/engine/security/)
- [OpenAI Agent Safety](https://developers.openai.com/api/docs/guides/agent-builder-safety)
