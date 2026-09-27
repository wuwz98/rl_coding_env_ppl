# 从 Docker 入门到第一个可验证 PR 环境

更新时间：2026-09-27。下文保留最初的 **9 月 25 日 09:00 至 9 月 26 日 17:00**
学习计划作为过程记录；当前 Lab 00–04 已完成 repo 候选发现、真实模型 rollout、
candidate patch 导出和独立 grader reward 闭环。

当前已有可执行的 [Docker Lab 00](../labs/00-docker-cli/README.md)、
[Python 环境 Lab 01](../labs/01-docker-python/README.md)、
[Task Compiler Lab 02](../labs/02-pr-task-compiler/README.md) 和
[Agent Harness Lab 03](../labs/03-agent-harness/README.md)，以及
[Candidate Discovery Lab 04](../labs/04-candidate-discovery/README.md)。

截止明天 17:00 的必交范围不是“做出一个 Docker 镜像”就结束，而是跑通一条真正可调用的：

```text
真实 GitHub repo + issue/PR
    -> 固定 base commit、问题描述、G/T
    -> 不含答案的 Docker image
    -> 干净环境中的 baseline / bug / gold 证据
    -> reset / execute / finish / grade / close
    -> reward=0 与 reward=1 的可重放 episode
```

其中 G 是参考源码修复，T 是验收测试补丁。必交物是一个真实任务包、构建与验证入口、最小 RL 环境接口、两条可重放 episode 和一页报告。有可用模型端点时再争取一条 agent 轨迹；Ray、小批量采集、完整 RL 训练和 Kubernetes 不作为这次完成条件。

## 1. 今天中午前只走三份材料

1. [Docker Lab 00：不用 Docker Desktop，先弄懂 Docker](../labs/00-docker-cli/README.md)：使用 Docker CLI + Colima，亲手观察 CLI、daemon、Linux VM、image、container 和 process。
2. [Docker Lab 01：给一个 Python bug 准备可重复的考场](../labs/01-docker-python/README.md)：构建镜像、注入测试、修改容器、重置环境，并保存可验证结果。
3. [MiMo 环境导读：一个 coding agent 的命令怎样变成 reward](04-mimo-environment-guide.md)：用同一个 bug 串起环境接口与 Docker 实现，再按阅读表定位源码。

不同时通读 local-pipeline 全文，不先安装整套 MiMo，不把上午花在 Ray/Kubernetes 配置上。12:00 前的目标是理解并亲手完成一个小实验。

## 2. 从现在到明天 17:00

| 时间 | 内容 | 当段产出 |
| --- | --- | --- |
| 9/25 09:00–09:35 | Lab 00：安装/启动 Colima；观察 CLI、daemon、VM 和容器进程 | 不依赖 Desktop，能解释 `docker run` 的完整链路 |
| 09:35–10:40 | Lab 01 第 0–7 步：build、run、exec、cp、测试、重置、挂载 | 亲眼看到 3 pass → 2 fail → 5 pass，并保存 JSON |
| 10:40–10:50 | 休息；有余量再跑 Lab 01 第 9 步 | 不以自动脚本替代手动练习 |
| 10:50–11:35 | MiMo 导读与指定源码 | 用一条 episode 讲清任务层、执行层和 reward |
| 11:35–12:00 | 回答导读最后六问，回看 lab 对照表 | 完成上午验收，记录仍不确定的点 |
| 14:00–14:30 | 定向读 SWE-bench 导读与本项目验收定义 | 写出任务入选、B/G/T 和拒绝条件 |
| 14:30–15:15 | 手选真实 PR，确认 issue 与准确 base | 固定 1 题和至多 1 个备用，不批量采集 |
| 15:15–17:30 | 保存来源、拆 G/T、还原 B、固定依赖 | task record 完整，宿主机上先复现行为 |
| 19:30–21:30 | 构建无答案镜像，完成首次 B / B+T / B+G+T | 干净容器中的失败/通过证据 |
| 9/26 09:00–10:15 | 封装 build 与 validate 入口 | 从 task record 可重建镜像并生成结构化结果 |
| 10:15–11:30 | 各重复 3 次，检查空补丁、错误补丁、缺失测试和残留状态 | verifier 不误报，测试 ID 与版本齐全 |
| 11:30–12:00 | 固定最小 RL Env 协议和 episode 记录格式 | 接口、终止原因、reward 语义明确 |
| 13:30–15:00 | 实现 `reset/execute/finish/grade/close` 与脚本化 episode | 无修改 reward=0，gold reward=1 |
| 15:00–16:00 | 从来源记录开始完整重跑一次；有余量才接真实 agent | 得到端到端证据，或额外一条 agent 轨迹 |
| 16:00–17:00 | 冻结范围，整理报告、从头演示并留修错缓冲 | 一条可执行路径、一份证据报告、三分钟讲解 |

