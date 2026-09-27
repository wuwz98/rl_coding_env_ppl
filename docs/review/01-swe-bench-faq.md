# SWE-bench 复习问答：从 base commit 到可信 reward

这份复习材料只回答一个问题：

> 怎样把真实 GitHub issue / PR 转成一道可执行、可评分的代码任务？

重点是容易混淆的 B、T、G、C、public tests、Task Image、Rollout Container、Grader Container 和 verifier 完整性。数字与模型排行榜不是本轮重点。

## 1. B、T、G、C 到底是什么？

### Q1：B 是什么？

**B（Base）是 base commit 对应的完整仓库 tree。**

它通常包含：

```text
B
├── 仍有 bug 的生产源码
├── base commit 已经存在的测试
├── pyproject.toml / pom.xml / build.gradle 等构建配置
├── 文档、脚本和其他仓库文件
└── Git 中该 commit 所记录的其他内容
```

B 不只是“bug 代码”，也不只是“非测试文件”。我们不会先把 base commit 拆成源码 B 和 public tests，再重新组合。

严格区分：

```text
B        = Git 仓库在 base commit 的状态
Runtime  = Python/JDK、系统库、依赖、Maven/Gradle 等运行条件
Task Image = B + Runtime + tools + 可见性/安全策略
```

### Q2：public tests 是不是额外加在 B 上的？

不是。**Public/visible tests 通常就是 B 中原本已经存在、agent 可以看到的测试。**

“运行 B + public tests”是一种不严谨的口头说法。更准确的是：

```text
在 B 上运行 B 原有的 baseline tests
```

如果 PR 修改了一个已有测试文件：

- 该文件在 B 中的旧版本仍属于 public tests；
- PR 对该文件产生的新增/修改 hunks 属于 T；
- rollout 时 agent 看到旧版本，不看到 T 带来的变化。

### Q3：T 是什么？

**T（Test Patch）是从 PR diff 中提取出的测试与验收变更。**

它可能包含：

- 新增测试文件；
- 修改已有测试；
- 新 fixture；
- verifier script；
- 测试运行所需的少量验收资产。

T 的作用是把 issue 的目标行为变成可执行判据。它通常在正式评分时才注入，不在 rollout 初态中提供给 agent。

### Q4：G 是什么？

**G（Gold Patch）是从 PR diff 中提取出的人类参考源码修复。**

它也叫：

- gold patch；
- reference patch；
- reference solution；
- oracle patch。

G 用于任务构建阶段证明：

```text
至少存在一个已知修复，使目标测试通过且原有行为不回归。
```

G 不提供给解题 agent，也不要求模型逐字复现。行为正确的不同实现也可以成功。

### Q5：C 是什么？

**C（Candidate Patch）是本次 agent/model 提交的候选修复。**

它也可能叫：

- candidate patch；
- model patch；
- prediction；
- submission。

C 与 G 处在相同的“候选源码修复”位置，但来源不同：

```text
G：历史 PR 中的人类参考修复
C：模型本次独立产生的修复
```

正式评分评价 C，不应用 G。

## 2. base commit 和 PR 到底怎样拆？

### Q6：base commit 和 PR commit 都要拆成测试/非测试两部分吗？

不需要。**保留 base commit 的完整 tree，只拆 base 到 fixed state 的 diff。**

```text
B = tree(base_commit)
D = diff(B, selected_fixed_state)
D = G + T
```

示例：

```text
base commit B:
  src/stats.py                 旧源码，有 bug
  tests/test_stats.py          原有测试

PR diff D:
  src/stats.py                 修改 -> G
  tests/test_stats.py          修改 -> T
  tests/test_regression.py     新增 -> T
```

于是：

```text
B
  = bug source + 原有 tests

B + T
  = bug source + 原有 tests + PR 的测试变化

B + G + T
  = fixed source + 原有 tests + PR 的测试变化
```

### Q7：fixed state 是否一定等于“PR 合并后的 master commit”？

不一定。真实 GitHub PR 可能使用：

- merge commit；
- squash merge；
- rebase merge；
- 合并时的冲突解决；
- 已经继续前进的 base branch。

