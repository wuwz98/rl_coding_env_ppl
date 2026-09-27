# Lab 04：GitHub Candidate Discovery

Lab 02 的输入是一个已经知道编号的：

```text
<repo, merged PR, linked issue>
```

Lab 04 负责从 repo 反向发现这些输入，并保存后续筛选需要的特征：

```text
GitHub repo
  -> 枚举 merged PR
  -> 提取显式 closes/fixes/resolves issue
  -> 获取 PR files、issue 和 discussion
  -> 计算确定性特征
  -> 可选 LLM 语义审核
  -> candidates.jsonl / candidates.csv / rejected.jsonl / summary.json
```

它回答的是“哪些记录值得送进 Lab 02”，不负责证明任务可运行。

## 1. 状态边界

Lab 04 的成功候选固定使用：

```json
{
  "status": "DISCOVERED",
  "verification_status": "NOT_RUN"
}
```

含义是：

- PR 已合并；
- PR body 显式关闭当前 repo 的 issue；
- 目标确实是 issue，不是另一个 PR；
- 按 Lab 02 当前路径规则，PR 同时包含 source patch 和 test patch；
- 元数据和特征已被记录。

它不代表：

- B 能正常构建；
- B 原测试健康；
- T 能在 B 上复现问题；
- G+T 能通过；
- F2P 非空、P2F 为空；
- 测试稳定。

只有 Lab 02 执行真实构建与对照测试后，才能产出 `VERIFIED`。因此正确漏斗是：

```text
Lab 04 DISCOVERED
  -> Lab 02 VERIFIED / REJECTED
  -> Lab 03 trajectory + candidate patch + reward
```

## 2. 文件入口

```text
discover_candidates.py       采集、特征提取、语义审核和输出
test_discover_candidates.py  不联网的单元测试
verify_lab.py                 检查单元测试和保存的真实样例
runs/                        保存的发现结果
.cache/                      GitHub/LLM 原始响应缓存，不提交
```

离线检查：

```bash
cd /Users/bytedance/PycharmProjects/RL_Coding_ENV/labs/04-candidate-discovery
python3 -m unittest -v test_discover_candidates.py
python3 verify_lab.py
```

## 3. 最小运行方式

扫描一个 repo 的全部 closed PR，并从中筛选 merged PR：

```bash
python3 discover_candidates.py \
  --repo more-itertools/more-itertools
```

GitHub 未认证 REST API 通常只有每小时 60 次请求。完整仓库扫描建议设置 token：

```bash
export GITHUB_TOKEN='<github-token>'

python3 discover_candidates.py \
  --repo more-itertools/more-itertools \
  --run-name full-scan
```

脚本不会把 token 写入 cache 或输出。

先做低成本有界试采集：

```bash
python3 discover_candidates.py \
  --repo more-itertools/more-itertools \
  --max-prs 20 \
  --comment-mode counts \
  --run-name latest-20
```

只复现已知 PR：

```bash
python3 discover_candidates.py \
  --repo more-itertools/more-itertools \
  --pr 720 \
  --run-name pr-720
```

按 merge 日期过滤：

```bash
python3 discover_candidates.py \
  --repo more-itertools/more-itertools \
  --merged-since 2023-01-01 \
  --merged-until 2023-12-31
```

GitHub 的 Pulls REST API 不提供 merged 日期服务端过滤。脚本会分页枚举 closed PR 后在
本地过滤；如果同时设置 `--max-pages`，结果只是有界采样，不能视为该日期范围的完整集合。

## 4. `iter_pulls`：定义扫描范围

默认入口使用：

```text
GET /repos/{owner}/{repo}/pulls
  ?state=closed
  &sort=created
  &direction=desc
  &per_page=100
  &page=N
```

closed PR 还包括未合并 PR，因此脚本继续检查 `merged_at`。`summary.json` 用
`scan_scope` 明确本次结果的口径：

| 值 | 含义 |
| --- | --- |
| `TARGETED` | 使用一个或多个 `--pr`，只检查指定 PR |
| `BOUNDED` | 使用 `--max-prs` 或 `--max-pages`，只做有界采样 |
| `FULL_REPOSITORY` | 分页到结尾，完整扫描 repo |

`--max-prs 20` 指日期过滤后最多检查 20 个 merged PR，不是前 20 个 closed PR。
`summary.json` 同时记录 `pulls_seen`、`not_merged` 和 `merged_prs_scanned`，保留每层分母。

## 5. 关联 issue 的定义

当前实现与 Lab 02 的 P0 契约一致，只接受 PR body 中显式出现的 closing keyword：

```text
Closes #719
Fixes owner/repo#719
Resolves https://github.com/owner/repo/issues/719
```

