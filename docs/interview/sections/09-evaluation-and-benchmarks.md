下面继续以“项目负责人 / 核心开发者”的第一人称回答。回答严格区分三类信息：

- 当前仓库能够直接证明的架构和运行机制。
- README 已发布、但当前 checkout 未附完整原始产物的成绩。
- 基于现有设计构造的故障处理回答。

## 1. Suite、Backend、ArtifactStore 分别解决什么问题？

> `Suite` 定义任务语义，包括使用哪个镜像和工作目录、模型能看到什么任务、哪些私有数据只能用于评分，以及容器内如何构造任务和提取结果。
>
> `Backend` 定义任务在哪里执行以及生命周期如何管理，包括启动、等待、中止和收集本地进程、本地 Docker 或远程 Docker。
>
> `ArtifactStore` 定义输入、结果、轨迹和 Manifest 的字节如何传输和持久化。它不理解 Benchmark，也不负责启动容器。
>
> Runner 只负责把三者装配起来，不包含 `if benchmark == swebench` 之类的领域分支。

## 2. 为什么要把任务语义、执行环境和运行产物解耦？

> 这三个维度的变化频率和所有者不同。Benchmark 决定任务和评分语义，基础设施决定运行位置，ArtifactStore 决定数据传输方式。
>
> 如果耦合在一起，每新增一个 Benchmark 都要重新实现 Docker 生命周期；每更换远程机器，也要修改任务构造和评分代码。
>
> 解耦之后，我可以使用同一个 Suite 从 LocalProcess 切换到 LocalDocker，也可以在 RemoteDocker 中根据网络拓扑选择 HostHttpStore 或 host-pull，而不修改 Agent 的任务逻辑。

## 3. 本地进程、本地 Docker、远程 Docker 如何保持一致？

> 三种 Backend 接收相同的 `RunSpec`，执行相同的 Container Half，并遵循相同的输入键、输出键和目录协议。
>
> LocalProcess 直接在传入的 workspace 中调用 Container Half；LocalDocker 在镜像工作区内调用；RemoteDocker 通过远程 Docker daemon 启动同样的容器入口。
>
> 它们共同产出：
>
> ```text
> input/instance.json
> input/eval.json
> out/result.json
> out/trajectory.jsonl
> ```
>
> 我不会声称三种环境在操作系统和依赖上完全相同。真正保持一致的是执行协议、Suite 逻辑和产物 Schema，环境差异由 `LaunchSpec` 显式表达。

## 4. Benchmark 任务如何转换成统一执行协议？

> Host Half 先加载原始实例，通过 `task_input()`删除 gold、隐藏测试等私有字段，再将公开输入写入 `input/instance.json`。
>
> Container Half 的 `build_task()`把公开实例转成 Agent 可理解的任务；运行结束后，`extract_result()`从工作区提取真实产品，例如 Git Patch、完整 workspace 压缩包或答案。
>
> 私有评分数据通过 `eval_inputs()`单独写入 `input/eval.json`，不会进入 Agent Context。评分可以在环境内执行，也可以交给后续 Judge Run 或官方 Harness。

## 5. SWE-bench Pro 的任务成功条件是什么？

> Agent 最终输出文本不决定成功。Agent 在实例对应的代码仓库中修改文件，系统提取相对 pre-agent Git baseline 的 `model_patch`，再交给官方 SWE-bench Pro Harness。
>
> Patch 必须先能够通过严格的 `git apply --check`。应用、reset、checkout 或 patch 校验失败时，该实例直接记为 unresolved。
>
> Patch 成功应用后，由官方测试和解析逻辑判断是否解决该实例。只有官方结果中的 `resolved=true` 才算通过。

## 6. Terminal-Bench 2.1 如何判定任务完成？

