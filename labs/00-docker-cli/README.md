# Docker Lab 00：不用 Docker Desktop，先弄懂 Docker 在运行什么

这个 lab 假设你没有任何 Docker 经验。先不要构建 Python 项目，也不要背 Dockerfile。

你只需要解决一个问题：

> 当我在终端输入 `docker run ...` 时，到底是谁创建并运行了容器？

建议用时 35–45 分钟。全部操作都在 macOS 终端完成，使用 **Docker CLI + Colima**，不需要打开 Docker Desktop。

## 0. 先看全局：Docker 不是一个单独程序

在这台 Mac 上，完整链路是：

```text
你输入 docker 命令
    -> Docker CLI 客户端
    -> 通过 Unix socket 请求 Docker daemon
    -> daemon 运行在 Colima 创建的 Linux VM 中
    -> daemon 创建 Linux container
    -> container 中运行真正的进程
```

五个对象不要混在一起：

| 对象 | 是什么 | 本 lab 怎样看到它 |
| --- | --- | --- |
| Docker CLI | 把你的命令发送给 Docker API 的客户端 | `docker --version` |
| Docker daemon (`dockerd`) | 管理镜像、容器、网络和存储的后台服务 | `docker version` 的 Server |
| Linux VM | macOS 上承载 Linux 内核和 daemon 的虚拟机 | `colima status`、`colima ssh` |
| Image | 创建容器所用的只读模板 | `docker image ls` |
| Container | 由 image 创建的一次隔离运行实例 | `docker ps -a` |

为什么 macOS 需要 VM？主流 Docker 镜像中的程序依赖 Linux 内核能力，而 macOS 不是 Linux。Colima 基于 Lima 启动轻量 Linux VM，让 daemon 和 Linux 容器在里面运行。

Docker Desktop 也会提供 Linux VM、daemon、CLI 集成和图形界面，但它不是 Docker 协议的一部分，更不是服务器上的必需组件。

**Checkpoint 0：** 先口头回答：CLI 是不是 daemon？image 是不是正在运行的 process？macOS 为什么需要 Linux VM？

## 1. 检查并安装命令行组件

先看本机已有内容：

```bash
command -v brew
command -v docker
command -v colima
docker --version
```

这四条命令只检查可执行文件，不要求 daemon 已经运行。

本机目前已经有 Docker CLI，但尚未安装 Colima。执行：

```bash
brew install colima
```

如果另一台机器连 `docker` 命令也没有，再执行：

```bash
brew install docker colima
```

安装后确认：

```bash
docker --version
colima version
```

这里仍然只是安装了客户端和 VM 管理工具，还没有证明 daemon 正在运行。

**Checkpoint 1：** `command -v docker` 和 `command -v colima` 都返回路径；你能解释“命令存在”和“后台服务可用”不是同一件事。

## 2. 先观察 daemon 不存在时会发生什么

当前 Docker context 可能仍指向旧的 `desktop-linux`。先观察：

```bash
docker context ls
docker context show
docker version
```

`docker version` 的 Client 部分来自本机 CLI；如果 daemon 不可达，Server 部分会缺失，并出现 `Cannot connect to the Docker daemon`。

这个错误准确表达了当前状态：

```text
客户端存在
    +
客户端知道一个 daemon 地址
    +
该地址没有可响应的 daemon
```

它不表示 Python 项目坏了，也不表示镜像构建失败，因为构建请求还没有被任何 daemon 接收。

**Checkpoint 2：** 能根据 `docker version` 区分 Client 信息和 Server 信息，并解释为什么只有 Client 时不能运行容器。

## 3. 用 Colima 启动 Linux VM 和 Docker daemon

给本次学习环境分配 4 CPU、8 GiB 内存和 30 GiB 磁盘：

```bash
colima start --runtime docker --cpu 4 --memory 8 --disk 30
```

第一次启动会下载 Linux VM 镜像，因此需要网络并可能等待几分钟。完成后检查：

```bash
colima status
docker context ls
docker context show
docker version
```

Colima 通常会创建并切换到名为 `colima` 的 Docker context。如果当前不是它，执行：

```bash
docker context use colima
docker version
```

现在 `docker version` 应该同时有 Client 和 Server。

再分别观察宿主机与 VM：

