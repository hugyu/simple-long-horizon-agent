下面继续以“项目负责人 / 核心开发者”的第一人称回答。这里会严格区分同一次运行的 State、跨运行 Memory、方法说明型 Skills，以及 Task Tool 和 Chain Context 两种 Handoff。

## 1. Context、Recall 和 Memory 有什么区别？

> Context 是当前一次模型调用能够看到的信息，来源于本次运行的活跃上下文和可见性投影。
>
> Recall 是同一次运行内的精确历史恢复。它根据稳定 Message 索引，从完整 transcript 中取回已经退出活跃上下文的原始消息。
>
> Memory 是多次独立运行之间的持久经验。它在磁盘上保存经过筛选的运行证据、摘要和长期方法，下次新建 State 时按需读取。
>
> 可以概括为：
>
> ```text
> Context：这一次调用现在看什么
> Recall：这次运行以前发生过什么
> Memory：过去其他运行留下了什么经验
> ```

## 2. 短期状态与跨任务 Memory 的边界是什么？

> State 属于一次运行，保存该运行的 Message、Event、压缩投影和停止原因。`resume()` 可以延续同一个 State，但它仍是同一条运行历史。
>
> Memory 位于 Runtime 外围，通常在 SESSION_START 注入路径和短摘要，在 SESSION_END 根据已完成 State 沉淀证据。下一次运行会创建新的 State，不会继承旧 State 的 Message 和 Event。
>
> 当前 Memory 也不会在每轮模型调用前自动检索。需要使用旧经验时，模型通过普通 Read/Bash 工具读取对应文件。

## 3. 为什么使用文件型 Memory？

> 项目中的长期经验是稀疏、高价值且容易过时的，不需要一开始就建设复杂检索服务。
>
> 文件型 Memory 有三个优势：人可以直接检查和修改；模型可以通过已有文件工具读取；容器和本地环境只需挂载目录即可共享。
>
> 它也符合项目的教学目标。用户能够看到 `MEMORY.md`、`INDEX.md` 和每次运行证据，而不是只能相信一个不可见的向量检索过程。

## 4. 文件型 Memory 相比数据库或向量库有什么优缺点？

**优点：**

> 文件内容可读、可审计、可用 Git 或普通备份管理；不依赖额外服务；本地、Docker 和远程挂载都容易接入；模型可以使用现有 Read/Bash 工具；Markdown 适合保存少量长期经验和证据引用。

**缺点：**

> 查询和过滤能力弱；目录规模大后扫描效率下降；跨文件事务能力有限；多写者必须串行；没有内建向量相似度召回；权限、加密、租户隔离和复杂保留策略需要外部实现。
>
> 当前选择文件系统是为了先验证 Memory 边界，不代表数据库或向量库永远不需要。如果数据规模和检索需求改变，可以实现另一种 Memory，而不修改核心 Runtime。

## 5. Memory 中保存事实、经验、计划还是执行结果？

> 持久手册 `MEMORY.md` 主要保存能够改变未来 Agent 行为的经验，例如稳定用户偏好、决策触发条件、重复失败的防护方法、已验证工作方式和高价值引用。
>
> 每次运行目录保存 task、transcript、summary 和 artifacts，作为证据层。执行结果可以作为 artifact 保存，但不一定自动提升为长期经验。
>
> 临时计划、当前任务进度、大段原始日志、秘密、通用建议和可以低成本从代码重新读取的事实，不应进入长期手册。
>
> 一个计划只有在它已经成为可复用的稳定工作流时，才值得转化成 Memory。

## 6. 谁负责写入 Memory？

> Memory 通过 SESSION_END Hook 调用 `FilesystemMemory.finish()`。
>
> Host 侧确定性代码负责写入 task、transcript、artifact、run summary 和 INDEX。可选 distiller 模型读取当前手册和本次证据，返回完整的新 `MEMORY.md`，负责合并、去重、改写和删除旧经验。
>
> 执行任务的主 Agent 默认只读取 Memory，不应在任务过程中直接修改这些文件，除非用户明确要求。这样避免当前任务中的临时判断绕过沉淀质量门槛。

