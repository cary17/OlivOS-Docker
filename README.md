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

## 配置项

| 环境变量 | 说明 |
|---|---|
| `EXTRA_PACKAGES` | 启动时额外安装的 pip 包，空格分隔；生产环境建议使用固定版本的派生镜像 |

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
