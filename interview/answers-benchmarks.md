# 面试专题回答：Benchmark 与结果可信度

本文覆盖 [`question-checklist.md`](question-checklist.md) 中第 9 个专题。公开数字以当前
[`README.md`](../README.md) 为准。仓库没有提交三项完整逐任务产物、重复运行统计或组件级
Ablation，因此回答会明确区分已发布结果、框架能力与尚不能证明的因果结论。

## 1. 三个分数各自如何计算？

SWE-bench Pro 的 63.20% 是 resolved rate，也就是官方 harness 判定解决的实例占比。
Terminal-Bench 2.1 的 77.53% 是其官方 score，由 Harbor 负责环境、verifier 和 job 结果。
PostTrainBench 的 45.88% 是 weighted average；README 脚注说明底层评测 Qwen3-4B-Base 在
AIME 2025、BFCL、GSM8K 和 HumanEval 上的 post-training 结果，并按 PostTrainBench Lite
方法归一化 reward。

当前仓库没有足够原始产物让我从头复算这三个发布值，所以面试时我会引用各自官方评分语义，
不会自行猜测任务数、权重或 case 数。

## 2. Baseline 的具体定义是什么？

README 当前列出的 Baseline 分别是 59.10%、64.70% 和 43.97%，并声明对比使用相同模型和
任务预算。前两项链接到公开榜单，PostTrainBench 链接到其公开页面。

但仓库没有同时提供三套 Baseline 的逐任务 run manifest、prompt 和完整轨迹，因此我只能把它们
称为“当前公开对比口径”，不能进一步声称全是我在完全相同基础设施里亲自复跑的受控实验。

## 3. Baseline 与实验组是否使用同一模型、Prompt、Token 和成本预算？

从 README 能确认的主张是同模型、同任务预算；不能从现有材料确认 prompt 完全相同，也不能
确认 Token、实际美元成本和所有重试条件逐项相同。当前表格只公开了实验组前两项 cost/task，
PostTrainBench 成本未发布，Baseline 成本也没有列出。

所以严格回答是：模型和任务预算按发布口径对齐，但更细的 prompt、Token 和成本公平性需要原始
manifest 支撑；现有仓库证据不足时我不会把它说成完全控制变量实验。

## 4. 为什么用“百分点”而不是相对提升比例？

两种都可以，但必须说清单位。原始分数差最直观：SWE-bench Pro 是 63.20−59.10=4.10 个百分点；
Terminal-Bench 是 12.83 个百分点；PostTrainBench 是 1.91 个百分点。

README 当前展示的是相对提升比例，即分数差除以 Baseline：6.94%、19.83% 和 4.34%。因此不能
说 Terminal-Bench “提升 19.83 个百分点”；准确说法是“绝对提升 12.83 个百分点，相对提升
19.83%”。简历文档使用百分点，README 使用相对比例，数学上并不冲突。

## 5. 提升分别来自哪些机制？

现有结果只能支持整套配置的端到端效果，不能把提升拆给某个组件。README 对 SWE-bench Pro 只
明确说明使用类似 ChainSWE 的 chained workflow，并加入 `task` 工具；Terminal-Bench 和
PostTrainBench 也分别包含模型、prompt、工具、环境和评分流程的共同作用。

没有控制变量实验，我不会说 4.10 个百分点来自 Context Compact，或者 12.83 个百分点来自
并行工具。合理结论是组合方案取得了这些结果，组件贡献仍需 Ablation。

## 6. 做过哪些消融实验？

当前仓库没有公开覆盖三项结果的组件级 Ablation 表。已有单元测试和 Context effectiveness
测试验证局部机制行为，但它们不是 Benchmark 成功率消融，不能替代“去掉某组件重新跑整套任务”。

如果补实验，我会固定模型快照、任务、镜像、prompt、工具和总预算，至少比较 basic agent、
去掉 Task Tool、去掉 chain/context 机制，以及完整配置；同时报告分数、Token、成本、时延和
失败分布。

## 7. 每项 Benchmark 跑了多少任务、几次重复？

从当前 README、已提交文档和公开结果摘要里不能可靠确认三项各自的任务数与重复次数，我不会
根据百分比反推分母。仓库也没有随结果提交重复运行清单。

面试现场如果没有额外实验记录，我会直接说明这个证据边界。正式发布可复现报告时，应给出
dataset/split、精确 instance ID manifest、attempt 数、缺失任务处理和每次运行 ID。

## 8. 是否存在随机误差或置信区间？

一定存在模型非确定性、服务端变化和环境噪声的可能，但当前材料没有给出重复运行方差或置信区间，
所以无法量化 63.20% 等数字的统计不确定性。精确到两位小数不代表误差也精确到两位。

