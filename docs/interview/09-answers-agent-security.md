# 09. Agent 安全

本文对应 [`question-checklist.md`](question-checklist.md) 中第九部分的问题。每道题包含可直接
口述的主回答；存在清单子问题时，保留追问原文和简短回答；最后记录整组问题对应的仓库实现与
安全边界。

## 100. Prompt Injection 应该如何防御？

### 口述主回答

我把 Prompt Injection 看成“让 Agent 代替攻击者使用系统权限”的问题，而不只是恶意文本分类。
网页、文档、邮件和 Tool Result 无论写得多像系统指令，都只能作为不可信数据；模型可以读取和
总结它们，但不能据此改变任务目标、扩大权限或生成有效审批。注入检测可以帮助告警，却不能决定
动作是否放行。

真正的控制点在模型之外。模型只能提出 Tool Call，执行前的授权层根据 Agent 身份、当前任务、
规范化参数、目标资源和数据流独立判断。工具按最小权限暴露；高风险操作的审批绑定具体调用和参数
哈希；Secret 不进入模型上下文，而是在执行端按单次操作注入短期凭据；文件与网络再由沙箱和出口
策略限制。即使模型完全遵从恶意内容，它提出的动作仍然不能越过这些确定性边界。

还要防止多个单独看似允许的动作组合成外泄。例如读取普通文件和访问外网可能分别允许，但读取结果
一旦被标为敏感，就不能流向未批准的网络目标。系统需要保存数据来源和分类，并对“敏感来源到外部
接收方”的组合默认拒绝。当前项目只有 `PRE_TOOL_USE` Hook 这个执行前接入点，还没有来源标签、
资源授权、数据流策略和沙箱；Read/Edit 可访问绝对路径，Bash 继承宿主权限，因此现状不能称为
已经具备 Prompt Injection 防御。

### 追问问题与回答

**追问：Tool Output、网页内容、文档和外部检索结果是否可信？**

默认不可信。它们可以作为事实候选和任务数据，但不能成为新的系统指令、权限证明或审批结果；被
模型转述后也不能自动提升信任级别。

**追问：网页要求 Agent 忽略原指令并上传 `~/.ssh/id_rsa` 时，系统应如何识别和阻止？**

不需要先证明这句话是攻击才能阻止。网页内容本身无权授权；文件策略拒绝读取敏感路径，凭据不对
模型可见，网络策略拒绝未批准目标，数据流策略禁止敏感内容外发。任何一层命中都应阻止调用并记录
原因。

**追问：Prompt 约束、权限控制和执行隔离分别能解决什么问题？**

Prompt 约束降低模型误把数据当指令的概率；权限控制决定动作是否允许；执行隔离限制已经放行的
工具能接触什么文件、网络和凭据。Prompt 是行为引导，权限和隔离才是确定性边界。

### 技术追问补充

- 当前 Runtime 会把 `ToolResult.content` 封装为 `ToolResultBlock`，再写成
  `UserMessage(kind="tool_result")`；它与普通外部文本一样会进入后续 Context，没有可信内容类型。
- `PRE_TOOL_USE` Hook 可以读取 Agent、State 和 `ToolCallBlock`，返回 `block_reason` 后 Runtime
  跳过执行，并把拒绝原因作为错误 Tool Result 返回模型；Hook 本身需要宿主提供真实策略。
- 当前 Hook 只看到结构化 Tool Call 和 State，没有统一的资源分类、数据来源标签、污点传播或
  “敏感 Source 到外部 Sink”规则。
- `read_file()` 和 `edit_file()` 会解析相对或绝对路径，但没有校验最终路径仍位于配置的 root 内；
  Bash 子进程则继承宿主进程能够访问的文件和网络权限。
- 可以为外部内容增加来源、采集工具、资源标识、信任级别、数据分类和内容哈希；这些标签随摘要、
  Recall 和子 Agent 传递，模型改写文本不能清除来源。
