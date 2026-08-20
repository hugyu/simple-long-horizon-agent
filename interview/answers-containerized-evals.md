# 面试专题回答：容器化 Eval 系统

本文覆盖 [`question-checklist.md`](question-checklist.md) 中第 8 个专题。回答以通用
Suite、Backend、ArtifactStore 协议和现有 SWE-bench、ProgramBench、Harbor 接入为边界。

## 1. 描述一次 Eval 从任务输入到最终评分的完整过程

Host 先由 Suite 解析实例：`task_input()` 生成模型可见输入，`eval_inputs()` 单独生成可选的
私有评分数据，`launch_spec()` 给出 image、workdir 和资源配置。Runner 为该实例创建隔离的
run directory，把两类输入写进 ArtifactStore，再由 Backend 启动本地进程、Docker 或远程
Docker 环境。

环境内的通用 runner 加载 Suite container half，构建任务和 Agent，运行时持续写
`trajectory.jsonl`，结束后由 `extract_result()` 生成 `result.json`。评分可以在环境内 evaluate、
作为后续 Judge run，或由 Host 的官方 harness 完成。最终结果必须关联实例、配置、轨迹、产物
和评分证据，而不只是一个聚合分数。

## 2. 为什么必须用 Docker？

不是所有 Eval 都必须用 Docker。OneMillion-Bench 这种单轮 Q&A 可以用 LocalProcess；但
SWE-bench 和 ProgramBench 需要特定仓库、依赖、工具链和隔离的可执行环境，Docker 才能让
Agent 面对一致工作区，并把任务间污染控制在容器边界。

Docker 也不是安全性的充分条件。镜像、capability、network 和 secret 配置仍需要最小权限；
项目允许 `seccomp=unconfined` 等 suite-specific 设置，所以不能把所有容器都描述成强沙箱。

## 3. 每个任务是否使用独立容器？

在 LocalDocker 和 RemoteDocker 路径中，每个实例由独立 RunSpec 启动一个容器，并使用独立
`<run_id>/<instance_id>/` 目录。SWE-bench 不同实例还可能使用各自的预构建镜像。

LocalProcess 路径没有容器；若传入固定共享 workspace，就不应并发，或者必须使用 workspace
factory 为每个实例生成独立目录。Harbor 的环境生命周期由 Harbor harness 管理，也不是 SAL
通用 Backend 自己重复创建。

## 4. 如何限制 CPU、内存、网络、磁盘和执行时间？

`LaunchSpec` 支持 `nano_cpus`、`mem_limit`、`memswap_limit` 和 `network_mode`，Docker Backend
把这些字段传给 daemon；Suite 还可配置 capabilities 和 security options。总 wall-clock 可通过
运行参数传给容器内 Agent 控制，单工具还有自己的 timeout。

边界是：这些限制只有显式配置才生效，当前 LaunchSpec 对磁盘没有统一 quota，Docker SDK 等待
也不等于强制杀掉任意外部进程树。production 还需要 daemon 级存储配额、pids limit、只读挂载、
egress policy 和独立 watchdog。

## 5. 本地与远程 Docker 如何保持行为一致？

二者使用同一个 Suite 和 backend-neutral `RunSpec`，容器内运行相同 wheel、container module、
task input 和结果 Schema。区别只在 Artifact 传输：本地常用 bind mount；RemoteDocker 在
Host 只能访问 Worker 时通过同一 Docker 连接 `put_archive/get_archive` 推拉输入和输出。

真正可复现还需要固定镜像 digest、项目 wheel、Provider/model 配置和环境变量。当前接口保证
协议一致，不会自动保证不同 daemon 的 CPU 架构、镜像 tag 内容和网络条件完全相同。

## 6. Agent 如何与容器内环境通信？

Agent Runtime 本身就在容器内运行，Bash、Read、Edit 等工具直接操作容器工作目录，不是 Host
逐条转发命令。Host 与容器之间通过 ArtifactStore 交换输入、`result.json` 和 trajectory。

LocalDirStore 用挂载目录；HostHttpStore 适合 Worker 能访问 Host 的网络；Remote Docker 的
host-pull 模式通过 archive API 搬运。Harbor 集成同样让 SAL runner 进入 Harbor task environment，
没有虚构的 `harbor_exec` 转发工具。

## 7. 任务超时后如何确保子进程和容器被清理？

Backend 在创建或启动失败时会 force remove 半启动容器；阻塞运行结束后收集日志，并在默认配置
下 remove 容器。Remote backend 也在 finally 中停止 live poller 并 force remove。Bash 工具有
subprocess timeout，父 abort 会传入 Agent 和支持它的工具。

