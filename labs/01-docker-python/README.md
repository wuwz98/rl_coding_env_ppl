# Docker 小项目：给一个 Python bug 准备可重复的考场

如果你还不能解释 Docker CLI、daemon、Linux VM、image 和 container 的区别，先完成 [Lab 00：不用 Docker Desktop，先弄懂 Docker](../00-docker-cli/README.md)。

完成 Lab 00 后再做这个练习，最后读 [MiMo 导读](../../docs/04-mimo-environment-guide.md)。建议用时 60–70 分钟，做到第 8 步后留时间读源码；第 9 步为可选复查。

材料已经通过 Colima 完成镜像构建和独立容器验证，详见 [验证记录](VALIDATION.md)。这只证明教程可执行，不代表你已经完成练习；仍请从第 0 步按顺序操作并观察每个 checkpoint。

先想象你收到一个问题：

> `largest([-8, -2, -5])` 应该返回 `-2`，现在却返回 `0`。请修复，同时保留已有的正数、混合数字和空列表行为。

修复本身很小。我们要练的是另一件事：怎样把这份代码放进一个稳定的环境，让别人每次都看到同一个 bug，并能用测试确认它被修复。

这个练习不需要装 pytest、Ray 或 MiMo。使用 Python 标准库 `unittest`，先把注意力放在容器和验收上。它是人工编写的教学题；下午再把相同过程换成真实 GitHub PR。`private_tests` 对学习者可见，只是在初始镜像中不可见。

做完后应得到四个可以现场展示的结果：

| 结果 | 证据 |
| --- | --- |
| 自己从 Dockerfile 构建 Python 镜像 | `docker image inspect rl-env-lab:v1` |
| 初始公开测试健康，但问题确实存在 | `baseline.json` 为 3 pass；`bug.json` 为 3 pass + 2 fail |
| 参考修复能解决问题且不破坏原行为 | `gold.json` 为 5 pass |
| 能解释什么会保留、什么会重置 | 重启保留可写层；新容器恢复镜像；bind mount 保留宿主文件 |

## 0. 确认 Lab 00 的 Colima 环境仍在运行（09:35–09:40）

下文代码块全部在 **Mac 的终端** 执行，按顺序逐块运行；不需要进入容器的交互 shell。不把整个文档一次粘贴成脚本，遇到预期失败时要停下来观察。

```bash
cd /Users/bytedance/PycharmProjects/RL_Coding_ENV/labs/01-docker-python
colima status
docker context show
docker version
```

应该看到 Colima 为 Running、Docker context 为 `colima`，并且 `docker version` 同时有 Client 和 Server。

如果 Colima 未运行：

```bash
colima start
```

如果 context 仍指向 `desktop-linux` 或其他不可用地址：

```bash
docker context use colima
docker version
```

再观察已有对象：

```bash
docker info --format 'server={{.ServerVersion}} os={{.OperatingSystem}} arch={{.Architecture}}'
docker ps -a
docker image ls
```

这里 `docker ps` 只显示运行中的容器，`docker ps -a` 也显示已经退出的容器。镜像和容器是两类对象，所以分别由 `docker image ls` 与 `docker ps -a` 查看。

项目材料已经准备好：

```text
src/stats.py                    有 bug 的初始代码
public_tests/test_stats.py       原有的 3 个测试
private_tests/test_regression.py 新增的 2 个问题测试，稍后才放入容器
solution/stats.py                参考修复，稍后才复制
Dockerfile                      镜像的构建说明
run_tests.py                    执行测试并生成 JSON 结果
verify_lab.py                   手工练习之后用的重复验证脚本
```

先读 [初始代码](src/stats.py) 和 [原有测试](public_tests/test_stats.py)，暂时不改文件。原有测试没有覆盖全负数输入，所以有 bug 的代码也可能通过它们。

**Checkpoint 0：** `docker context show` 输出 `colima`，`docker version` 同时有 Client/Server。否则回到 Lab 00 第 2–4 步，不继续假装做了容器实验。

## 1. 把代码与运行时装进镜像（09:40–09:50）

打开 [Dockerfile](Dockerfile)，把它理解为一份构建配方：

```dockerfile
FROM hub.byted.org/alpine:3.20
RUN apk add --no-cache python3
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
COPY src/stats.py ./stats.py
COPY public_tests/ ./tests/
COPY run_tests.py ./run_tests.py
RUN python -m unittest discover -s tests -v
CMD ["python", "run_tests.py", "--expect", "3"]
```

