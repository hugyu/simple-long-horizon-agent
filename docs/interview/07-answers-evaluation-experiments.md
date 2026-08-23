# 07. Eval 与实验设计

本文对应 [`question-checklist.md`](question-checklist.md) 中第七部分的四个问题。回答以当前代码、
测试、项目文档和 README 已发布结果为事实边界。

## 75. 介绍一下 Terminal-Bench 2.1 和 SWE-bench Pro 这两个任务。

### 口述主回答

这两个 Benchmark 分别覆盖终端操作和真实软件工程。

Terminal-Bench 2.1 给 Agent 一个隔离的终端环境和任务说明，要求它通过命令行完成系统配置、
文件处理、编译调试等真实操作。项目通过 Harbor 接入这个任务，Harbor 负责数据集、容器环境和
Verifier，Simple Long Horizon Agent 负责在容器里运行 Agent Loop 和 Bash、Read、Task 等工具。

SWE-bench Pro 给 Agent 一个真实仓库和 Issue，要求它定位问题、修改代码并留下 Patch。项目会从
Agent 修改后的工作区提取 `model_patch`，再交给官方 Pro Harness 在基准提交上应用 Patch 并执行
测试。

### 技术追问补充

- Terminal-Bench 通过 `runs/harbor/run_terminal_bench_2_1_*.sh` 启动；Harbor 集成边界记录在
  `evals/harbor/README.md`，Agent Adapter 位于
  `simple_long_horizon_agent.evals.harbor.agent`。
- SWE-bench Pro 的 Host Half 位于 `evals/swebench/`，Container Half 位于
  `simple_long_horizon_agent.evals.suites.swebench`；Agent 产物是工作区 Patch，不是模型口述的
  完成声明。
- README 发布的端到端分数分别是 SWE-bench Pro 63.20% 和 Terminal-Bench 2.1 77.53%。

## 76. 这两个任务分别怎么判断成功？

### 口述主回答

我不会用“Agent 说自己完成了”作为成功标准，而是使用每个 Benchmark 的外部验证信号。

Terminal-Bench 由任务自带的 Verifier 检查环境最终状态并给出 Reward，Harbor 汇总这些 Reward
形成官方分数。SWE-bench Pro 的单任务成功标准是官方 Harness 判定 `resolved`：Patch 必须能够
在基准版本上正确应用，并通过该实例要求的测试；只生成 Patch、命令退出码为零或者 Agent 自己
运行过测试都不等于最终通过。

### 技术追问补充

- Harbor 的任务环境、Verifier 和规范化 `result.json` 都由 Harbor 管理；SAL 生成的
  `sal-summary.json` 和 `sal-trajectory.jsonl` 只用于调试，不是最终分数来源。
- SWE-bench 适配器把官方结果映射为项目自己的 `EvalResult`：`resolved=True` 对应
  `passed=True`、`score=1.0`，否则为 `0.0`。
- SWE-bench Pro 适配器额外记录 Patch 的 reset、checkout、`git apply --check` 和 apply 状态；
  任一步失败都强制记为 unresolved。

## 77. 这两个任务的结果是怎么检验的？

### 口述主回答

Terminal-Bench 是直接交给 Harbor 做端到端检验。Harbor 启动任务环境、运行 Agent、执行任务
Verifier、下载日志和产物，最终写出规范化的 Job `result.json`。项目读取这个文件中的试验数量、
错误数和 Reward 汇总，不用自己的 Trace 替代官方结果。

SWE-bench Pro 分成生成和评分两步。生成阶段在实例容器里运行 Agent，并从工作区提取 Patch；
评分阶段把每个实例的 `result.json` 收集成官方 Predictions，再由 Pro Harness 在干净环境中应用
Patch、运行测试并输出 resolved 或 unresolved。收集时会固定预期实例清单，失败任务用空 Patch
进入分母，重复或意外实例直接报错，避免只统计成功产物。

### 技术追问补充

- Harbor 的 `result.json` 是 Terminal-Bench 分数的 Source of Truth；
  `simple_long_horizon_agent.evals.harbor.results` 只抽取稳定汇总字段。
- SWE-bench Pro 通过 `predictions_from_run_dirs()` 收集 Patch；预期但缺失的实例会生成空 Patch，
  因此不会从分母中消失。
- `evals/swebench/evaluate_predictions.py` 可以调用官方 Pro Harness，也可以把已有官方结果
  规范化为项目的 `eval_result` JSONL。
- `tests/unit/test_harbor_*` 和 `tests/unit/test_swebench_*` 验证命令构造、结果解析、Patch 提取和
  评分映射；它们不是 77.53% 或 63.20% 的成绩证明。
- 当前 `evals/out/` 没有两项发布结果对应的完整 Run manifest、逐任务结果和原始评分产物，因此
  这些公开分数不能只靠当前 Checkout 独立复算。

## 78. Baseline 是什么？

### 口述主回答

Baseline 是实验组的对照方案。严格来说，两组应该使用同一模型、同一任务清单、同一 Prompt、
工具、Token 和时间预算、重试规则以及同一评分器，只改变真正要研究的 Agent 方案。否则分数差
只能说明两个端到端配置不同，不能单独归因于 Runtime。

README 当前列出的 Baseline 分别是 SWE-bench Pro 59.10% 和 Terminal-Bench 64.70%；项目结果
分别是 63.20% 和 77.53%。对应的绝对提升是 4.10 和 12.83 个百分点；README 中的 6.94% 和
19.83% 是相对提升百分比，两种口径不能混用。

当前仓库没有同时提交两组 Baseline 的完整运行配置、逐任务结果和 Trace，所以我会把它们描述为
README 采用的公开对照结果，不会进一步声称已经通过本地材料证明所有控制变量完全一致，也不会
把全部提升归因到某一个 Runtime 模块。

### 技术追问补充

- README 对 Baseline 的文字定义是“相同模型和任务预算下的对照结果”，目的是尽量区分模型能力
  与 Agent 方案带来的差异。
- 严格可复现实验还需要固定实例 Manifest、模型快照、Reasoning 配置、Agent Flavor、Prompt、
  Tool 集合、最大 Turn、Timeout、并发、重试和评分器版本。
- README 表格中的 `Delta vs. Baseline` 使用相对提升：
  `(experiment - baseline) / baseline`；简历描述使用的是绝对百分点差。
- 代码中的 “pre-agent baseline commit” 只是 SWE-bench 提取工作区 Diff 的 Git 基线，不是用于
  比较 Agent 性能的实验 Baseline，二者不能混淆。
