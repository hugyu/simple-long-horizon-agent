# Simple Long Horizon Agent｜核心开发者

**技术栈：** Python、LLM Agent、Context Engineering、Tool Calling、MCP、Docker、
OpenAI / Anthropic API

- 设计并实现轻量级长周期 Agent Runtime，通过显式 Agent Loop 与自定义
  `Message / Event / State` 协议统一模型调用、工具反馈、状态演进和停止控制，并以
  Adapter 模式兼容 OpenAI、Anthropic 及 OpenAI-compatible 模型服务。
- 针对长任务上下文膨胀和关键信息遗失问题，分离完整运行历史与模型活跃上下文，
  实现工具结果压缩、模型摘要、主动 Compact 与 Recall；在降低上下文占用的同时
  保留原始证据，支持运行回放与信息恢复。
- 设计 Skills 渐进式加载与分级管理机制，启动阶段仅暴露 Skill 名称、描述和路径，
  按任务命中情况加载 `SKILL.md` 及附属资源，并通过 Repo、User、Bundled 三级覆盖
  控制能力范围和 Context 开销。
- 构建并行工具调用、超时与中止控制、MCP 接入和子 Agent 委派机制，在统一 Runtime
  上实现 Planner-Executor、Reflection、Parallel 和 Goal Loop 等工作流，并通过
  外部验证信号判断任务是否真正完成。
- 建设覆盖运行、观测与评测的工程闭环：增量记录 JSONL Trace，统计 Turn、Span、
  Token、成本与停止原因；设计容器化 Eval 框架，支持本地及远程 Docker 环境下的
  批量任务执行与结果评分。
- 接入 SWE-bench Pro、Terminal-Bench 2.1 评测任务，分别取得 **63.20%**
  和 **77.53%**，较对应 Baseline 提升 **4.10** 和 **12.83 个百分点**。

## 精简版本

### Simple Long Horizon Agent｜核心开发者

- 设计 Python 长周期 Agent Runtime，以显式 Agent Loop 和 Provider-neutral
  数据协议统一模型调用、工具反馈、状态管理与停止控制，兼容 OpenAI、Anthropic
  等模型服务。
- 设计完整历史与活跃上下文分层机制，实现工具结果压缩、模型摘要、Compact 与
  Recall，在控制 Token 消耗的同时保留原始证据和运行审计能力。
- 实现 Skills 渐进式加载与分级管理，按任务命中情况加载技能正文及附属资源，
  通过项目级、用户级和内置级覆盖控制 Context 开销。
- 实现并行 Tool Calling、MCP、子 Agent 委派和 Goal Loop，通过外部验证信号区分
  “模型声明完成”与“任务实际完成”。
- 建设 JSONL Trace 和容器化 Eval 系统；SWE-bench Pro 与 Terminal-Bench 2.1
  分别达到 **63.20%** 和 **77.53%**，较对应 Baseline 提升 **4.10** 和
  **12.83 个百分点**。
