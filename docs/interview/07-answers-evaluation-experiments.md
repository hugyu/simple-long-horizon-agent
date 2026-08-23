# 07. Eval 与实验设计

本文对应 [`question-checklist.md`](question-checklist.md) 中第七部分的问题。每道题包含可直接
口述的主回答；存在清单子问题时，保留追问原文和简短回答；最后记录整组问题对应的仓库实现。

## 75. Eval Framework 的整体架构是什么？

### 口述主回答

我把评测拆成任务语义、执行位置和产物存储三个正交边界。Suite 负责把某个 Benchmark 实例变成
模型任务和启动配置；Backend 决定在本地进程、本机 Docker 还是远程 Docker 执行；Store 负责
输入、结果和 Trace 的字节传递。

Runner 只负责把三者组装起来执行单个实例，Dataset 驱动负责并发运行多个实例。评分可以在运行
环境内完成，也可以作为后续 Judge Run 或官方 Harness 执行，不强迫所有套件使用同一种评分器。

### 追问问题与回答

**追问：Dataset、Suite、Backend、Store、Runner 和 Scorer 分别负责什么？**

Dataset 提供实例集合，Suite 定义任务语义，Backend 决定执行位置，Store 传递产物，Runner 组装
单次运行，Scorer 根据结果和私有证据评分。

**追问：本地进程、本地 Docker 和远程 Docker 如何复用同一套评测协议？**

三种 Backend 都消费同一个 `RunSpec`，执行同一个 Container Half，并通过同一种 Store key
读写输入、结果和 Trace；变化的只是环境和传输方式。

### 技术追问补充

- `Suite` Host Half 提供 `launch_spec()`、`task_input()`、`eval_inputs()`、名称和
  `container_module`；Container Half 提供 `build_task()` 与 `extract_result()`。
- `run_suite_instance()` 负责准备运行目录、写入输入、构造 `RunSpec`、调用 Backend 并收集
  `RunArtifacts`，自身不包含 Benchmark 特殊分支。
- `ContainerBackend` 统一接收 RunSpec、Store 和 Binding；`ArtifactStore` 统一使用
  `input/instance.json`、`out/result.json` 和 `out/trajectory.jsonl` 等 key。
- 评分可由 Container Half 的 `evaluate()`、后续 Judge Suite 或 Host 侧官方 Harness 完成，
  `result.json` 是运行与评分之间的解耦产物。

## 76. 一个 Eval Task 包含哪些内容？

### 口述主回答

一个评测任务不只是 Prompt。它至少包括公开任务输入、执行环境、Agent 配置、运行预算、结果提取
方式和评分证据。项目会把模型可见输入和隐藏答案、测试脚本等私有评分数据分开存放。

运行结束后，任务产品写入 `result.json`，过程写入 `trajectory.jsonl`，评分结果再合并或单独
保存。这样可以区分 Agent 做了什么、环境产生了什么，以及评分器为什么判定通过。

### 追问问题与回答

**追问：任务输入、环境、Agent 配置、预算、验证器和产物之间如何分离？**

公开任务与私有评分输入分开存储；环境由 LaunchSpec 描述，Agent 和预算进入 RunSpec；产品和
Trace 独立落盘，验证器只读取它需要的结果和私有证据。

### 技术追问补充

- Host 将 `suite.task_input(instance)` 写入 `input/instance.json`；`suite.eval_inputs(instance)` 若
  非空则单独写入 `input/eval.json`，不会作为普通 Agent 任务输入。
- `LaunchSpec` 保存 image、workdir、shell、network、capability 和资源限制；`RunSpec` 追加模型
  接口、最大回合、墙钟时间和 Provider 环境。
- Container Runner 调用 `build_task()`、运行 Agent、调用 `extract_result()`；若存在 evaluate 且
  staged eval input 可用，再把评分合入 `result.json`。
- 每个 `<run_id>/<instance_id>/` 目录独立保存 input、result、trajectory 和可选 raw sidecar。

## 77. Agent Benchmark 最大的问题是什么？

### 口述主回答

