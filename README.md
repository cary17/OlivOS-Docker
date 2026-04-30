# OlivOS Docker

自动构建上游项目 [OlivOS](https://github.com/OlivOS-Team/OlivOS) 的多架构 Docker 镜像，每 12 小时检测新版本并自动触发构建。

## 支持架构

- `linux/amd64`
- `linux/arm64`
- `linux/arm/v7`

## 镜像地址

- Docker Hub: `cary17/olivos`
- GHCR: `ghcr.io/cary17/olivos`

## 使用方法

```bash
docker compose up -d
```

## 配置项

| 环境变量 | 说明 |
|---|---|
| `EXTRA_PACKAGES` | 启动时额外安装的 pip 包，空格分隔 |


脚本运行安装

国外
```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/cary17/OlivOS-Docker/main/OlivOS.sh)"
```
国内
```bash
bash -c "$(curl -fsSL https://ghproxy.net/https://raw.githubusercontent.com/cary17/OlivOS-Docker/main/OlivOS.sh)"
```