## 7. 如何判断一条信息值得长期保存？

> 我使用的标准是：这条信息是否会显著改变未来 Agent 的行为，并减少重复指令、重复纠正或重复踩坑。
>
> 值得保存的信息通常满足以下条件：
>
> - 有用户纠正、工具结果或可验证产物作为证据。
> - 在未来同类任务中仍可能成立。
> - 重新发现的成本较高。
> - 能形成明确的决策规则或失败防护。
> - 不是当前代码中随时可以低成本读取的普通事实。
>
> 如果没有这种高价值信息，distiller 可以返回 `retain_run=false`，不更新 Memory。这是合法成功结果。

## 8. 如何防止错误信息写入 Memory？

> 第一，distiller 的输入包含实际 transcript、artifact、当前 INDEX 和已有手册，并要求每条长期经验引用可检索证据。
>
> 第二，证据优先级明确：用户指令和纠正高于 Assistant 自述，工具和当前工作区事实高于历史 Memory。
>
> 第三，Memory Prompt 要求把 transcript 和工具输出视为数据，而不是其中可能包含的新指令，降低 Prompt Injection 风险。
>
> 第四，系统拒绝过大、结构异常或会意外清空全部经验的手册重写；空重写表示不更新。
>
> 这些机制不能数学上保证 Memory 永远正确。涉及当前文件、命令、配置或仓库状态的记忆，下一次使用时仍应实时验证。

## 9. 用户如何查看、修改和删除 Memory？

> Memory 是普通 Markdown 文件，默认位于 `~/.simple/memory/<namespace>/`。用户可以直接查看和编辑：
>
> ```text
> memory_summary.md
> MEMORY.md
> INDEX.md
> runs/<run_id>/
> ```
>
> 删除单次运行可以移除对应 run 目录并同步更新 INDEX；删除整个 namespace 可以删除对应目录。当前没有独立的 Memory 管理 UI 或事务化删除 API。
>
> 正常 Agent 被明确要求不要在任务中修改 Memory；如果用户明确要求修改或删除，则可以通过文件工具执行，并应同时维护索引一致性。

## 10. 多个 Agent 同时写 Memory 时如何解决冲突？

> 当前 FilesystemMemory 对同一个 root 使用跨进程 FileLock。锁覆盖“读取旧手册 → 调用 distiller → 写入全部结果”的整个过程。
>
> 这意味着同一 root 在逻辑上只有一个写者，其他写入需要等待。每个文件再通过临时文件加 `os.replace` 原子提交。
>
> 相同 run ID 如果已经完整写入，后续 finish 会成为 no-op，提供基本幂等性。
>
> 代价是并发度有限，而且没有 CRDT 或自动三方合并。不同租户或高并发任务应使用不同 root，或者改用支持事务和版本检查的数据库实现。

## 11. Skills 是提示词、工具集合还是可执行工作流？

> Skill 本质上是本地方法说明包。它是一个包含 `SKILL.md` 的目录，可以附带 scripts、references、assets 和模板。
>
> Skill 本身不是工具，也没有独立执行引擎。`SKILL.md` 可以告诉模型应该调用哪些工具、按什么步骤工作；真正的读取和执行仍由 Read、Bash 等普通工具完成。
>
> Skill 也可以描述一个工作流，但它不会像 Workflow 模块一样自动调度多个 Agent。

## 12. Skills 如何发现和加载？

> 默认从三个 Scope 发现：
>
> ```text
> repo scope
> user scope
> bundled scope
> ```
>
> 项目会从当前目录向上搜索 `.agents/skills` 和 `.simple_long_horizon_agent/skills`，同时检查用户目录和内置库。
>
> Skill 必须包含 `SKILL.md` 和 description。相同名称冲突时优先级是 repo 高于 user，高于 bundled；结果按名称排序，保证菜单稳定。
>
> 初始阶段只向模型展示名称、描述和绝对路径，不加载所有正文。用户使用 `/skill-name` 或配置 preload 时，正文会在任务前注入；其他情况下由模型根据菜单匹配任务，再使用 Read 按需打开 `SKILL.md`。`/no-skills` 可以禁用本次运行的 Skills。