最大问题是结果同时受模型采样、工具环境、网络、依赖版本、预算和评分器影响。一次失败可能是
Agent 判断错误，也可能是容器、服务或评分基础设施异常，如果不分类就无法公平比较。

我会固定模型快照、任务清单、Prompt、工具、预算、镜像和评分器版本，并让不同方案在同一批实例
上做配对运行。基础设施失败单独记录，不能悄悄从分母中删除。

### 追问问题与回答

**追问：模型采样、工具环境、网络、依赖和评分器带来的随机性如何控制？**

固定模型与推理配置、镜像和依赖，使用相同网络策略和评分器，并保存每次运行的配置、结果和 Trace；
仍无法消除的模型或服务波动通过重复实验衡量。

**追问：如何保证不同 Agent 方案使用相同模型、任务和预算进行公平比较？**

使用同一实例清单做配对运行，只改变目标实验变量，并固定模型、Prompt、工具、回合、Token、超时、
并发、重试和评分路径。

### 技术追问补充

- `RunSpec` 固定 suite、container module、instance ID、LaunchSpec、max_turns、Provider/API 和墙钟
  时间；Agent 运行 Trace 记录实际模型和过程。
- `run_dataset()` 对同一 instances 列表并发调用单实例 Runner，最终结果重新按输入实例顺序排列。
- `max_attempts` 只重试抛出的基础设施异常；已经完成但 status code 非零或评分未通过的运行不会被
  当成异常自动重跑。
- 框架不会自动比较两个 Profile 是否只差一个变量，也不会消除服务端模型漂移，公平性仍需实验
  manifest 和人工审查保证。

## 78. 同一个任务应该运行几次？

### 口述主回答

没有固定次数，要看模型随机性、任务成本和方案差距。探索阶段可以先做少量重复；当两个方案分数
接近时，应增加配对重复，直到置信区间足以支持结论。

只运行一次得到 63.20% 可以作为一次已发布结果，但不能证明长期期望值就是 63.20%。当前仓库没有
这项结果的重复运行方差，所以我不会声称它已经具有统计稳定性。

### 追问问题与回答

**追问：只运行一次得到 63.20% 时，这个结果是否可靠？**

它能说明那一次已发布运行的结果，但不能证明长期均值、方差或统计稳定性。

**追问：应该如何报告重复实验、方差、置信区间、失败样本和成本？**

报告每次总分和逐任务结果，再给出均值、方差或置信区间，并同时公开基础设施失败、失败样本、
Token、成本和墙钟时间。

### 技术追问补充

- 当前 `run_dataset()` 运行一组 instance 一次，没有内建“每实例重复 N 次”和统计置信区间接口。
- 重复实验需要调用者使用不同 run ID 多次运行相同 instance manifest，并保存每次 result 和
  trajectory。
- `DatasetReport` 只汇总 total/ok/failed 基础运行状态，不等于 Benchmark 分数统计报告。
- 当前发布材料没有 63.20% 对应的重复运行矩阵、方差或置信区间。

## 79. SWE-bench Pro 的 63.20% 是如何计算出来的？

### 口述主回答

63.20% 的指标语义是官方 Harness 判定解决的实例数除以纳入评分的实例总数。当前 README 能确认
发布分数是 63.20%，模型是 GPT-5.4 xHigh，平均每任务成本是 7.7823 美元。

但当前仓库没有提交对应运行的预测文件、逐任务评分结果和官方汇总输出，所以我不能确认具体通过
数、实际重复次数，也不能只根据百分比反推分子。仓库虽有 731 实例的 Pro 全量清单，但现有证据
不足以证明发布分数使用的就是这份完整产物链。

### 追问问题与回答

**追问：总共有多少 Task，Pass 了多少？**

当前仓库能看到 731 实例的 Pro 全量清单，但没有发布结果对应的逐任务评分文件，因此不能确认实际
分母和通过数。

**追问：使用 Full Set 还是 Subset，运行了几次，使用什么模型和配置？**