> Terminal-Bench 由 Harbor 管理任务环境、生命周期和 Verifier。SAL 只负责在 Harbor 任务容器内运行 Agent 和工具。
>
> Agent 的 final summary 不直接算分。任务结束后由 Harbor 执行任务自带的 Verifier，并把规范结果写入 Harbor 的 job-level `result.json`。
>
> 因此 SAL 的 trajectory 和 summary 是调试材料，Harbor 的正式结果才是分数来源。

## 7. PostTrainBench 为什么能代表长周期 Agent 能力？

> PostTrainBench 测试的不是一次回答，而是 Agent 能否完成模型后训练流程，并让目标模型在多个外部能力指标上产生可验证提升。
>
> 当前公开结果中，GPT-5.5 xHigh 是执行后训练任务的 Agent 模型，Qwen3-4B-Base 是被训练和评估的目标模型。最终结果综合 AIME 2025、BFCL、GSM8K 和 HumanEval，并使用 normalized reward。
>
> 它代表的是系统级长周期任务：理解目标、修改训练方案、运行训练、读取评测并迭代，而不是说四个子 Benchmark 的每一道题本身都是长周期 Agent 任务。
>
> 当前 checkout 没有 PostTrainBench 的本地 Suite、训练配置和逐子项产物，所以我只能引用 README 发布结果，不能声称它像 SWE-bench Adapter 一样可以从当前仓库完整核验。

## 8. ProgramBench 与 PostTrainBench 是什么关系？

> 它们是两个不同 Benchmark，不是同一个项目的两个名字。
>
> ProgramBench 是逆向工程任务：Agent 只能观察编译后的 `./executable` 和文档，需要从零重建行为一致的代码库。当前仓库有完整 Adapter 和官方评分入口，但没有在 README 发布最终榜单成绩。
>
> PostTrainBench 是自主模型后训练任务。README 发布了 45.88% 的结果，但当前仓库没有对应本地 Suite 和完整产物。
>
> 简历原句先说“接入了 SWE-bench、Terminal-Bench、ProgramBench 等评测”，后面再列三项公开结果，其中第三项是 PostTrainBench。这个表达容易误读，面试时我会主动把“已接入”和“已发布成绩”分开说明。

## 9. Baseline 具体是什么配置？

> README 对 Baseline 的定义是：相同模型和任务预算下的基础方案。
>
> 当前公开对应关系是：
>
> | Benchmark | 项目配置模型 | 项目分数 | Baseline |
> | --- | --- | ---: | ---: |
> | SWE-bench Pro | GPT-5.4 xHigh | 63.20% | 59.10% |
> | Terminal-Bench 2.1 | GPT-5.3-Codex xHigh | 77.53% | 64.70% |
> | PostTrainBench | GPT-5.5 xHigh | 45.88% | 43.97% |
>
> 但当前 checkout 没有随仓库提交与三项成绩一一配对的 baseline trajectory、run manifest 和逐实例结果。因此我可以确认公开口径，不能只凭当前文件独立核验 Baseline 的全部参数。

## 10. “相同模型、相同任务预算”包括什么？

> 最低限度应固定模型版本、reasoning effort、任务集合、每任务最大回合、总 Token 或等价计算预算、墙钟时间、工具权限、容器环境和评分器版本。
>
> 如果加入 Task Tool，父 Agent 和子 Agent 消耗的 Token、回合和时间必须一起计入预算，不能只计算父 Agent。
>
> 并发数主要影响总实验耗时，不应改变单任务预算。基础设施重试也必须单独记录，不能把失败实例无限重跑到成功。

## 11. 是否固定模型版本、温度、最大 Token 和工具权限？

> 正式对照实验应该全部固定并写入 Manifest。当前公开结果明确给出了模型名称和 xHigh reasoning effort。
>
> SWE-bench Pro 的实验 Runner 能记录模型、API Kind、reasoning effort、max turns、Agent flavor、Task Tool、压缩策略、上下文阈值和 Handoff 配置。Terminal-Bench 启动器固定 150 turns、300 秒 Bash 上限和三倍任务超时。
>
> OpenAI Responses 路径通常不发送 temperature，因为接口和推理模型可能不接受该参数。这里的“固定”可以是明确记录为“不发送”，不一定强制设为零。
>
> 但当前发布成绩没有附完整 Manifest，因此我不会现场编造对应成绩的 max output tokens 或全部工具白名单。

