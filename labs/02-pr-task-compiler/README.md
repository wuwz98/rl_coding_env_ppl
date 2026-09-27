# Lab 02：PR Task Compiler MVP

Lab 00 解释 Docker 的执行模型，Lab 01 手工构造并验证 B/G/T。Lab 02 把这套手工过程
变成一条自动化流水线：

```text
输入 <repo, PR, issue>
  -> 冻结来源并提取 B/G/T
  -> 构建只包含 B 的 Task Image
  -> 验证 B、B+T、B+G+T
  -> 输出 VERIFIED task 或 _REJECTED.md
```

本 lab 使用真实任务：

```text
Repo:  more-itertools/more-itertools
Issue: #719 `unique_in_window` is unclear or wrong
PR:    #720 Fix unique_in_window to match described behavior
```

## 1. 当前边界

Lab 02 是 **Task Compiler**，输入是一个已经找到的候选三元组：

```text
<GitHub repository, merged PR number, linked issue number>
```

它不负责：

- 从 GitHub 批量发现候选，这属于后续 Lab 04；
- 运行 coding agent；
- 接收 agent 候选补丁 C 并计算正式 reward；
- RL rollout、轨迹采集或模型训练。

当前 MVP 还有意采用严格而简单的适用范围：

- 公开 GitHub 仓库；
- merged PR，且 PR body 显式关闭输入 issue；
- 假设 `base.sha` 是 `head.sha` 的祖先；
- G/T 可以按常见测试路径分离；
- Python 标准库 `unittest`；
- 测试位于 `tests/test*.py`；
- 项目不需要额外 Python 依赖或验证期网络请求。

这些不是 Task Compiler 的最终通用定义，而是本题 adapter 的支持范围。无法可靠处理的
候选应被拒绝，不应猜测。

## 2. 代码入口

Lab 02 已收敛为三个主要文件：

```text
compile_task.py       完整 Task Compiler
unittest_runner.py    容器内测试执行与逐 test ID 记录
test_compile_task.py  不联网的 helper tests
```

快速检查：

```bash
cd /Users/bytedance/PycharmProjects/RL_Coding_ENV/labs/02-pr-task-compiler
python3 -m unittest -v test_compile_task.py
```

完整入口：

```bash
python3 compile_task.py \
  --repo more-itertools/more-itertools \
  --pr 720 \
  --issue 719 \
  --repeats 3
```

主要调用链：

```text
main
└── compile_task
    ├── collect_metadata
    ├── extract_bgt
    ├── write_dockerfile
    ├── build_image
    ├── run_case: baseline
    ├── run_case: noop / gold，重复 3 次
    └── quality_gate
```

## 3. `collect_metadata`：冻结候选身份

`collect_metadata` 分别调用 GitHub Pull Request API 和 Issue API，然后检查：

1. repository 输入可归一化为 `owner/repo`；
2. PR 已经 merged；
3. 输入的 issue number 确实是 issue，不是另一个 PR；
4. PR body 包含 `closes/fixes/resolves #N`；
5. GitHub 返回完整的 base/head commit SHA。

本题冻结为：

```text
base.sha = c8baa10e66de18662114b71edcfa32f5dfdd2881
head.sha = 46a253b36b74edead1fea1c09d762ddca4e895fd
```

branch 名称会继续移动，commit SHA 才能作为可重放输入。

### Git 拓扑限制

本题中 `base.sha` 是 `head.sha` 的祖先，因此：

```text
B = base.sha
G+T = git diff base.sha head.sha
```

但一般 PR 可能在旧 commit M 分叉，而目标分支继续前进到 D：

```text
A -- M -- C -- D       base.sha = D
      \
       E -- F          head.sha = F
```

此时 `D` 不是 `F` 的祖先，PR 自身改动应从共同祖先计算：

```bash
M=$(git merge-base D F)
git diff "$M" "$F"
```

当前 MVP 尚未自动执行 ancestry gate，因此候选进入流水线前要满足简单线性历史。扩展时
应增加：

```bash
git merge-base --is-ancestor "$BASE_SHA" "$HEAD_SHA"
```

不满足时先输出 `BASE_NOT_ANCESTOR`，不要直接把 `git diff D F` 当作 PR 自身改动。

## 4. `extract_bgt`：生成宿主机资产

该函数在一个临时 Git repository 中：

```text
fetch base/head 精确 SHA
  -> 列出变更路径
  -> 测试路径进入 T
  -> 其他路径进入 G
  -> 检查 G/T 可以应用
  -> git archive B
```