更可靠的做法是对同一任务做配对重复，报告 bootstrap 置信区间或配对显著性，并单独标记基础设施
失败。没有这组数据时，这些分数应理解为已发布运行结果，不应扩展成稳定期望值。

## 9. 失败任务主要分为哪些类型？

框架能够区分 Agent/任务未通过、评分失败和基础设施异常，也能从 Trace 看到 max turns、abort、
tool error 等停止路径。但当前公开结果没有附带三项 Benchmark 的人工 failure taxonomy 和各类
占比。

因此我不能声称主要失败是定位错误、上下文遗失或工具超时。真正分析时我会按环境/依赖失败、
理解与定位、修改错误、验证不足、预算耗尽和评分/基础设施问题标注，并保留原始 evidence。

## 10. 成功率提高是否伴随成本或延迟上升？

README 报告实验组 SWE-bench Pro 平均每任务 $7.7823，Terminal-Bench 为 $0.5667，
PostTrainBench 未发布成本；没有列 Baseline 成本和三项 wall-clock 延迟。因此目前不能算出提升的
增量成本，也不能判断是否处于更优 Pareto 前沿。

Chain、Task Tool 和 Reflection 理论上都可能增加调用数。面试中我会把分数和成本同时报出，但
不会在没有 Baseline 资源数据时声称“同成本更高分”或“没有延迟代价”。

## 11. 是否排除模型升级、Prompt 修改和基础设施变化的影响？

当前 README 每项结果标了模型和 reasoning effort，Eval 框架也支持保存 Profile、轨迹和运行
产物；但公开摘要没有展示完整实验时间线和逐字段 diff，所以不能证明所有混杂变量都已排除。

严格受控比较需要锁定模型 snapshot、API、prompt hash、Agent commit、镜像 digest、任务 manifest、
budget、并发和 scorer 版本。缺少其中任何一项，结果更适合叫公开参考对比，而不是强因果实验。

## 12. 结果能否由其他人使用固定配置复现？

仓库已经提供 SWE-bench、Terminal-Bench/Harbor 和 ProgramBench 等运行与评分入口，通用 Eval
框架也保存 per-instance result 和 trajectory。但三个 README 分数的完整固定配置、全部实例
manifest、原始结果和外部凭证并未一起发布。

所以目前是“基础设施提供复现路径”，不是“仅克隆仓库即可逐位复现全部数字”。要达到后者还需
发布 immutable profile、commit、镜像 digest、任务列表、每任务结果、官方 scorer 输出和失败记录。

## 13. 你个人对这些结果具体贡献了什么？

仅凭仓库和简历描述无法确认个人分工，我不会把团队实现自动算成我的个人贡献。能够从项目材料
确认的是工作范围包括 Runtime、Context、Tool/MCP、Workflow、Trace 和 Eval，但“我具体写了
哪些模块、运行了哪些实验、负责哪次提交”必须由我的真实经历和 commit/review 记录回答。

正式面试前我会把这一题补成可核验的三部分：我主导的设计决策、我直接实现并测试的文件或模块，
以及我亲自执行和分析的 Benchmark run。没有参与的官方 harness 或外部 Baseline 只说“接入”或
“采用”，不说“由我设计”。

## 14. ProgramBench 和 PostTrainBench 为什么一个在接入列表、一个在成绩列表？

它们不是同一个 Benchmark。ProgramBench 是仓库里已有 host/container adapter 和官方 evaluator
入口的逆向工程任务套件；当前 README 没有发布它的成绩。PostTrainBench 是 README 成绩表里的
模型后训练评测，45.88% 按 AIME 2025、BFCL、GSM8K 和 HumanEval 的归一化 reward 加权得到；
当前仓库没有一个同名 Eval Suite。

所以“接入列表”描述项目代码已经支持运行哪些评测，“成绩列表”描述当前选择公开哪些结果，两者
不必完全相同。不过简历把它们并排写容易引起误解，面试时我会主动说明：ProgramBench 是已接入
能力，PostTrainBench 是已发布结果，不会暗示 45.88% 由 ProgramBench adapter 产生。

## 核对依据

- [`README.md`](../README.md)
- [`15-resume-project-description.md`](../docs/design/15-resume-project-description.md)
- [`evals/swebench/README.md`](../evals/swebench/README.md)
- [`evals/harbor/README.md`](../evals/harbor/README.md)
- [`evals/programbench/README.md`](../evals/programbench/README.md)
- [`10-evaluation-and-operations.md`](../docs/design/10-evaluation-and-operations.md)
