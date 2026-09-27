# Lab 03：Rollout + Candidate Grader

Lab 02 已经把 `<repo, PR, issue>` 编译成一个可执行 task：

```text
image(B)
public/task.json
private/gold.patch
private/test.patch
private/unittest_runner.py
validation evidence
```

Lab 03 完成一次完整 episode：

```text
task + LLM + harness
  -> 模型在 rollout container 中工作
  -> controller 导出 candidate.patch（C）
  -> 保存 trajectory 和运行元数据
  -> fresh grader 执行 B+C+T
  -> 检查 F2P/P2P
  -> 输出 reward
```

本 lab 继续使用：

```text
Task:
  more-itertools__more-itertools-issue-719-pr-720

Image:
  rl-task:more-itertools__more-itertools-issue-719-pr-720
```

## 1. 当前边界

Lab 03 负责：

- 从 Lab 02 读取公开 task 和 Task Image；
- 调用一个真实大模型；
- 提供 Bash-only harness；
- 保存 model/tool trajectory；
- 从最终 workspace 导出 C；
- 在独立 container 中注入 T；
- 检查 F2P/P2P 并产生 reward。

Lab 03 不负责：

- 从 GitHub 发现 repo/PR/issue，留给 Lab 04；
- 通用多语言 agent tools；
- 并行 rollout；
- RL 参数更新；
- 强安全恶意代码沙箱。

Rollout 与 grader 必须是两个 container：

```text
rollout container
  image(B) -> model commands -> workspace -> C

grader container
  fresh image(B) -> C -> T -> tests -> reward
```

不能直接信任 rollout container 中的测试结果，因为 agent 可以修改测试、伪造日志或留下
污染状态。

## 2. 代码入口

主要文件：

```text
run_agent.py       模型协议、rollout 生命周期和 C 导出
grader.py          fresh B+C+T 独立评分
test_run_agent.py  模型协议纯逻辑测试
test_grader.py     patch policy 与 F2P/P2P 测试
runs/              episode 产物
```

快速测试：

```bash
cd /Users/bytedance/PycharmProjects/RL_Coding_ENV/labs/03-agent-harness
python3 -m unittest -v test_run_agent.py test_grader.py
```

完整入口：

```bash
python3 run_agent.py
```

主要调用链：

```text
main
├── load_model_config
└── run_episode
    ├── create_container
    ├── ResponsesClient.create
    ├── execute_bash / finish
    ├── export_candidate_patch
    ├── remove rollout container
    └── grade_candidate
        ├── load_contract
        ├── patch_paths
        ├── create fresh grader container
        ├── apply C
        ├── reset_test_paths
        ├── apply T
        ├── execute unittest runner
        ├── evaluate_report
        └── remove grader container
```

## 3. `load_model_config`：读取模型端点

推荐通过环境变量配置：

```bash
export ARK_API_KEY='<secret>'
export ARK_MODEL='<model-or-endpoint-id>'
export ARK_BASE_URL='https://.../api/v3'
```

当前本机也可以复用项目根目录未提交的：

```text
LLM_ENDPOINT.md
```

其格式为：

```text
API_KEY=...
model='...'
base_url='...'
```

优先级：

```text
CLI --model / --base-url
-> ARK_* 环境变量
-> LLM_ENDPOINT.md
```

`ModelConfig.api_key` 使用 `repr=False`，API key 不会写入：

- prompt；
- trajectory；
- summary；
- candidate patch；
- container。

模型调用发生在 host，rollout container 本身保持 `--network none`。

## 4. `create_container`：启动 Rollout 环境

`run_episode` 先读取：

```text
<task-dir>/public/task.json
```

模型只会收到其中的：

```text
problem_statement
workspace
```

随后创建 container：

```bash
docker run --detach \
  --name rl-lab03-<id> \
  --network none \
  --cpus 1 \
  --memory 1g \
  <task-image> \
  sleep infinity
```

初始状态来自 Lab 02：

```text
/workspace/repo = B
```

Harness 立即记录合成 Git 初始提交：