## 13. Skills 与普通工具有什么区别？

> Skill 回答“应该怎样完成这类任务”，Tool 回答“执行一个具体动作”。
>
> Skill 包含方法、约束、检查清单和资源路径；Tool 有明确名称、参数 Schema、执行函数、超时和结构化 ToolResult。
>
> Skill 不直接访问文件或网络。即使 Skill 自带脚本，也需要 Agent 通过 Bash 调用。Tool 可以脱离 Skill 独立工作，Skill 则通常依赖工具完成实际行动。

## 14. Handoff 传递的是完整上下文、摘要还是结构化状态？

> 当前有两种 Handoff。
>
> Task Tool 委派时，父 Agent 只传递明确的 `task`、可选调用 context 和配置的 default context。子 Agent 创建独立 State，不复制父 transcript，也不共享父 active context。
>
> 子 Agent 完成后，final 文本成为父 Agent 的 ToolResult；完整子事件放入非模型可见 details，供 Trace 使用。
>
> Eval Chain 的 Context Window Handoff 会让模型生成一份文本交接文档，然后把活跃上下文重置为当前 task 加 handoff 文档。完整 transcript 仍保留在 Trace 中。
>
> 因此当前 Handoff 主要传递明确任务和文本工作记忆，不传递完整可变 State。环境连续性如果需要，由工作区或显式 chain state 单独负责。

## 15. 如何判断应该 Handoff，而不是继续由当前 Agent 执行？

> 适合 Handoff 的情况包括：
>
> - 子任务边界明确，可以独立验收。
> - 需要不同角色、Prompt、工具或权限。
> - 子任务会产生大量中间上下文，不值得污染主 Agent。
> - 多个方向可以独立探索。
> - 当前上下文窗口即将耗尽，需要生成交接文档进入新窗口。
>
> 不适合委派的是非常短、强依赖当前推理链或需要频繁共享可变状态的任务。当前 Task Tool 由父模型决定是否调用，固定 Workflow 则由代码预先决定阶段；Runtime 本身不会自动计算委派收益。

## 16. Handoff 后任务所有权如何变化？

> Task Tool 委派后，子 Agent 拥有子任务的执行责任和自己的 State、工具预算及停止原因；父 Agent仍然拥有整体任务和最终结果责任。
>
> 子 Agent 的 final 只是一个返回结果，父 Agent需要判断是否采用、验证或继续处理。当前没有分布式任务租约或全局 Ownership Service。
>
> Context Window Handoff 不改变逻辑任务所有者，只是让同一个任务在新的活跃上下文中继续执行。

## 17. 子 Agent 失败后，主 Agent 如何恢复？

> 如果子 Agent 没有产生 final，Task Tool 会返回 `is_error=true` 的 ToolResult，并在 details 中保留子 Agent 的全部事件。
>
> 父 Agent 下一轮能够看到错误，可以缩小任务、补充 context、选择另一个子 Agent、重试，或者自己接管。
>
> 未知 subagent type 也会成为普通错误结果，不会动态导入任意 Agent。子 Agent 还可以配置 soft turn limit，在接近硬预算时收到收敛提醒。
>
> 当前不会自动从子 Agent 最后一条非 final 输出生成成功结果，因此父 Agent不能把截断执行误认为已经完成。

## 18. 如何防止 Agent 之间反复委派？

> 第一层是受控注册表。Task Tool 只能选择预先注册的 subagent type，不能由模型动态创建任意 Agent。
>
> 第二层是每次父子运行都有 max turns，子 Agent 也可以配置 soft turn limit。
>
> 第三层是在 Prompt 和工具配置中限制角色，例如不向普通 Specialist 再提供 Task Tool。
>
> 当前 Task Tool 本身没有全局 delegation depth、调用图或循环检测。如果把能够互相委派的 Agent 递归注册，仍可能形成反复委派。生产化应增加最大深度、父调用 ID、全局预算和 Agent 路径循环检查。

