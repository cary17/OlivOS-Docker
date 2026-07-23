# OlivOS Docker

自动构建上游项目 [OlivOS](https://github.com/OlivOS-Team/OlivOS) 的多架构 Docker 镜像，每 12 小时检测新版本并自动触发构建。

## 支持架构

- `linux/amd64`
- `linux/arm64`

## 镜像地址

- Docker Hub: `cary17/olivos`
- GHCR: `ghcr.io/cary17/olivos`

## 镜像标签

- 正式版：`latest`、`latest-core`、`latest-dev`、`v版本号`
- 测试版：`testing`、`testing-core`、`testing-dev`、`v测试版本号`

`latest` 系列标签只指向正式版。Pre-release 只会更新具体版本号标签和 `testing` 系列标签。

## 使用方法

```bash
docker compose up -d
```

## 额外 Python 依赖

默认容器启动时不会联网安装 Python 包。需要额外依赖时，将依赖写入 `requirements-extra.txt`，然后使用派生镜像配置构建：

```bash
docker compose -f docker-compose.yml -f docker-compose.extra.yml build
docker compose -f docker-compose.yml -f docker-compose.extra.yml up -d
```

例如：

```text
psutil==5.9.0
lxml==5.4.0
```

派生镜像默认基于 `ghcr.io/cary17/olivos:latest`。如需使用 core、dev、testing 或指定版本，可在构建时设置：

```bash
OLIVOS_BASE_IMAGE=ghcr.io/cary17/olivos:testing \
docker compose -f docker-compose.yml -f docker-compose.extra.yml build
```

依赖在镜像构建阶段安装。安装失败时构建会失败，原有可用容器不会因启动时网络故障进入重启循环；构建成功后的容器启动也不再依赖 PyPI 网络。

`core` 表示不预装 OPK 插件；`full` 预装 `opk.txt` 及 `opk/` 中列出的插件；`dev` 在核心镜像基础上增加调试工具。


脚本运行安装

国外
```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/cary17/OlivOS-Docker/main/OlivOS.sh)"
```
国内
```bash
bash -c "$(curl -fsSL https://ghproxy.net/https://raw.githubusercontent.com/cary17/OlivOS-Docker/main/OlivOS.sh)"
```