## 12. 每个任务运行一次还是多次？

> 当前公开分数更接近一次全量评测中每个实例产生一个正式 Prediction，而不是多次采样后取最好结果。
>
> 仓库没有发布多 seed、多次重复实验的聚合数据。因此目前不能报告重复次数、均值和方差，也不能证明结果对模型随机性稳定。
>
> 如果后续做稳定性实验，我会将每次重复作为独立 run ID 保存，不能把不同重复中的最佳实例拼成一个总分。

## 13. 结果有没有置信区间？

> 当前 README 只发布点估计，没有置信区间。
>
> SWE-bench Pro 可以对 731 个 paired instance 使用 bootstrap 区间，并对候选与 Baseline 的逐实例胜负使用 paired bootstrap 或 McNemar 检验。
>
> 对有连续 reward 的 Benchmark，应保留每任务 reward，再对任务维度做 bootstrap。没有逐实例配对产物时，不能只根据两个总分可靠推导显著性。

## 14. 如何处理模型输出的随机性？

> 当前机制主要通过固定模型、reasoning effort、任务集合、预算、Prompt、工具和环境减少非随机差异，但不能消除服务端模型的随机性和版本漂移。
>
> Trace 会保留每次 Model Turn、工具调用、Token 和结果，保证单次运行可以解释；它不等于运行可以逐 Token 重放。
>
> 更严谨的方案是每个实验臂至少重复多次，报告均值、区间、paired win/loss 和成本分布。当前公开结果还没有达到这一证据级别。

## 15. 是否存在失败重试？重试是否计入预算？

> 我区分三类重试。
>
> 第一类是 Provider 的限流和临时服务错误，可以在同一次模型调用边界内退避重试。它不产生新的 Benchmark 样本，但要记录 API 尝试、延迟和成本。
>
> 第二类是 Docker daemon 中断等基础设施异常。`run_dataset` 只对抛出的临时异常按 `max_attempts` 重试。
>
> 第三类是 Agent 已经完成但测试失败、exit code 非零或官方评分 unresolved。这是有效失败结果，不自动重跑成成功。
>
> 例如容器已经产出错误 Patch，我不会把它包装成“基础设施失败”重新执行。否则结果会变成隐式 best-of-N。

## 16. Benchmark 是否存在数据污染风险？

> 存在，而且需要分别处理推理时泄漏和模型预训练污染。
>
> 推理时，`task_input` 与 `eval_inputs` 使用不同通道。SWE-bench 的 gold patch、test patch 和隐藏测试不能进入模型上下文；官方 Judge 在独立干净容器中执行。
>
> ProgramBench 的 Agent Bash 命令运行在无网络 namespace 中；如果 `unshare --net` 不可用，默认 fail closed，而不是静默取消反作弊。
>
> 但模型是否在预训练阶段见过公开仓库、Issue 或 Benchmark 数据，单靠 Runtime 无法完全证明。能做的是使用时间切分、私有任务、污染检查和多 Benchmark 交叉验证，并明确这一限制。

## 17. 63.20% 的分母是多少？

> SWE-bench Pro 全量 test split 是 731 个实例。63.20% 对应 462 个 resolved：
>
> ```text
> 462 / 731 = 63.201...%
> ```
>
> 对应的 59.10% Baseline 约为：
>
> ```text
> 432 / 731 = 59.097...%
> ```
>
> 框架收集结果时会保留全部计划实例。运行失败或缺失结果会写成空 Patch 并计为 unresolved，不能通过遗漏失败样本缩小分母。

## 18. 相对提升和绝对百分点提升有什么区别？