上午超时就先完成余下 Docker/MiMo 内容，之后压缩扩展项；不要跳过对照来赶进度。9/26 16:00 后不接新工具、不换题，保留演示和修错时间。3–5 题、30–100 题属于后续里程碑。

## 3. 本次各工具学到哪里

| 工具 | 在本项目中解决什么 | 本次学习深度 |
| --- | --- | --- |
| Colima | 在 macOS 上提供 Linux VM 和其中的 Docker daemon | 必须理解它是本地运行时载体，不是 Docker CLI |
| Docker | 固定初态与依赖，隔离执行，重建下一次尝试 | 必须亲手 pull/build/run/exec/cp，理解 client/server 与文件生命周期 |
| Python 控制器 | 把容器生命周期、动作执行、补丁导出和评分组成 episode | 本次必须实现最小接口 |
| Ray Core | 并行执行多道验证任务 | 本次完全后置，不占 9/26 的主线时间 |
| Ray Data | 批量读取、过滤、模型打分和写出 | 后续数据处理阶段再学 |
| Kubernetes | 在多台机器调度和维护容器工作负载 | 本次只知道位置，不搭集群 |

Ray 可在本机运行，无需 Kubernetes。大规模清洗也不必然依赖 Ray；常规 ETL 和模型批量推理的瓶颈不同，应按实际工作选择。第一题用 Python 调 Docker 足够。

Docker 必须能解释：CLI/daemon、macOS/Linux VM、image/container/process、COPY/mount、run/exec、重启/重置、退出码与测试结果。暂不学 Compose 多服务、网络插件和容器内核原理。

此前核实本机为 M4 Pro、48 GiB 内存、arm64；Docker CLI 28.4.0 已连接 Colima 中的 Docker Engine 29.5.2（linux/arm64），Lab 00/01 已验证可运行。daemon 状态仍以 `colima status` 和 `docker version` 的 Server 输出为准。第一题优先原生 arm64 依赖，避免把架构兼容失败误记为任务失败。