```bash
uname -a
colima ssh -- uname -a
colima ssh -- pgrep -a dockerd
```

第一条显示 Darwin/macOS，第二条显示 Linux。第三条应看到 VM 中的 `dockerd` 进程。Docker CLI 仍运行在 Mac 上，daemon 则运行在 Linux VM 中。

**Checkpoint 3：** `colima status` 为 Running，当前 context 是 `colima`，`docker version` 有 Server，并能亲眼看到宿主机是 Darwin、VM 是 Linux。

## 4. 用一次 stop/start 证明 CLI 和 daemon 相互独立

停止 Colima：

```bash
colima stop
docker --version
docker version
```

预期现象：

- `docker --version` 仍然成功，因为 CLI 文件还在。
- `docker version` 仍能打印 Client，但 Server 连接失败，因为 daemon 随 VM 停止了。

重新启动：

```bash
colima start
docker version
```

现在 Server 恢复。这是理解 daemon 最直接的实验。

注意：`colima stop` 停的是整台本地 Linux VM；`docker stop <container>` 只停一个容器。两者不是同一层操作。

**Checkpoint 4：** 你已经实际观察到“CLI 一直存在，daemon 可以停止和恢复”。

## 5. 第一次拉取 image

清理本 lab 可能残留的同名容器：

```bash
docker rm -f docker-lab00-once docker-lab00-work 2>/dev/null || true
```

拉取一个很小的 Linux image。当前网络无法连接 Docker Hub，因此使用已验证可用的内部 registry 完整名称：

```bash
docker pull hub.byted.org/alpine:3.20
docker image ls hub.byted.org/alpine
docker image inspect hub.byted.org/alpine:3.20 \
  --format 'id={{.Id}} os={{.Os}} arch={{.Architecture}} size={{.Size}}'
```

`pull` 请求 daemon 从 registry 下载 image。CLI 自己不保存镜像层；镜像存储在 Colima 的 Linux VM 中。

image 是模板，不是正在运行的系统。此时：

```bash
docker ps -a
```

不会因为已经 pull 了 Alpine 就自动出现一个新容器。

镜像引用中的各部分是：

```text
hub.byted.org / alpine : 3.20
registry        repo     tag
```

省略 registry 写成 `alpine:3.20` 时，Docker 默认去 `docker.io/library/alpine:3.20` 查找。你拉取的内部镜像不会自动获得这个短名称，所以后续命令继续使用完整名称。`Digest` 是 registry 返回的内容摘要，比可变的 tag 更适合记录可复现环境。

**Checkpoint 5：** `docker image inspect` 显示 `os=linux arch=arm64`；能解释 pull 得到的是 image 而不是 container，并能指出镜像引用中的 registry、repo 和 tag。

## 6. 从 image 创建一个会退出的 container

执行：

```bash
docker run --name docker-lab00-once hub.byted.org/alpine:3.20 \
  sh -c 'echo "hello from container"; cat /etc/os-release; uname -m'
echo $?
docker ps
docker ps -a --filter name=docker-lab00-once
docker logs docker-lab00-once
docker inspect docker-lab00-once \
  --format 'status={{.State.Status}} exit={{.State.ExitCode}} image={{.Image}}'
```

`docker run` 实际做了三件事：

1. 根据 `hub.byted.org/alpine:3.20` 创建容器；
2. 启动容器里的 `sh -c ...` 进程；
3. 等主进程结束后，让容器进入 exited 状态。

容器不是必须长期运行的“小虚拟机”。容器主进程结束，容器就停止。停止后的容器仍保留元数据、日志和可写层，所以 `docker ps -a`、`logs`、`inspect` 仍能看到它。

**Checkpoint 6：** 命令退出码是 0；`docker ps` 看不到它，`docker ps -a` 显示 `Exited (0)`；`logs` 能读到刚才的输出。

## 7. 创建一个持续运行的 container

```bash
docker run -d --name docker-lab00-work hub.byted.org/alpine:3.20 sleep infinity
docker ps --filter name=docker-lab00-work
docker top docker-lab00-work
```

`-d` 表示在后台运行；`sleep infinity` 是容器主进程，所以容器会持续处于 running。

在这个已有容器中执行新命令：

```bash
docker exec docker-lab00-work sh -c 'echo first > /tmp/note'
docker exec docker-lab00-work cat /tmp/note
docker diff docker-lab00-work
```