> 以 SWE-bench Pro 为例：
>
> ```text
> 绝对提升 = 63.20% - 59.10%
>          = 4.10 个百分点
>
> 相对提升 = 4.10 / 59.10
>          ≈ 6.94%
> ```
>
> 因此准确说法是“提高 4.10 个百分点，相对提升 6.94%”，不能说“提高 6.94 个百分点”。

## 19. 为什么使用相对提升作为简历口径？

> 相对提升便于表达在原有能力基础上的增益比例，也能让不同原始分数的 Benchmark 更容易比较。
>
> 但只写相对提升容易放大小基数效果，所以简历和面试中我会同时给出项目分数、Baseline、绝对百分点差和相对提升。
>
> 相对提升是展示口径，不替代显著性、成本和逐任务分析。

## 20. 有没有组件消融实验？

> 当前没有完整、公开、可独立核验的组件消融表。
>
> 仓库已经提供 Task Tool、bash/bash_task、Compression、Handoff、Memory、Loop 和 PDR 等可配置实验臂，但“存在开关”不等于“已经完成严格消融”。
>
> 所以我不会声称 Task Tool 单独贡献 4.10 个百分点，也不会给 Compression、Handoff 或 Reflection 编造收益数字。

## 21. 没有消融实验，如何判断提升来自哪里？

> 严格来说，不能判断单个组件的因果贡献。
>
> 当前能够得出的结论是：公开的端到端 Agent 配置在对应 Benchmark 上高于列出的 Baseline。
>
> Trace 可以帮助提出假设，例如成功任务是否更常使用 Task Tool、是否发生 Handoff、压缩后是否成功 Recall，但这种相关性分析不能替代随机或配对消融。

## 22. 三个 Benchmark 是否使用同一套 Agent 配置？

> 不是。它们共享 Runtime、工具协议、Trace 和 Eval 设计原则，但模型、任务产品、工具组合、预算和评分器不同。
>
> SWE-bench Pro 使用 GPT-5.4 xHigh，公开说明采用类似 ChainSWE 的链式方式并加入 Task Tool。
>
> Terminal-Bench 2.1 使用 GPT-5.3-Codex xHigh，由 Harbor 管理任务和 Verifier。仓库有 bash 与 bash_task 两种入口，但当前成绩行没有明确绑定其中哪一个。
>
> PostTrainBench 使用 GPT-5.5 xHigh 执行后训练任务，目标模型是 Qwen3-4B-Base。
>
> 因此这三项证明的是同一 Runtime 思路适配不同长任务，不是“一套完全相同配置横扫三个 Benchmark”。

## 23. 是否针对 Benchmark 单独调整 Prompt 或 Workflow？

> 会有必要的 Suite 级适配，但必须区分领域适配和针对答案调参。
>
> SWE-bench 的任务要求修改仓库并验证测试，ProgramBench 要求观察黑盒程序并重建 workspace，Terminal-Bench 则是终端操作。它们不可能使用完全相同的任务说明和结果提取方式。
>
> 核心 Agent Loop 和工具协议保持通用；Suite 负责 build task、环境、结果提取和评分边界。调优应基于训练集或开发集，不能读取 test gold 后逐实例改 Prompt。
>
> 所有 Benchmark 专用 Prompt、Workflow 和预算都应该进入 Manifest，否则无法判断收益来自 Runtime 还是 Benchmark 定制。

## 24. Eval Harness 本身是否经过验证？

> 验证分为三层。
>
> 第一层是无网络单元测试：使用 FakeBackend 验证 Runner、Store、目录隔离、重试、Submit/Reconcile 和结果收集。
>
> 第二层是 LocalProcess 加 Fake Provider，执行真实 Container Half，验证 `build_task → Agent → extract_result`。
>
> 第三层是官方 Harness parity。SWE-bench 提供 gold patch smoke，并可比较环境内评分和独立官方 Harness 的 resolved 结果；ProgramBench 使用官方 evaluator；Terminal-Bench 直接以 Harbor verifier 和 job result 为准。
>
> 这能证明评分路径和映射机制，但当前 checkout 没有三项公开成绩的完整原始目录，因此不能完成对发布数字的逐项重新审计。