`FROM` 使用刚才已验证的内部 Alpine Linux 镜像；第一条 `RUN` 通过 Alpine 包管理器安装 Python。`WORKDIR` 让后续操作位于 `/app`；`COPY` 把指定文件放进镜像。第二条 `RUN` 在**构建时**运行已有测试；`CMD` 是以后启动容器时的默认命令。这个练习没有 Python 第三方依赖，因此没有 pip 安装步骤。下午换真实仓库时才在构建阶段安装它的固定依赖。

`.dockerignore` 将参考修复、隐藏测试和日志排除出构建上下文；Dockerfile 也只复制明确列出的文件。不要改成 `COPY . .` 后把所有题目资产一起送进去。

```bash
docker build -t rl-env-lab:v1 .
docker image ls rl-env-lab
docker image inspect rl-env-lab:v1 \
  --format 'id={{.Id}} os={{.Os}} arch={{.Architecture}} size={{.Size}}'
```

最后的 `.` 是构建上下文，也就是当前目录。第一次需要联网下载基础镜像，可能比后面的练习慢。

**应该看到：Alpine 安装 Python、3 个测试通过，镜像列表出现 `rl-env-lab:v1`。** 这里的 `v1` 是我们给本地镜像起的标签。`hub.byted.org/alpine:3.20` 仍是可变 tag，方便入门；正式任务要记录基础镜像 digest、依赖版本和最终镜像 ID，不能把 tag 当成永久冻结的环境。

停下来想一下：构建成功是否意味着 bug 不存在？不意味着，只说明当前三条测试没有发现它。

**Checkpoint 1：** build 末尾有 `Ran 3 tests` 和 `OK`；inspect 输出一个以 `sha256:` 开头的 image ID、`linux` 和你的容器架构。把 image ID 记下来，后续所有新容器都从这个只读初态开始。

## 2. 第一次运行：镜像怎样变成容器（09:50–10:00）

```bash
docker run --name rl-lab-baseline --network none --cpus 1 --memory 256m rl-env-lab:v1
echo $?
docker ps -a --filter name=rl-lab-baseline
docker inspect rl-lab-baseline \
  --format 'status={{.State.Status}} exit={{.State.ExitCode}} image={{.Image}}'
docker logs rl-lab-baseline
```

应该看到 `tests_run: 3`、`reward: 1`，紧接运行命令的 `echo $?` 为 `0`。测试执行完，容器处于 `Exited (0)`；这是正常结束。

`docker run` 根据镜像新建并启动一个容器。这里的默认命令就是 Dockerfile 的 `CMD`。`--network none` 限制运行期网络，`--cpus` 和 `--memory` 给练习设置上限。构建时联网与执行时断网是两回事。

现在把 JSON 结果取回宿主机：

```bash
mkdir -p artifacts
docker cp rl-lab-baseline:/tmp/results.json ./artifacts/baseline.json
cat artifacts/baseline.json
docker rm rl-lab-baseline
```

容器已经结束，仍能读取日志和复制文件；删除容器之后，这些容器内的文件才随之消失。取回宿主机的 `baseline.json` 会保留。

**Checkpoint 2：** `inspect` 显示 `status=exited exit=0`；`baseline.json` 中 `tests_run` 为 3、`complete` 为 `true`、`reward` 为 1；删除容器后 `docker ps -a --filter name=rl-lab-baseline` 不再列出它。

## 3. 为什么 agent 需要一个持续运行的容器（10:00–10:10）

agent 需要先读文件、再改代码、再测试，不能每执行一条命令就丢掉修改。因此启动一个持续运行的容器：

```bash
docker run -d --name rl-lab-work --network none --cpus 1 --memory 256m rl-env-lab:v1 sleep infinity
docker ps --filter name=rl-lab-work
docker top rl-lab-work
docker exec rl-lab-work pwd
docker exec rl-lab-work cat stats.py
docker exec rl-lab-work python -c 'from stats import largest; print(largest([-8, -2, -5]))'
```

应该看到 `/app`、初始代码以及错误结果 `0`。`sleep infinity` 覆盖镜像的默认命令，使容器保持运行；`-d` 让它在后台运行；`exec` 在**已有容器**里启动新的命令。

`run` 接镜像名，`exec` 接容器名。它们不是两种同义的“运行”。MiMo 的 Docker 后端正是沿用这一模式。

**Checkpoint 3：** `docker top` 能看到 `sleep infinity` 主进程；两次 `exec` 在同一个容器里工作；函数调用稳定输出错误值 `0`。

## 4. 补上真正能抓住问题的测试（10:10–10:20）

先证明初始镜像没有这两条测试，再由宿主机注入：