README 能确认模型是 GPT-5.4 xHigh；当前材料不能确认发布 Run 使用 Full Set 还是 Subset，也不能
确认重复次数和完整配置。

**追问：缺少逐任务原始产物时，哪些数字可以确认，哪些不能现场推断？**

可以确认 README 中的 63.20%、59.10% Baseline、相对提升和平均成本；不能推断通过数、任务清单、
重复次数和统计显著性。

### 技术追问补充

- README 发布 SWE-bench Pro resolved rate 63.20%、Baseline 59.10%、相对提升 6.94% 和
  $7.7823/Task，并标注 GPT-5.4 xHigh。
- `evals/swebench/data/` 的 vendored manifest 说明标准 Pro split 有 731 个实例，并用于当前链式
 运行入口；它不是发布成绩的官方结果文件。
- 当前 `evals/out/` 只有目录说明，没有该发布 Run 的 predictions、逐实例 result、官方 Harness
  report 或 Run manifest。
- 官方复现路径要求先收集 predictions，再运行 Pro Harness；没有这些产物时不能用
  `63.20% × 731` 反推出已证实的通过数。

## 80. Baseline 是什么？

### 口述主回答

Baseline 应该是除实验变量外尽量一致的对照方案，例如使用相同模型、任务、预算和评分器，但关闭
被研究的 Agent 机制。这样分数差才有机会归因到该机制。

README 当前列出的 SWE-bench Pro Baseline 是 59.10%，并声明使用相同模型和任务预算。但仓库没有
同时提交完整 Prompt、工具、Token、超时和逐任务轨迹，所以它是公开对比口径，不足以证明所有
控制变量完全一致。

### 追问问题与回答

**追问：Baseline 与当前实验的 Model、Prompt、Tool、Token Budget 和 Timeout 是否完全一致？**

当前发布口径只确认相同模型和任务预算；仓库证据不足以确认 Prompt、工具、实际 Token 和 Timeout
逐项完全一致。

**追问：如果条件不一致，结果还能否直接归因于 Agent Runtime？**

不能。条件不一致时只能比较整体方案，无法把分数差单独归因于 Runtime。

### 技术追问补充

- README 将 SWE-bench Pro Baseline 标为 59.10%，并链接外部公开榜单；当前仓库没有该 Baseline
  的本地 predictions、trajectory 或 Run manifest。
- README 文字声明 Delta 使用相同模型和任务预算，但未发布 Prompt hash、工具集合、超时、并发和
  重试的逐字段对照。
- 当前 Run Profile 可以保存公开 env 和 CLI 参数，但不会自动证明两个实验只差一个变量。
- 严格 Baseline 需要和实验组使用同一 instance manifest、评分器版本和失败处理规则，并保留逐任务
  结果。

## 81. 4.10 个百分点的提升是否具有统计意义？

### 口述主回答

4.10 个百分点只是 63.20% 减去 59.10% 的绝对分数差，不自动代表统计显著。README 的 6.94%
则是相对提升，两种口径不能混用。

要判断显著性，需要同一批任务上的配对结果，最好还有重复运行，再计算置信区间或配对检验。当前
仓库没有这些统计产物，所以我只能报告分数差，不能声称已经证明显著。

### 追问问题与回答

**追问：如何报告重复运行、方差、置信区间和配对任务差异？**

对相同任务做配对重复，报告每次分数、均值、方差或置信区间，并列出实验组成功而 Baseline 失败
以及反向变化的任务。

### 技术追问补充

- 4.10 个百分点来自 `63.20 - 59.10`；README 展示的 6.94% 是相对提升，两者口径不同。
- 当前仓库没有发布该结果的逐任务配对矩阵、重复运行记录、方差或置信区间。
- 若有单次同任务二元结果，可使用配对 bootstrap 或 McNemar 检验；多次运行还需统计任务内波动。
- 精确到两位小数只描述已发布分数格式，不代表统计误差也精确到两位。

## 82. Benchmark 提升主要来自哪个模块？

### 口述主回答

当前证据不能回答。公开结果是模型、Prompt、工具、Context、Workflow 和预算共同作用的端到端
结果，没有覆盖这些模块的逐项 Ablation。