普通提及不算：

```text
Related to #719
See #719
```

这是一个刻意保守的 provenance 规则。GitHub sidebar 手工关联、commit message 关联和
跨仓库 issue 尚未进入 P0。没有本 repo 显式 closing reference 的 merged PR 写入：

```json
{
  "status": "REJECTED",
  "reason": {
    "code": "NO_LOCAL_CLOSING_ISSUE"
  }
}
```

一个 PR 可以显式关闭多个本 repo issue，此时输出多个 pair。每个 pair 都保留原始
`keyword` 和 `reference` 作为关联证据。

## 6. `file_features`：统计最终改动

对存在本地 closing issue 的 PR，脚本分页请求：

```text
GET /repos/{owner}/{repo}/pulls/{pr}/files
```

每个文件保存：

```json
{
  "path": "more_itertools/more.py",
  "status": "modified",
  "is_test": false,
  "additions": 11,
  "deletions": 10,
  "changes": 21
}
```

汇总分三层：

```text
all.changed_lines    = PR 全部 additions + deletions
source.changed_lines = 非测试路径 additions + deletions
tests.changed_lines  = 测试路径 additions + deletions
```

`changed_lines` 是 diff 行数，不是文件最终总行数，也不是净增长量：

```text
changed_lines = additions + deletions
net_lines     = additions - deletions
```

路径分类直接复用 Lab 02 当前规则：

```text
tests/**
test/**
spec/**
test_*.py
*_test.py
*_tests.py
```

若没有 source path 或没有 test path，则记录 `PATCH_NOT_SEPARABLE`，不进入
`candidates.jsonl`。这只是 P0 规则；文档、fixture、内联测试和其他语言仍需要 repo
adapter。

默认推荐源码改动不超过 300 行：

```json
{
  "within_recommended_source_change_limit": true,
  "recommended_source_change_limit": 300
}
```

该阈值只是一项排序特征，不会自动拒绝候选。这样后续可以比较不同阈值，而不必重新抓取
原始数据。

## 7. Comment 特征

默认 `--comment-mode full` 会采集四类 discussion：

| 字段 | GitHub API | 内容 |
| --- | --- | --- |
| `comments.issue` | issue comments | issue 下的需求澄清 |
| `comments.pr_conversation` | PR issue comments | PR 普通讨论 |
| `comments.pr_reviews` | PR reviews | approve/request changes 及 review body |
| `comments.pr_review_inline` | PR review comments | 代码行级评论 |

每条保留 id、作者、时间、正文、URL、association；行级评论额外保留 path 和 line。
PR/issue API 自带的 comment count 也会写入 CSV。

当只需要快速统计时：

```bash
--comment-mode counts
```

此时不会请求 comment body。语义审核依赖 PR 创建前的 issue 澄清，因此
`--semantic-mode llm` 强制要求 `--comment-mode full`。

时间切分很重要：

```text
original issue
  -> pre-PR issue comments
  -> PR created
  -> review / merge
  -> post-merge discussion
```

用于构造题目的澄清只能来自答案产生前的 discussion。PR review 往往包含实现细节，
可用于质量分析，但不能直接进入公开 problem statement。

## 8. 确定性信号与语义结论

无 LLM 时脚本仍完整工作，并保存便宜、可复现的文本信号：

```json
{
  "body_chars": 1561,
  "has_body": true,
  "has_code_fence": true,
  "has_expected_behavior_language": false,
  "has_observed_behavior_language": true,
  "mentions_ambiguity": true,
  "question_mark_count": 0
}
```

这些是 feature，不是“清晰/歧义”的真值。默认语义字段明确写成：

```json
{
  "status": "NOT_ASSESSED",
  "method": "none",
  "assessment": null
}
```

启用结构化 LLM 审核：

```bash
python3 discover_candidates.py \
  --repo more-itertools/more-itertools \
  --pr 720 \
  --semantic-mode llm \
  --model ep-20260510173911-8tf68 \
  --run-name pr-720-semantic
```

模型配置优先读取：

```text
ARK_API_KEY
ARK_MODEL
ARK_BASE_URL
```

未设置时可读取项目根目录未提交的 `LLM_ENDPOINT.md`。输出区分三个判断：

```text
original_issue
with_pre_pr_comments
pr_issue_alignment
```

每个判断都包含 label、confidence、rationale 和原文 evidence quote。模型还可以给出只描述
预期行为、不描述实现方式的 `behavioral_spec`，但它仍是待人工或执行验证的派生特征，
不能覆盖原始文本。

模型响应缓存键包含：

```text
model + prompt_version + semantic input
```

