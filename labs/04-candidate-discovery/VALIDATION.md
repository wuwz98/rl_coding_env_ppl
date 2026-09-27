# Lab 04 Validation

验证日期：2026-09-27。

## 1. 离线测试

执行：

```bash
python3 -m unittest -v test_discover_candidates.py
```

结果：

```text
Ran 11 tests
OK
```

覆盖：

- GitHub repo 输入归一化；
- closing keyword 识别、去重和跨 repo 区分；
- 与 Lab 02 一致的 test path 规则；
- merge 日期边界；
- source/test 文件及 diff 行数汇总；
- 文本启发式只输出信号，不伪造语义 label；
- `DISCOVERED/NOT_RUN` 状态边界；
- `PATCH_NOT_SEPARABLE` 拒绝；
- JSONL/CSV/summary 输出。

## 2. 指定 PR 的真实采集

执行：

```bash
python3 discover_candidates.py \
  --repo more-itertools/more-itertools \
  --pr 720 \
  --semantic-mode off \
  --run-name more-itertools-pr-720 \
  --max-requests 15
```

结果：

```text
status: DISCOVERY_COMPLETE
repo: more-itertools/more-itertools
candidates: 1
rejected records: 0
```

采集到的 GitHub 确定性事实：

```text
PR: #720
Issue: #719
PR total: 17 additions, 11 deletions
source: 1 file, 11 additions, 10 deletions, 21 changed lines
tests: 1 file, 6 additions, 1 deletion, 7 changed lines
issue comments: 1
issue comments before PR: 1
PR conversation comments: 2
PR inline review comments: 3
```

路径：

```text
source: more_itertools/more.py
tests:  tests/test_more.py
```

状态：

```text
status = DISCOVERED
verification_status = NOT_RUN
```

该状态有意低于 Lab 02 已验证样例的 `VERIFIED`。

## 3. 语义审核

执行：

```bash
python3 discover_candidates.py \
  --repo more-itertools/more-itertools \
  --pr 720 \
  --semantic-mode llm \
  --model ep-20260510173911-8tf68 \
  --run-name more-itertools-pr-720-pro \
  --max-requests 1
```

GitHub 请求全部命中本地 cache：

```text
github network_requests: 0
github cache_hits: 9
semantic network_requests: 1
```

结构化结论：

```text
original_issue.label = AMBIGUOUS
with_pre_pr_comments.label = CLEAR
pr_issue_alignment.label = ALIGNED
recommended_action = ACCEPT
```

模型识别出的两种原始解释：

```text
1. 修代码：window 是最近 n 个输入元素
2. 保留代码：文档改为最近 n 个已 yield 元素
```

PR 创建前的 maintainer 评论：

```text
Let's change it to your interpretation, which is probably what most readers
would assume.
```

审核将它正确解析为选择第一种解释，行为规格只陈述：

```text
仅当 item 未出现在 input iterable 紧邻的前 n 个元素中时才 yield。
```

### Prompt 校准记录

第一次语义 smoke run 虽然正确给出 `AMBIGUOUS -> CLEAR`，但错误解析了
`your interpretation` 的指代，生成了相反的行为规格。随后完成两项修正：

1. schema 强制每项结论提供原文 evidence quote；
2. prompt 明确要求区分 issue author 提议的行为与被报告的当前行为；
3. cache key 加入 `prompt_version`，避免复用旧结论。

修正后使用更强模型复核并得到上述正确结果。这个过程说明 LLM label 只能作为筛选特征，
不能替代人工抽查或 Lab 02 执行验证。

## 4. Repo 自动枚举 smoke run

执行：

```bash
python3 discover_candidates.py \
  --repo more-itertools/more-itertools \
  --max-prs 5 \
  --comment-mode counts \
  --semantic-mode off \
  --run-name more-itertools-latest-5 \
  --max-requests 20
```

结果：

```text
scan_scope: BOUNDED
closed pulls seen: 13
not merged: 8
merged PRs scanned: 5
discovered pairs: 2
rejected records: 3
GitHub network requests: 7
```

候选：

```text
issue #1293 / PR #1294: source 9 lines, tests 26 lines
issue #1284 / PR #1285: source 13 lines, tests 32 lines
```

三个 rejection 均为：

```text
NO_LOCAL_CLOSING_ISSUE
```

这证明主入口可以从 repo 分页发现 pair，而不依赖手工提供 issue/PR 编号。

## 5. 离线总验收

执行：

```bash
python3 verify_lab.py
```

它先运行全部单元测试，再检查保存的真实样例：

- 只有一个 #719/#720 candidate；
- source/test changed lines 分别为 21/7；
- 存在一条 PR 前 issue 澄清；
- 原 issue 为 `AMBIGUOUS`；
- 加评论后为 `CLEAR`；
- behavioral spec 选择 input-window 行为；
- 最终仍为 `DISCOVERED/NOT_RUN`。

## 6. 已验证与未验证

已验证：

- REST 分页入口、指定 PR 入口和本地 cache；
- 显式 issue/PR 关联；
- diff 与 discussion 特征；
- JSONL、CSV、rejection 和 funnel；
- 可选结构化语义审核；
- #719/#720 能被发现并与 Lab 02 现有 task 对齐。

仍未验证：

- 大型 repo 的完整无界扫描；
- GitHub authenticated 5,000 requests/hour 路径；
- GraphQL/sidebar 关联；
- 多语言测试路径 adapter；
- semantic label 在人工标注集上的 precision/recall；
- Lab 04 批量自动调用 Lab 02 的成功率。