不过当前通用框架没有一套能证明任意 timeout 都杀净所有孙进程的强保证；尤其线程中的 Python
工具无法安全强杀。需要强隔离时应让操作留在容器或独立 process group，并由外部 watchdog 在
wall-clock 到达时停止/删除容器，再验证没有遗留资源。

## 8. 如何收集 Patch、日志、Trace 和评分结果？

任务产品统一进入 `out/result.json`，例如 SWE-bench container half 从 workspace 提取并过滤
`model_patch`。Trace 写入 `out/trajectory.jsonl`，Provider raw 可进入相邻 sidecar。Backend
收集 stdout/stderr 作为 logs；官方 scorer 的原始报告保留在各 suite 的 ignored output 目录。

所有文件通过 ArtifactStore key 传递，Runner 不从终端文本猜 Patch 或分数。这个边界使评分器
可以晚于 Agent 运行，也便于重新聚合而不重跑模型。

## 9. 如何保证评测可重复？

至少要固定 dataset/split/instance IDs、模型与 reasoning 配置、Agent flavor、prompt、turn/time/
并发预算、镜像和项目版本、评分器版本，并保存每个实例的 result、trajectory 和失败记录。任务
失败不能从 denominator 消失，重复或意外 instance ID 要失败而不是静默覆盖。

当前 Profile、run manifest、每实例目录和官方 scorer wrapper 支持这条路径，但 README 的公开
汇总结果并没有在仓库中附带全部逐任务产物和完整环境锁定信息，因此只能说框架支持可复现流程，
不能说任意人现在仅凭仓库就能逐位复现三个公开数字。

## 10. 如何处理模型输出的非确定性？

单次运行必须保留具体 trajectory、配置和结果，不能只存均值。要比较策略，应使用相同实例做
配对运行；如果模型仍随机，至少做多 seed 或多次重复，报告方差或置信区间，并把 infrastructure
失败与 task failure 分开。

当前公开结果没有随仓库记录重复次数和置信区间，所以我不会声称已经消除随机误差。Adapter
可以传部分 Provider 的 seed，但也不能假设所有服务真正提供确定性。

## 11. 批量执行失败后能否断点续跑？

短批量 `run_dataset` 是阻塞式的，只对抛出的基础设施异常按 `max_attempts` 重试，不会把任务
未通过自动重跑成成功。长运行可以用 `submit_dataset`：每启动一个容器就原子更新 `batch.json`；
新进程再用 `reconcile_dataset` 读取 handle 并轮询。

`result.json` 是终态事实，即使 daemon 已经找不到容器，reconcile 也能识别已完成实例。当前这
解决的是已提交运行的重入与收集，不是任意 Workflow step 的通用 checkpoint/restart。

## 12. 如何避免不同任务之间的环境污染？

Docker 路径每实例独立容器、独立 run directory 和明确输入 key；任务输入与私有 eval input
分开；SWE-bench 的产物从该实例 workspace 提取。批量结果最终按输入顺序汇总，不依赖并发完成
顺序。

需要持久 Memory 或 Chain State 时必须显式 opt-in 并使用 namespace/run ID 或
`chain_state.json` 传递，不能靠全局临时目录隐式共享。LocalProcess 固定 workspace 是已知例外，
并发时必须改成实例级 workspace。

## 13. 远程执行时如何处理认证和不可信代码？

Provider token 通过运行环境注入，不写进公开 Profile；远程 Docker 连接本身需要 SSH/TLS 等
宿主认证。Worker 不需要反向访问 Host 时，RemoteDocker 使用现有 Host→Worker 连接搬运产物，
减少额外入站面。

但当前项目不是完整多租户执行平台。运行不可信代码还需要隔离 Docker daemon、最小 capability、
网络 allowlist、临时凭证、只读或受控 mount、镜像验证和产物脱敏。把远程 daemon 暴露在无认证
TCP 上不可接受，HostHttpStore 也需要在真实部署补认证和传输保护。

## 核对依据

- [`protocols.py`](../src/simple_long_horizon_agent/evals/protocols.py)
- [`runner.py`](../src/simple_long_horizon_agent/evals/runner.py)
- [`backends/`](../src/simple_long_horizon_agent/evals/backends/)
- [`dataset.py`](../src/simple_long_horizon_agent/evals/dataset.py)
- [`batch.py`](../src/simple_long_horizon_agent/evals/batch.py)
- [`10-evaluation-and-operations.md`](../docs/design/10-evaluation-and-operations.md)
- [`tests/unit/test_evals_framework.py`](../tests/unit/test_evals_framework.py)