修改 prompt 后不会误用旧结论。模型调用失败只把该 pair 标为
`semantic_review.status=ERROR`，不会丢失已经采集的确定性特征。

## 9. 真实 #719/#720 样例

保存的语义样例位于：

```text
runs/more-itertools-pr-720-pro/
```

确定性结果：

| 特征 | 值 |
| --- | ---: |
| PR additions / deletions | 17 / 11 |
| source files | 1 |
| source additions / deletions | 11 / 10 |
| source changed lines | 21 |
| test files | 1 |
| test additions / deletions | 6 / 1 |
| test changed lines | 7 |
| issue comments | 1 |
| issue comments before PR | 1 |
| PR conversation comments | 2 |
| PR inline review comments | 3 |

语义结果：

```text
原始 issue：AMBIGUOUS
  - 修代码，使 window 指最近 n 个输入元素
  - 或保留实现，只把文档改成最近 n 个已 yield 元素

加入 PR 前 maintainer 评论：CLEAR
  - “Let's change it to your interpretation”
  - 选择最近 n 个输入元素

PR 与澄清需求：ALIGNED
```

这正是为什么不能只把 issue title/body 原样送给 agent。Lab 04 保留原文、澄清评论和语义
证据；后续 Task Compilation 可以据此编译无歧义的 behavioral spec，但必须人工抽查并由
Lab 02 的 T/G 执行证据确认。

## 10. 输出契约

每次 run 目录包含：

```text
runs/<run-name>/
├── candidates.jsonl
├── candidates.csv
├── rejected.jsonl
└── summary.json
```

`candidates.jsonl` 是完整记录，包含原始文本、comments、文件级统计和语义审核。
`candidates.csv` 是便于排序和分析的扁平视图。它包含：

```text
repo / PR / issue
source/test file count
source/test additions/deletions/changed_lines
issue/PR comment count
recommended scope flag
original/clarified clarity
PR alignment
semantic action
```

`rejected.jsonl` 当前可能包含：

| reason code | 含义 |
| --- | --- |
| `NO_LOCAL_CLOSING_ISSUE` | PR body 没有显式关闭本 repo issue |
| `PATCH_NOT_SEPARABLE` | 按 P0 路径规则无法同时得到 G/T |
| `FILES_TRUNCATED` | 文件分页结果与 PR changed_files 不一致 |
| `ISSUE_FETCH_FAILED` | linked issue 无法读取 |
| `LINKED_TARGET_IS_PR` | closing reference 指向另一个 PR |

`summary.json` 保存扫描参数、漏斗计数、拒绝原因分布、GitHub cache 命中、网络请求数和
最后一次 rate-limit 信息。

## 11. API 缓存与请求预算

GitHub 原始 JSON 响应保存在：

```text
.cache/github/<request-url-sha256>.json
```

cache 记录 URL、HTTP status、ETag、抓取时间和原始 JSON body，不记录 Authorization
header。默认优先使用 cache；`--refresh` 才重新请求。

可用 `--max-requests` 给一次实验设置硬预算：

```bash
python3 discover_candidates.py \
  --repo more-itertools/more-itertools \
  --max-prs 20 \
  --max-requests 50
```

脚本记录 `X-RateLimit-*` 响应头。如果额度耗尽，会输出 `_FAILED.json` 并停止，不把网络
失败误记为“没有候选”。

## 12. 从 Candidate 进入 Lab 02

每个 candidate 都生成下一阶段命令，例如：

```bash
python3 ../02-pr-task-compiler/compile_task.py \
  --repo more-itertools/more-itertools \
  --pr 720 \
  --issue 719
```

批量生产时应：

1. 先按确定性特征排序，例如 G/T 可分离、源码 changed lines、comment 数和 repo 属性；
2. 再做 LLM 语义审核与人工抽查；
3. 将保留 pair 逐个送入 Lab 02；
4. 合并 Lab 04 discovery funnel 与 Lab 02 execution funnel；
5. 分开报告 `DISCOVERED`、`VERIFIED` 和最终 episode 数。

## 13. 当前没有覆盖的部分

- GitHub sidebar 和 GraphQL `closingIssuesReferences`；
- 跨 repo issue；
- 非常规测试目录、内联测试和 fixture 分类；
- PR base/head 的真实 ancestry 与 merge 策略校验；
- 从历史编辑记录恢复 issue 在 PR 创建时的正文；
- license 文本审核、benchmark contamination 和任务去重；
- LLM semantic label 的人工校准集与 precision/recall；
- 自动调用 Lab 02 并汇总动态验证结果；
- 多 repo 并发与断点任务队列。

因此，本 lab 完成的是可审计的候选发现层，不是“一次静态扫描即可得到训练数据”。
