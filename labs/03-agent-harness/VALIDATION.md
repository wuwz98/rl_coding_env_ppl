# Lab 03 MVP 验证记录

验证日期：2026-09-26。

## 环境

```text
Task:
  more-itertools__more-itertools-issue-719-pr-720
Image:
  rl-task:more-itertools__more-itertools-issue-719-pr-720
Workspace:
  /workspace/repo
Container network:
  none
Model protocol:
  OpenAI-compatible Responses API with function tools
Harness tools:
  bash, finish
```

API key 从环境变量或项目根目录的未提交 `LLM_ENDPOINT.md` 读取，没有写入 Lab 03
代码和产物。

## 快速测试

```bash
python3 -m unittest -v test_run_agent.py test_grader.py
```

结果：13 tests passed。除模型协议外，还覆盖：

- endpoint assignment 解析；
- 环境变量优先级；
- `ModelConfig` repr 不泄漏 API key；
- Responses function call 提取；
- response text 提取；
- observation 中间截断；
- token usage 聚合；
- prompt 不包含 Gold/Test patch 名称。
- candidate patch touched-path 解析；
- Lab 02 F2P/P2P contract 加载；
- required test 完整性；
- PASS/FAIL reward 计算。

## Responses Tool Smoke Test

实际向当前 Ark endpoint 发送一个要求调用 Bash tool 的最小请求，返回：

```text
status: completed
output type: function_call
tool name: bash
arguments.command: printf hello
```

这证明当前 endpoint 支持 Lab 03 使用的 Responses function-call wire format。

## 真实 Episode

命令：

```bash
python3 run_agent.py \
  --run-name completed-example \
  --max-turns 24 \
  --max-output-tokens 8192 \
  --max-observation-chars 12000
```

结果：

```text
termination: FINISHED
error: null
model turns: 13
tool calls: 13
input tokens: 116,610
output tokens: 10,853
total tokens: 127,463
candidate bytes: 819
changed files:
  more_itertools/more.py
container removed: true
```

证据：

- [summary.json](runs/completed-example/summary.json)
- [trajectory.json](runs/completed-example/trajectory.json)
- [candidate.patch](runs/completed-example/candidate.patch)
- [grade.json](runs/completed-example/grading/grade.json)
- [results.json](runs/completed-example/grading/results.json)
- [test.log](runs/completed-example/grading/test.log)

## Candidate Grader

Lab 03 在另一个干净 container 中执行：

```text
B + candidate.patch + private/test.patch
```

结果：

```text
734 tests run
733 pass
1 fail
exit code: 1
failed:
  tests.test_more.UniqueInWindowTests.test_basic
```

模型把 issue 解释成文档澄清问题，只修改 docstring，没有完成预期行为修复。Grader
返回：

```text
reward: 0
verdict: FAIL
reason_code: TESTS_FAILED
F2P not passing:
  tests.test_more.UniqueInWindowTests.test_basic
P2P not passing: []
missing required tests: []
```

这同时证明：

1. 模型确实在 Lab 02 image 的 workspace 中操作；
2. harness 没有用 Gold 修饰模型结果；
3. environment 能导出真实 C；
4. candidate extraction 与 candidate grading 使用不同 container；
5. reward 已写入 summary、trajectory 和 grading/grade.json。

## Grader 控制实验

| Candidate | Verdict | Reward | 结果 |
| --- | --- | ---: | --- |
| 空 patch | `FAIL` | 0 | 733 pass、1 F2P fail |
| Gold patch | `PASS` | 1 | 734 pass |
| 真实模型 C | `FAIL` | 0 | 733 pass、1 F2P fail |
| 修改测试的 patch | `INVALID_CANDIDATE` | 0 | `CANDIDATE_PATH_REJECTED` |

每次测试均在 fresh `--network none` grader container 中执行，结束后无 container
残留。Gold 只作为正控制，不参与正常 candidate reward。

## 首轮失败与修复

首轮真实请求在第 6 个 model turn 返回 `status=incomplete` 且没有 tool call。旧逻辑把
它误判为 `MODEL_STOPPED`，导出空 patch。

Harness 随后增加：

- `incomplete` continuation；
- 无修改停止时的继续提示；
- 剩余 4 turn 的收尾提示；
- prompt 中“行为修复、临时文件放 `/tmp`”约束。

第二轮证明可以从 turn limit 导出非空 workspace diff；第三轮得到上面的
`FINISHED` episode。前两轮产物仍保留在本机 `runs/` 中，但默认被 `.gitignore`
忽略。

## 已知边界

- 当前轨迹没有 context compaction，累计 input token 较高。
- Bash 命令在 disposable container 内以 root 身份执行；这不是恶意代码安全沙箱。
- Harness 忠实导出所有 workspace 修改，grader 再执行 P0 source-path allowlist。
- Grader 区分 candidate failure、invalid candidate 和 infrastructure error。
- 当前 anti-hack 仍不完整，source allowlist 不能防御所有恶意 candidate。
- 当前模型的 candidate 是错误答案；这不是模型能力基线，只是一条集成 smoke run。
