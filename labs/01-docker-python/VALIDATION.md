# 教学材料验证记录

日期：2026-09-25。以下是准备材料时的检查，不表示学习者已经完成练习。

## 已完成

在宿主机 Python 中，每个案例使用独立临时目录，运行与镜像中相同的源文件、测试和 `run_tests.py`：

| 案例 | 实测结果 | 退出码 / reward |
| --- | --- | --- |
| 初始代码 + 原有测试 | 3 pass | 0 / 1 |
| 初始代码 + 全部测试 | 3 pass、2 fail | 1 / 0 |
| 参考修复 + 全部测试 | 5 pass | 0 / 1 |
| 缺失新增测试，仍要求执行 5 条 | 只执行 3 条，complete=false | 1 / 0 |
| 零测试 | complete=false | 1 / 0 |
| 跳过测试 | complete=false | 1 / 0 |
| 错误修复 | 行为测试失败 | 1 / 0 |

本地原始证据：[summary.json](artifacts/author-host-check/summary.json)。这些结果验证教学 bug 与测试记录器，**不等价于 Docker 内运行成功**。

另外检查了 Python 语法、教程中 Bash 命令块语法、文档本地链接和代码围栏；MiMo 阅读摘录的 SHA-256 与来源清单一致。

`run_tests.py` 按容器布局从当前目录的 `tests/` 收集测试，不能直接在 lab 根目录执行；宿主机复核也必须先在临时目录中构造 `/app` 的等价布局。否则 Python 可能误发现全局环境中另一个同名 `tests` 包，这正说明执行目录也是环境契约的一部分。

## Colima 容器验证

早期直接从 Docker Hub 拉取 `python:3.11-slim` 时超时：

```text
failed to resolve source metadata for docker.io/library/python:3.11-slim
Head https://registry-1.docker.io/v2/library/python/manifests/3.11-slim:
context deadline exceeded
```

用户随后安装并启动 Colima，Docker CLI 成功连接 VM 中的 Docker Engine。内部镜像 `hub.byted.org/alpine:3.20` 验证为 `linux/arm64`，digest 为：

```text
sha256:0a4eaa0eecf5f8c050e5bba433f58c052be7587ee8af3e8b3910ef9ab5fbe9f5
```

`hub.byted.org/python:3.11-slim` 的 manifest 内容不完整，未将其写进 Dockerfile。当前 Dockerfile 改为从内部 Alpine 镜像出发，通过可用的 Alpine 软件源安装 Python 3.12.13。

在 `colima` context 下实测：

| 检查 | 结果 |
| --- | --- |
| `docker build -t rl-env-lab:v1 .` | 成功；构建阶段 3 个公开测试通过 |
| 最终 image | `linux/arm64`，ID `sha256:65fcfd467e4a02ab725a98071ac7988bf7e80f125a45265b8f85004e766b9f83` |
| baseline | 3 pass，退出码 0，reward 1 |
| bug，独立容器重复 3 次 | 每次 3 pass、2 fail，退出码 1，reward 0 |
| gold，独立容器重复 3 次 | 每次 5 pass，退出码 0，reward 1 |
| 容器清理 | `rl-lab-check-*` 无残留 |

容器原始证据：[summary.json](artifacts/9076a82d1419/summary.json) 和同目录逐次 JSON/log。该结果证明教程可执行，不表示学习者已经亲手完成 Lab 00/01。