## 25. 如何确保容器环境没有泄漏答案？

> Host Half 在写入模型可见输入前删除 gold 和 private 字段。私有评分数据只能进入独立的 `input/eval.json`，不会被 `build_task()`加入 Context。
>
> SWE-bench 的 gold patch、test patch 和 FAIL_TO_PASS/PASS_TO_PASS 属于 Host 评分路径；Agent 只能看到 problem statement 和工作区。
>
> 正式评分在独立干净环境中运行，避免 Agent 修改评分器。ProgramBench 对 Agent Bash 命令禁网，并在评分时恢复原始 executable、校验 SHA256。
>
> 此外还需要控制 bind mount、环境变量和日志脱敏。通道隔离可以降低泄漏风险，但不能代替对镜像、数据集和模型历史污染的审计。

## 26. 成功率提高后，平均成本和耗时增加了多少？

> 当前 README 发布的单任务成本是：
>
> - SWE-bench Pro：`$7.7823`
> - Terminal-Bench 2.1：`$0.5667`
> - PostTrainBench：尚未发布
>
> 当前没有发布与 Baseline 配对的平均成本、P50/P95 耗时和增量 Token，因此不能回答“为了这些提升额外增加了百分之多少成本”。
>
> 这是现有结果报告的缺口。下一版实验应同时报告成功率、输入/输出 Token、缓存 Token、美元成本、模型调用次数和墙钟时间。

## 27. 成功率提升 10%，但 Token 成本提升 100%，如何评价？

> 首先要确认比较是否仍满足“相同任务预算”。如果 Token 翻倍，就不能再将分数差简单描述为同预算下的架构收益。
>
> 工程上要看边际价值：
>
> ```text
> 每新增一个成功任务的成本
> = 新方案总成本增量 / 新增成功任务数
> ```
>
> 对高价值代码修复，成功率提升可能值得双倍 Token；对低价值批量任务则可能不值得。
>
> 我会画成功率—成本 Pareto 曲线，并提供固定成本、固定延迟和最高成功率三个运行档位，而不是只追求最高分。

## 28. 有没有挑选最优运行结果？

> 标准评测口径不应该挑选成功结果。每个计划实例必须对应一个 Prediction；缺失、基础设施失败和空 Patch 都留在分母中。
>
> 收集器会拒绝重复或非预期 instance ID，并把失败实例写成空 Patch，因此不能只收集成功目录。
>
> 当前仓库没有发布对应成绩的逐实例 artifacts，所以外部读者无法仅凭 checkout 独立审计是否存在 best-of-N。正式发布时应该附 run ID、expected ID 列表、预测文件和官方评分结果，消除这个证据缺口。

## 29. 如何复现实验？

> 方法层面的复现流程是：
>
> 1. 固定代码 Commit、数据集 Split、镜像和官方评分器版本。
> 2. 通过 `uv sync --extra <benchmark>`安装对应可选依赖。
> 3. 使用 Profile 或 Experiment Manifest 固定模型、API Kind、reasoning effort、Agent flavor、工具和预算。
> 4. 通过统一 `runs.run_bench` 入口运行全部实例。
> 5. 保留 `result.json`、`trajectory.jsonl`、预测文件、Manifest 和异常记录。
> 6. 使用官方 Harness 评分，并确认失败实例没有从分母消失。
>
> SWE-bench Pro、ProgramBench 和 Terminal-Bench 的运行入口当前都存在。但要精确复算 README 的三项公开数字，还缺对应成绩的完整 Manifest 和产物；PostTrainBench 当前还缺本地运行 Adapter。因此准确说法是“仓库可复现评测方法和主要 Adapter”，不是“当前 checkout 可以一键复算全部三项成绩”。

## 30. 这些数字对应哪个模型和什么硬件？