```bash
BASE_REF=$(git rev-parse HEAD)
```

即使 agent 后续自己创建 commit，controller 仍能从原始 `$BASE_REF` 导出完整 C。

模型无法读取：

- Lab 02 `private/`；
- Gold patch；
- Test patch；
- Lab 02 validation logs；
- host 文件系统；
- Docker socket；
- API key。

## 5. `ResponsesClient`：调用模型

当前使用 OpenAI-compatible Responses API，不依赖 Ark/OpenAI Python SDK。

Host 发送：

```text
instructions
public problem statement
此前 Responses output items
此前 function_call_output
bash / finish tool definitions
```

模型只有两个工具。

### `bash(command)`

用于：

- 查看仓库；
- 编辑源码；
- 运行公开测试；
- 查看 Git diff。

工具定义要求参数是：

```json
{
  "command": "python3 -m unittest discover -s tests -v"
}
```

### `finish(summary)`

模型认为工作结束时调用：

```json
{
  "summary": "Changed the implementation and ran relevant tests."
}
```

模型的 `summary` 只是 trajectory 元数据，不参与 reward。

## 6. `run_episode`：模型与 Container 多轮交互

当 Responses API 返回：

```json
{
  "type": "function_call",
  "name": "bash",
  "arguments": "{\"command\":\"sed -n '1,120p' file.py\"}"
}
```

Harness 执行：

```bash
docker exec \
  --workdir /workspace/repo \
  <rollout-container> \
  timeout -s KILL 120s \
  bash -lc '<command>'
```

结果转换成：

```json
{
  "command": "...",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "..."
}
```

再作为 `function_call_output` 返回模型。

因此一轮是：

```text
model response
-> function call
-> docker exec
-> observation
-> next model response
```

命令输出超过 `max_observation_chars` 时保留开头和结尾，避免一次 observation 填满模型
上下文。

每轮保存：

- Responses response ID；
- 原始 output items；
- token usage；
- tool name/arguments；
- exit code；
- stdout/stderr。

## 7. Rollout 终止条件

| Termination | 含义 |
| --- | --- |
| `FINISHED` | 模型调用 `finish` |
| `MODEL_STOPPED` | 模型停止调用工具 |
| `TURN_LIMIT` | 达到最大 model turns |
| `ERROR` | API、Docker 或 harness 异常 |

Responses `status=incomplete` 不会被当作正常结束。Harness 会要求模型继续。

如果模型没有修改 workspace 就停止，harness 最多提示两次继续工作。

剩余 4 turn 时注入收尾提示：

```text
停止可选探索
完成行为修复
删除临时文件
运行最高价值测试
调用 finish
```

无论 termination 是什么，只要 rollout container 已启动，controller 都会尝试导出当时
的 workspace diff。

## 8. `export_candidate_patch`：产生 C

不同 harness 可能以不同方式修改仓库：

```text
mini-swe-agent -> shell 命令
Claude Code    -> 文件编辑工具
其他 agent     -> 自己生成 diff
```

稳定边界不是模型最终文字，而是 workspace 相对 B 的真实变化。

导出前执行：

```bash
git add --intent-to-add --all
```

这让未跟踪文件也出现在 diff 中，但不把 agent 的状态当作可信提交。

随后：

```bash
git diff --binary --no-ext-diff "$BASE_REF"
```

得到：

```text
candidate.patch = C
```

它会包含：

- tracked file 修改；
- 删除文件；
- 新增文件；
- agent 已 commit 的修改；
- agent 忘记清理的临时文件。

Harness 不替模型美化 C。Candidate 必须忠实反映最终 workspace。

导出后删除 rollout container。

## 9. `load_contract`：读取可信评分资产

Grader 不读取 trajectory 中的模型自报结果，而是重新读取 Lab 02 task：

```text
public/task.json
  -> task ID、image、workspace

private/source.json
  -> source_paths、test_paths

private/test.patch
  -> T

private/unittest_runner.py
  -> trusted result recorder

validation/summary.json
  -> F2P、P2P count

validation/gold-1/results.json
  -> 完整 required test IDs
```

