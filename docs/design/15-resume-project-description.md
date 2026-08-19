# 15. 简历项目描述

本文将 Simple Long Horizon Agent 整理为适合秋招简历的项目经历。推荐定位为
**大模型 / Agent 应用算法工程师、AI Agent 开发工程师或 Agent 基础设施方向**，
突出 Agent Runtime、Context Engineering、Tool Use、多 Agent 编排和 Eval
工程能力，不把功能数量当作项目价值。

所有表述均以当前代码、设计文档和 README 已发布结果为依据。简历只写能够解释、
能够定位到实现、能够经受面试追问的内容。

## 1. 推荐版本

### Simple Long Horizon Agent｜项目作者 / 核心开发者

**技术栈：** Python、LLM Agent、OpenAI / Anthropic API、MCP、Docker、Linux、
JSONL、uv

**项目简介：** 主导设计并实现轻量级长周期 Agent Runtime，使大模型能够在有限
上下文和明确预算内持续调用工具、读取环境反馈、修正方案并接受外部结果验证；项目
面向 Agent 学习、架构实验及软件工程等真实多步骤任务。

**核心工作与成果：**

- 设计基于同步生成器的显式 Agent Loop，以项目自有的 `Message`、`Event`、
  `State` 统一模型请求、工具反馈、状态演进和停止原因；通过 Provider Adapter
  隔离协议差异，接入 OpenAI Chat / Responses、Anthropic Messages 及
  OpenAI-compatible 服务，使核心 Runtime 不依赖供应商原生对象。
- 针对长任务上下文持续膨胀问题，将追加式完整历史、活跃上下文和单轮模型输入分层，
  实现工具结果压缩、模型摘要、Agent 主动 Compact 与 Recall；压缩只更新模型可见
  投影，不删除原始消息，使关键信息可恢复，运行过程可回放、可审计。
- 构建 Bash、Read、Edit、Task 等工具与 MCP 接入，支持并行调用、超时、中止和
  Hook 拦截；基于同一 Runtime 实现子 Agent 委派、Planner-Executor、Reflection、
  Routing、Parallel、PDR 和 Goal Loop，并将“模型输出完成”与“外部检查通过”
  分离。
- 建设 Event → Trace → Eval 工程闭环：增量写入 JSONL 轨迹，派生 Model Turn、
  Span、Token / 成本统计和 Trace Viewer；以 `Suite`、`Backend`、
  `ArtifactStore` 解耦任务语义、执行环境和运行产物，支持本地进程、本地 Docker
  与远程 Docker 评测。
- 接入 SWE-bench Pro、Terminal-Bench、ProgramBench 等评测基础设施；README
  公布的 SWE-bench Pro、Terminal-Bench 2.1、PostTrainBench 成绩分别为
  **63.20% / 77.53% / 45.88%**，较所列 Baseline 分别提高
  **4.10 / 12.83 / 1.91 个百分点**。

## 2. 一页简历版本

一页中文简历优先使用下面四条。它保留岗位关键词、核心技术难点和量化结果，不堆叠
次要模块。

### Simple Long Horizon Agent｜项目作者 / 核心开发者

**技术栈：** Python、LLM Agent、OpenAI / Anthropic API、MCP、Docker、Linux

- 主导设计轻量级长周期 Agent Runtime，以显式 Agent Loop 和自有
  `Message` / `Event` / `State` 协议统一模型调用、工具反馈、状态演进与停止控制，
  支持 OpenAI、Anthropic 和 OpenAI-compatible 模型服务。
- 将完整历史、活跃上下文和单轮模型输入分层，实现工具结果压缩、模型摘要、主动
  Compact 与 Recall，在控制上下文规模的同时保留原始证据、信息恢复与轨迹审计能力。
- 实现并行 Tool Use、MCP、Hook、子 Agent 委派及 Planner-Executor、
  Reflection、Parallel、Goal Loop；通过外部检查区分“模型认为完成”和“任务实际
  完成”。
- 建设 JSONL Trace、Span / Token / 成本统计与容器化 Eval 基础设施；
  SWE-bench Pro、Terminal-Bench 2.1、PostTrainBench 分别达到 **63.20%、
  77.53%、45.88%**，较对应 Baseline 提高 **4.10、12.83、1.91 个百分点**。

## 3. 极简版本

项目经历只能保留三条时，使用下面版本：

### Simple Long Horizon Agent｜项目作者 / 核心开发者

- 主导设计并实现 Python 长周期 Agent Runtime，以显式事件循环和
  Provider-neutral 数据协议统一模型调用、工具反馈、状态管理与停止控制。
- 构建上下文压缩与 Recall、并行 Tool Use、MCP、子 Agent、Goal Loop 及
  Trace / Eval 闭环，支持模型在有限上下文中持续行动、恢复证据并接受外部验证。
- 在 SWE-bench Pro、Terminal-Bench 2.1、PostTrainBench 上分别达到
  **63.20%、77.53%、45.88%**，较对应 Baseline 提高
  **4.10、12.83、1.91 个百分点**。

## 4. 岗位定向

### 4.1 大模型 / Agent 应用算法工程师

使用“推荐版本”。关键词优先级为：

```text
Agent Runtime
Context Engineering
Tool Use / Function Calling
Multi-Agent
MCP
Evaluation
SWE-bench
Python
```

这个方向最匹配当前项目，因为代码直接覆盖 Agent 循环、模型协议适配、工具调用、
上下文管理、多 Agent 工作流、轨迹和 Benchmark。