单元测试只能证明机制按设计工作，不能证明它提高 Benchmark 成功率。因此我不会把提升归因到
Compact、并行工具、Reflection、Recall 或 Goal Loop 中的任何一个。

### 追问问题与回答

**追问：是否做过 Baseline、Context Management、Parallel Tool、Reflection、Recall 和 Goal Loop 的逐项 Ablation？**

当前没有公开覆盖这些组件的完整 Benchmark Ablation 表。

**追问：如果没有 Ablation，为什么不能把端到端提升归因到某一个模块？**

因为模型、Prompt、工具、预算和多个 Runtime 机制同时变化，任何一个都可能影响结果，无法分离
单个模块的边际贡献。

### 技术追问补充

- 仓库有 Context effectiveness 单元测试，验证上下文有界、压缩确实减小输入和 Recall 可恢复原文；
  这些测试不运行 Benchmark scorer。
- Agent Builder 支持关闭默认压缩、替换 ContextPolicy 和选择不同 Workflow flavor，为消融提供
  配置入口。
- 当前 `evals/out/` 没有逐项 remove-one 运行产物或汇总表。
- 组件归因需要固定其他配置后重复运行同一任务清单，并同时比较分数、Token、成本和延迟。

## 83. 去掉 Compact 后下降多少，Recall 对哪些任务提升最大？

### 口述主回答

当前没有对应实验数据，所以不能给出下降幅度或受益最大的任务类型。机制测试证明压缩能控制上下文、
Recall 能恢复旧消息，但这不等于 Benchmark 成功率提升。

我会设计 remove-one 实验：完整配置与去掉 Compact、去掉 Recall 的配置在同一实例、模型和预算下
配对运行，再按任务轨迹长度、工具输出规模和是否发生压缩分组分析。

### 追问问题与回答

**追问：如何设计 remove-one 和按任务类型分组的实验回答这两个问题？**

固定模型、Prompt、任务和预算，分别运行完整配置、无 Compact 和无 Recall；再按轨迹长度、工具
输出规模、压缩是否触发和任务类型做配对分组。

### 技术追问补充

- 无压缩配置可以使用空 `ContextPolicy` 或关闭默认 compression；Recall 可通过不向 Agent 绑定
  `make_recall_tool()` 构造 remove-one 版本。
- `ContextCompressionEvent` 和 compression metrics 可记录压缩次数、前后 Token、峰值和策略归因。
- Recall Tool Result details 记录实际返回索引，可识别哪些任务真正使用了 Recall。
- 当前仓库只有机制效果测试，没有这些配置在公开 Benchmark 上的 remove-one 结果。

## 84. 哪些任务因为当前 Runtime 反而下降？

### 口述主回答

当前公开结果没有逐任务对照和人工失败分类，所以我不能列出“最大的三个下降类别”或 SWE-bench
Pro 最常见失败原因。

真正分析时，我会先区分基础设施问题、定位错误、修改错误、验证不足、上下文丢失和预算耗尽，再
对比 Baseline 与实验组的逐任务状态，找出只在当前 Runtime 下由成功变失败的案例。

### 追问问题与回答

**追问：失败 Case 最大的三个类别是什么？**

当前没有发布人工失败分类和占比，因此不能确认最大的三个类别。

**追问：SWE-bench Pro 中最常见的失败原因是什么？**

当前没有对应逐任务分析，不能声称某一种原因最常见。

### 技术追问补充

- Runtime Trace 可识别 max_turns、abort、tool error、压缩和模型行为；`result.json` 保存任务产物，
  官方 Harness 提供 resolved/unresolved 评分。
- 这些结构能支持后续人工标注，但当前没有把失败自动分类为定位、修改、验证或上下文问题的逻辑。
- 下降分析必须比较同一任务在 Baseline 与实验组中的状态，单看当前方案失败样本无法判断是否是
  Runtime 导致下降。
- 当前本地没有发布 Run 的逐任务 Trace、Patch 和官方报告，因此不能统计前三类别。

