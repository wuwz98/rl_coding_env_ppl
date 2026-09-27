# MiMo 环境导读：一个 coding agent 的命令怎样变成 reward？

先接着上午的 [Docker Lab 00](../labs/00-docker-cli/README.md) 和 [Python Lab 01](../labs/01-docker-python/README.md) 想一个问题。

你已经能在容器里复现 `largest([-8, -2, -5])` 返回 `0` 的 bug，也能复制参考修复，让五条测试全部通过。现在把鼠标和键盘交给一个 coding agent：它自己读文件、改代码、执行测试。

你需要补齐的，是一套能够反复回答下面三个问题的程序：

1. 这次尝试在哪里运行，从什么代码开始？
2. agent 发出一条命令以后，怎样拿到真实执行结果？
3. 它说“修好了”以后，怎样验证，并生成 reward？

**MiMo 的这部分代码，把“怎样执行命令”和“怎样定义、验收一道题”分成了两层。** 先理解两层怎样协作，就能读懂主线；不需要先学 Kubernetes 或逐行读完整个仓库。

本文沿用 step-prepare 的 [SWE-bench 导读](../../../step-prepare/resources/01_papers/reading_guide/swe_bench_guide.md)、[Self-Instruct 导读](../../../step-prepare/resources/01_papers/reading_guide/self_instruct_guide.md)和 [Toolformer 导读](../../../step-prepare/resources/01_papers/reading_guide/toolformer_guide.md) 的方式：从一个具体任务出发，先看数据长什么样，再走完整机制，最后带着问题回源码。第一遍建议用 60–75 分钟。