- 授权输入至少包含主体、租户、任务、工具、规范化参数、资源、数据分类、网络目标、策略版本和审批
  引用。授权结果是允许、拒绝或需要审批，并附稳定原因。
- 路径授权应先解析绝对路径和符号链接，再检查是否位于允许根目录；网络授权应同时检查域名、解析后
  地址、端口和重定向，避免只对白名单字符串做比较。
- 数据流策略不应依赖模型准确说明“这些数据来自哪里”。工具结果产生时由执行端赋予分类，后续写
  文件、发送请求或调用外部服务时由 Runtime 根据 State 中的来源记录判断。
- 审批必须绑定 `tool_call_id`、工具版本、参数哈希、资源范围和有效期；参数变化后旧审批失效，不能
  使用一次宽泛批准授权后续调用。
- Secret 通过执行端的凭据代理按单次操作注入，模型、Message、Tool Result 和 Trace 只保存引用；
  即使模型要求打印凭据，工具也没有返回明文的能力。
- Prompt Injection 分类器和内容清洗只能作为风险信号。无法识别攻击时，最小权限、执行前授权、
  数据流策略和沙箱仍应阻止危险结果。
- 防御测试要覆盖直接和间接注入、编码或分段指令、跨多次 Tool Result 的组合攻击，以及
  “读取敏感数据 → 写入文件 → 外部上传”等多步骤外泄链路；断言点应是执行端确定性拒绝，而不是
  模型是否口头拒绝。

## 101. 如何限制 Tool Capability？

### 口述主回答

我不会把 Tool Capability 简化成“Agent 能不能看到某个工具名”。真正的权限应该是一份绑定当前
Run 的能力授权，明确哪个 Agent 可以使用哪个工具版本、执行什么动作、访问哪些资源、满足哪些参数
约束、有效到什么时候，以及是否需要审批。有效权限取会话授权、Agent 角色、委派任务和运行环境的
交集，后续只能收窄，不能由模型自行扩大。

执行时做两次校验。组装 Agent 时先根据能力授权生成最小工具列表，未授权工具不发给模型；模型产生
Tool Call 后，再把参数规范化成真实资源，例如解析文件最终路径、网络目标、租户和数据范围，然后
逐调用授权。执行端还要验证绑定操作编号和参数哈希的短期能力令牌，并在受限文件、网络和凭据环境
中运行，因此即使模型或 Runtime 判断出错，也不能获得环境中的默认权限。

Shell 是最难授权的能力，因为一段命令可以组合文件、网络和子进程操作。能用读取、编辑、搜索、
查询等窄工具表达时，就不直接暴露 Bash；必须使用时放进低权限沙箱，并限制挂载、网络、用户和资源。
不可逆、对外可见、提升权限、访问 Secret 或高价值资产的操作，再增加绑定准确参数的一次性审批和
幂等键。

当前项目已经有两个基础边界：`Agent.tools` 决定模型可见工具，`PRE_TOOL_USE` 可以在执行前拒绝
调用。但 JSON Schema 主要约束参数形状，Read/Edit 没有根目录 containment，Bash 也不是安全沙箱；
项目还没有结构化能力授权、短期令牌和审批状态机，所以现有机制只是接入点。

### 追问问题与回答

**追问：如何控制文件路径、命令、网络、数据范围和有副作用操作？**

先把参数解析成真实资源，再用同一份能力授权判断：文件限制根目录和操作类型；命令优先换成窄工具，
否则进入沙箱；网络通过出口代理默认拒绝；数据访问由服务端强制租户和行列范围；副作用操作绑定
幂等键和一次性审批。执行端必须再次校验。

**追问：哪些操作需要用户确认或 Policy 审批？**

不可逆、高价值、对外可见、跨越租户或信任边界、提升权限以及访问 Secret 的操作需要审批。审批
绑定操作编号、准确参数、资源、策略版本和有效期，任何变化都使旧审批失效。

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
- 可以定义结构化能力授权，包含 `run_id`、Agent、工具及版本、允许动作、资源选择器、参数约束、
  数据分类、环境、过期时间和审批要求；默认拒绝授权中没有明确允许的组合。
