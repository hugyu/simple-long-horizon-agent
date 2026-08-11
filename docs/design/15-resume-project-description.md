# 15. 简历项目描述

本文将 Simple Long Horizon Agent 的项目定位、核心工作和实验结果整理为可直接用于
简历的表述。内容以当前实现和已发布结果为依据，重点呈现项目解决的问题、关键技术
决策和可量化成果，不罗列目录或重复实现细节。

## 1. 推荐版本

### Simple Long Horizon Agent｜项目作者 / 核心开发者

**技术栈：** Python、LLM Agent、OpenAI / Anthropic API、MCP、Docker、JSONL、
HTML / CSS / JavaScript、uv

**项目简介：** 从零设计并实现面向长周期任务的轻量级 AI Agent Runtime，使模型
能够在有限上下文中持续规划、调用工具、观察结果并迭代验证。项目强调显式运行机制、
清晰模块边界和可复现实验，适用于 Agent 学习、架构研究及软件工程等真实多步骤任务。

**核心工作与成果：**

- 设计并实现基于生成器的显式 Agent 循环，以项目自有的 `Message`、`Event` 和
  `State` 协议统一模型调用、工具反馈、状态演进与停止控制；通过 Provider Adapter
  隔离供应商数据结构，支持 OpenAI Chat、OpenAI Responses、Anthropic Messages
  及 OpenAI-compatible 模型服务。
- 构建面向长任务的上下文管理体系，将完整历史、活跃上下文与单轮模型输入分层，
  结合工具结果压缩、模型摘要、Agent 主动 Compact、Recall、Skills、跨任务文件型
  Memory 和 Handoff，在控制上下文规模的同时保留原始证据与恢复路径。
- 实现 Bash、Read、Edit、Task 等内置工具及 MCP 集成，支持并行工具调用、超时与
  中止、Hook 策略拦截和子 Agent 委派；提供规划执行、反思、路由、并行、PDR 和
  Goal Loop 等工作流，覆盖动态委派与确定性编排两类多 Agent 协作模式。
- 打通 Event、Trace 与 Eval 工程闭环，实现增量 JSONL 轨迹、Span / Model Turn、
  Token 与成本统计及可视化 Trace Viewer；以 `Suite`、`Backend`、`ArtifactStore`
  解耦任务语义、执行环境和运行产物，支持本地进程、本地 Docker 与远程 Docker
  评测。
- 接入 SWE-bench Pro、Terminal-Bench、ProgramBench 等长周期评测；在同模型、
  同任务预算的 Baseline 下，SWE-bench Pro、Terminal-Bench 2.1 和 PostTrainBench
  分别达到 63.20%、77.53% 和 45.88%，相对提升 6.94%、19.83% 和 4.34%。

## 2. 一页简历版本

当简历空间有限时，使用下面四条。它保留项目的核心技术链路和量化结果，删除次要
模块与实现枚举。

### Simple Long Horizon Agent｜项目作者 / 核心开发者

**技术栈：** Python、LLM Agent、OpenAI / Anthropic API、MCP、Docker、uv

- 从零设计并实现轻量级长周期 Agent Runtime，以显式事件循环和自有
  `Message` / `Event` / `State` 协议统一模型调用、工具反馈、状态演进与停止控制，
  支持 OpenAI、Anthropic 及 OpenAI-compatible 模型服务。
- 构建长上下文管理体系，通过分层压缩、Recall、Skills、文件型 Memory 和
  Handoff 控制 Token 消耗，同时保留完整运行历史、精确信息恢复能力和审计证据。
- 实现并行工具调用、MCP、Hook、子 Agent 委派及规划执行、反思、路由、并行、
  PDR、Goal Loop 等工作流，并建设 Trace Viewer 与容器化 Eval 基础设施。
- 在 SWE-bench Pro、Terminal-Bench 2.1、PostTrainBench 上分别达到 63.20%、
  77.53%、45.88%，相对同模型同任务预算 Baseline 提升 6.94%、19.83%、4.34%。

## 3. 极简版本

当项目经历只能保留三条时，将架构和结果压缩为：

### Simple Long Horizon Agent｜项目作者 / 核心开发者

- 从零设计并实现轻量级长周期 Agent Runtime，以显式事件循环和 Provider-neutral
  数据协议统一模型调用、工具反馈、状态管理与停止控制。
- 构建上下文压缩、Recall、Memory、MCP、子 Agent、工作流及 Trace / Eval 闭环，
  支持模型在有限上下文中持续行动、恢复信息并接受外部验证。
- 在 SWE-bench Pro、Terminal-Bench 2.1、PostTrainBench 上分别达到 63.20%、
  77.53%、45.88%，相对同模型同任务预算 Baseline 提升 6.94%、19.83%、4.34%。

## 4. 口头介绍

面试中的 60～90 秒介绍可以使用下面的结构：

> Simple Long Horizon Agent 是我从零设计并实现的一套轻量级长周期 Agent Runtime。
> 它解决的不是单轮生成，而是模型如何在有限上下文中持续调用工具、观察环境、修正
> 方案并由外部结果验证完成。项目以一套显式 Agent 循环为核心，通过自有消息与事件
> 协议隔离模型供应商，再组合上下文压缩、Recall、Memory、MCP、子 Agent、Trace
> 和容器化 Eval。整套方案已经在软件工程、终端任务和模型后训练三类 Benchmark
> 上完成评测，并在同模型同预算条件下取得可量化提升。

## 5. 数字口径

简历中的三项提升均为相对提升，不是绝对百分点差：

| Benchmark | 项目结果 | Baseline | 绝对提升 | 相对提升 |
| --- | ---: | ---: | ---: | ---: |
| SWE-bench Pro | 63.20% | 59.10% | 4.10 个百分点 | 6.94% |
| Terminal-Bench 2.1 | 77.53% | 64.70% | 12.83 个百分点 | 19.83% |
| PostTrainBench | 45.88% | 43.97% | 1.91 个百分点 | 4.34% |

面试时应明确 Baseline 使用相同模型和任务预算。当前结果证明整套 Agent 配置有效，
在没有组件消融数据时，不将提升单独归因于 Task Tool、压缩、Handoff 或某个工作流。

## 6. 相关材料

- [项目目标与范围](01-product-goals-and-scope.md)
- [系统总体架构](02-system-architecture.md)
- [Agent 核心运行机制](04-agent-runtime.md)
- [上下文与长周期能力](07-context-and-long-horizon.md)
- [Agent 组合与工作流](08-agent-composition-and-workflows.md)
- [轨迹与可观测性](09-trace-and-observability.md)
- [评测与运行体系](10-evaluation-and-operations.md)
- [面试深挖与证据边界](13-interview-deep-dive.md)
- [无代码上下文的面试问答](14-interview-questions-and-answers.md)