`load_contract` 会检查：

- Gold validation 中所有测试都通过；
- F2P ID 存在于 Gold 测试集合；
- `expected_ids - F2P` 的数量等于 Lab 02 记录的 P2P count；
- source/test path policy 非空。

这防止把损坏或不完整的 Lab 02 task 用于 reward。

## 10. `patch_paths`：Candidate Policy

当前 P0 只允许 C 修改 Lab 02 Gold 涉及的 source paths。

本题：

```text
allowed:
  more_itertools/more.py
```

使用：

```bash
git apply --numstat candidate.patch
```

解析 touched paths。

如果 C 修改：

```text
tests/test_more.py
build config
其他不允许路径
```

返回：

```text
verdict = INVALID_CANDIDATE
reward = 0
reason_code = CANDIDATE_PATH_REJECTED
```

这个策略比通用 SWE-bench 更严格，但适合当前纯源码 bug-fix MVP。

## 11. `grade_candidate`：Fresh B+C+T

评分必须另起 container：

```bash
docker run --detach \
  --name rl-lab03-grade-<id> \
  --network none \
  <task-image> \
  sleep infinity
```

初始状态重新是：

```text
B
```

随后：

```text
应用 candidate C
-> 将 test_paths 恢复到 HEAD(B)
-> 注入 private T
-> 注入 trusted unittest runner
-> 执行完整 tests
```

恢复 `test_paths` 的目的，是保证评分测试来自 controller，而不是 rollout workspace。

测试命令：

```bash
python3 /tmp/unittest_runner.py \
  --output /tmp/results.json
```

Grader 保存：

```text
grading/results.json
grading/test.log
```

结束后删除 grader container。

## 12. `evaluate_report`：F2P/P2P 与 Reward

Grader 将本次结果按 test ID 与 Lab 02 Gold oracle 对齐。

检查：

```text
results.json 是否完整
所有 required IDs 是否实际出现
所有 F2P 是否 pass
所有 P2P 是否 pass
runner exit code 是否为 0
```

判定：

| Verdict | Reward | 含义 |
| --- | ---: | --- |
| `PASS` | 1 | 所有 required F2P/P2P 运行并通过 |
| `FAIL` | 0 | C 可评分，但测试失败、缺失或超时 |
| `INVALID_CANDIDATE` | 0 | C 非法、不可应用或修改禁止路径 |
| `INFRA_ERROR` | null | image、T、runner 等可信基础设施失败 |

`INFRA_ERROR` 不能伪装成 reward 0，否则训练数据会把环境故障当成模型失败。

Grader 不比较：

```text
C 是否与 Gold patch 完全相同
```

只比较行为和测试完整性，因此不同于 Gold 的正确修复也可以 reward 1。

## 13. 运行完整 Episode

先确认 Lab 02 image 存在：

```bash
docker image inspect \
  rl-task:more-itertools__more-itertools-issue-719-pr-720
```

运行：

```bash
cd /Users/bytedance/PycharmProjects/RL_Coding_ENV/labs/03-agent-harness

python3 run_agent.py \
  ../02-pr-task-compiler/tasks/more-itertools__more-itertools-issue-719-pr-720 \
  --run-name my-first-run \
  --max-turns 20 \
  --max-output-tokens 8192 \
  --command-timeout 120 \
  --grade-timeout 180
```

不传 task path 时默认使用上面的 more-itertools task：

```bash
python3 run_agent.py
```

run name 已存在时拒绝覆盖，避免误删 trajectory。

## 14. 独立重放已有 C

不调用模型，只重新评分：

```bash
python3 grader.py \
  ../02-pr-task-compiler/tasks/more-itertools__more-itertools-issue-719-pr-720 \
  runs/completed-example/candidate.patch \
  --output /tmp/lab03-grade
```

CLI 退出码：

```text
0 -> reward 1
1 -> reward 0 / invalid candidate
2 -> infrastructure error
```

## 15. Episode 产物