## 85. 三类 Benchmark 使用的 Agent 策略有什么区别？

### 口述主回答

SWE-bench Pro 面向代码仓库修改，README 明确使用类似 ChainSWE 的链式流程并加入 `task` 工具；
Terminal-Bench 由 Harbor 管理终端任务环境和 Verifier，Agent 在环境内使用 Bash 等工具执行；
PostTrainBench 是后训练效果评测，README 只公开了组合指标，仓库没有同名 Suite 可核对完整策略。

因此三项提升幅度不能直接横向解释。它们使用的模型、任务、Baseline 和评分语义都不同，12.83 和
1.91 是绝对百分点差，不代表某个 Runtime 模块在前者更有效。

### 追问问题与回答

**追问：为什么 Terminal-Bench 提升 12.83 个百分点，而 PostTrainBench 只提升 1.91 个百分点？**

两项使用不同模型、任务、Baseline 和指标，不能从分数差直接推断 Runtime 在 Terminal-Bench 上
贡献更大；需要各自受控实验和消融。

### 技术追问补充

- SWE-bench Pro 通过 Suite/Container Half 运行代码修改，提取 git patch，再由官方 Harness 在
  评分环境中判定 resolved；README 还说明使用链式流程和 task 工具。
- Terminal-Bench 通过 Harbor 管理任务环境和 verifier；仓库启动脚本提供 bash 与 bash_task
  方案，并配置 xHigh reasoning 和较长回合预算。
- PostTrainBench 的 README 结果是 AIME 2025、BFCL、GSM8K 和 HumanEval 的归一化加权结果；
  当前仓库没有同名 Eval Suite 和逐任务运行产物。
- 三项结果的模型、成本和评分语义都不同，绝对百分点差不能作为跨 Benchmark 的机制归因。

## 86. 如何证明提升来自 Runtime，而不是 Prompt 或配置变化？

### 口述主回答

需要做控制变量实验：固定模型快照、Prompt、工具、任务、预算、超时和评分器，只改变一个 Runtime
机制。最好在同一批任务上做配对重复，观察成功率和资源成本。

当前公开材料没有完整逐项 Ablation，所以只能说完整 Agent 配置取得了更高结果，不能证明提升由
Runtime、Prompt 或某个单独组件造成。

### 追问问题与回答

**追问：是否做过逐项 Ablation、remove-one 实验或相同 Prompt 的对照实验？**

当前没有公开足以支持这项因果结论的完整实验产物。

**追问：没有 Ablation 时，哪些结论不能做因果归因？**

不能声称提升来自 Runtime 整体，也不能归因到 Compact、Recall、并行工具、Task Tool、Reflection
或 Goal Loop 中的任何一个。

### 技术追问补充

- Run Profile、Provider 环境、Agent flavor、max_turns、超时和 ContextPolicy 都是可记录或配置的
  变量，但当前框架不会自动生成两个实验的配置 diff。
- 相同 Prompt 的 basic loop、add-one 和 remove-one 可通过不同 Agent 配置复用同一 Suite、
  Backend、Store 和 instance manifest。
- 公平对照还需要固定模型实际版本、镜像、并发、重试和 scorer；只固定 Prompt 仍不足够。
- 当前公开证据只支持“完整组合配置取得该端到端结果”，不支持 Runtime 或组件级因果结论。

## 87. 如何处理模型版本更新和 Benchmark Contamination？

### 口述主回答

每次实验应记录请求模型、供应商实际返回的模型标识、运行日期、代码提交、镜像摘要、任务清单和
评分器版本。使用可变模型别名时，即使名称相同，服务端行为也可能变化。

新旧模型结果不能直接当作同一实验比较。污染风险也很难完全证明不存在，只能通过使用更新或私有
任务、检查异常记忆化行为，并公开模型和数据时间边界来降低风险。

### 追问问题与回答

**追问：如何固定模型快照、记录运行日期和检测训练数据污染风险？**

优先使用不可变模型版本，并记录实际服务模型、运行日期、代码提交、镜像摘要和任务版本；污染风险
通过更新或私有任务、时间切分和异常记忆化分析降低，但无法仅凭结果完全排除。