- 父子 Agent 委派时，Child 的能力是父级可委派范围、Child 角色和子任务策略的交集；继续委派时
  再次求交，不能复制父级完整权限。
- Agent 组装时用能力授权过滤工具列表，执行前再使用同一策略校验规范化参数；模型可见集合和
  Runtime 可执行集合必须一致，不能只隐藏声明而保留后门调用。
- 文件授权要处理相对路径、绝对路径、符号链接和检查后替换问题。高安全场景应使用目录文件描述符
  等方式在打开时约束路径，而不只在执行前做字符串 containment 检查。
- 网络访问通过统一出口代理校验协议、域名、解析后地址、端口和重定向，防止 DNS 重绑定和跳转绕过
  目标策略；默认无网络比维护宽泛黑名单更可靠。
- 数据库和业务 API 不接受模型自由传入 tenant 条件。租户、账户、字段和金额上限由执行端根据能力
  授权强制注入或校验，服务端仍做最终行级授权。
- 短期能力令牌绑定 `operation_id`、工具版本、参数哈希、资源范围、租户、过期时间和 fencing
  token；Tool Worker 重新验证，不能信任 Runtime 已经放行。
- 审批记录至少包含操作编号、请求哈希、工具、规范化参数、资源、申请者、审批者、策略版本、有效期
  和一次性状态；执行前重新计算哈希，参数变化后必须重新审批。
- 所有允许、拒绝和审批决定都记录策略版本和原因，但审计记录不保存 Secret 明文。测试要覆盖直接
  调用、伪造工具名、路径逃逸、网络跳转、跨租户参数和审批后篡改。

## 102. 子 Agent 的权限应该继承父 Agent 吗？

### 口述主回答

Sub-Agent 不应该继承 Parent 的完整权限，而且要区分两件事：Parent 自己可以使用什么，以及 Parent
被允许向下委派什么。父 Agent 即使能修改整个仓库，也可能只被允许把某个日志目录的读取权限委派
出去，不能因为 Prompt 里写了“你可以修改文件”就把自己的权限复制给 Child。

委派时，Parent 只提交子任务、目标 Agent 和所需能力。平台授权层根据 Parent 的可委派范围、
Child 的角色上限、子任务策略和运行环境求交，创建一条委派记录，并签发短期、不可扩大的能力令牌。
这份授权同时限制工具、资源、数据范围、预算、有效期和最大委派深度；Child 的工具列表、凭据、
工作区和可见上下文都从这份授权生成。

每次 Child 调用工具时，执行端重新校验令牌和真实参数。Child 若继续委派，只能从自己剩余的可委派
范围中再次收窄，不能创建新的权限；父任务取消、授权到期或权限被撤销后，所有后代令牌一起失效。
Child 返回的是结果、证据和 Artifact 引用，不会把凭据或能力令牌作为普通 Tool Result 传回。

当前项目的 `task_tool()` 只会选择显式注册的 Sub-Agent，并使用该 Agent 构造时已有的工具、Hook 和
Context Policy，不会自动复制 Parent 工具，这提供了静态隔离。但它没有区分可使用与可委派权限，
也没有动态授权、敏感上下文过滤、共享预算和递归深度控制，因此是否最小权限仍取决于组装方式。

### 追问问题与回答

**追问：如何遵循最小权限原则并防止委派造成权限扩大？**

把 Parent 的可委派范围与自身使用权限分开；Child 权限取可委派范围、Child 角色、子任务和环境约束
的交集。令牌只能衰减，不能由 Child 签发更大范围；每次工具调用和继续委派都重新校验。

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
- 可以定义 `DelegationGrant`，记录 parent run、委派节点、Child 身份、任务哈希、允许工具、资源
  范围、数据分类、预算、有效期、最大深度、可继续委派范围和策略版本。
- Parent 的 effective capabilities 与 delegable capabilities 分开保存；委派授权机构而不是 Parent
  模型签发令牌，防止模型把自身可用权限错误地当作可转授权权限。
