# OlivOS Docker

自动跟踪上游 [OlivOS](https://github.com/OlivOS-Team/OlivOS) Release，构建并发布多架构 Docker 镜像。工作流每 12 小时检查一次正式版、测试版和预装 OPK 插件是否更新。

## 支持架构

- `linux/amd64`
- `linux/arm64`

## 镜像地址

- Docker Hub：`cary17/olivos`
- GHCR：`ghcr.io/cary17/olivos`

镜像和 GitHub 仓库均为公开资源，普通用户拉取镜像或克隆仓库不需要仓库写入权限。

## 镜像版本

| 类型 | 正式版 | 测试版 | 说明 |
|---|---|---|---|
| full | `latest` | `testing` | OlivOS 核心和预装 OPK 插件 |
| core | `latest-core` | `testing-core` | 仅 OlivOS 核心，不预装 OPK |
| dev | `latest-dev` | `testing-dev` | 核心版加调试工具 |

每个上游版本还会发布不可移动的具体版本标签：

```text
v0.11.81
v0.11.81-core
v0.11.81-dev
```

`latest` 系列只指向正式版，Pre-release 只更新 `testing` 系列和对应具体版本标签。

## 快速开始

### 1. 创建持久化目录

```bash
sudo mkdir -p /opt/olivos/conf /opt/olivos/plugin
```

### 2. 获取 Compose 配置

可以克隆公开仓库：

```bash
git clone https://github.com/cary17/OlivOS-Docker.git
cd OlivOS-Docker
```

也可以只下载 Compose 文件：

```bash
curl -fsSLO https://raw.githubusercontent.com/cary17/OlivOS-Docker/main/docker-compose.yml
```

### 3. 启动

```bash
docker compose pull
docker compose up -d
```

### 4. 查看状态和日志

```bash
docker compose ps
docker compose logs -f olivos
```

默认挂载：

| 宿主机目录 | 容器目录 | 用途 |
|---|---|---|
| `/opt/olivos/conf` | `/app/OlivOS/conf` | OlivOS 配置和账号信息 |
| `/opt/olivos/plugin` | `/app/OlivOS/plugin` | 插件和插件数据 |

## 切换官方镜像版本

编辑 `docker-compose.yml` 中的 `image`：

```yaml
image: ghcr.io/cary17/olivos:latest
```

例如改为测试核心版：

```yaml
image: ghcr.io/cary17/olivos:testing-core
```

然后更新容器：

```bash
docker compose pull
docker compose up -d
```

## 使用派生镜像安装额外 Python 依赖

### 为什么使用派生镜像

额外 Python 依赖不再通过 `EXTRA_PACKAGES` 在容器启动时安装，而是在 Docker 镜像构建阶段安装。

这样可以保证：

- 依赖安装失败时只会导致构建失败，不会替换当前正在运行的容器；
- OlivOS 启动和重启时不需要访问 PyPI；
- 不会因为网络故障进入容器反复重启；
- 依赖可固定版本，环境更容易复现；
- 派生镜像会覆盖基础镜像入口，即使选用较旧基础镜像，也不会重新启用 `EXTRA_PACKAGES`。

> 从旧配置升级时，请删除 Compose、`.env` 或其他 override 文件中的 `EXTRA_PACKAGES`，并将依赖迁移到 `requirements-extra.txt`。

### 方法一：克隆公开仓库后构建（推荐）

用户不需要本仓库的写入权限：

```bash
git clone https://github.com/cary17/OlivOS-Docker.git
cd OlivOS-Docker
```

编辑 `requirements-extra.txt`，每行填写一个依赖，建议固定版本：

```text
psutil==5.9.0
lxml==5.4.0
```

构建派生镜像：

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.extra.yml \
  build
```

构建成功后启动：

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.extra.yml \
  up -d
```

生成的本地镜像名称为：

```text
olivos-custom:latest
```

检查依赖是否安装成功：

```bash
docker run --rm \
  --entrypoint python \
  olivos-custom:latest \
  -c 'import psutil, lxml; print("extra dependencies OK")'
```

### 选择派生镜像的基础版本

默认基础镜像为：

```text
ghcr.io/cary17/olivos:latest
```

基于 testing 构建：

```bash
OLIVOS_BASE_IMAGE=ghcr.io/cary17/olivos:testing \
docker compose \
  -f docker-compose.yml \
  -f docker-compose.extra.yml \
  build
```

基于 core 构建：

```bash
OLIVOS_BASE_IMAGE=ghcr.io/cary17/olivos:latest-core \
docker compose \
  -f docker-compose.yml \
  -f docker-compose.extra.yml \
  build
```

基于具体版本构建：

```bash
OLIVOS_BASE_IMAGE=ghcr.io/cary17/olivos:v0.11.81 \
docker compose \
  -f docker-compose.yml \
  -f docker-compose.extra.yml \
  build
```

构建时指定的 `OLIVOS_BASE_IMAGE` 只影响构建。以后执行 `up -d` 时使用已经生成的 `olivos-custom:latest`，不需要再次设置该变量。

### 更新派生镜像

上游基础镜像或 `requirements-extra.txt` 更新后，重新构建并替换容器：

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.extra.yml \
  build --pull

docker compose \
  -f docker-compose.yml \
  -f docker-compose.extra.yml \
  up -d
```

如果构建失败，现有容器仍保持运行。修复网络或依赖版本后重新执行 `build` 即可。

### 仅下载派生镜像所需文件

不想克隆完整仓库时，至少下载以下文件：

```bash
mkdir olivos-custom
cd olivos-custom

curl -fsSLO https://raw.githubusercontent.com/cary17/OlivOS-Docker/main/docker-compose.yml
curl -fsSLO https://raw.githubusercontent.com/cary17/OlivOS-Docker/main/docker-compose.extra.yml
curl -fsSLO https://raw.githubusercontent.com/cary17/OlivOS-Docker/main/Dockerfile.extra
curl -fsSLO https://raw.githubusercontent.com/cary17/OlivOS-Docker/main/entrypoint.sh
curl -fsSLO https://raw.githubusercontent.com/cary17/OlivOS-Docker/main/requirements-extra.txt
```

随后编辑 `requirements-extra.txt`，再按前面的 Compose 命令构建和启动。

### 不使用 Compose，直接构建

```bash
docker build \
  --build-arg BASE_IMAGE=ghcr.io/cary17/olivos:latest \
  -t olivos-custom:latest \
  -f Dockerfile.extra \
  .
```

运行时可继续使用原 Compose，只需把其中的镜像改为：

```yaml
image: olivos-custom:latest
```

并删除 `build:` 配置，或直接继续使用 `docker-compose.extra.yml`。

### 推送到用户自己的镜像仓库

用户可以把本地派生镜像推送到自己的 Docker Hub 或 GHCR，不需要本仓库的写权限：

```bash
docker tag olivos-custom:latest 用户名/olivos-custom:latest
docker push 用户名/olivos-custom:latest
```

其他机器随后可以直接使用：

```yaml
image: 用户名/olivos-custom:latest
```

## 常用维护命令

更新官方镜像：

```bash
docker compose pull
docker compose up -d
```

更新派生镜像：

```bash
docker compose -f docker-compose.yml -f docker-compose.extra.yml build --pull
docker compose -f docker-compose.yml -f docker-compose.extra.yml up -d
```

停止服务：

```bash
docker compose down
```

查看日志：

```bash
docker compose logs -f --tail=100 olivos
```

## 故障排查

### 派生镜像构建时无法连接 PyPI

配置 Docker 构建代理，或者在网络恢复后重新构建。例如：

```bash
HTTP_PROXY=http://127.0.0.1:7890 \
HTTPS_PROXY=http://127.0.0.1:7890 \
docker compose \
  -f docker-compose.yml \
  -f docker-compose.extra.yml \
  build
```

代理地址必须能从 Docker 构建环境访问；如果代理运行在宿主机回环地址但 BuildKit 无法访问，请改用宿主机实际地址或正确配置 Docker 代理。

### 修改依赖后没有生效

重新构建并替换容器：

```bash
docker compose -f docker-compose.yml -f docker-compose.extra.yml build --no-cache
docker compose -f docker-compose.yml -f docker-compose.extra.yml up -d
```

### 验证当前容器使用的镜像

```bash
docker inspect olivos --format '{{.Config.Image}}'
```

### 查看派生镜像内安装的包

```bash
docker run --rm --entrypoint python olivos-custom:latest -m pip list
```

## 脚本安装

国外网络：

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/cary17/OlivOS-Docker/main/OlivOS.sh)"
```

国内网络：

```bash
bash -c "$(curl -fsSL https://ghproxy.net/https://raw.githubusercontent.com/cary17/OlivOS-Docker/main/OlivOS.sh)"
```