> 模型对应关系是：
>
> - SWE-bench Pro：GPT-5.4 xHigh
> - Terminal-Bench 2.1：GPT-5.3-Codex xHigh
> - PostTrainBench：GPT-5.5 xHigh，目标模型为 Qwen3-4B-Base
>
> 模型推理运行在供应商服务端，本地硬件主要负责容器、代码执行、测试和产物收集，不决定模型本身的算力配置。
>
> SWE-bench 使用 per-instance Docker 镜像，远程 Worker 可以只是运行 Docker daemon 的 Linux 主机。仓库也支持 Apple Silicon 通过 Rosetta 调试，但这不代表公开成绩就是在 Mac 上完成。
>
> 当前 README 没有发布对应成绩的 Host CPU、内存、Worker 数量和完整 Docker 版本，因此我不会编造具体机器型号。

# 高压追问

## 31. 为什么没有做完整消融实验？

> 第一阶段我优先完成端到端 Runtime、真实环境接入、官方评分、Trace 和全量 Benchmark，先确认系统能够在外部标准下工作。
>
> 完整消融成本很高。SWE-bench Pro 一次 731 实例的运行，按当前公开单任务成本估算已经是数千美元；如果再对 Task Tool、Compression、Handoff、Memory 和 Workflow 做多次重复，成本会迅速扩大。
>
> 但成本不是免除证据责任的理由。没有消融意味着我只能报告整体配置结果。下一阶段的优先级应该从继续增加功能转向配对消融和统计稳定性。

## 32. 当前证明的是系统整体有效，还是某组配置有效？

> 严格结论是：某些明确的 Agent 配置在对应任务集和公开比较口径下有效。
>
> 三项结果说明这套 Runtime 能够承载软件修复、终端操作和模型后训练，但不能证明所有组件都有效，也不能证明它对所有长周期任务都优于 Baseline。
>
> “系统整体有效”可以作为工程概括；研究结论必须缩小为“这些配置在这些 Benchmark 上取得这些结果”。

## 33. 如何设计 Context、Handoff、Reflection、Task Tool 的消融实验？

> 我会先固定：
>
> ```text
> 同一代码 Commit
> 同一模型和 reasoning effort
> 同一 731 个实例
> 同一镜像和官方评分器
> 同一总 Token、turn 和墙钟预算
> 同一重复次数
> ```
>
> 然后设计配对实验臂：
>
> | 组件 | Control | Treatment |
> | --- | --- | --- |
> | Task Tool | bash | bash + task |
> | Context | 不压缩或固定截断 | summarize |
> | Handoff | summarize 或无保护 | 相同阈值下 handoff |
> | Reflection | 单次 solver | solver + bounded critic/revision |
>
> Task Tool 必须把父子 Agent 的总 Token 计入预算。Handoff 与 summarize 使用相同触发阈值，例如 217,600 Token。Reflection 不能通过额外无限计算取得不公平优势。
>
> 每个实验报告总分、paired win/loss/unchanged、置信区间、McNemar 或 paired bootstrap、Token、成本和延迟。先做单变量实验，再对可能存在交互的 Task Tool × Context/Handoff 做有限因子实验。

## 34. 最大的混杂变量是什么？

> 最大风险是 Baseline 与候选方案是否真正形成同任务、同模型版本、同预算的逐实例配对。
>
> 当前 README 链接外部 Baseline，但 checkout 没有完整 baseline artifacts。即使模型名称相同，服务端模型快照、Harness 版本、Prompt、工具权限、超时和重试策略不同，都可能造成差异。
>
> 第二类风险是 Benchmark 专用 Workflow 和 Prompt，使我们难以区分通用 Runtime 收益与专项调优收益。
>
> 第三类风险是模型随机性和缺少重复实验。
>
> 因此最重要的改进不是再增加一个 Agent 模块，而是提交候选与 Baseline 的完整配对 Manifest、逐实例结果、成本和重复实验数据。