- Child Agent 组装时根据委派授权过滤工具列表、MCP 工具、工作目录和网络；执行端仍按第 101 题的
  能力策略逐调用校验，不能只依赖静态工具集合。
- 上下文传递经过数据分类过滤：只发送完成子任务所需的消息或 Artifact 引用，Secret、其他租户数据
  和与任务无关的原始 Trace 默认不传；Child 输出也经过同样的数据外发检查。
- 短期令牌绑定 `delegation_id`、Child、工具版本、资源、参数约束、预算、过期时间和父级 fencing
  token；撤销父委派时可以沿委派树使全部后代失效。
- Child 若绑定 `task` 工具，新的授权必须取当前剩余可委派范围与下一层策略的交集，同时扣减共享
  深度、调用次数和 Token 预算；不能通过创建新 State 重置限制。
- 工具结果只返回业务内容和 Artifact 引用。令牌、凭据和完整授权对象属于执行控制面，不进入
  Message、Tool Result details 或子事件 Trace。
- 审计记录委派者、Child、授权范围、策略版本、每次权限衰减和撤销原因，但敏感上下文与令牌值不
  写入普通 Trace。

## 103. MCP Server 是否可信？

### 口述主回答

MCP Server 不能整体标记成“可信”或“不可信”，要拆成三件事分别判断：连接到的是不是预期服务，
它被允许获得哪些数据和权限，以及它返回的内容能不能被模型当作事实或指令。身份验证通过，只能
说明连接对象正确，不代表它的所有工具都已获批，也不代表返回内容可信。

接入时我会先注册一个不可变的服务身份，例如受信地址和证书身份，或者本地可执行文件和包的内容
哈希。`list_tools()` 返回的工具只进入候选清单，平台把名称、参数结构、风险等级和所需数据与已批准
清单比较；新增工具、参数范围扩大、权限变化或版本来源变化都需要重新审核。纯描述文字变化可以
记录告警，但不能静默扩大能力。

运行时按单次调用授权。服务只获得完成这次操作需要的数据和短期凭据；本地 stdio 服务运行在受限
进程或容器中，远程服务通过明确的网络出口和服务身份连接。它返回的文本、图片、错误和结构化内容
仍按外部不可信数据处理，受大小、类型、敏感数据和 Prompt Injection 策略约束，不能反向授予新
权限。

当前项目完成的是 MCP 握手、工具发现、名称隔离、调用和连接生命周期。它没有验证服务来源、固定
工具清单、比较 Schema 版本或限制返回内容，因此不能把“配置成功并能调用”解释成“该 Server 已经
可信”。

### 追问问题与回答

**追问：如何验证 Server 身份、Tool Schema、返回内容和版本变化？**

服务身份使用受信地址、证书或本地制品哈希固定；发现的工具与批准清单和 Schema 哈希比较；返回
内容始终按不可信数据处理。新增工具、参数或权限扩大必须重新批准，不能只更新哈希后自动接受。

**追问：第三方 MCP Server 应该运行在哪种隔离和授权边界中？**

stdio 服务放在独立低权限进程或沙箱中，只挂载必要目录并使用清理后的环境；远程服务通过受控网络
出口和专用身份访问，只获得短期、最小范围凭据。它不与 Runtime 共用宿主目录和完整 Secret。

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
- 可以建立服务注册记录，包含传输类型、服务身份、制品或证书哈希、负责人、允许网络目标、工具清单
  版本、审批状态和撤销状态；连接前必须匹配有效记录。
- 工具清单使用规范化 Schema 计算哈希，并把变化分为收窄、兼容新增和权限扩大。权限扩大包括新增
  写操作、放宽资源字段、增加自由字符串命令或扩大数据返回范围，必须重新审核。
- 调用授权绑定服务、工具版本、参数哈希、数据范围和短期凭据。服务版本变化后，旧授权不能自动用于
  新工具清单。
