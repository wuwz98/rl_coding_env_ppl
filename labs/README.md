# RL Coding Environment Labs 总览

这组 lab 按“运行隔离 -> 单题验收 -> 自动任务生产 -> 模型交互”递进。每一层都在回答
不同问题。

| Lab | 核心问题 | 输入 | 最终输出 |
| --- | --- | --- | --- |
| [Lab 00](00-docker-cli/README.md) | Docker 命令到底由谁执行？ | 本机 Docker CLI + Colima | 能区分 CLI、daemon、VM、image、container、process |
| [Lab 01](01-docker-python/README.md) | 怎样给一个已知 bug 准备可重置考场？ | 人工编写的 Python bug、公开测试、隐藏测试、参考修复 | 一个可构建 image，以及 baseline/bug/Gold 证据 |
| [Lab 02](02-pr-task-compiler/README.md) | 怎样把真实 issue/PR 自动编译成可靠 task？ | `<repo, merged PR, linked issue>` | VERIFIED task 或带 reason code 的 `_REJECTED.md` |
| [Lab 03](03-agent-harness/README.md) | 怎样 rollout 并独立评分候选补丁？ | Lab 02 task + model endpoint | trajectory + C + reward |
| [Lab 04](04-candidate-discovery/README.md) | 怎样从 repo 批量发现候选？ | GitHub repo | DISCOVERED issue/PR pairs、特征和拒绝漏斗 |

## Lab 00：理解执行底座

Lab 00 不构建项目。它通过实际命令观察：

```text
Docker CLI
  -> Unix socket
  -> Colima Linux VM 中的 Docker daemon
  -> image
  -> container
  -> container 主进程
```

完成后应能解释：

- `docker --version` 成功为什么不代表 daemon 可用；
- macOS 为什么需要 Linux VM；
- image 与 container 的区别；
- `run`、`exec`、`stop/start`、`rm` 分别改变什么；
- 为什么重启容器不等于重置环境。

这为后续的“每个验证 case 新建干净容器”建立运行时直觉。

## Lab 01：人工构造一间可靠考场

Lab 01 给出一个小型 Python bug：

```text
largest([-8, -2, -5]) 错误返回 0
```

你手工完成：

```text
初始源码 + 公开测试 -> build image
image + 隐藏测试   -> 稳定复现 bug
image + 修复 + 测试 -> 全部通过
```

它重点教授：

- Dockerfile 怎样固定代码和运行时；
- 公开测试健康不等于问题不存在；
- hidden test 与 Gold 为什么稍后注入；
- F2P、P2P 和 reward 的最小含义；
- 为什么验证输出应写到宿主机，任务修改应随容器删除。

Lab 01 的代码和测试由人预先准备，所以它没有回答真实数据如何规模化生产。

## Lab 02：把候选 PR 编译成 task

Lab 02 把 Lab 01 的手工步骤变成 Task Compiler：

```text
<repo, PR, issue>
  -> 冻结 GitHub 元数据与 B
  -> 从 PR diff 拆 G/T
  -> 构建无答案 image
  -> clean(B), clean(B)+T, clean(B)+G+T
  -> F2P/P2P/P2F 与重复稳定性门禁
  -> VERIFIED task 或 REJECTED reason
```

它进一步加入：

- provenance 与准确 commit SHA；
- 明确的成功/拒绝输出契约；
- 通过独立 build context 隔离 private 资产；
- 逐 test ID 的结构化记录；
- no-op 与 Gold 控制验证；
- 一个可直接运行和继续扩展的 MVP 实现。

Lab 02 的自动化边界是“编译一个已知候选”，不是“从整个 GitHub 找候选”。测试路径
分类、runtime 识别、candidate grader 和语义审核仍需要随着仓库类型扩展。

## Lab 03：Rollout 与 Candidate Grading

Lab 03 使用 Lab 02 的公开 task 和 image：

```text
public task + image(B)
  -> host 上的模型 API
  -> Bash-only harness
  -> container 内多轮观察、编辑、测试
  -> controller 导出 candidate.patch（C）
  -> 保存 trajectory、token usage 和终止原因
  -> fresh grader 执行 B+C+T
  -> 检查 F2P/P2P 并输出 reward
```

它把不同模型/harness 的最终代码结果统一成 C。模型的文字结论和自报测试结果不作为
可信判分依据。独立 grader 只信任 Lab 02 private assets，在新的 container 中执行
`B+C+T`。真实 example 成功导出了 C，但 grader 发现一个 F2P 仍失败，因此 reward=0。

## Lab 04：发现候选并提取特征

Lab 04 从一个 GitHub repo 的 merged PR 反向查找显式关闭的 issue：

```text
repo
  -> merged PR
  -> closes/fixes/resolves issue
  -> PR files + issue/PR discussion
  -> 确定性特征
  -> 可选语义审核
  -> candidates / rejected / funnel
```

它记录 source/test 文件与增删行、comment 数量及正文、原始 issue 是否清晰、加入 PR 前
澄清后是否清晰，以及 PR 与需求是否对齐。确定性采集不依赖 LLM；LLM 结论保留模型、
证据和 rationale。

Lab 04 的候选只标记为 `DISCOVERED/NOT_RUN`。真正的构建、F2P/P2P/P2F 和稳定性检查
仍由 Lab 02 完成。

## Labs 职责边界

```text
Lab 00: 我能否稳定创建、观察、删除隔离执行环境？
    |
Lab 01: 给定 B/G/T，我能否证明 bug 与 reward 都有效？
    |
Lab 02: 给定 repo/PR/issue，我能否自动得到并验证 B/G/T/task？
    |
Lab 03: 模型怎样在 task 中工作，并在 fresh B 上评分 C+T？
    |
Lab 04: 我怎样批量发现高质量 repo/PR/issue 候选？
    |
RL Trainer: 怎样用 trajectory + reward 更新模型参数？
```

Task Compiler、Agent Harness、Candidate Grader 和 RL Trainer 是不同组件。Lab 03
内部仍保持 rollout/grader container 隔离。完成 Lab 03 表示已经得到真实 trajectory、
candidate patch 和 reward，不表示已经完成强化学习训练。

## 推荐学习顺序

1. 完成 Lab 00 的 Checkpoint 0–8。
2. 手工完成 Lab 01 第 0–8 步，再运行它的 `verify_lab.py`。
3. 阅读 Lab 02 的输出契约和调用链，再运行完整 compiler。
4. 检查成功 task 的 B/G/T、image 和 validation evidence。
5. 运行 Lab 03，检查 tool calls、candidate patch、F2P/P2P 和 reward。
6. 运行 Lab 04，查看 repo 级发现、特征、语义审核与拒绝漏斗。
7. 把 `DISCOVERED` pair 送回 Lab 02，统计最终 `VERIFIED` 转化率。

完成当前 labs 后，你应能沿着一条具体 evidence chain 回答：

```text
一个 GitHub 修复记录
怎样从 repo 中被发现
为什么能成为题
怎样进入无答案容器
怎样被模型修改
怎样在独立环境评分
为什么这个 reward 值可信
```