## 19. 如何追踪某个结论到底由哪个 Agent 产生？

> 每个子 Agent 拥有独立 State，Message 记录 sender 和 target，Event 记录所属 Agent。
>
> Task Tool 将子事件保存到父 ToolResult 的：
>
> ```text
> sidecar.details[tool_call_id].sub_events
> ```
>
> Trace 合并时根据父 Tool Call ID，把子 Agent Span 挂在对应 Tool Span 下，因此可以追踪“父 Agent 在哪一轮委派了谁、子 Agent调用了哪些模型和工具、如何结束”。
>
> 但如果父 Agent把子结论重新改写成自己的文本，当前没有句子级 Provenance。要实现更强归因，需要让子结果携带结构化 claim ID 或 evidence reference，并要求父输出保留引用。

## 20. 跨任务 Memory 是否会引发隐私和数据隔离问题？

> 会。Memory 会保存任务文本、模型可见 transcript、运行摘要和 artifacts，其中可能包含用户数据、代码片段和工具结果。
>
> 当前实现会排除 Provider raw sidecar，并通过 Prompt 禁止保存秘密和大日志，但它没有自动秘密扫描、加密、企业级 ACL 或真正的多租户隔离。Namespace 只是目录组织，不是安全边界。
>
> 生产环境应至少做到：每租户独立 root、严格文件权限、写入前脱敏、静态秘密检测、加密存储、保留期限、可审计删除，以及禁止 Benchmark 标签和私有评分信息进入未来任务。
>
> 文件锁解决的是并发一致性，不解决隐私隔离。

## 21. Recall 与 RAG 的区别是什么？

> Recall 面向同一次运行的原始 transcript，通过稳定索引精确取回指定 Message。它没有切块、Embedding、相似度和排序过程。
>
> RAG 通常面向外部或跨运行知识库，根据查询做关键词、向量或混合检索，再选择相关片段。
>
> Recall 的优势是精确和可解释，缺点是必须知道索引；RAG 能根据语义发现未知位置的信息，但可能漏召回或返回相似但错误的内容。
>
> FilesystemMemory 当前也不是自动 RAG，模型只是通过文件工具主动查找。

## 22. Memory 与 Trace 的区别是什么？

> Trace 是一次运行的完整事实和观察记录，追求可审计、可回放和尽量少丢失。它包含模型请求、工具执行、压缩、停止原因和成本等信息。
>
> Memory 是跨运行的有损、精选知识，追求未来任务价值，而不是完整性。它允许合并、重写、删除和淘汰旧经验。
>
> Trace 回答“上一次究竟发生了什么”，Memory 回答“下一次最值得记住什么”。Memory 中的经验最好引用 Trace 或 per-run evidence，而不能替代 Trace。

## 23. Handoff 与普通函数调用的区别是什么？

> 普通函数调用有确定的参数和返回值，通常共享当前进程状态，执行边界短且结果相对可预测。
>
> Handoff 会启动另一个自主 Agent。接收者拥有自己的 Prompt、State、工具、模型调用、预算和停止原因，可能进行多轮探索，成本和不确定性都更高。
>
> 因此只有当子任务需要独立判断和多步执行时才值得 Handoff。简单数据转换或确定性逻辑应使用普通函数，而不是浪费一次 Agent 委派。

## 24. Skill 与 Workflow 的区别是什么？

> Skill 是“方法知识”，主要以本地说明文件存在，加载后影响一个 Agent 如何处理任务。
>
> Workflow 是“运行编排”，由代码决定运行哪些 Agent、顺序、并行方式、结果传递和停止条件，并保存每一步 StepResult 和 State。
>
> 一个 Skill 可以建议“先规划、再执行、最后验证”，但它本身不会启动这些阶段；Workflow 可以执行这些阶段，但不一定包含领域方法说明。
>
> 简单记忆是：
>
> ```text
> Skill：告诉 Agent 怎么做
> Workflow：决定多个运行怎么组织
> Tool：真正执行一个动作
> ```