```text
runs/<run-id>/
├── candidate.patch
├── summary.json
├── trajectory.json
└── grading/
    ├── grade.json
    ├── results.json
    └── test.log
```

`summary.json`：

```text
model
termination
turns / tool calls
token usage
candidate bytes / changed files
reward / verdict
container cleanup
```

`trajectory.json`：

```text
完整 model/tool events
observations
termination reason
candidate SHA-256
完整 grading result
```

`grade.json`：

```text
reward / verdict / reason_code
candidate touched/allowed paths
expected/F2P/P2P counts
missing required tests
F2P/P2P not passing
grader container cleanup
```

所有产物都不保存 API key。

## 16. 已保存的真实 Example

```text
runs/completed-example/
├── candidate.patch
├── summary.json
├── trajectory.json
└── grading/
    ├── grade.json
    ├── results.json
    └── test.log
```

Rollout：

```text
termination: FINISHED
model turns: 13
tool calls: 13（含 finish）
input tokens: 116,610
output tokens: 10,853
total tokens: 127,463
candidate bytes: 819
changed files: more_itertools/more.py
rollout container removed: true
```

模型把 issue 解释为文档问题，只修改了 docstring，没有实现 Gold 的行为修复。

Grader：

```text
tests run: 734
pass: 733
fail: 1
missing required tests: 0
F2P not passing:
  tests.test_more.UniqueInWindowTests.test_basic
P2P not passing: 0
reward: 0
verdict: FAIL
grader container removed: true
```

这条 example 证明：

```text
模型完成真实 rollout
-> environment 导出真实 C
-> grader 没有用 Gold 修饰 C
-> fresh B+C+T 独立执行
-> 错误 C 得到 reward 0
```

## 17. Grader 控制实验

| Candidate | Tests | Verdict | Reward |
| --- | --- | --- | ---: |
| 空 patch | 733 pass、1 F2P fail | `FAIL` | 0 |
| Gold patch | 734 pass | `PASS` | 1 |
| 真实模型 C | 733 pass、1 F2P fail | `FAIL` | 0 |
| 修改测试的 patch | 未执行 | `INVALID_CANDIDATE` | 0 |

这些控制证明：

- grader 可以区分 no-op 和 Gold；
- 真实模型 C 没有因 `finish` 或自报成功而得分；
- 修改测试不能绕过 verifier；
- F2P/P2P 按完整 test ID 检查。

## 18. 检查 Example

摘要：

```bash
jq . runs/completed-example/summary.json
```

Candidate：

```bash
cat runs/completed-example/candidate.patch
```

Reward：

```bash
jq . runs/completed-example/grading/grade.json
```

模型工具调用：

```bash
jq -r '
  .events[]
  | select(.kind == "tool")
  | [.turn, .name, (.arguments.command // .arguments.summary // "")]
  | @tsv
' runs/completed-example/trajectory.json
```

检查无残留：

```bash
docker ps -a --filter name=rl-lab03
```

## 19. 当前 MVP 限制

- 只支持 OpenAI-compatible Responses API；
- 只提供 Bash 和 finish 两个 agent 工具；
- 一个 episode 只运行一个模型；
- 不做 context compaction，长轨迹 input tokens 增长较快；
- P0 只允许修改 Gold 涉及的 source paths；
- 只支持 Lab 02 当前 Python `unittest` task contract；
- source allowlist 不能防御所有 verifier hacking；
- rollout/grader container 内的不可信代码仍以 root 运行；
- 没有并行 rollout；
- 没有自动恢复中断中的 episode；
- 没有转换为训练框架 JSONL。

[Lab 04](../04-candidate-discovery/README.md) 已负责上游候选发现：

```text
GitHub repo/PR/issue
  -> 低成本筛选
  -> 候选队列
```

自动调用 Lab 02、Lab 03 并汇总完整 candidate/task/episode funnel 仍是后续编排工作。

实测记录见 [VALIDATION.md](VALIDATION.md)，整体位置见
[Labs 总览](../README.md)。