- MCP 返回内容进入模型前执行大小、MIME、数据分类和敏感信息检查；`structuredContent` 虽不默认
  进入模型正文，仍会进入 State 和 Trace，不能绕过安全策略。
- 服务健康异常与服务不可信是不同状态。超时和断连可以重连，身份或清单校验失败则应撤销服务并
  停止暴露工具。
- 安全测试应覆盖服务被替换、DNS 或证书变化、工具清单扩大、Schema 降级、超大返回、恶意
  Tool Result 和凭据越权，而不只测试能否成功调用。

## 104. Docker Sandbox 能否解决所有安全问题？

### 口述主回答

不能。Docker 主要限制进程能看到的文件系统、资源和网络，但它不解决 Agent 获得了什么业务权限、
Secret 是否暴露、Prompt Injection、镜像供应链以及外部副作用。容器还共享宿主内核，所以一旦给了
root、高权限 Capability、可写宿主挂载或 Docker Socket，隔离边界会被大幅削弱。

我会根据任务威胁等级选择隔离层，而不是把“放进容器”当作统一答案。低风险、可信代码可以使用普通
低权限容器；未知代码使用非 root、只读根文件系统、临时工作目录、关闭网络、清空 Capability 和
严格资源限制的加固容器；多租户或主动对抗场景使用更强的用户态内核、微虚拟机或独立虚拟机。安全
配置由平台生成，模型和任务输入不能自行放宽。

容器外还要有独立控制：只挂载单个任务需要的目录，默认不暴露宿主服务和 Docker Socket；网络通过
出口代理授权；凭据按单次 Tool Call 发放；镜像使用固定摘要、签名和漏洞策略；宿主内核与容器运行
时持续更新。这样即使容器内 Agent 被完全控制，攻击面仍限制在这次任务获得的最小资源内。

当前 Eval Backend 允许调用者传网络、Capability、安全选项和资源限制，但默认用户是 root，也没有
强制只读根文件系统、清空 Capability 或关闭网络。它是可配置的执行后端，不是项目已经提供的安全
沙箱。

### 追问问题与回答

**追问：容器逃逸、挂载目录、Docker Socket、网络、Kernel 和 Secret 泄露仍有哪些风险？**

内核漏洞可能导致容器逃逸；可写挂载把宿主文件直接交给容器；Docker Socket 等同于高权限控制面；
开放网络允许横向移动和外泄；注入容器的 Secret 可被任意进程、依赖或日志读取。这些风险必须由
挂载、网络、凭据和宿主安全分别控制。

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
- 可以定义平台管理的安全配置档位，固定 user、rootfs、Capability、seccomp、PID、内存、CPU、
  网络和挂载规则；任务只能选择被允许的档位，不能提交任意 Docker 参数。
- 所有镜像使用不可变 digest，并经过来源签名、依赖和漏洞策略检查；标签变化不能在不审核的情况下
  替换实际镜像。
- 工作区使用每任务独立卷，默认不挂宿主路径；必须挂载时只暴露必要子目录，并区分只读输入和受控
  输出。Artifact 通过 Store 交换，避免共享宽目录。
- 网络默认关闭。需要联网时通过出口代理按协议、域名、地址、端口和流量上限授权，并记录实际目标；
  容器不能直接访问云元数据、控制面和其他租户网络。
- Docker Socket、宿主 PID/IPC/网络命名空间和高危设备默认禁止；需要高权限的兼容任务应进入独立
  节点池，而不是与普通多租户任务混跑。
- Secret 不进入通用容器环境。执行端按单次操作通过凭据代理或专用挂载注入，任务完成或租约失效后
  立即撤销。
- 安全验证包括逃逸防护配置测试、挂载越界、网络外发、资源耗尽、镜像替换和 Secret 泄露测试；
  还需要宿主补丁、节点隔离和运行时告警，不能只检查容器启动参数。

## 105. Secrets 应该如何传递给 Tool？

### 口述主回答