因此不能盲目使用采集时 master 的最新 commit。需要固定准确 SHA，并验证：

```text
apply(B, G, T)
```

是否得到选定的修复状态或与之等价的预期 tree。

### Q8：按文件名拆 G/T 一定可靠吗？

不一定。

测试通常在 `tests/` 或文件名含 `test`，但真实仓库可能存在：

- fixture 放在普通模块中；
- 内联测试；
- 测试专用依赖；
- 构建配置同时影响生产和测试；
- 生产路径名字中恰好包含 `test`。

首个 MVP 可以挑选 G/T 容易人工确认的 PR。批量化时需要 repository adapter、执行验证和人工抽查，不能只依赖路径字符串。

## 3. 为什么要运行 B、B+T、B+G+T？

### Q9：在 B 上运行原有测试证明什么？

证明任务初态和运行环境基本健康：

- 代码能安装或编译；
- 依赖基本齐全；
- 选定 baseline tests 能收集和执行；
- 环境不是在 agent 开始前就已经损坏。

它不能证明代码没有 bug。原有测试可能没有覆盖 issue。

### Q10：B+T 失败证明什么？

它证明 T 能在修复前状态中暴露目标问题。

有效失败应来自目标断言，而不是：

- 依赖缺失；
- 编译失败；
- 测试收集失败；
- 工作目录错误；
- 超时；
- 零测试；
- parser 失败。

### Q11：B+G+T 通过证明什么？

证明：

- 任务至少存在一个已知解；
- G 能使目标测试通过；
- 约定范围内的原有行为没有回归；
- T 与选定 B/G 基本匹配。

它不证明 G 是唯一、最简或工程质量最好的修复。

### Q12：为什么还要执行 B+C+T？

这是正式评价模型的步骤：

```text
干净 B
  -> 应用 Candidate C
  -> 注入 Test Patch T
  -> 运行 trusted verifier
  -> 产生 reward
```

G 不参与这一步。

## 4. F2P、P2P、P2F 如何定义？

### Q13：F2P 和 P2P 是整条测试命令的结果吗？

不是。它们是比较**同一个 test ID** 在不同状态下的结果。

| Test ID | B+T | B+G+T | 分类 |
| --- | --- | --- | --- |
| `test_negative` | fail | pass | F2P |
| `test_empty` | pass | pass | P2P |
| `test_mixed` | pass | fail | P2F |

- **F2P（FAIL_TO_PASS）**：G 修复的目标测试。
- **P2P（PASS_TO_PASS）**：G 前后都通过的回归保护测试。
- **P2F（PASS_TO_FAIL）**：G 引入的回归。

任务构建阶段通常要求：

```text
|F2P| > 0
P2F = empty
```

### Q14：Candidate C 的 P2F 怎样判断？

任务构建时先用 G 冻结 expected F2P/P2P。评分 C 时：

- expected F2P 在 B+C+T 中失败：问题尚未完全修复；
- expected P2P 在 B+C+T 中失败：Candidate 引入 P2F 回归。

所以 P2F 并没有被忽略。它是“expected P2P 没能继续通过”的观察结果。

### Q15：resolved=true 的完整条件是什么？

核心条件是：

```python
resolved = (
    evaluation_is_valid
    and all_expected_tests_were_collected
    and no_expected_test_was_skipped
    and f2p_passed == f2p_total
    and p2p_passed == p2p_total
)
```

通常还要满足：

- Candidate patch 符合修改范围政策；
- test patch 完整应用；
- verifier 没有超时或崩溃；
- 结构化结果成功解析；
- 没有额外约定范围内的失败测试。

`resolved` 是布尔值，不是“通过了几个测试”。

## 5. Task Image、Rollout 和 Grader 是什么？

### Q16：Task Image 应包含什么？

Task Image 是可重复创建 rollout 的无答案初态：

```text
Task Image
├── B 的仓库文件
├── public/visible tests（它们本来就在 B 中）
├── 固定 Runtime 和依赖
├── shell、git、编译器、构建工具等
└── 必要的环境配置
```

通常不应包含：