参考：[Docker 运行容器](https://docs.docker.com/engine/containers/run/)、[Ray Core](https://docs.ray.io/en/latest/ray-core/walkthrough.html)、[Ray Data](https://docs.ray.io/en/latest/data/quickstart.html)、[Kubernetes 概览](https://kubernetes.io/docs/concepts/overview/)。

## 4. MiMo 阅读的边界

已保存 [源码阅读材料](../references/mimoagent/README.md)，commit 固定为 `467f0a19016f0ac4d63b8d17a1f0da9ba07f232c`。上午只读任务定义、执行后端、评分流程和必要的转发关系。

官方[发布说明](https://mimo.mi.com/docs/zh-CN/news/latest/v2-6)宣布开放 7k+ 多领域环境；本次已核实框架接口，尚未核实完整环境数据包的直接下载入口。不能把本地源码摘录称为 7k 环境数据集。

框架安装及完整 quickstart 留到后面；源码阅读不需要更换本项目 Python 环境。后续可读 [convert_deepswe.py](https://github.com/XiaomiMiMo/mimoagent/blob/467f0a19016f0ac4d63b8d17a1f0da9ba07f232c/scripts/convert_deepswe.py)，理解已有任务目录如何转为 JSONL，但 DeepSWE 是独立评测集，不等于 MiMo 的 7k 训练集，也不作为我们训练任务的来源。

## 5. 今天下午如何开始真实 PR

理论阅读只用 45 分钟：

- 20 分钟读 [Step 3.5 §5.3.3–5.4](https://arxiv.org/html/2602.10604v1#S5.SS3.SSS3)，看任务环境构造与执行基础设施。
- 15 分钟读 [DeepSeek-V3.2 §3.2.3 Code Agent](https://arxiv.org/html/2512.02556v1#S3.SS2.SSS3.Px2)，看 issue/PR、测试补丁和验收。
- 10 分钟回到 [本项目验收定义](01-research.md#4-验证器是环境质量的核心)，写下初态、依赖、验收、重置要求。

更完整但仍经过筛选的阅读顺序见 [RL Coding Env 论文导读](05-rl-coding-env-paper-guide.md)；当前一题只读其中 P0，不在实现前通读所有报告。

选题优先检查 [more-itertools](https://github.com/more-itertools/more-itertools)，[Click](https://github.com/pallets/click) 作为备用。尚未确认具体 PR；只挑一个行为明确、源码与测试可拆分、依赖简单的纯 Python 修复。

之后按 [local-pipeline 当前交付范围](02-local-pipeline.md) 操作。先手选题、手工审核补丁，不先造通用采集器、数据库和调度平台。

用 B 表示修复前代码，G 表示源码修复，T 表示问题测试。至少证明：B 的原有测试健康；B + T 真实复现问题；B + G + T 通过且没有目标范围内的回归。两种候选都从独立干净容器开始，分别重复三次。

## 6. 什么才算已经得到 RL Env

Docker 镜像只提供初态；测试脚本只提供验证逻辑。两者接成可反复交互、结束后给 reward 的生命周期，才是这次要交付的最小 RL Env：

```python
obs = env.reset(task_id)
obs = env.execute("sed -n '1,200p' target.py")
obs = env.execute("python -m pytest ...")
patch = env.finish()
grade = env.grade(patch)
env.close()
```

`reset` 必须从同一无答案镜像创建新容器；`execute` 返回 stdout/stderr、退出码、耗时与超时；`finish` 只导出允许范围的候选源码补丁；`grade` 在新的 grader 容器中注入 T 并返回结构化逐测试结果与 reward；`close` 回收本次容器。先用 no-op 和 gold 两条确定性动作序列证明协议成立，不要求先接模型。

Ray 解决的是多个环境的调度，不决定单个环境是否正确。等这一接口稳定且确实需要并发时再学 Ray Core；Ray Data、Train、Serve、Tune、RLlib 和 KubeRay 全部后置。

## 7. 两个验收点

**9/25 中午：**

- [ ] 不使用 Docker Desktop，能用 Colima 启停 Linux VM 和 Docker daemon。
- [ ] 能解释 CLI、daemon、VM、image、container、process，以及 `docker --version` 与 `docker version` 的区别。
- [ ] 自己构建并运行小 Python 镜像，保留 baseline/bug/gold JSON。
- [ ] 观察到 3 个原有测试通过、2 个问题测试失败、修复后 5 个通过。
- [ ] 亲手证明重启不重置，新容器不保留旧可写层，而挂载目录会保留文件。
- [ ] 能解释 MiMo 的任务层与 Docker 执行层，以及测试何时注入、如何得到 reward。

**9/26 17:00：**

- [ ] 一个真实 PR 的来源、准确初态、G/T 与依赖记录可追溯。
- [ ] 可脚本化构建、重置和验证，不依赖手动改过的残留容器。
- [ ] 修复前后各三次一致，有逐测试结果；缺失测试、skip、解析失败不会误报成功。
- [ ] 参考答案与验收资产不在 agent 的初始镜像中。
- [ ] 可运行 `reset → execute → finish → grade → close`；保存 no-op reward=0 与 gold reward=1 的 episode。
- [ ] 一页报告区分实测、未实现和局限；能用三分钟演示完整过程。

有实际 agent rollout 才报告 agent 轨迹；未接入时如实标为“真实任务 + 可交互 RL Env MVP”。模型参数更新是后续阶段。

## 8. 超时如何调整

Docker 启动或拉取阻塞超过 20 分钟就先排障；可以继续读代码，但不把宿主机执行说成容器验证。选 PR 设 45 分钟预算，依赖问题约 30 分钟；超过后记录原因，切换更简单候选，不删掉失败测试来制造成功。

如果明天中午还没有稳定的一题，就取消 agent 接入，把下午全部用于主闭环和报告。最小环境接口不能再后置；可以减少通用性，只支持这一题，但必须真实执行、能重置并产生 reward。是否完成按真实证据判断，不为赶截止时间降低验收要求。