### 4.2 AI Agent 开发 / 后端工程师

将第四条提前，强化工程系统设计：

- 设计追加式 Event Stream 和 JSONL 增量轨迹，支持运行回放、Span 构建、Token /
  成本统计及长任务实时观察。
- 以 `Suite`、`Backend`、`ArtifactStore` 三个协议解耦 Benchmark、Docker
  执行与产物传输，支持本地进程、本地 Docker 和远程 Docker。

### 4.3 Agent 评测 / 算法工程师

将 Benchmark 和可复现性作为主线：

- 构建容器化 Agent Eval 框架，分离模型可见任务与私有评分数据，统一保存配置、
  轨迹、结果和评分产物。
- 接入 SWE-bench Pro、Terminal-Bench、ProgramBench 等任务，并使用官方 Harness
  或 Verifier 判定结果，不以 Agent 最终文本代替客观成功条件。
- 公开成绩覆盖软件工程、终端操作和模型后训练三类长周期任务，并记录项目分数、
  Baseline、绝对百分点差和成本口径。

## 5. 技能栏建议

可从当前项目提炼以下技能，但应与其他经历共同控制篇幅：

```text
编程与工程：Python、Linux、Git、Docker、uv、unittest
大模型应用：LLM Agent、Prompt / Context Engineering、Tool Calling、MCP、
             Multi-Agent Workflow、Memory / Recall、Evaluation
模型接入：OpenAI Chat / Responses API、Anthropic Messages API、
          OpenAI-compatible API
系统能力：事件驱动状态管理、并发工具调度、超时 / 中止、JSONL Trace、
          容器化评测与可观测性
```

当前项目不能直接证明 PyTorch、Transformers、SFT、RLHF、分布式训练或模型部署
能力。若目标岗位强依赖这些要求，应由其他项目补充，不要把 PostTrainBench 成绩
改写成“实现了大模型训练框架”。

## 6. 60 秒口头介绍

> Simple Long Horizon Agent 是我主导设计和实现的一套轻量级长周期 Agent
> Runtime。它主要解决模型如何在有限上下文中持续调用工具、读取环境反馈、修正方案，
> 并通过外部信号判断任务是否真正完成。
>
> 我先设计了项目自有的 Message、Event 和 State 协议，用一条显式 Agent Loop
> 统一模型调用、工具执行、状态演进和停止控制，再在边界层适配 OpenAI 和 Anthropic。
> 针对长任务，我把完整历史和模型当前可见上下文分开，通过压缩和 Recall 控制 Token，
> 同时保留原始证据。其上又实现了 MCP、子 Agent、Goal Loop、Trace Viewer 和
> 容器化 Eval。
>
> 项目在 SWE-bench Pro、Terminal-Bench 2.1 和 PostTrainBench 上分别达到
> 63.20%、77.53% 和 45.88%。我更关注的是把 Agent 行为做成可解释、可验证和
> 可评测的工程系统，而不只是调用一次模型 API。

## 7. 数字与表述边界

三项成绩的简历口径如下：

| Benchmark | 项目结果 | Baseline | 绝对提升 | 相对提升 |
| --- | ---: | ---: | ---: | ---: |
| SWE-bench Pro | 63.20% | 59.10% | 4.10 个百分点 | 6.94% |
| Terminal-Bench 2.1 | 77.53% | 64.70% | 12.83 个百分点 | 19.83% |
| PostTrainBench | 45.88% | 43.97% | 1.91 个百分点 | 4.34% |

简历正文优先写绝对百分点差，面试时再补充相对提升。当前公开结果能够支持“端到端
Agent 配置优于所列 Baseline”，但没有完整公开的组件消融，因此不能把全部提升归因
于 Task Tool、Compression、Handoff 或某一个 Workflow。

`ProgramBench` 与 `PostTrainBench` 是两个不同 Benchmark：

- ProgramBench：当前仓库已实现 Suite 和评分接入，但 README 未公布最终成绩；
- PostTrainBench：README 已公布 45.88% 成绩，但当前仓库没有对应的完整本地 Suite
  和逐项实验产物。

简历必须把“已接入的评测”和“已公布成绩的评测”分开表达。

## 8. 不建议继续使用的表述

- 不写“精通 Agent 全栈”或“生产级 Agent 平台”：项目定位仍是学习、研究和小团队
  实验，不是多租户生产平台。
- 不在一条经历中罗列所有 Workflow 和工具名：优先解释解决的问题和统一机制。
- 不写“通过某单一组件提升 19.83%”：当前没有公开组件消融能够证明因果归因。
- 不把 Docker 运行写成“分布式 Agent 集群”：当前实现是本地 / 远程容器执行与
  产物协调，不是通用分布式调度系统。
- 不把 Provider Adapter 写成“自研大模型”：项目实现的是模型访问协议适配与
  Agent Runtime，不是基础模型训练。

## 9. 相关依据

- [项目目标与范围](01-product-goals-and-scope.md)
- [系统总体架构](02-system-architecture.md)
- [Agent 核心运行机制](04-agent-runtime.md)
- [上下文与长周期能力](07-context-and-long-horizon.md)
- [Agent 组合与工作流](08-agent-composition-and-workflows.md)
- [轨迹与可观测性](09-trace-and-observability.md)
- [评测与运行体系](10-evaluation-and-operations.md)