**追问：新旧模型结果能否直接比较？**

不能作为同一受控实验直接比较。模型变化后应重新运行 Baseline，并把模型版本作为实验变量。

### 技术追问补充

- Adapter 从响应中提取实际服务模型写入 `LLMResponse.model`，随后进入 AssistantMessage 和
  `ModelResponseEvent`；若供应商不返回，才回退到请求模型名。
- RunTrace Header 支持调用者传入任意 `meta`，但当前默认协议不会自动写入 Git commit、运行日期、
  镜像 digest 或数据集版本。
- Run Profile 保存环境和 CLI 默认值，显式命令行仍可覆盖；它不是锁定后的不可变实验 manifest。
- 当前仓库没有 Benchmark Contamination 检测器或污染审计产物，高分不能证明训练数据未覆盖任务。

## 88. 如何保证不同 Agent Config 的实验公平？

### 口述主回答

先明确唯一实验变量，其余配置全部固定。模型、Prompt、工具、任务清单、Token 和回合预算、超时、
并发、重试、网络、镜像和评分器都应该是控制变量。

项目的 Run Profile、每实例目录、Trace 和 result 可以保存大部分运行证据，但公平性仍需要实验
设计者检查配置差异，框架不会自动证明两次实验完全等价。

### 追问问题与回答

**追问：Model、Prompt、Tool、Token Budget、Timeout、并发度和重试策略是否一致？**

公平对照中这些都应一致，除非其中某一项就是明确的实验变量；任何差异都要写入配置和结果说明。

**追问：哪些变量是控制变量，哪些是实验变量？**

实验变量是本次要验证的唯一改变；其余模型、任务、环境、工具、预算、执行和评分条件都是控制变量。

### 技术追问补充

- Run Profile 的 `env` 与 `run` 两部分可保存模型相关环境和命令参数；已存在环境变量和显式 CLI
  参数会覆盖 Profile，因此实际运行配置仍需另存 manifest。
- `RunSpec` 保存 max_turns、wall time、Provider/API 和 LaunchSpec，但 Prompt、工具集合、并发和
  重试等信息分布在 Agent 配置、Dataset Driver 和运行入口中。
- 每实例目录、Trace 和 result 能保留运行证据；当前没有自动生成完整配置指纹或两个实验的差异报告。
- `run_dataset()` 会恢复输入实例顺序，并只重试抛出的基础设施异常；比较双方必须使用相同缺失任务
  和失败计数规则。

## 89. 使用两倍 Token 但成功率更高，应该如何比较？

### 口述主回答

不能只看成功率，要同时看成本和约束。至少报告成功率、平均 Token、美元成本、墙钟时间，并比较
固定预算下谁解决更多任务。

更合理的是画成功率与成本的 Pareto 曲线，或者计算每成功任务成本。两倍 Token 如果带来很小提升，
可能不值得；如果在高价值任务上显著减少失败，则可能合理。

### 追问问题与回答

**追问：如何同时报告成功率、成本、延迟和预算约束下的效率？**

同时报告成功率、每任务 Token 和美元成本、墙钟时间及其分布，并比较固定 Token、固定美元或固定
时间预算下的成功率，以及每成功任务成本。

### 技术追问补充

- 每个 Run 的 ModelResponseEvent 和 RunCost 可提供模型 Token 与美元成本；Event elapsed 和 Span
  可用于计算模型、工具和整次运行耗时。
- Eval 的 `result.json` 和官方评分产物提供任务结果；当前框架没有内建 Pareto 曲线或
  cost-per-success 报告。
- 汇总时应报告中位数、分位数和长尾，而不只报告平均值；当前 DatasetReport 只提供运行完成状态
  的基础计数。
- 外部工具账单目前不进入 RunCost，因此涉及付费 Tool 时，总成本需要调用者额外合并。

## 90. Pass@1 和 Pass@k 有什么区别？

### 口述主回答