```bash
docker exec rl-lab-work ls tests
docker cp ./private_tests/. rl-lab-work:/app/tests/
docker diff rl-lab-work
docker exec rl-lab-work python run_tests.py --expect 5
echo $?
```

**这里应该失败。** 应看到 5 个测试被执行，其中 2 个 `FAIL`，退出码 `1`。这是测试成功复现问题，不是 Docker 坏了。

```bash
docker cp rl-lab-work:/tmp/results.json ./artifacts/bug.json
cat artifacts/bug.json
```

文件中原有 3 个测试仍是 `pass`，新增 2 个是 `fail`，`complete` 为 `true`，`reward` 为 `0`。`complete` 只表示测试按预期数量执行完整，并不表示代码正确。

此时不要依赖 `docker logs rl-lab-work` 找到刚才的测试输出：`logs` 主要读取容器主进程的日志；通过 `exec` 运行的测试输出在那次命令的终端中。这也是为什么我们另外保存 JSON 结果。

`docker diff` 站在镜像视角列出容器可写层中的变化：`A` 是新增、`C` 是修改、`D` 是删除。它不展示文本 diff 内容；代码仓库内部的源码差异仍应使用 `git diff`。

**Checkpoint 4：** 命令退出码是 1，但 `tests_run=5`、`complete=true`；失败来自两条明确断言，而不是缺包、收集失败或超时。这叫“有效失败”。

## 5. 改好代码，再看测试会发生什么（10:20–10:30）

可以先自己想怎样修，再查看 [参考修复](solution/stats.py)。修复把初始最大值从 `0` 改为第一个实际元素。

```bash
docker cp ./solution/stats.py rl-lab-work:/app/stats.py
docker exec rl-lab-work python run_tests.py --expect 5
echo $?
docker cp rl-lab-work:/tmp/results.json ./artifacts/gold.json
docker diff rl-lab-work
```

应该看到 5 个测试全部通过，退出码 `0`、`reward: 1`。注意我们只覆盖了这个容器中的源文件，没有修改宿主机的 `src/stats.py`，也没有重新构建镜像。

到这里可以解释两组测试了：两条新增测试从失败变成功，叫 F2P；三条原有测试一直成功，叫 P2P。模型不需要复制参考修复的文字，只需要满足测试所表达的行为要求。

这里先在同一个容器中观察前后变化。正式 PR 验证要让无修改与参考修复分别从干净容器开始，第 9 步会复查这一点。

**Checkpoint 5：** `gold.json` 有 5 个 `pass`、`complete=true`、`reward=1`；`docker diff` 同时能看到新增测试和修改后的 `/app/stats.py`。这是容器可写层，不是新镜像。

## 6. 重启为什么不等于重置（10:30–10:35）

先在当前容器留一个标记，然后停止再启动：

```bash
docker exec rl-lab-work touch /tmp/round-one
docker stop rl-lab-work
docker start rl-lab-work
docker exec rl-lab-work ls /tmp/round-one
docker exec rl-lab-work python -c 'from stats import largest; print(largest([-8, -2, -5]))'
```

标记还在，结果还是修好后的 `-2`。重启保留了容器可写层。

现在从原镜像创建一个新容器：

```bash
docker run --rm --network none rl-env-lab:v1 python -c 'from stats import largest; from pathlib import Path; print(largest([-8, -2, -5])); print(Path("/tmp/round-one").exists()); print(Path("tests/test_regression.py").exists())'
```

应该看到 `0`、`False`、`False`：bug 回来了，标记和新增测试都不在。这才是我们要的初态恢复。`--rm` 表示本次运行结束就删除新容器。

**Checkpoint 6：** 同一个容器 stop/start 后仍有修复和标记；从同一镜像新建的容器则回到 `0 / False / False`。因此 `restart != reset`，RL rollout 的 reset 应创建干净容器，而不是重启旧容器。

## 7. 哪些文件应该留下来（10:35–10:40）

任务修改需要清空，实验日志却要保留。把宿主机的日志目录挂载进去：

```bash
docker run --rm --network none --mount "type=bind,source=$(pwd)/artifacts,target=/results" rl-env-lab:v1 python run_tests.py --expect 3 --report /results/mounted.json
cat artifacts/mounted.json
docker inspect rl-lab-work --format '{{json .Mounts}}'
docker rm -f rl-lab-work
```

`source` 在 Mac，`target` 在容器。这个命令把测试结果直接写进 Mac 的 `artifacts`，所以容器删除后还能读取。没有挂载代码目录，初始代码仍来自镜像。