本题分离结果：

```text
G: more_itertools/more.py
T: tests/test_more.py
B: base commit 的完整文件树
```

持久产物写入 task 目录：

```text
private/gold.patch       G
private/test.patch       T
build/source.tar.gz      B
```

临时 clone 随 `TemporaryDirectory` 删除；上面三份 task 资产继续保留。

`git archive B` 会打包 B 中所有被 Git 跟踪的文件，因此包含：

- 项目源码；
- B 原有测试；
- build 配置；
- 文档和许可证。

它不包含 `.git` object database 或未来 commit 历史。

### 当前 G/T 分类策略

`is_test_path` 只识别常见形式：

```text
tests/**
test/**
spec/**
test_*.py
*_test.py
*_tests.py
```

这只适合本题。Java Maven、Gradle、pytest 自定义路径或测试与源码混放的仓库，需要独立
adapter；无法可靠判断时应返回 `PATCH_NOT_SEPARABLE`。

## 5. `write_dockerfile`：定义无答案镜像

`write_dockerfile` 只生成：

```text
build/Dockerfile
```

它此时还没有运行 Docker，也没有复制文件。

真正执行构建的是 `build_image`：

```bash
docker build --tag <image-tag> tasks/<task-id>/build/
```

注意 build context 是 `build/`，其中只有：

```text
Dockerfile
source.tar.gz
```

`private/gold.patch` 和 `private/test.patch` 位于 build context 外，不可能被 Dockerfile
意外 COPY 进 image。

Dockerfile 的关键步骤：

```dockerfile
WORKDIR /workspace/repo
COPY source.tar.gz /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz -C /workspace/repo \
    && git init \
    && git add -A \
    && git commit -m "answer-free base snapshot"
```

最终 Task Image 中：

```text
/workspace/repo
├── B 的源码
├── B 原有测试
└── .git
    └── 一个新创建的 snapshot commit
```

这是一个合成 Git repository：

- 只有 B 的文件状态；
- 没有 G/T；
- 没有上游 Git 历史；
- 没有 GitHub remote。

## 6. `run_case`：在一次性 Container 中测试

Image 是不可变模板；每个 `run_case` 都创建一个新的 container：

```bash
docker run --detach \
  --network none \
  --name <unique-name> \
  <task-image> \
  sleep infinity
```

根据 case 类型，controller 再使用 `docker cp` 和 `git apply` 注入 patch：

| Case | Container 状态 | 期望 |
| --- | --- | --- |
| baseline | B | 原测试全部通过 |
| noop | B+T | 至少一个行为测试失败 |
| gold | B+G+T | 全部测试通过 |

`unittest_runner.py` 也通过 `docker cp` 放入当前 container 的 `/tmp`，不会修改 image。
container 删除后，runner 和 patch 修改同时消失。

测试命令：

```bash
docker exec --workdir /workspace/repo <container> \
  python3 /tmp/unittest_runner.py \
  --output /tmp/results.json
```

执行结束后：

```text
container:/tmp/results.json
    -> host: validation/<case>/results.json

docker exec 的 stdout/stderr
    -> host: validation/<case>/test.log
```

`finally` 中无条件删除 container，下一次 case 重新从原始 image(B) 开始。

## 7. `unittest_runner.py`：将 unittest 转成结构化结果

runner 使用：

```python
unittest.defaultTestLoader.discover(
    start_dir="tests",
    pattern="test*.py",
    top_level_dir=".",
)
```

它不是静态解析 Python 源码，而是 import 匹配的模块，通过 `unittest` loader 找到
`TestCase` 和测试方法并执行。

自定义 `Result(unittest.TextTestResult)` 监听：

```text
startTest
addSuccess
addFailure
addError
addSkip
addSubTest
stopTest
```

并输出：

```json
{
  "tests_run": 734,
  "complete": true,
  "counts": {
    "pass": 733,
    "fail": 1
  },
  "tests": [
    {
      "id": "tests.test_more.UniqueInWindowTests.test_basic",
      "status": "fail"
    }
  ]
}
```

`fail` 和 `error` 必须区分：

- `fail`：测试正常执行，断言不成立，可能是真实 bug；
- `error`：导入失败、语法错误或运行异常，通常表示测试或环境坏了。

subtest failure 被归一到父 test ID，避免一个参数化测试被错误统计成多个 F2P。

runner 的退出码：

```text
完整执行且全部通过 -> 0
存在 fail/error     -> 1
零测试或执行不完整  -> 1
```

