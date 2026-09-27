# Lab 02 MVP 验证记录

验证日期：2026-09-26。以下结果来自当前 Lab 02 实现。

## 实现规模

```text
compile_task.py       单文件 pipeline
unittest_runner.py    容器内 JSON runner
test_compile_task.py  4 个快速 helper tests
```

当前版本只保留 Lab 02 的核心：从一个已知三元组生产并验证 task。

## 固定输入

```text
Repo: more-itertools/more-itertools
Issue: #719
PR: #720
B: c8baa10e66de18662114b71edcfa32f5dfdd2881
head: 46a253b36b74edead1fea1c09d762ddca4e895fd
G path: more_itertools/more.py
T path: tests/test_more.py
```

## 实测命令

```bash
python3 -m unittest -v test_compile_task.py

python3 compile_task.py \
  --repo more-itertools/more-itertools \
  --pr 720 \
  --issue 719 \
  --repeats 3
```

结果：

| Case | 重复 | 结果 |
| --- | ---: | --- |
| B | 1 | 734 pass |
| B+T | 3 | 每次 733 pass、1 fail |
| B+G+T | 3 | 每次 734 pass |

```text
F2P: tests.test_more.UniqueInWindowTests.test_basic
P2P count: 733
P2F: none
```

结构化证据见
[`validation/summary.json`](tasks/more-itertools__more-itertools-issue-719-pr-720/validation/summary.json)
和同级 case 目录。

## 拒绝路径

使用 `<more-itertools/more-itertools, PR #720, issue #718>` 时，`#718` 被识别为
pull request。命令退出码为 1，输出目录只包含
[`_REJECTED.md`](tasks/more-itertools__more-itertools-issue-718-pr-720/_REJECTED.md)，
reason code 为 `ISSUE_IS_PULL_REQUEST`。

## 边界

- 只支持 GitHub 和 Python 标准库 `unittest`。
- G/T 只按常见测试路径分离。
- 自动门禁不替代 issue/T/G 的人工语义审核。
- 构建阶段需要网络安装 Alpine packages，rollout 验证阶段断网。
- 该公开 PR 适合验证工程链路，不用于主张无污染泛化能力。