`COPY` 在构建时复制一份文件；bind mount 在运行时连接同一个外部目录。新建容器不会清空挂载目录，所以每个 rollout 的输出应该使用独立路径。

上面的 `inspect` 对 `rl-lab-work` 会输出 `[]`，因为它没有挂载；刚才带 `--rm` 的临时容器已经自动删除，不能再 inspect。这个对照提醒你：mount 是**创建某个容器时**声明的，不是镜像属性。

**Checkpoint 7：** 临时容器已经自动删除，但 `artifacts/mounted.json` 仍在宿主机；`rl-lab-work` 的 Mounts 是空数组。你已经区分了镜像内 COPY、容器可写层和宿主机 bind mount。

## 8. 到这里就可以读 MiMo 了（10:40 前后）

先不看笔记，用自己的话回答：

1. 为什么同一份镜像可以启动出互不影响的两次尝试？什么情况下挂载会破坏这种隔离？
2. `docker run` 和 `docker exec` 分别做了什么？
3. 为什么原有测试全部通过，仍可能没有修复用户的问题？
4. 为什么参考解和新增验收测试不能都放进 agent 的初始镜像？
5. 怎样区分“问题没有修好”和“测试根本没有正确执行”？

然后打开 [MiMo 导读](../../docs/04-mimo-environment-guide.md)。先认识完整执行过程，再找源码里的对应方法，不逐行啃完所有类。

把今天用过的常用命令按对象归类，而不是孤立背诵：

| 目的 | 命令 |
| --- | --- |
| 构建/查看镜像 | `docker build`、`docker image ls`、`docker image inspect` |
| 创建/查看容器 | `docker run`、`docker ps -a`、`docker inspect` |
| 在容器中操作 | `docker exec`、`docker cp`、`docker top`、`docker diff` |
| 观察结果 | `docker logs`、命令退出码、结构化 JSON |
| 生命周期 | `docker stop`、`docker start`、`docker rm`、`--rm` |

**Checkpoint 8：** 不看上表，能用自己的话解释 build、run、exec、logs、cp、inspect、stop/start、rm 和 mount 分别作用于哪个对象。解释不清的命令回到对应步骤再执行一次。

## 9. 可选：让脚本重复刚才的实验

完成手工步骤后再运行，预计几分钟；不要拿它代替前面的学习：

```bash
python3 verify_lab.py
```

脚本使用已构建的 `rl-env-lab:v1`，做一次原有测试基线、三组独立的 bug/gold 验证。每次新建容器，按测试 ID 检查结果，保存镜像 ID、架构和逐测试日志，最后删除自己的容器。

产物位于 `artifacts/<run_id>/`。脚本只操作名称以 `rl-lab-check-` 开头且由本次运行创建的容器，不清理你其他项目的 Docker 数据。没有使用 Ray；七次小验证足够先证明执行过程正确。

`run_tests.py` 是便于教学的结果记录器，测试数量检查不能替代正式 verifier 的完整性与隔离设计。它并不防御恶意代码。下午的真实 PR 需要保存完整版本信息、固定测试 ID，并在独立 grader 中验收候选补丁。

## 遇到卡点时先看这里

| 现象 | 先检查什么 |
| --- | --- |
| 无法连接 daemon | `colima status`；必要时 `colima start`，再确认 `docker context use colima` |
| 基础镜像下载失败 | 确认使用 `hub.byted.org/alpine:3.20`；registry 或网络失败发生在构建前，不是测试失败 |
| 提示容器名称已经存在 | 用 `docker ps -a --filter name=rl-lab` 看上次练习；只删除确认属于本练习的命名容器再重跑该步 |
| 第 4 步全通过 | 是否把宿主机 `src/stats.py` 提前修好或用了旧镜像；检查文件并重新 build |
| 第 5 步仍失败 | `docker cp` 的目标是否是 `/app/stats.py`；用 `exec ... cat stats.py` 确认 |
| 没有执行 5 个测试 | 检查测试是否复制到了 `/app/tests/`，不要因为退出码好看而跳过 |
| `exec` 提示容器没有运行 | 一次性测试容器本来会退出；第 3 步用 `sleep infinity` 启动工作容器 |

如果启动/拉取卡住超过 20 分钟，保留错误先排查；仍可以读代码，但不能把宿主机运行报告成容器验收完成。

官方材料按需查：[运行容器](https://docs.docker.com/engine/containers/run/)、[Dockerfile](https://docs.docker.com/get-started/docker-concepts/building-images/writing-a-dockerfile/)、[bind mount](https://docs.docker.com/engine/storage/bind-mounts/)。