我的目标不是“安全地把 Secret 交给模型”，而是尽量让模型和通用 Runtime 永远看不到 Secret。
模型产生的 Tool Call 只携带凭据引用和业务参数；执行端在授权通过后，使用 Run 与工具身份向凭据
服务申请短期凭据，作用域绑定目标服务、租户、资源和动作。

如果目标服务支持工作负载身份或代理调用，我会让工具根本拿不到 Token，由本地凭据代理代签请求；
确实需要把凭据交给进程时，通过仅该进程可读的文件描述符、内存或短期挂载注入，不放在命令行和
模型可见参数中，也不把整组环境变量传给任意 Shell。执行完成、租约失效或权限撤销后，凭据立即
失效，工具结果只返回业务结果和凭据引用编号。

Secret 生命周期由身份与凭据系统管理：策略决定谁能申请什么凭据，凭据服务负责签发、续期、轮换
和吊销，工具执行端负责使用和清理。长任务需要续期时重新授权，而不是提前发一个两小时有效的宽
权限 Token。审计记录申请者、凭据类型、目标和使用结果，不记录 Secret 值。

当前项目通过环境变量向 Provider、容器和 MCP 传递凭据，适合本地配置，但环境会被同进程代码、
Bash、子进程和诊断信息读取。项目没有凭据代理、动态签发和自动脱敏，因此现有方案不能作为生产
Secret 隔离。

### 追问问题与回答

**追问：如何避免把 API Key 放进 Prompt、环境快照、命令行参数或模型可见 Context？**

Prompt 和 Tool 参数只传凭据引用。执行端按需解析，并优先通过代理调用；必须注入时使用文件描述符、
内存或专用挂载。进程环境、诊断快照和日志采用明确字段清单，Trace 写入前删除凭据值。

**追问：Secret 的作用域、生命周期和轮换由谁管理？**

身份和凭据系统管理。授权策略根据租户、Run、Agent、工具和资源决定是否签发；凭据服务负责短期
凭据、续期、轮换和吊销；执行端只负责本次使用与清理，模型不能决定生命周期。

### 技术追问补充

- `provider_from_env()` 读取模型、Token、Base URL 和 API Kind；`resolve_api_key()` 在 Adapter
  调用前从 `os.environ` 取出凭据，未配置时失败。
- Model config 支持 inline `auth_token`，但会发出 Warning 并把值写入私有环境变量；这避免直接
  放入 Provider 对象，不代表 Secret 已离开进程环境。
- `container_provider_env()` 只收集调用者列出的 passthrough names 以及 NO_PROXY，但生成的
  `RunSpec.provider_env` 最终作为整个容器环境，而不是只对模型 Adapter 可见。
- MCP stdio config 可以携带 env，HTTP config 可以携带 headers；当前 JSON 配置格式没有
  secret-reference 类型，直接写 Token 会产生静态配置泄露风险。
- 生产工具请求只携带 `credential_ref`、目标受众、所需动作和资源；凭据服务校验 Run、工具操作和
  能力令牌后，返回代理会话或短期凭据。
- 凭据引用与凭据值分库存储。Run State、Message、Tool 参数、Tool Result 和 Trace 只允许出现引用
  和使用状态，不能出现可用的明文值。
- 代理模式下，Tool Worker 把业务请求交给本地代理，代理注入凭据并限制目标地址；这样即使工具进程
  被控制，也无法读取或转发 Token。
- 必须直接注入时，注入通道要与工具生命周期一致，并对同容器其他进程不可见；环境变量只适合受控
  低风险进程，不应作为高权限工具的默认方式。
- `ToolResult.details` 虽不进入普通模型请求，但会进入 State、Trace 和 Viewer，绝不能存放 Secret；
  错误、命令输出和子进程环境也要在返回前清理。
- 长任务续期需要再次校验租约、策略和资源范围。权限被撤销后，凭据服务拒绝续期并使旧 Token 尽快
  失效；执行端停止后续调用。
- 轮换保留新旧凭据的短暂重叠只由凭据系统控制。审计记录凭据 ID、主体、目标、时间和结果，不记录
  Secret Value，也不把 Token 哈希当作可公开标识。