```text
G
T
trusted grader
未来 Git commit / PR branch / reference answer
云凭据、SSH key、宿主机 Docker socket
```

Problem statement 可以由 harness 作为任务消息交给 agent，不一定必须保存成容器文件。

### Q17：Rollout Container 做什么？

```text
Task Image
  -> 创建 Rollout Container
  -> agent 读文件、执行命令、修改工作区
  -> harness 记录 action/observation
  -> 结束时捕获 raw working diff
  -> 校验修改范围
  -> 得到 Candidate C
```

一次完整尝试叫：

- episode；
- rollout；
- trajectory（更强调可保存的交互记录）。

### Q18：Grader Container 做什么？

正式的 clean-room 方案是：

```text
新的干净 Task Image/B
  -> 应用 C
  -> 注入 T
  -> 运行 trusted verifier
  -> 检查 expected F2P/P2P 和完整性
  -> 输出 GradeResult/reward
```

不能只相信 rollout 中 agent 自报“测试通过”。

### Q19：为什么不用 agent 已经修改过的 Rollout Container 直接评分？

因为 agent 可能已经：

- 修改或删除 public tests；
- 修改测试发现配置；
- 伪造结果文件；
- 改写测试 runner；
- 留下影响结果的进程或文件；
- 只为可见测试硬编码。

新的 Grader Container 将“模型工作状态”和“可信评分状态”分开。

## 6. Candidate C 为什么还要做路径政策检查？

### Q20：B 包含 tests，agent 是否可以在 rollout 中修改 tests？

文件系统层面通常可以，除非另加权限限制。Agent 在 rollout 中可能修改 B 的任何可写文件。

因此结束时先得到：

```text
raw agent diff = diff(B, rollout workspace)
```

再按任务政策检查。例如首个 MVP 只允许生产源码：

```text
src/**                  allowed
tests/**                protected
grader/**               protected
pyproject.toml          protected for source-only MVP
```

如果 raw diff 修改 protected path，最清晰的政策是将 submission 标记为 invalid，而不是静默接受这些改动。

### Q21：为什么构建配置有时可见，有时又禁止修改？

仓库原有构建配置属于 B，通常必须让 agent 看见：

```text
pyproject.toml
requirements.txt
pom.xml
build.gradle
Cargo.toml
```

“可见”和“允许修改”是两回事。

- 纯源码 bug fix：可以看，但 Candidate policy 禁止修改。
- 依赖或构建修复任务：可以看，也必须允许修改。

不同任务类型需要不同的 allowed-path policy，不能永远硬编码“构建文件不可改”。

## 7. Verifier 完整性为什么是独立问题？

### Q22：为什么测试命令 exit 0 仍不一定可信？

可能发生：

- 零测试被执行；
- 目标测试没有被收集；
- 全部测试被 skip；
- runner 被修改为直接退出 0；
- agent 伪造了结果文件；
- parser 没读到真实结果；
- Candidate 代码干扰测试框架。

因此 `exit_code == 0` 只是必要信号之一。

### Q23：可信 verifier 至少检查什么？

第一层，可信重放：

- 从干净 B 创建 grader；
- 只应用通过 policy check 的 C；
- 由控制器注入 T 和 verifier；
- 不使用 rollout 生成的结果文件。

第二层，测试完整性：

- test patch 的 hash 和应用结果正确；
- 冻结的 test IDs 全部被收集；
- 每个 required test 都真实执行；
- missing/skip/error/timeout 不得误判成功；
- 解析结构化结果，如 JUnit；
- 保存进程退出码、逐测试状态和原始日志。

第三层，对抗性检查：

- no-op 必须失败；
- gold 必须成功；
- 错误 patch 必须失败；
- 删除测试、修改 runner、伪造日志不得成功；
- 测试检测和 reward hacking 需要额外防御。

第三层是后续 SWE-Universe 等工作的重点。首个 MVP 至少完成前两层和几个负对照。

### Q24：Candidate 失败和基础设施失败怎样区分？

Candidate 失败：

```text
C 导致语法错误、编译失败、断言失败、超时
-> valid evaluation
-> reward=0
```

基础设施失败：