`run_case` 使用 `check=False` 执行它，因为 noop 的退出码 1 是预期实验结果，而不是
controller 自身异常。

当前 runner 是 Python `unittest` adapter，不是通用 grader。其他生态应调用原生测试
工具并解析结构化报告，例如：

| 项目 | 测试命令 | 结构化结果 |
| --- | --- | --- |
| pytest | `pytest --junitxml=...` | JUnit XML |
| Maven | `mvn test` | `target/surefire-reports/TEST-*.xml` |
| Gradle | `./gradlew test` | `build/test-results/test/TEST-*.xml` |

## 8. `quality_gate`：接受或拒绝候选

完整编译执行：

```text
B                  baseline，1 次
B+T                noop-1
B+G+T              gold-1
B+T                noop-2
B+G+T              gold-2
B+T                noop-3
B+G+T              gold-3
```

总计 7 个独立 container。

门禁要求：

```text
baseline exit = 0
每次 noop exit = 1，且没有 error
每次 gold exit = 0
重复运行的逐 test ID 状态完全相同
F2P 非空
P2F 为空
```

集合定义：

```text
F2P = 在 B+T 失败、在 B+G+T 通过的 test ID
P2P = 在 B+T 和 B+G+T 都通过的 test ID
P2F = 在 B 通过、在 B+G+T 不再通过的 test ID
```

本题实测：

```text
baseline: 734 pass
noop x3: 733 pass, 1 fail
gold x3: 734 pass
F2P: tests.test_more.UniqueInWindowTests.test_basic
P2P: 733
P2F: 0
```

## 9. 成功与拒绝产物

成功目录：

```text
tasks/<task-id>/
├── public/
│   └── task.json
├── private/
│   ├── source.json
│   ├── gold.patch
│   ├── test.patch
│   └── unittest_runner.py
├── build/
│   ├── Dockerfile
│   ├── source.tar.gz
│   └── manifest.json
└── validation/
    ├── docker-build.log
    ├── summary.json
    ├── baseline/
    ├── noop-1/ ... noop-3/
    └── gold-1/ ... gold-3/
```

其中：

- `public/task.json` 是未来 agent 可以看到的任务说明与 image/workspace 信息；
- `private/` 是 controller-only 资产；
- `build/` 是 image 构建输入与记录；
- `validation/` 是 task 入库前的构建证据，不是 agent 轨迹。

如果任一阶段失败，partial task 会被删除，只输出：

```text
tasks/<task-id>/_REJECTED.md
```

当前已有成功样例：

```text
tasks/more-itertools__more-itertools-issue-719-pr-720/
```

以及三个拒绝样例：

```text
tasks/more-itertools__more-itertools-issue-718-pr-720/
tasks/more-itertools__more-itertools-issue-719-pr-710/
tasks/more-itertools__more-itertools-issue-730-pr-720/
```

## 10. 检查命令

查看 task：

```bash
TASK=tasks/more-itertools__more-itertools-issue-719-pr-720

python3 -m json.tool "$TASK/public/task.json"
python3 -m json.tool "$TASK/private/source.json"
python3 -m json.tool "$TASK/validation/summary.json"
```

检查 image 中只有一个合成 commit、没有 remote 和未提交修改：

```bash
IMAGE=rl-task:more-itertools__more-itertools-issue-719-pr-720

docker run --rm --network none "$IMAGE" sh -c '
  cd /workspace/repo
  git log --oneline --all
  git remote
  git status --short
  test ! -e /tmp/gold.patch
  test ! -e /tmp/test.patch
'
```

检查验证 container 无残留：

```bash
docker ps -a --filter name=rl-lab02
```

## 11. 当前实现没有覆盖的部分

- 自动发现候选 PR；
- 非线性 PR 历史和多种 GitHub merge 策略；
- Python 外部依赖解析和离线缓存；
- pytest、Maven、Gradle 等测试 adapter；
- 无法按路径分离的 G/T；
- issue、T、G 的自动语义审核；
- agent rollout；
- 候选补丁 C 的正式独立 grader；
- 恶意代码安全隔离；
- 数据集去重、污染检查和 split。

当前 `unittest_runner.py` 只是测试执行与结果记录器。完整 candidate grader 未来应执行：

```text
image(B)
  -> 应用 agent candidate C
  -> 恢复 T 涉及路径
  -> 注入 T
  -> 执行测试并检查 required test IDs
  -> reward 0/1
```

实测摘要见 [VALIDATION.md](VALIDATION.md)，三个 labs 的关系见
[Labs 总览](../README.md)。