- 测试应使用可识别的假 Secret，覆盖 Prompt、argv、环境快照、Tool Result、异常、Trace 和 Artifact，
  并断言所有非授权通道都无法找到该值。

## 106. Trace 如何避免记录 API Key 和敏感数据？

### 口述主回答

我会把普通运行 Trace 和高权限取证数据分成两种产品。普通 Trace 默认只保存运行身份、状态变化、
工具和模型调用元数据、经过处理的摘要以及 Artifact 引用，足够定位问题但不保存 Secret 和大段用户
原文。供应商原始请求响应只在明确开启调试时进入独立取证存储，使用更严格的权限和更短保留期。

脱敏必须在写入任何持久介质之前完成。事件和工具协议先给字段标注数据类型与敏感级别，统一
Sanitizer 根据结构化字段处理：凭据替换成不可用引用，用户标识做租户内假名化，敏感正文外置成
受控 Artifact，错误保留类型、状态码和定位信息但移除 Header、Token 和请求正文。无法确定是否
安全时，高敏任务拒绝写入，而不是先落盘再等待扫描。

为了保留调试能力，脱敏后仍保存字段位置、数据类型、长度、稳定关联编号和命中规则。例如两个事件
引用同一个账号时可以使用同一租户内假名，但不能恢复原值。读取端再按租户和角色授权，所有访问
都有审计；删除和保留策略同时覆盖主 Trace、raw、Artifact、索引和备份。

当前 Trajectory v5 会删除可重建的 `llm_payload`，并把 Provider raw 外置到 `*.raw.jsonl`，但这
只是体积优化。`json_safe()` 仍会序列化 Message、工具参数、结果、details 和未知对象的表示，
项目没有统一脱敏、加密和访问控制，所以现有 Trace 不能默认视为安全。

### 追问问题与回答

**追问：应该在采集前、序列化时还是存储后执行脱敏？**

先在采集前最小化，再在序列化前执行结构化脱敏；存储侧的加密、访问控制、审计和保留期是最后防线。
存储后扫描只用于发现遗漏，不能作为主要脱敏步骤。

**追问：Provider Raw、Tool 参数、Tool Result 和错误堆栈分别如何处理？**

Provider Raw 默认关闭，启用时进入独立取证存储；Tool 参数按 Schema 和资源类型处理；Tool Result
保存必要摘要与受控 Artifact 引用；错误保留错误类型、状态码和代码位置，但删除凭据、Header、
请求正文和用户私有数据。

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
- 可以在 `event_record()` 与所有 Trace Writer 之间增加唯一的 Sanitizer 边界，普通写入、实时写入、
  子 Trace 和训练导出都必须经过同一入口，避免某个旁路漏掉脱敏。
- Sanitizer 根据事件类型和工具 Schema 处理字段，并禁止未知对象直接使用可能泄密的 `repr()`；
  未知类型应拒绝或只记录类型名。
- 普通 Trace 和取证 raw 使用不同存储位置、加密密钥、访问角色和保留策略。普通 Viewer 不自动读取
  raw；临时调试授权到期后立即失效。
- 脱敏值可保留租户内稳定假名、内容哈希、长度和分类，用于关联事件和比较变化；哈希前加入租户密钥，
  防止对常见 Secret 或邮箱进行字典反查。
- Artifact 引用也要携带数据分类和访问策略。把敏感正文外置并不会自动安全，下载接口必须重新授权，
  生命周期结束后同步删除对象和索引。
- 子 Agent `sub_events`、MCP `structuredContent` 和 Workflow 子 Trace 需要递归清洗；不能只处理主
  Event 的第一层字段。
- 脱敏规则需要版本化，Trace Header 记录规则版本。审计只记录命中的规则、字段路径和处理动作，不
  再保存原始值。
- 使用假 Secret 和敏感用户数据做端到端测试，覆盖普通 Trace、实时 Trace、raw sidecar、Viewer、
  训练导出和错误路径；高敏配置下任何未处理命中都应使写入失败。

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