`docker exec` 不会新建容器，只是在现有容器里启动额外进程。`/tmp/note` 写入这个容器自己的可写层；`docker diff` 会显示相对 image 的文件变化。

**Checkpoint 7：** `docker top` 能看到主进程，第二次 `exec` 能读到第一次写入的文件，证明多次命令共享同一个容器状态。

## 8. stop/start、重新创建、删除分别意味着什么

先停止再启动同一个容器：

```bash
docker stop docker-lab00-work
docker start docker-lab00-work
docker exec docker-lab00-work cat /tmp/note
```

文件仍在，因为 stop/start 没有删除容器可写层。

再从同一 image 创建另一个临时容器：

```bash
docker run --rm hub.byted.org/alpine:3.20 \
  sh -c 'if [ -e /tmp/note ]; then echo exists; else echo missing; fi'
```

应该输出 `missing`。两个容器共享 image，但各有自己的可写层。`--rm` 让这个临时容器在退出后自动删除。

最后清理 Lab 00：

```bash
docker rm -f docker-lab00-once docker-lab00-work
docker ps -a --filter name=docker-lab00
```

不要执行 `docker system prune -a`。它会清理当前 daemon 管理的其他未使用资源，不适合作为入门练习的默认命令。

**Checkpoint 8：** 能解释：

- stop/start 为什么保留 `/tmp/note`；
- 新容器为什么没有 `/tmp/note`；
- `--rm` 与手工 `docker rm` 的区别；
- image 为什么可以继续创建新容器。

## 9. 工业环境里也需要 Docker Desktop 吗？

通常不需要。

| 场景 | 常见运行方式 |
| --- | --- |
| macOS 本地开发 | Docker Desktop、Colima、OrbStack 或 Podman machine 提供 Linux VM |
| Linux 开发机/服务器 | 直接运行 Docker Engine，或使用 rootless Docker |
| Kubernetes 集群 | 节点通常运行 containerd、CRI-O 等容器 runtime，不需要 Docker Desktop |
| CI/环境构建 | Linux worker 上使用 Docker/BuildKit，或使用其他镜像构建器 |

MiMo 的 [DockerEnvironment](../../references/mimoagent/src/mimoagent/environments/docker.py) 调用的是 `docker run`、`docker exec`、`docker cp` 和 `docker rm`。它要求 Docker CLI 能连接一个兼容 daemon，但没有依赖 Docker Desktop 的图形界面。

因此本项目的本地映射是：

```text
MiMo / Python 控制器
    -> docker CLI
    -> Colima 中的 Docker daemon
    -> 任务容器
```

生产环境可能换成 Linux 主机、Kubernetes Pod 或其他容器 runtime，但“控制器提交动作，隔离环境执行，再返回观察和结果”的职责仍然存在。

## 10. 完成标准与命令速查

完成 Lab 00 后，不看正文回答：

1. Docker CLI、daemon、image、container、process 分别是什么？
2. 为什么 macOS 运行 Linux container 需要 VM？
3. 为什么 `docker --version` 成功，但 `docker run` 仍可能失败？
4. `docker run` 和 `docker exec` 的对象有什么不同？
5. 容器主进程退出后，日志和文件一定立即消失吗？
6. stop/start、删除并重建、停止 Colima 分别影响哪一层？
7. MiMo 的 Docker 后端为什么不关心 daemon 来自 Desktop 还是 Colima？

常用命令按对象记：

| 目的 | 命令 |
| --- | --- |
| 管理本地 Linux VM | `colima start/status/stop/ssh` |
| 选择 daemon | `docker context ls/use/show` |
| 检查客户端与服务端 | `docker version`、`docker info` |
| 管理 image | `docker pull`、`docker image ls/inspect/rm` |
| 创建和查看 container | `docker run`、`docker ps -a`、`docker inspect` |
| 在运行中 container 操作 | `docker exec`、`docker top`、`docker diff` |
| 读取主进程日志 | `docker logs` |
| 控制生命周期 | `docker stop/start/rm`、`--rm` |

七个问题能独立讲清，并且 Checkpoint 1–8 都实际观察过，再进入 [Lab 01：构建 Python 测试环境](../01-docker-python/README.md)。