Pass@1 表示一次尝试能否成功，更接近真实单次部署体验。Pass@k 表示同一任务做 k 次独立尝试时，
至少有一次成功的概率，体现额外采样预算带来的上限。

如果运行多次后挑最好结果，还必须说明选择器是否使用了隐藏答案。Pass@k 不能和单次 Agent 成绩
直接比较，因为它消耗了 k 倍左右的执行和模型预算。

### 追问问题与回答

**追问：Agent Benchmark 中多次采样、选择最佳结果和单次真实运行分别代表什么？**

单次运行对应 Pass@1；多次独立采样衡量额外预算下的成功机会；选择最佳结果还包含选择器能力，
如果使用隐藏答案或官方评分器选择，就不是可部署的在线策略。

### 技术追问补充

- 当前 `run_dataset()` 默认每个实例执行一次，没有 Pass@k 计算器、每实例 k 次采样参数或无偏
  Pass@k 估计实现。
- 多次采样需要为每个 instance/attempt 使用独立 Run ID 或目录，保存各自 result、trajectory 和
  成本，不能只保留 winner。
- `run_parallel()` 可以产生多个候选并由 Aggregator 综合，但这属于一个多 Agent Workflow，不等于
  官方统计意义上的独立 Pass@k。
- 使用官方 scorer 事后挑出成功样本是 oracle selection；模型或独立 selector 选择则是另一种可评测
  系统，必须把选择器成本和错误计入。

## 91. Agent Eval 为什么比普通 LLM Eval 更难？

### 口述主回答

普通 LLM Eval 主要比较输入和输出，Agent Eval 还包含多轮决策、工具副作用、环境变化、网络和
外部评分。前面一步的随机差异会改变后续状态，所以误差会沿整条轨迹放大。

要复现结果，不仅要固定模型和 Prompt，还要固定环境镜像、依赖、工具权限、预算、任务顺序和评分器，
并保存完整轨迹和产物。即使这样，外部服务和模型采样仍可能带来波动。

### 追问问题与回答

**追问：模型随机性、环境状态、工具副作用、长时间执行和外部评分如何影响可复现性？**

模型随机性会改变行动路径，环境和工具副作用会改变后续状态，长任务增加超时和依赖漂移概率，
评分器版本或 Judge 波动又会改变最终标签，因此必须同时固定并记录整条运行链路。

### 技术追问补充

- Suite 的 LaunchSpec 固定镜像和工作目录，SWE Container Half 从 baseline commit 提取 Patch；
  但使用可变 image tag 时仍不能保证镜像内容永远一致。
- Docker Backend 为实例提供独立环境；LocalProcess 使用固定 workspace 并发时会共享状态，必须
  串行或提供 workspace factory。
- Dataset Driver 区分抛出异常的基础设施失败和已完成但未通过的任务结果；评分失败还需要由
  result/官方 scorer 证据单独识别。
- Trace、result 和评分产物能解释一次运行，但当前没有自动环境哈希、确定性重放或跨重复实验的
  统计聚合系统。

## 核对依据

- [`README.md`](../../README.md)
- [`15-resume-project-description.md`](../design/15-resume-project-description.md)
- [`evals/README.md`](../../evals/README.md)
- [`evals/swebench/README.md`](../../evals/swebench/README.md)
- [`evals/harbor/README.md`](../../evals/harbor/README.md)
- [`evals/swebench/data/README.md`](../../evals/swebench/data/README.md)
- [`src/simple_long_horizon_agent/evals/protocols.py`](../../src/simple_long_horizon_agent/evals/protocols.py)
- [`src/simple_long_horizon_agent/evals/runner.py`](../../src/simple_long_horizon_agent/evals/runner.py)
- [`src/simple_long_horizon_agent/evals/dataset.py`](../../src/simple_long_horizon_agent/evals/dataset.py)
- [`10-evaluation-and-operations.md`](../design/10-evaluation-and-operations.md)
- [`test_evals_framework.py`](../../tests/unit/test_evals_framework.py)
- [`test_swebench_evaluate_predictions.py`](../../tests/unit/test_swebench_evaluate_predictions.py)