```text
镜像拉取失败、daemon 不可达、T 无法传输、
grader 自身损坏、结果解析器异常
-> infra_error
-> reward=null 或该 trajectory 被 mask/retry
```

不能把所有 infra error 都算成模型能力不足。

## 8. Retrieval 和任务规模怎样理解？

### Q25：为什么 patch 能 `git apply` 不等于 resolved？

`git apply` 只说明 diff 的文件路径和上下文位置可以匹配。它不证明：

- 代码能编译；
- issue 被修复；
- 没有回归；
- 测试执行完整；
- 实现符合性能或安全要求。

### Q26：BM25 retrieval 和 oracle retrieval 有什么区别？

- **BM25**：根据 problem statement 的文本相关性检索代码，可真实部署但能力有限。
- **Oracle retrieval**：偷看 G 修改了哪些文件，再把这些文件给模型。

Oracle 用于回答：

> 如果文件定位问题已经解决，模型剩余的修复能力有多强？

它依赖答案信息，只是分析上界，不是未知 issue 的真实解决方案。

### Q27：为什么更多 context 不一定更好？

更多 context 可能提高相关文件召回，但也会加入：

- 无关文件；
- 相似但错误的实现；
- 更多跨文件依赖；
- 更长的注意力距离。

所以必须同时观察 retrieval recall、context 长度和最终 resolved，而不能只追求塞入更多 token。

## 9. 不同语言是否改变这套协议？

### Q28：Python、Java、Go、Rust 的任务结构是否不同？

B/T/G/C 和评分协议不变，变化的是 Runtime 与 repository adapter。

| 语言 | Runtime/工具 | 常见 grader 命令 |
| --- | --- | --- |
| Python | Python、pip/uv | `python -m pytest` |
| Java | JDK、Maven/Gradle | `mvn clean test` |
| Go | Go toolchain | `go test ./...` |
| Rust | rustc、Cargo | `cargo test` |
| C/C++ | compiler、CMake/Make | `cmake ... && ctest` |

编译型项目的 Grader Container 仍然：

```text
干净 B -> C -> T -> clean build -> test -> reward
```

不能只把最终 JAR/binary 给 agent；agent 需要源码、构建系统和编译工具。

## 10. 面试版完整回答

### Q29：请用两分钟讲清从 PR 到 reward

可以按下面顺序复述：

```text
1. 从已合并 PR 找到关联 issue 和准确 base commit。
2. B 是 base commit 的完整仓库状态，包含旧源码、原有测试和构建配置。
3. 计算 base 到修复状态的 PR diff，将源码修复拆为 G，测试变化拆为 T。
4. 在固定 Runtime 中验证 B 的 baseline tests 健康。
5. 执行 B+T，确认至少一个稳定 F2P。
6. 执行 B+G+T，确认所有 F2P 转为通过，P2P 保持通过且无 P2F。
7. 用 B、Runtime 和工具构建不含 G/T 的 Task Image。
8. Agent 在 Rollout Container 中根据 problem statement 产生 raw diff。
9. Harness 检查修改范围并导出 Candidate C。
10. 从干净 B 创建 Grader Container，应用 C、注入 T、运行 trusted verifier。
11. 所有 expected F2P/P2P 完整执行且通过时 reward=1；有效失败 reward=0；基础设施故障单独处理。
12. 保存 task 版本、image digest、action/observation、C、逐测试结果、reward 和失败原因。
```

## 11. 最小自测

不看前文回答：

1. B 是否包含 public tests？为什么？
2. 我们拆的是 base commit，还是 base 到 fixed state 的 diff？
3. G 和 C 分别在什么阶段使用？
4. expected P2P 在 Candidate 上失败叫什么？
5. 为什么 `resolved` 不能只判断 exit code？
6. Agent 在 rollout 中修改 tests 时，控制器应怎样处理？
7. 为什么 repository build config 可以可见但不一定允许修改？
8. 为什么 Grader 应从干净 B 创建？
9. Oracle retrieval 为什么不能部署？
10. infra error 为什么不应直接算 reward=0？

这十题能独立回答，就可以进入 [MiMo 环境导读](../04-mimo-environment-guide.md)。