依据为 XiaomiMiMo/mimoagent 的 `mimo-oss` 分支，固定 commit [`467f0a19016f`](https://github.com/XiaomiMiMo/mimoagent/tree/467f0a19016f0ac4d63b8d17a1f0da9ba07f232c)。本地 [源码摘录](../references/mimoagent/README.md) 保留了原文件、许可证与内容校验值，后面的阅读不依赖网页行号变化。

## 1. 先确定读到哪里就够

| 阅读层次 | 今天需要掌握什么                                           | 到什么程度                                        |
| ---- | -------------------------------------------------- | -------------------------------------------- |
| 必须   | 一条 `opensource-code` 任务记录                          | 能把字段分成题目、执行初态和验收三组                           |
| 必须   | `DockerEnvironment`                                | 能把 start/execute/copy\_to/cleanup 对应到上午的 CLI |
| 必须   | `DatasetEnvironment` 与 `OpenSourceCodeEnvironment` | 能解释谁转发命令，谁注入测试，谁产生 reward                    |
| 必须   | 一次 episode 的完整生命周期                                 | 能从 start 讲到 cleanup，并区分模型失败与设施失败             |
| 第二遍  | Git 历史截断、测试路径恢复                                    | 理解为什么做，不必读完全部 helper                         |
| 暂时跳过 | rubric judge、其他 dataset、Kubernetes、batch runner    | 不影响今天理解 Docker 环境主线                          |

这篇导读不负责教你安装整套 MiMo，也不声称本地摘录可以直接运行。对于当前目标，只读本文和四份本地源码摘录已经足以理解单任务的 Docker 执行与 programmatic reward 主线；它不能覆盖完整 runner、配置加载、模型调用、Kubernetes 后端和 7k+ 数据资产。

## 2. 为什么有一个 Docker 镜像还不够？

上午的 `rl-env-lab:v1` 确定了 Python、源文件和原有测试。它能保证另一次运行仍然遇到那个全负数 bug。

但镜像本身不知道用户想修什么。它也不知道哪两条测试能够证明全负数问题已经解决，更不知道什么时候应该评分。

所以，一道任务至少要把三件事连起来：

```text
题目：全负数列表应该返回实际最大值
初态：含有 bug 的代码和所需依赖
验收：问题测试通过，既有行为不回归
```

MiMo 的 [opensource\_code.py](../references/mimoagent/src/mimoagent/environments/datasets/opensource_code.py) 为这一类任务定义了一个数据接口。把上午的教学题写成相同形状，大致如下。**这是教学映射，不是 MiMo 发布数据中的真实记录：**

```json
{
  "dataset_type": "opensource-code",
  "docker_image": "rl-env-lab:v1",
  "cwd": "/app",
  "instance_id": "docker-lab-largest-negative",
  "problem_statement": "largest 对全负数输入应返回实际最大值",
  "test_patch": "<新增两条 regression tests 的 unified diff>",
  "test_command": "python run_tests.py --expect 5",
  "verifier_timeout_sec": 60
}
```

先按意义把字段分成三组：

- `docker_image`、`cwd`：到哪个环境、哪个目录工作。
- `instance_id`、`dataset_type`、`problem_statement`：这是哪道题、用哪种适配器、让 agent 做什么。
- `test_patch`、`test_command`、`verifier_timeout_sec`：评分时加入什么、怎样执行、最多等多久。

不用背字段名。你要看出来：**题目、执行初态和验收资产一起定义了任务，但它们不应该全部原样交给模型。**

`problem_statement` 可以成为 agent 的任务输入；`test_patch` 属于评分资产，只在 reward 阶段出现。`docker_image` 提供的是修复前初态，不应含参考修复或隐藏测试。

这是通用 coding 任务接口，不能从这份代码推断所有 MiMo 任务都使用同样的判分方式。官方[发布说明](https://mimo.mi.com/docs/zh-CN/news/latest/v2-6)提到 7k+ 多领域环境；我们这里读到的是框架接口，没有拿到并验收完整的 7k+ 环境数据包。

### 2.1 把 SWE-bench 的 B/T/G/C 映射到 MiMo

先复习 [SWE-bench FAQ](review/01-swe-bench-faq.md) 中的严格定义，再看 MiMo 中对象落在哪里：

| SWE-bench 对象                  | MiMo `opensource-code` 中在哪里    | 是否给 agent             |
| ----------------------------- | ------------------------------ | --------------------- |
| B：base commit 的完整仓库状态         | 已经物化在 `docker_image` 的 `cwd` 中 | 是                     |
| Runtime 与 tools               | 已经安装在 `docker_image` 中         | 是                     |
| public tests                  | 如果存在，本来就在 B 中                  | 是                     |
| problem statement             | `problem_statement`            | 是，作为任务文本              |
| T：hidden tests/verifier patch | `test_patch`                   | rollout 时否；reward 时注入 |
| G：gold/reference patch        | 不在这条任务 row 和 reward 路径中        | 否                     |
| C：candidate/model patch       | reward 前从工作区相对 HEAD 捕获         | 作为结果保存                |

这里最容易读错的地方有三个。

**第一，B 不是** **`docker_image`** **字段中的一段文本。** 上游 builder 已经把 B、Runtime 和工具做进 image；MiMo 直接启动它，不负责从 GitHub checkout base commit。

**第二，G 不参与正式 reward。** 它应该已经在环境生产阶段证明 B+G+T 可通过。MiMo 收到的是已构造任务，只评价 agent 当前产生的 C。

**第三，MiMo 当前捕获的是完整 model diff。** `base.py` 的 `_capture_model_diff` 执行 `git add -A` 后对 `HEAD` 做 `git diff`。本地摘录中的这条路径没有实现“只允许修改 `src/**`”的 Candidate allowlist；修改范围政策要由任务、runner 或更外层系统补充。

### 2.2 为什么任务 row 没有 `base_commit` 字段？

这份接口把 image 中真实的 Git `HEAD` 当作唯一事实来源。`_setup_dataset_specific` 在 agent 工作前执行：

```text
确认 cwd 是 Git work tree
    -> git rev-parse HEAD
    -> 保存为不可变的 _base_ref
    -> 检查是否有 base ancestry 之外的可达 commit
```

如果 image 只有源码、没有 `.git`，它会在容器中 `git init`，把当前文件提交为 baseline，再把该 commit 当 `_base_ref`。

这样避免 task row 写一个 SHA、image 却装着另一个 SHA。但代价是：正确 B、依赖、答案清理和完整 Git 对象清理仍然必须由上游 builder 保证。

## 3. 两层环境：一个负责干活，一个负责出题和验收

先看调用关系，不急着读构造函数的每个参数：

```text
任务记录
  -> OpenSourceCodeEnvironment：知道题目怎样准备和验收
  -> DatasetEnvironment：提供公共生命周期、转发与外层奖励处理
  -> DockerEnvironment：把命令变成 docker CLI 调用
  -> 容器里的代码和进程
```

`DockerEnvironment` 只解决执行问题：启动容器、执行 shell 命令、复制文件和清理。它不知道全负数结果应该是 `-2`。

它也不知道 daemon 来自哪里。只要 `docker` CLI 当前连接到兼容的 Docker daemon，同一份实现可以在本机 Colima、Linux 开发机或远程 Docker context 上工作；源码没有调用 Docker Desktop 图形界面。

`OpenSourceCodeEnvironment` 负责这一类题目的准备和评分。它继承 `DatasetEnvironment`，把底层执行环境放在 `self.env` 中。到 [base.py](../references/mimoagent/src/mimoagent/environments/datasets/base.py) 搜索 `def execute`，会发现核心只是把命令继续交给 `self.env.execute(...)`。

因此它**不是 DockerEnvironment 的子类**。它持有一个执行后端，通过这个后端操作容器。这也是将来可以更换执行后端，而保留任务验收逻辑的原因。

注册表中的 `opensource-code` 对应哪个类，可以最后看 [datasets/__init__.py](../references/mimoagent/src/mimoagent/environments/datasets/__init__.py) 的 `DATASET_REGISTRY`。第一遍不用追完所有数据集类型。

先用一个表固定职责：

| 层                           | 输入                            | 输出                           | 不负责什么                   |
| --------------------------- | ----------------------------- | ---------------------------- | ----------------------- |
| `DockerEnvironment`         | image、cwd、shell command       | output、returncode、容器文件变化     | 不理解 issue，不判断修复是否正确     |
| `DatasetEnvironment`        | 底层环境、任务记录                     | setup、命令转发、外层 reward 结果      | 不定义某个数据集的具体测试命令         |
| `OpenSourceCodeEnvironment` | `test_patch`、`test_command` 等 | 代码题的 setup 与 verifier reward | 不从 GitHub 自动找 PR，也不构建镜像 |

对象组合可以用下面的伪代码理解。它表达职责，不是需要直接运行的完整 MiMo API：

```python
base_env = DockerEnvironment(
    image=instance["docker_image"],
    cwd=instance["cwd"],
)
env = OpenSourceCodeEnvironment(base_env, instance)

env.setup_environment()
observation = env.execute("cat target.py")
reward, test_output, extra = env.calculate_reward()
env.cleanup()
```

实际系统通过 registry/factory 选择 dataset adapter。`create_from_registry(...)` 根据 `dataset_type` 把已经创建的 `base_env` 包成 `OpenSourceCodeEnvironment`。完整的 `make_dataset_env` 工厂不在本地摘录中，不影响今天理解下面的运行主线。

## 4. 先走完一条 episode，再看每个方法

把 agent 换成一个非常机械的操作者，一次完整过程是：

```text
setup_environment
  -> DockerEnvironment.start：从 image 创建持续运行的容器
  -> _setup_dataset_specific：确认 Git 工作树、记录 base ref、检查历史

agent 工作
  -> execute("cat ...")：观察代码
  -> execute("...edit...")：修改文件
  -> execute("...test...")：运行可见测试

calculate_reward
  -> 捕获 agent 相对 HEAD 的 model patch
  -> 恢复 test_patch 涉及的路径
  -> 注入并 git apply test_patch
  -> 执行 test_command
  -> exit code 0 => reward 1，否则 reward 0
  -> finally 再清理 test patch 涉及的路径

cleanup
  -> 删除本次容器
```

把文件状态按时间展开：

| 时刻          | 源码                | public tests   | T/隐藏测试          | Git 基准              |
| ----------- | ----------------- | -------------- | --------------- | ------------------- |
| image 初态    | B 中的 bug source   | B 原有版本         | 不存在             | image 的 HEAD        |
| agent 工作后   | B + agent 修改      | 也可能被 agent 修改  | 仍不存在            | HEAD 不变，worktree 改变 |
| 捕获 C 时      | agent 最终 worktree | 包含 agent 的所有改动 | 尚未注入            | `git diff HEAD`     |
| reward 执行时  | 保留 agent 源码修改     | T 涉及路径先恢复到 B   | 应用 `test_patch` | `_base_ref`         |
| reward 结束后  | agent 源码修改仍在      | T 涉及路径再次恢复     | 被移除/恢复          | `_base_ref`         |
| 下一条 rollout | 从 image 新建另一容器    | 回到 B           | 不存在             | image 的 HEAD        |

注意：reward 前的路径恢复只针对 **T 触及的文件**。它不是把 agent 整个工作区 reset 到 B，否则 C 也会被删除。

这里有两个不同的“恢复”：

- reward 前后只恢复 **T 涉及的测试路径**，目的是可靠注入/移除验收资产，同时保留 agent 的源码修改。
- 下一条 rollout 要从 image 新建容器，恢复 **整个任务初态**。

理解这两个范围不同，比记住 helper 名更重要。

## 5. agent 的一轮操作，怎样变成 Docker 命令？

现在打开 [docker.py](../references/mimoagent/src/mimoagent/environments/docker.py)。只沿四个动作读。

先看 `DockerEnvironmentConfig`，不要只盯着 image：

| 配置            | 作用                   | 需要留意                     |
| ------------- | -------------------- | ------------------------ |
| `image`       | Task Image 名称        | image 必须已经正确构建           |
| `cwd`         | 每次命令的默认工作目录          | 应指向 B 的仓库根目录             |
| `env`         | 固定注入容器命令的环境变量        | 明文配置不应放秘密                |
| `forward_env` | 从控制器选择性转发宿主环境变量      | 转发凭据有泄漏风险                |
| `timeout`     | 单条命令默认超时             | 不等于 episode 总预算          |
| `executable`  | Docker CLI 可执行文件     | 默认 `docker`，因此可连接 Colima |
| `run_args`    | 追加到 `docker run` 的参数 | 网络、CPU、内存限制需要显式配置        |

### 5.1 开始工作：先准备一个一直活着的容器

找 `start` 和 `_start_container`。你会看到它拼出类似下面的命令：

```bash
docker run -d --name <本次生成的名字> -w <工作目录> <镜像> sleep infinity
```

这是源码行为的简化示意，不是需要粘贴执行的命令。

它与你上午启动 `rl-lab-work` 的方式相同：先让容器持续运行，之后的多条命令共享这个容器里的文件修改。镜像在这里已经存在或可以拉取；**这个类不会从 GitHub PR 自动构建镜像**。环境生产是更上游的工作。

### 5.2 收到动作：在已有容器中执行命令

接着找 `execute`。假设 agent 想读文件，传入 `cat stats.py`，后端会组织成类似：

```bash
docker exec -w /app <容器ID> bash -c 'cat stats.py'
```

源码通过 `subprocess.run` 执行 Docker CLI，将 stdout 和 stderr 合并收集，返回包含 `output` 与 `returncode` 的字典。

一次结果的最小形状是：

```json
{
  "output": "命令的 stdout 与 stderr 合并文本",
  "returncode": 0
}
```

因此这一层没有结构化 test result、reward 或 resolved；它只报告“这个 shell process 怎样结束”。

agent 的观察由这里产生：读文件得到源码，运行错误得到报错，执行测试得到测试结果。后端提供这些真实反馈；模型是否会利用反馈，是 agent 策略的事情。

此时问自己：如果某条命令 `cd` 进了另一个目录，下次调用是否还在那个目录？不一定。这里每次开启新的 `bash -c`，工作目录由每次 `exec -w` 指定；**文件修改会保留，shell 的临时状态不会自动保留。**

这里还有一个具体镜像契约：源码调用的是 `bash -c`，所以 Task Image 必须存在 `bash`。我们上午的 Alpine toy image 默认只有 `sh`，没有安装 bash，因此它不能不加修改地交给这个 MiMo Docker backend。这正说明“能被 `docker run` 启动”和“满足某个 harness 的环境协议”是两件事。

### 5.3 需要注入文件：通过复制传送

再找 `copy_to`。它检查宿主文件、准备目标目录，再调用 `docker cp`。这对应上午把问题测试和参考修复复制进容器的动作。

注意调用者决定复制什么。Docker 后端不判断“这是隐藏测试还是答案”，这种边界由上层任务流程维护。

### 5.4 一次尝试结束：清理容器

最后找 `cleanup`，它发起删除本次容器的 Docker 命令。你应该能把这四个动作对应到上午用过的命令，而不需要记住每个重试或编码选项。

`run_args` 可以传入额外的容器参数，资源与网络限制需要具体配置。不要仅因为使用了这个类，就推断它默认断网或拥有严格的超时进程回收。这里 Python 的 subprocess timeout 与容器内进程是否被彻底终止，是两个需要分别验证的问题；实现正式执行器时再处理。

另外，`cleanup` 使用后台的 `docker rm -f ... &` 发起异步删除。它适合作为轻量清理，但若控制器必须确认资源已经回收，还需要额外等待或检查，不能只根据方法返回就断言容器已消失。

## 6. 什么时候加入测试，为什么不能一开始全放进去？

回到 [opensource\_code.py](../references/mimoagent/src/mimoagent/environments/datasets/opensource_code.py)，找 `_do_calculate_reward`。

上午你先让容器运行原有测试，之后才把两条问题测试复制进去。MiMo 的这一适配器有相似的时间边界：agent 工作期间，隐藏测试与验收脚本不放在它能读取的文件系统中；评分时再通过 `test_patch` 注入。

原因很实际：在这个实现中，agent 与 grader 共用同一个执行环境。若验收脚本从一开始就在镜像中，agent 可以直接读取它。仅把路径叫作 `private` 不会形成隔离。

评分路径可以先记成三个动作：

```text
恢复测试补丁会触及的路径
          ↓
完整应用 test_patch
          ↓
执行 test_command，取得执行结果
```

源码中的完整顺序其实是五步：

```text
1. 从 test_patch 的 diff header 提取所有 touched paths
2. 对每个路径恢复到 _base_ref：
   - B 中原本存在 -> git checkout _base_ref -- <path>
   - B 中不存在   -> 从 index 和工作区删除 agent 预创建的同名文件
3. 将 test_patch 通过临时文件复制到 /tmp/_opensource_code_test.patch
4. 执行 git apply --verbose，只接受完整应用
5. 删除临时 patch；reward 结束时再次恢复 touched paths
```

为什么新增文件要单独删除？假设 T 要新增：

```text
tests/test_regression.py
```

agent 在 rollout 中提前创建了同名文件。因为该路径在 B 中不存在，普通 `git checkout B -- tests/test_regression.py` 无法恢复它；如果不先删除，`git apply` 会报文件已存在。MiMo 将这种冲突按 testbed 问题处理，而不是让一个并非由修复质量造成的 patch apply 失败直接污染 reward。

这里的“恢复”不是把整个仓库恢复到初态。否则 agent 刚修好的代码也会被抹掉。它只处理测试补丁涉及的路径，让验收资产能可靠地安装进去，同时保留候选源码修改。

构建侧必须保证测试资产与允许修改的源码范围分清，否则“恢复测试路径”也可能影响候选解。这正是下午拆分源码修复 G 和测试补丁 T 的原因。

还要注意：这里的 `test_patch` 不只可以包含 tests，也可以包含执行 tests 的 verifier script。源码注释明确要求两者一起在 reward 时出现，避免 verifier 提前留在 image 中被 agent 读取。

## 7. 一个 reward=1 到底保证了什么？

继续看同一个方法：这个 verifier 分支根据验收命令的退出码给出二值结果，同时记录退出码、耗时和是否 resolved 等信息。

`DatasetEnvironment.calculate_reward` 先在 T 出现前捕获 C：

```bash
git add -A
git -c core.fileMode=false diff HEAD
```

因此保存的 `model_patch` 是 agent 工作区相对 B/HEAD 的完整 diff，不包含随后注入的 T。之后才进入 `_do_calculate_reward`。

`OpenSourceCodeEnvironment._do_calculate_reward` 的返回形状是：

```python
(
    reward,
    test_output,
    {
        "verifier_returncode": rc,
        "resolved": reward == 1.0,
        "test_duration": duration,
        "test_command": test_command,
    },
)
```

最简单的 programmatic 模式下：

```text
test_command return code == 0 -> reward = 1.0
其他 return code              -> reward = 0.0
```

外层再把 `model_patch` 放入 `extra` 返回。**G 从头到尾没有进入这条 reward 路径。**

但是，一个脚本即使没有真正执行测试，也可能退出 0。因此 **适配器负责执行评分流程，验收脚本本身必须保证测试真的执行完整、问题修好、没有目标范围内的回归。**

上午的 `run_tests.py` 检查测试数量和跳过情况，是一个很小的示范。正式任务还需检查冻结的测试 ID、逐测试状态、异常和超时，不能只匹配日志里的 `PASS`。

再到 `base.py` 找 `calculate_reward`，你会看到外层还会捕获模型补丁、处理异常，并根据配置组合 verifier 或 rubric judge 的结果。第一遍沿“使用测试 verifier”的路径读即可；不要把内部 `_do_calculate_reward` 的二值规则误认为所有配置下的最终奖励规则。

两个失败也值得分开理解：

- 测试正常执行，断言仍然失败：候选修复未满足要求。
- 测试补丁无法应用、环境损坏或传输出错：这次运行缺少有效验收，训练和统计需要保留故障原因。

如果把后者全当成模型能力不足，后续得到的成功率与训练信号都会混入基础设施噪声。

源码当前对基础设施错误的表示需要精确理解：

- 测试路径恢复或 T 应用失败：返回 `reward=0.0`，并带 `error_category="reward/testbed_corrupted"`。
- `TransportError`：外层返回 `reward=0.0`，并带 `transport_error=true`。
- 其他异常：外层也回退为 `reward=0.0`，错误文本放在输出中。

所以 MiMo 的返回标量本身没有使用 `reward=null`。训练器或统计系统必须查看 `extra` 中的错误类别，将 testbed/transport fault mask、重试或单独统计；不能只消费那个 `0.0`。

### 7.1 MiMo 的 reward=1 与 SWE-bench resolved 有什么关系？

MiMo adapter 只信任 `test_command` 的退出码。因此：

```text
MiMo adapter 的职责：
  按正确时机注入 T
  执行 verifier
  将进程结果转成 reward

test_command/verifier 的职责：
  确认 expected F2P 全通过
  确认 expected P2P 全通过
  确认 required test IDs 没有 missing/skip/error
  在完整性成立时才 exit 0
```

如果 `test_command` 只是 `echo PASS`，MiMo 仍会给 reward=1。这不是 adapter 能自动发现的问题。可信 reward 是“环境流程正确”与“verifier 设计正确”的共同产物。

## 8. 为什么镜像还需要清理 Git 历史？

先想象一个漏洞：题目要求修复 bug，但容器里保留了修复后的上游分支。agent 不用分析问题，只要查看 Git 历史，就可能找到人类答案。

`_setup_dataset_specific` 会准备工作树，记录初始 commit，并检查是否还有初态祖先链之外可达的提交。这个检查帮助发现未来分支或标签带来的答案泄漏。

不过它只是便宜的检查，不会自动证明镜像每个角落都没有答案。源码开头明确把初态准备、历史清理、无答案残留等条件交给构建侧。完整 Git 对象清理与磁盘资产检查仍然属于我们的 pipeline。

所以，下午不能把完整上游 clone 直接当作 agent 的工作目录。控制器保留完整历史用于构造任务，交给 agent 的环境只包含它应该看到的初态。

## 9. 把上午实验和 MiMo 接起来

| 上午实际做的事                     | MiMo 中对应的职责                            |
| --------------------------- | -------------------------------------- |
| build 初始镜像                  | 上游构建 pipeline；不在 DockerEnvironment 内完成 |
| 说明全负数输入的 bug                | `problem_statement`                    |
| `run -d ... sleep infinity` | `start / _start_container`             |
| 多次 `exec` 读代码、运行测试          | `execute`                              |
| 评分时复制新增测试                   | 通过 `test_patch` 注入验收资产；这里实际使用 Git 补丁   |
| 根据真实测试结果判断成功                | verifier 及其外层奖励流程                      |
| 删除容器，再从镜像创建                 | 执行环境生命周期；恢复下一次尝试的初态                    |

这是一张概念对应表，**上午的镜像并不能直接填进 MiMo 数据行后运行**。它没有准备 Git 和 MiMo 所要求的任务记录、统一 diff、验收脚本等条件，也没有安装 MiMo 框架。现在先学清流程，下午再补真实 PR 的任务资产。

我们的正式方案还准备从 agent 导出候选源码补丁，在另一个干净 grader 容器中重放验收。这与本文适配器共享工作区、评分时恢复测试路径的做法存在差别；阅读源码是借鉴接口与设计，不是必须逐项照搬。

### 9.1 MiMo 当前实现与我们的 P0 方案哪里不同？

| 维度                | MiMo `opensource-code` 当前路径      | 本项目 P0 计划                                   |
| ----------------- | -------------------------------- | ------------------------------------------- |
| Rollout 与 grading | 同一个容器、同一工作区                      | Rollout 与 Grader 使用不同干净容器                   |
| C 的获得             | reward 前捕获完整 `git diff HEAD`     | 导出 raw diff 后做 Candidate path policy        |
| T 的出现             | reward 时恢复路径并注入                  | 新 Grader Container 中注入                      |
| 判定入口              | `test_command` 的退出码              | verifier 解析冻结 F2P/P2P 和完整性后决定退出码/reward     |
| 测试路径冲突            | 按 `_base_ref` 恢复 T touched paths | 新容器天然没有 rollout 残留，再校验 T 应用                 |
| infra error 标量    | 通常仍返回 0.0，依靠 `extra` 分类          | 计划在结果层显式区分 `reward=null/infra_error`        |
| 下一次尝试             | cleanup 后重新创建环境                  | `reset` 从同一 Task Image 新建 Rollout Container |

这不表示 MiMo 的方案错误。它用较少容器切换完成 rollout 与 reward，效率和实现都更直接；本项目为了更容易解释信任边界，选择额外的 clean-room replay。

### 9.2 这四份源码没有覆盖什么？

读完后应明确哪些仍是上游工作：

- 从 GitHub issue/PR 选择候选；
- 确定准确 B，拆分 G/T；
- 构建并发布无答案 Task Image；
- 用 G 验证任务可解并冻结 F2P/P2P；
- 创建完整 agent prompt 和模型循环；
- 对 raw diff 执行 allowed-path policy；
- 大规模 session 调度、trajectory 保存和训练；
- 7k+ 数据资产的下载、字段分布与质量报告。

所以 MiMo 这几份代码回答的是：

> 已经有任务记录和 Task Image 后，怎样给 agent 一个持续的执行环境，并在终局运行 verifier。

它不回答完整的 environment production pipeline。

## 10. 现在带着问题读源码，而不是从第一行读到最后一行

| 时间预算  | 去哪里                                                      | 只需找到的答案                      |
| ----- | -------------------------------------------------------- | ---------------------------- |
| 15 分钟 | 本文第 1–4 节 + `opensource_code.py` 顶部说明                    | B/T/G/C 在哪里？一次 episode 怎样走完？ |
| 15 分钟 | `docker.py` 的 start、execute、copy\_to、cleanup             | 上午四种操作怎样被 Python 包装？         |
| 20 分钟 | 本文第 6–8 节 + `_capture_model_diff`、`_do_calculate_reward` | C/T 的先后顺序、路径恢复和错误分类是什么？      |
| 10 分钟 | `base.py` 的 setup\_environment、execute、calculate\_reward | 谁启动后端，谁转发命令，谁处理外层评分？         |
| 10 分钟 | 本文第 9 节                                                  | MiMo 当前路径与独立 grader 方案有何不同？  |

第一遍只读下列方法，按调用顺序而不是文件顺序：

| 顺序 | 文件与方法                                         | 只回答一个问题                     |
| -- | --------------------------------------------- | --------------------------- |
| 1  | `docker.py: start / _start_container`         | 容器怎样创建并保持运行？                |
| 2  | `docker.py: execute / copy_to / cleanup`      | 命令、文件和生命周期怎样映射到 CLI？        |
| 3  | `base.py: setup_environment / execute`        | 上层怎样启动和转发到底层环境？             |
| 4  | `opensource_code.py: _setup_dataset_specific` | 为什么要记录 base ref 并检查 Git 历史？ |
| 5  | `base.py: calculate_reward`                   | 外层怎样捕获 patch、异常和附加信息？       |
| 6  | `opensource_code.py: _do_calculate_reward`    | T 怎样注入，退出码怎样成为 reward？      |

先跳过 rubric judge 内部、其他数据集适配器、Kubernetes 后端、复杂 anti-hack 清理和训练器。遇到不懂的 helper，先看输入、输出与调用目的，第二遍再深入实现。

最后不看正文回答这十个问题：

1. B、Runtime、T、G、C 分别位于 MiMo 的哪个对象或阶段？
2. 为什么 `opensource-code` row 没有 `base_commit`，`_base_ref` 从哪里来？
3. `execute` 返回什么，为什么执行层不应该直接判断问题是否解决？
4. 为什么每次 `execute("cd /tmp")` 不会让下一次命令自动留在 `/tmp`？
5. C 在 T 注入之前还是之后捕获？为什么顺序重要？
6. T 新增的文件若被 agent 提前创建，MiMo 怎样处理？
7. reward 前后恢复测试文件，与下一次 rollout 重置整个环境有什么区别？
8. 如果 `test_command` 退出 0 却没有执行问题测试，应该在哪一层发现？
9. testbed/transport error 在源码中怎样表示，为什么不能只看 reward 标量？
10. MiMo 同容器 grading 与本项目独立 Grader Container 各自怎样工作？

第 10 题的核心不是“MiMo 写错了”，而是威胁模型和工程取舍不同：同容器注入更直接，独立 grader 的信任边界更清楚，但要额外处理 patch 导出与重放。

## 11. 一页复习卡

**一句话。** MiMo 用 `DatasetEnvironment` 把“题目与评分”包在 `DockerEnvironment` 这样的执行后端外面，让 agent 可以反复执行真实命令，并在终局把工作区状态转成 reward。

**任务 schema。** `docker_image + cwd` 定义执行初态，`problem_statement` 定义任务，`test_patch + test_command + timeout` 定义验收。

**B/T/G/C。** B 和 Runtime 已物化在 image 中；T 在 reward 时注入；G 不进入正式 reward；C 在 T 注入前由 `git diff HEAD` 捕获。

**Docker 主线。** `run -d ... sleep infinity` 创建持续容器；`exec` 执行动作并返回 output/returncode；`cp` 传文件；`rm -f` 清理。

**本机运行时。** 本项目用 Colima 提供 Linux VM 和 Docker daemon；MiMo 只调用 Docker CLI，不依赖 Docker Desktop。

**Reward 主线。** 捕获 model patch，恢复测试路径，注入 T，执行 verifier；退出 0 得 1，否则得 0。这个二值结果是否可信，取决于 verifier 是否检查测试完整性；infra fault 还要结合 `extra` 分类。

**两种 reset。** reward 阶段只恢复测试路径；新 rollout 要恢复整个环境初态。

**没有覆盖。** Docker 类不负责抓 GitHub、构建 task image 或定义正确测试；这条路径没有独立 grader 或显式 Candidate allowlist；框架接口不等于 7k 环境数据包；本地摘录也不是可运行安装。

能够用上午的 `largest` 例子讲清上面要点和十个自测问题，就达到本轮目标。下一步按 [RL Coding Env 论文导读](05-rl-coding-env-paper-guide.md) 阅读 DeepSeek-V3.2 的任务生产部分。
