#!/bin/bash

set -e

# ==================== 颜色定义 ====================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'

print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_header() {
    echo -e "\n${CYAN}========================================${NC}"
    echo -e "${CYAN}$1${NC}"
    echo -e "${CYAN}========================================${NC}"
}

# ==================== 全局状态变量 ====================
ENV_CHECKED=false
REGISTRY_CHECKED=false
IMAGE_SELECTED=false
IMAGE_BASE="cary17/olivos"
IMAGE_TAG="latest"
BUILD_TYPE="full"
NAPCAT_UID=""
NAPCAT_GID=""

# ==================== 权限检查 ====================
check_root() {
    if [ "$EUID" -ne 0 ]; then
        print_error "请使用 root 权限运行此脚本（sudo）"
        exit 1
    fi
}

# ==================== 网络检测 ====================
test_connectivity_time() {
    local url="$1"
    local timeout="${2:-3}"
    curl -s -o /dev/null -w "%{time_total}" --connect-timeout "$timeout" "$url" 2>/dev/null || echo "99"
}

# ==================== 检查是否已安装 ====================
is_installed() {
    if [ -f "/opt/olivos/docker-compose.yml" ]; then
        return 0
    else
        return 1
    fi
}

has_any_account() {
    if [ -f "/opt/olivos/conf/account.json" ]; then
        local count=$(python3 -c "
import json
with open('/opt/olivos/conf/account.json') as f:
    data = json.load(f)
print(len(data.get('account', [])))
" 2>/dev/null || echo "0")
        [ "$count" -gt 0 ] && return 0 || return 1
    else
        return 1
    fi
}

require_installed() {
    if ! is_installed; then
        print_warning "尚未安装 OlivOS，请先添加账号完成安装。"
        return 1
    fi
    return 0
}

# ==================== 环境检测和安装（延迟执行）====================
ensure_environment() {
    if [ "$ENV_CHECKED" = true ]; then
        return 0
    fi
    
    print_header "环境检测与安装"
    
    # 安装基础工具
    print_info "安装基础工具..."
    if command -v apt &> /dev/null; then
        apt update -y && apt upgrade -y
        apt install -y vnstat curl nftables bc python3
    elif command -v yum &> /dev/null; then
        yum update -y && yum install -y vnstat curl nftables bc python3
    fi
    print_success "基础工具安装完成。"

    # 安装 Docker
    print_header "Docker 安装"
    
    if command -v docker &> /dev/null && docker info &> /dev/null 2>&1; then
        print_success "Docker 已安装，跳过安装步骤。"
        if ! docker compose version &> /dev/null 2>&1; then
            apt install -y docker-compose-plugin 2>/dev/null || true
            yum install -y docker-compose-plugin 2>/dev/null || true
        fi
    else
        print_info "Docker 未安装，开始安装..."
        
        local direct_time=$(test_connectivity_time "https://get.docker.com" 5)
        local aliyun_time=$(test_connectivity_time "https://www.aliyun.com" 5)

        print_info "get.docker.com 响应: ${direct_time}s, 阿里云响应: ${aliyun_time}s"

        if (( $(echo "$direct_time > 3" | bc -l) )) || (( $(echo "$aliyun_time < $direct_time" | bc -l) )); then
            print_info "使用阿里云镜像安装 Docker..."
            curl -fsSL 'https://get.docker.com' | bash -s docker --mirror Aliyun
        else
            print_info "使用官方源安装 Docker..."
            curl -fsSL 'https://get.docker.com' | bash
        fi

        systemctl enable docker && systemctl start docker
        apt install -y docker-compose-plugin 2>/dev/null || yum install -y docker-compose-plugin 2>/dev/null || true
        print_success "Docker 安装完成。"
    fi

    # 配置 Docker 镜像加速
    print_info "配置 Docker 镜像加速..."
    mkdir -p /etc/docker

    local aliyun_time=$(test_connectivity_time "https://registry.cn-hangzhou.aliyuncs.com" 3)
    local dockerhub_time=$(test_connectivity_time "https://registry-1.docker.io" 3)

    print_info "阿里云镜像: ${aliyun_time}s, Docker Hub: ${dockerhub_time}s"

    if (( $(echo "$aliyun_time < 3" | bc -l) )) && (( $(echo "$aliyun_time < $dockerhub_time" | bc -l) )); then
        cat > /etc/docker/daemon.json << EOF
{
  "registry-mirrors": ["https://registry.cn-hangzhou.aliyuncs.com"],
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
        print_success "使用阿里云镜像加速"
    else
        cat > /etc/docker/daemon.json << EOF
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
        print_info "Docker Hub 连接正常，跳过镜像加速"
    fi

    systemctl daemon-reload && systemctl restart docker
    print_success "Docker 配置完成"
    
    # 获取系统信息
    NAPCAT_UID=$(id -u)
    NAPCAT_GID=$(id -g)
    print_info "当前用户 UID=$NAPCAT_UID, GID=$NAPCAT_GID"
    
    # 初始化目录
    print_info "初始化目录结构..."
    mkdir -p /opt/olivos/conf
    mkdir -p /opt/olivos/data
    mkdir -p /opt/olivos/plugin
    mkdir -p /opt/olivos/logfile
    
    if [ ! -f "/opt/olivos/conf/account.json" ]; then
        echo '{"account":[]}' > /opt/olivos/conf/account.json
    fi
    print_success "目录初始化完成"
    
    ENV_CHECKED=true
}

# ==================== 镜像仓库选择（仅首次）====================
ensure_registry() {
    if [ "$REGISTRY_CHECKED" = true ]; then
        return 0
    fi
    
    print_info "测试镜像仓库连接速度..."
    local dh_time=$(test_connectivity_time "https://registry-1.docker.io" 5)
    local ghcr_time=$(test_connectivity_time "https://ghcr.io" 5)

    print_info "Docker Hub: ${dh_time}s, GHCR: ${ghcr_time}s"

    if (( $(echo "$dh_time < $ghcr_time" | bc -l) )); then
        print_success "选择 Docker Hub"
        IMAGE_BASE="cary17/olivos"
    else
        print_success "选择 GHCR"
        IMAGE_BASE="ghcr.io/cary17/olivos"
    fi
    
    REGISTRY_CHECKED=true
}

# ==================== 版本选择 ====================
select_image_version() {
    print_header "OlivOS 镜像版本选择"
    echo ""
    echo -e "  ${GREEN}1) full (生产版)${NC}    - 包含 OlivOS 核心 + 官方插件包，开箱即用"
    echo -e "  ${YELLOW}2) core (核心版)${NC}    - 仅包含 OlivOS 核心，不含预装插件"
    echo -e "  ${BLUE}3) dev  (开发版)${NC}    - 包含开发调试工具（vim, curl, htop 等）"
    echo ""
    read -p "请选择版本 [1/2/3] (默认:1): " version_choice

    case "$version_choice" in
        2) BUILD_TYPE="core" ;;
        3) BUILD_TYPE="dev" ;;
        *) BUILD_TYPE="full" ;;
    esac

    echo ""
    read -p "指定具体版本？(例: 0.11.81，留空使用 latest): " specified_version
    specified_version=$(echo "$specified_version" | sed 's/^v//')

    if [ -n "$specified_version" ]; then
        IMAGE_TAG="v${specified_version}"
        [ "$BUILD_TYPE" != "full" ] && IMAGE_TAG="${IMAGE_TAG}-${BUILD_TYPE}"
    else
        IMAGE_TAG="latest"
        [ "$BUILD_TYPE" != "full" ] && IMAGE_TAG="latest-${BUILD_TYPE}"
    fi

    print_success "镜像: ${IMAGE_BASE}:${IMAGE_TAG}"
    IMAGE_SELECTED=true
}

# ==================== 随机字符串 ====================
generate_token() {
    cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w "${1:-16}" | head -n 1
}

# ==================== 从日志中提取 WebUI Token ====================
get_napcat_webui_token() {
    docker logs napcat 2>&1 | grep -oP 'WebUi Token: \K[a-f0-9]+' | tail -1 || echo ""
}

# ==================== 账号管理函数 ====================
has_qq_accounts() {
    if [ -f "/opt/olivos/conf/account.json" ]; then
        python3 -c "
import json
with open('/opt/olivos/conf/account.json') as f:
    data = json.load(f)
has = any(a.get('platform_type')=='qq' and a.get('sdk_type')=='onebot' for a in data.get('account',[]))
print('true' if has else 'false')
" 2>/dev/null || echo "false"
    else
        echo "false"
    fi
}

read_accounts() {
    [ -f "/opt/olivos/conf/account.json" ] && cat "$_" || echo '{"account":[]}'
}

write_accounts() {
    echo "$1" > /opt/olivos/conf/account.json
}

add_qq_account() {
    # 第一次添加账号时触发环境安装
    if ! is_installed; then
        ensure_environment
        ensure_registry
    fi
    
    print_header "添加 QQ 账号 (NapCat + Onebot)"

    mkdir -p /opt/napcat/config
    mkdir -p /opt/napcat/QQ_DATA

    local existing_count=0
    if [ -f "/opt/olivos/conf/account.json" ]; then
        existing_count=$(python3 -c "
import json
with open('/opt/olivos/conf/account.json') as f:
    data = json.load(f)
count = sum(1 for a in data.get('account',[]) if a.get('platform_type')=='qq' and a.get('sdk_type')=='onebot')
print(count)
" 2>/dev/null || echo "0")
    fi

    local webui_port=$((6099 + existing_count))

    read -p "请输入 QQ 号: " qq_id
    if [ -z "$qq_id" ]; then
        print_error "QQ 号不能为空！"
        return 1
    fi

    if [ -f "/opt/napcat/config/onebot11_${qq_id}.json" ]; then
        read -p "QQ $qq_id 的配置已存在，是否覆盖？[y/N]: " overwrite
        if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
            return 1
        fi
    fi

    read -p "Onebot HTTP 端口 [默认:3000]: " http_port
    http_port=${http_port:-3000}

    for config in /opt/napcat/config/onebot11_*.json; do
        [ -f "$config" ] || continue
        local ep=$(python3 -c "import json;print(json.load(open('$config'))['network']['httpServers'][0]['port'])" 2>/dev/null)
        if [ "$ep" = "$http_port" ]; then
            http_port=$((ep + 1))
            print_warning "端口被占用，自动分配: $http_port"
        fi
    done

    read -p "Access Token [留空自动生成]: " access_token
    if [ -z "$access_token" ]; then
        access_token=$(generate_token 16)
        print_info "已生成 Token: $access_token"
    fi

    cat > "/opt/napcat/config/onebot11_${qq_id}.json" << EOF
{
  "network": {
    "httpServers": [
      {
        "enable": true,
        "name": "olivos",
        "host": "0.0.0.0",
        "port": $http_port,
        "enableCors": true,
        "enableWebsocket": false,
        "messagePostFormat": "array",
        "token": "$access_token",
        "debug": false
      }
    ],
    "httpSseServers": [],
    "httpClients": [
      {
        "enable": true,
        "name": "OlivOSMsgApi",
        "url": "http://olivos:55001/OlivOSMsgApi/qq/onebot/default",
        "reportSelfMessage": false,
        "messagePostFormat": "array",
        "token": "",
        "debug": false
      }
    ],
    "websocketServers": [],
    "websocketClients": [],
    "plugins": []
  },
  "musicSignUrl": "",
  "enableLocalFile2Url": false,
  "parseMultMsg": false,
  "imageDownloadProxy": "",
  "timeout": {
    "baseTimeout": 10000,
    "uploadSpeedKBps": 256,
    "downloadSpeedKBps": 256,
    "maxTimeout": 1800000
  }
}
EOF

    print_success "NapCat 配置已创建: onebot11_${qq_id}.json"

    local accounts=$(read_accounts)
    local new_accounts=$(python3 << EOF
import json
accounts = json.loads('''$accounts''')
new_account = {
    "id": $qq_id,
    "password": "",
    "sdk_type": "onebot",
    "platform_type": "qq",
    "model_type": "default",
    "server": {
        "auto": False,
        "type": "post",
        "host": "http://napcat",
        "port": $http_port,
        "access_token": "$access_token"
    },
    "extends": {},
    "debug": False
}
accounts["account"].append(new_account)
print(json.dumps(accounts, indent=4, ensure_ascii=False))
EOF
)
    write_accounts "$new_accounts"

    if [ "$IMAGE_SELECTED" = false ]; then
        select_image_version
    fi

    generate_napcat_compose
    generate_olivos_compose

    print_success "QQ $qq_id 添加完成！"
    print_info "  WebUI 端口: ${webui_port}"
    print_info "  HTTP 端口: $http_port"
    print_info "  Access Token: $access_token"
}

add_other_platform_account() {
    # 第一次添加账号时触发环境安装
    if ! is_installed; then
        ensure_environment
        ensure_registry
    fi
    
    print_header "添加其他平台账号"
    echo ""
    echo "支持的平台："
    echo "  kaiheila    - KOOK"
    echo "  telegram    - Telegram"
    echo "  discord     - Discord"
    echo "  qqGuild     - QQ 官方"
    echo "  dodo        - 渡渡语音"
    echo "  fanbook     - Fanbook"
    echo "  biliLive    - B站直播间"
    echo "  terminal    - 虚拟终端"
    echo ""

    read -p "请输入平台类型: " platform_type
    if [ -z "$platform_type" ]; then
        print_error "平台类型不能为空！"
        return 1
    fi

    case "$platform_type" in
        kaiheila)
            print_info "配置 KOOK 账号"
            read -p "请输入 Bot Token: " access_token
            add_account_json "0" "" "kaiheila_link" "kaiheila" "default" "true" "websocket" "" "" "$access_token"
            ;;
        telegram)
            print_info "配置 Telegram 账号"
            print_info "格式: BotID:Token"
            read -p "请输入完整 Token: " full_token
            local bot_id=$(echo "$full_token" | cut -d: -f1)
            add_account_json "$bot_id" "" "telegram_poll" "telegram" "default" "true" "post" "" "" "$full_token"
            ;;
        discord)
            print_info "配置 Discord 账号"
            read -p "请输入 Bot Token: " access_token
            add_account_json "0" "" "discord_link" "discord" "default" "true" "websocket" "" "" "$access_token"
            ;;
        qqGuild)
            print_info "配置 QQ 官方机器人"
            echo "  1) V1  2) V2"
            read -p "选择版本 [1/2]: " qq_ver
            read -p "请输入 AppID: " app_id
            if [ "$qq_ver" = "2" ]; then
                read -p "请输入 AppSecret: " access_token
                echo "  1) public  2) private  3) public_guild_only"
                read -p "选择 [1/2/3]: " domain
                case "$domain" in
                    1) model="public" ;;
                    2) model="private" ;;
                    3) model="public_guild_only" ;;
                    *) model="public" ;;
                esac
                add_account_json "$app_id" "" "qqGuildv2_link" "qqGuild" "$model" "true" "websocket" "" "" "$access_token"
            else
                read -p "请输入机器人令牌: " access_token
                echo "  1) public  2) private"
                read -p "选择 [1/2]: " domain
                [ "$domain" = "2" ] && model="private" || model="public"
                add_account_json "$app_id" "" "qqGuild_link" "qqGuild" "$model" "true" "websocket" "" "" "$access_token"
            fi
            ;;
        dodo)
            print_info "配置 Dodo 账号"
            echo "  1) V1  2) V2"
            read -p "选择版本 [1/2]: " dodo_ver
            read -p "请输入 Bot ID: " bot_id
            read -p "请输入 Bot 私钥: " access_token
            [ "$dodo_ver" = "2" ] && model="default" || model="v1"
            add_account_json "$bot_id" "" "dodo_link" "dodo" "$model" "true" "websocket" "" "" "$access_token"
            ;;
        fanbook)
            print_info "配置 Fanbook 账号"
            read -p "请输入 Token: " access_token
            add_account_json "0" "" "fanbook_poll" "fanbook" "default" "true" "post" "" "" "$access_token"
            ;;
        biliLive)
            print_info "配置 B站直播间"
            read -p "请输入直播间 ID: " room_id
            echo "  1) 游客模式  2) 登录模式"
            read -p "选择 [1/2]: " mode
            [ "$mode" = "2" ] && model="login" || model="default"
            add_account_json "$room_id" "" "biliLive_link" "biliLive" "$model" "true" "websocket" "" "" "$room_id"
            ;;
        terminal)
            print_info "配置虚拟终端"
            read -p "请输入虚拟 Bot ID: " bot_id
            add_account_json "$bot_id" "" "terminal_link" "terminal" "default" "true" "websocket" "" "" ""
            ;;
        *)
            print_warning "未知平台类型，使用通用配置。"
            read -p "请输入账号 ID: " account_id
            read -p "请输入 SDK 类型: " sdk_type
            read -p "请输入 Token/密钥: " access_token
            add_account_json "$account_id" "" "$sdk_type" "$platform_type" "default" "true" "websocket" "" "" "$access_token"
            ;;
    esac
}

add_account_json() {
    local id="$1" password="$2" sdk_type="$3" platform_type="$4"
    local model_type="$5" server_auto="$6" server_type="$7"
    local server_host="$8" server_port="$9" access_token="${10}"

    local accounts=$(read_accounts)
    local new_accounts=$(python3 << EOF
import json
accounts = json.loads('''$accounts''')
try:
    account_id = int($id)
except:
    account_id = "$id"

new_account = {
    "id": account_id,
    "password": "$password",
    "sdk_type": "$sdk_type",
    "platform_type": "$platform_type",
    "model_type": "$model_type",
    "server": {
        "auto": $([ "$server_auto" = "true" ] && echo "True" || echo "False"),
        "type": "$server_type",
        "host": "$server_host",
        "port": $([ -n "$server_port" ] && echo "$server_port" || echo "0"),
        "access_token": "$access_token"
    },
    "extends": {},
    "debug": False
}
accounts["account"].append(new_account)
print(json.dumps(accounts, indent=4, ensure_ascii=False))
EOF
)
    write_accounts "$new_accounts"
    print_success "账号已添加到 account.json"
    
    if [ "$IMAGE_SELECTED" = false ]; then
        select_image_version
    fi
    
    generate_olivos_compose
}

view_accounts() {
    print_header "当前账号列表"
    
    if [ ! -f "/opt/olivos/conf/account.json" ]; then
        print_warning "暂无账号配置。"
        return
    fi

    python3 << 'EOF'
import json
with open('/opt/olivos/conf/account.json') as f:
    data = json.load(f)
if not data.get('account'):
    print('暂无账号。')
else:
    for i, a in enumerate(data['account']):
        print(f"  [{i}] ID: {a['id']} | 平台: {a['platform_type']} | SDK: {a['sdk_type']}")
        t = a.get('server', {}).get('access_token', '')
        if t:
            display = t[:16] + '...' if len(t) > 16 else t
            print(f"       Token: {display}")
EOF
}

remove_account() {
    print_header "删除账号"
    view_accounts

    if [ ! -f "/opt/olivos/conf/account.json" ]; then
        print_warning "没有可删除的账号。"
        return
    fi

    read -p "请输入要删除的账号索引: " index

    local accounts=$(read_accounts)
    local result=$(python3 << EOF
import json, os
accounts = json.loads('''$accounts''')
if 0 <= $index < len(accounts['account']):
    removed = accounts['account'].pop($index)
    if removed.get('platform_type') == 'qq' and removed.get('sdk_type') == 'onebot':
        cf = f'/opt/napcat/config/onebot11_{removed["id"]}.json'
        if os.path.exists(cf):
            os.remove(cf)
    print(json.dumps(accounts, indent=4, ensure_ascii=False))
else:
    print('INVALID_INDEX')
EOF
)

    if echo "$result" | grep -q "INVALID_INDEX"; then
        print_error "索引无效！"
        return 1
    fi

    write_accounts "$result"
    print_success "账号已删除。"
    
    if [ "$(has_qq_accounts)" = "true" ]; then
        generate_napcat_compose
    else
        docker stop napcat 2>/dev/null || true
        docker rm napcat 2>/dev/null || true
        rm -rf /opt/napcat
        print_info "已无 QQ 账号，NapCat 目录已清理。"
    fi
    generate_olivos_compose
}

# ==================== Compose 文件生成 ====================
generate_napcat_compose() {
    if [ "$(has_qq_accounts)" != "true" ]; then
        return 0
    fi

    print_info "生成 NapCat compose 配置..."
    
    local napcat_ports=""
    local configs=$(ls /opt/napcat/config/onebot11_*.json 2>/dev/null || true)
    local port_index=0

    if [ -z "$configs" ]; then
        napcat_ports="      - \"6099:6099\""
    else
        while IFS= read -r config; do
            [ -z "$config" ] && continue
            local http_port=$(python3 -c "import json;print(json.load(open('$config'))['network']['httpServers'][0]['port'])" 2>/dev/null)
            local webui_port=$((6099 + port_index))
            napcat_ports="${napcat_ports}
      - \"${webui_port}:6099\"
      - \"${http_port}:${http_port}\""
            port_index=$((port_index + 1))
        done <<< "$configs"
    fi

    cat > /opt/napcat/docker-compose.yml << EOF
services:
  napcat:
    image: mlikiowa/napcat-docker:latest
    container_name: napcat
    restart: always
    volumes:
      - "/opt/napcat/config:/app/napcat/config"
      - "/opt/napcat/QQ_DATA:/app/.config/QQ"
    environment:
      - TZ=Asia/Shanghai
      - NAPCAT_UID=${NAPCAT_UID}
      - NAPCAT_GID=${NAPCAT_GID}
    ports:$napcat_ports
    networks:
      - olivos
    mac_address: "02:42:16:74:4c:b0"

networks:
  olivos:
    name: olivos
    driver: bridge
EOF

    print_success "NapCat compose 已生成: /opt/napcat/docker-compose.yml"
}

generate_olivos_compose() {
    print_info "生成 OlivOS compose 配置..."
    
    read -p "是否需要安装额外的 pip 包？(空格分隔，留空跳过): " extra_packages
    
    local extra_env=""
    if [ -n "$extra_packages" ]; then
        extra_env="
      - EXTRA_PACKAGES=$extra_packages"
    fi

    # 根据 BUILD_TYPE 决定 plugin 挂载路径
    local plugin_volume=""
    if [ "$BUILD_TYPE" = "full" ]; then
        plugin_volume="      - /opt/olivos/plugin/data:/app/OlivOS/plugin/data"
        # 确保目录存在
        mkdir -p /opt/olivos/plugin/data
    else
        plugin_volume="      - /opt/olivos/plugin:/app/OlivOS/plugin"
    fi

    if [ "$(has_qq_accounts)" = "true" ]; then
        cat > /opt/olivos/docker-compose.yml << EOF
services:
  olivos:
    image: ${IMAGE_BASE}:${IMAGE_TAG}
    container_name: olivos
    restart: always
    volumes:
      - /opt/olivos/conf:/app/OlivOS/conf
      - /opt/olivos/data:/app/OlivOS/data
${plugin_volume}
      - /opt/olivos/logfile:/app/OlivOS/logfile
    environment:
      - TZ=Asia/Shanghai$extra_env
    networks:
      - olivos

networks:
  olivos:
    name: olivos
    external: true
EOF
    else
        cat > /opt/olivos/docker-compose.yml << EOF
services:
  olivos:
    image: ${IMAGE_BASE}:${IMAGE_TAG}
    container_name: olivos
    restart: always
    volumes:
      - /opt/olivos/conf:/app/OlivOS/conf
      - /opt/olivos/data:/app/OlivOS/data
${plugin_volume}
      - /opt/olivos/logfile:/app/OlivOS/logfile
    environment:
      - TZ=Asia/Shanghai$extra_env
    networks:
      - olivos

networks:
  olivos:
    name: olivos
    driver: bridge
EOF
    fi

    print_success "OlivOS compose 已生成: /opt/olivos/docker-compose.yml"
}

# ==================== 清理未使用的 Docker 资源 ====================
cleanup_docker() {
    print_info "清理未使用的 Docker 资源..."
    
    docker container prune -f 2>/dev/null && print_success "已清理停止的容器" || true
    docker image prune -f 2>/dev/null && print_success "已清理未使用的镜像" || true
    docker network prune -f 2>/dev/null && print_success "已清理未使用的网络" || true
    docker builder prune -f 2>/dev/null && print_success "已清理构建缓存" || true
    
    print_success "Docker 资源清理完成"
}

# ==================== 彻底移除 ====================
complete_removal() {
    if ! is_installed; then
        print_warning "未检测到 OlivOS 安装，无需移除。"
        return 0
    fi
    
    print_header "彻底移除 OlivOS 和 NapCat"
    echo ""
    print_warning "此操作将执行以下步骤："
    echo "  1. 停止并删除 olivos 和 napcat 容器"
    echo "  2. 删除相关 Docker 网络"
    echo "  3. 删除所有配置文件 (/opt/olivos, /opt/napcat)"
    echo "  4. 删除相关 Docker 镜像"
    echo "  5. 清理未使用的 Docker 资源"
    echo ""
    print_error "此操作不可逆！所有配置和数据将被永久删除！"
    echo ""
    read -p "确认要彻底移除吗？输入 'yes' 继续: " confirm
    
    if [ "$confirm" != "yes" ]; then
        print_info "操作已取消。"
        return 0
    fi
    
    echo ""
    print_info "开始彻底移除..."
    
    # 1. 停止并删除容器
    print_info "停止并删除容器..."
    docker stop olivos 2>/dev/null && print_success "已停止 olivos 容器" || print_info "olivos 容器未运行"
    docker rm olivos 2>/dev/null && print_success "已删除 olivos 容器" || print_info "olivos 容器不存在"
    
    docker stop napcat 2>/dev/null && print_success "已停止 napcat 容器" || print_info "napcat 容器未运行"
    docker rm napcat 2>/dev/null && print_success "已删除 napcat 容器" || print_info "napcat 容器不存在"
    
    # 2. 删除网络
    print_info "删除 Docker 网络..."
    docker network rm olivos 2>/dev/null && print_success "已删除 olivos 网络" || print_info "olivos 网络不存在"
    
    # 3. 删除配置文件目录
    print_info "删除配置文件..."
    if [ -d "/opt/olivos" ]; then
        rm -rf /opt/olivos
        print_success "已删除 /opt/olivos"
    fi
    
    if [ -d "/opt/napcat" ]; then
        rm -rf /opt/napcat
        print_success "已删除 /opt/napcat"
    fi
    
    # 4. 删除镜像
    print_info "删除 Docker 镜像..."
    docker rmi mlikiowa/napcat-docker:latest 2>/dev/null && print_success "已删除 napcat 镜像" || print_info "napcat 镜像不存在或正在使用"
    
    local olivos_images=$(docker images --format "{{.Repository}}:{{.Tag}}" | grep -i olivos || true)
    if [ -n "$olivos_images" ]; then
        echo "$olivos_images" | while read -r img; do
            docker rmi "$img" 2>/dev/null && print_success "已删除镜像: $img" || print_info "镜像 $img 无法删除（可能正在使用）"
        done
    fi
    
    # 5. 清理未使用的资源
    cleanup_docker
    
    # 6. 重置状态变量
    ENV_CHECKED=false
    REGISTRY_CHECKED=false
    IMAGE_SELECTED=false
    IMAGE_BASE="cary17/olivos"
    IMAGE_TAG="latest"
    BUILD_TYPE="full"
    
    echo ""
    print_success "彻底移除完成！"
}

# ==================== 服务管理 ====================
pull_images() {
    require_installed || return 1
    
    print_header "拉取镜像"
    
    if [ "$(has_qq_accounts)" = "true" ]; then
        print_info "拉取 NapCat 镜像..."
        docker pull mlikiowa/napcat-docker:latest || print_warning "NapCat 镜像拉取失败"
    fi
    
    print_info "拉取 OlivOS 镜像: ${IMAGE_BASE}:${IMAGE_TAG}"
    docker pull "${IMAGE_BASE}:${IMAGE_TAG}" || {
        print_error "OlivOS 镜像拉取失败"
        return 1
    }
    
    print_success "所有镜像拉取完成"
}

start_services() {
    require_installed || return 1
    
    print_info "清理旧容器和网络..."
    docker stop olivos napcat 2>/dev/null || true
    docker rm olivos napcat 2>/dev/null || true
    docker network rm olivos 2>/dev/null || true
    
    if [ "$(has_qq_accounts)" = "true" ]; then
        print_info "启动 NapCat 容器..."
        cd /opt/napcat
        docker compose up -d

        print_info "等待 NapCat 启动完成..."
        local retry=0
        while [ $retry -lt 30 ]; do
            if docker logs napcat 2>&1 | grep -qE "WebUi|服务已启动|登录"; then
                print_success "NapCat 已成功启动"
                break
            fi
            sleep 2
            retry=$((retry + 1))
            [ $((retry % 5)) -eq 0 ] && print_info "等待中... (${retry}/30)"
        done
        sleep 5
    fi

    print_info "启动 OlivOS 容器..."
    cd /opt/olivos
    docker compose up -d
    sleep 3

    if docker ps --format '{{.Names}}' | grep -q "olivos"; then
        print_success "OlivOS 已成功启动"
    else
        print_error "OlivOS 启动失败！查看日志："
        docker compose logs 2>/dev/null || docker logs olivos 2>/dev/null || true
    fi

    if [ "$(has_qq_accounts)" = "true" ]; then
        if docker ps --format '{{.Names}}' | grep -q "napcat"; then
            print_success "NapCat 已成功启动"
            
            local webui_token=$(get_napcat_webui_token)
            
            echo ""
            print_info "============================================"
            print_info "  NapCat WebUI 登录地址："
            local port_index=0
            for config in /opt/napcat/config/onebot11_*.json; do
                [ -f "$config" ] || continue
                local qq_id=$(basename "$config" | sed 's/onebot11_//;s/.json//')
                local webui_port=$((6099 + port_index))
                local ip=$(ip -4 addr show | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | grep -v 127.0.0.1 | head -1)
                
                if [ -n "$webui_token" ]; then
                    print_info "  QQ: $qq_id -> http://${ip:-服务器IP}:${webui_port}/webui?token=${webui_token}"
                else
                    print_info "  QQ: $qq_id -> http://${ip:-服务器IP}:${webui_port}/webui"
                fi
                port_index=$((port_index + 1))
            done
            print_info "============================================"
        else
            print_error "NapCat 启动失败！"
            cd /opt/napcat && docker compose logs 2>/dev/null || docker logs napcat 2>/dev/null || true
        fi
    fi
}

stop_services() {
    require_installed || return 1
    
    print_info "停止服务..."
    cd /opt/olivos && docker compose down 2>/dev/null || true
    cd /opt/napcat 2>/dev/null && docker compose down 2>/dev/null || true
    docker network rm olivos 2>/dev/null || true
    print_success "所有服务已停止"
}

restart_services() {
    require_installed || return 1
    
    start_services
    
    if [ "$(has_qq_accounts)" = "true" ]; then
        sleep 3
        show_logs
    fi
}

update_images() {
    require_installed || return 1
    
    print_header "更新镜像"
    
    local old_images=$(docker images --format "{{.Repository}}:{{.Tag}}" | grep -E "napcat|olivos" || true)
    
    if [ "$(has_qq_accounts)" = "true" ]; then
        print_info "拉取最新 NapCat 镜像..."
        docker pull mlikiowa/napcat-docker:latest || print_warning "NapCat 更新失败"
    fi
    
    print_info "拉取最新 OlivOS 镜像..."
    docker pull "${IMAGE_BASE}:${IMAGE_TAG}" || print_warning "OlivOS 更新失败"
    
    print_info "清理旧版本镜像..."
    if [ -n "$old_images" ]; then
        echo "$old_images" | while read -r old_img; do
            local tag=$(echo "$old_img" | cut -d: -f2)
            if [[ "$tag" != "latest" && "$tag" != "latest-core" && "$tag" != "latest-dev" ]]; then
                docker rmi "$old_img" 2>/dev/null && print_success "已删除镜像: $old_img" || true
            fi
        done
    fi
    
    docker image prune -f 2>/dev/null || true
    
    print_success "镜像更新完成"
    
    read -p "是否重启服务？[Y/n]: " restart_choice
    if [[ ! "$restart_choice" =~ ^[Nn]$ ]]; then
        restart_services
    fi
}

show_logs() {
    require_installed || return 1
    
    if [ "$(has_qq_accounts)" = "true" ] && docker ps --format '{{.Names}}' | grep -q "napcat"; then
        print_header "NapCat 日志（最后 40 行）"
        docker logs napcat 2>&1 | tail -40
        
        local webui_token=$(get_napcat_webui_token)
        
        echo ""
        print_info "============================================"
        print_info "  NapCat WebUI 登录地址："
        local port_index=0
        for config in /opt/napcat/config/onebot11_*.json; do
            [ -f "$config" ] || continue
            local qq_id=$(basename "$config" | sed 's/onebot11_//;s/.json//')
            local webui_port=$((6099 + port_index))
            local ip=$(ip -4 addr show | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | grep -v 127.0.0.1 | head -1)
            
            if [ -n "$webui_token" ]; then
                print_info "  QQ: $qq_id -> http://${ip:-服务器IP}:${webui_port}/webui?token=${webui_token}"
            else
                print_info "  QQ: $qq_id -> http://${ip:-服务器IP}:${webui_port}/webui"
            fi
            port_index=$((port_index + 1))
        done
        print_info "============================================"
    fi
    
    echo ""
    print_header "OlivOS 日志（最后 30 行）"
    docker logs olivos 2>&1 | tail -30 || print_warning "OlivOS 容器未运行"
}

show_status() {
    require_installed || return 1
    
    print_header "服务运行状态"
    echo ""
    
    if docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null | grep -qE "NAMES|napcat|olivos"; then
        docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null | grep -E "NAMES|napcat|olivos"
    else
        print_warning "没有运行中的服务。"
    fi
    
    echo ""
    echo -e "${CYAN}相关镜像：${NC}"
    docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}" | grep -E "REPOSITORY|napcat|olivos" 2>/dev/null || echo "  暂无镜像"
}

# ==================== 主菜单 ====================
show_main_menu() {
    while true; do
        print_header "OlivOS Docker 管理脚本"
        echo ""
        echo -e "  ${GREEN}账号管理：${NC}"
        echo "    1) 添加 QQ 账号 (NapCat)"
        echo "    2) 添加其他平台账号"
        echo "    3) 查看所有账号"
        echo "    4) 删除账号"
        echo ""
        echo -e "  ${BLUE}服务管理：${NC}"
        echo "    5) 拉取镜像并启动服务"
        echo "    6) 停止服务"
        echo "    7) 重启服务"
        echo "    8) 更新镜像（含清理旧版本）"
        echo "    9) 查看日志"
        echo "   10) 查看运行状态"
        echo ""
        echo -e "  ${YELLOW}配置管理：${NC}"
        echo "   11) 重新生成 compose 文件"
        echo "   12) 切换镜像版本/仓库"
        echo ""
        echo -e "  ${MAGENTA}工具：${NC}"
        echo "   13) 清理 Docker 未使用资源"
        echo ""
        echo -e "  ${RED}危险操作：${NC}"
        echo "   14) 彻底移除 OlivOS & NapCat"
        echo ""
        echo "    0) 退出"
        echo ""

        read -p "请选择操作 [0-14]: " choice

        case "$choice" in
            1)
                add_qq_account
                ;;
            2)
                add_other_platform_account
                ;;
            3)
                if ! is_installed; then
                    print_warning "尚未安装 OlivOS，请先添加账号。"
                else
                    view_accounts
                fi
                ;;
            4)
                if ! is_installed; then
                    print_warning "尚未安装 OlivOS，请先添加账号。"
                else
                    remove_account
                fi
                ;;
            5)
                if ! is_installed; then
                    print_warning "尚未安装 OlivOS，请先添加账号完成安装。"
                else
                    pull_images && start_services
                fi
                ;;
            6)
                stop_services
                ;;
            7)
                restart_services
                ;;
            8)
                update_images
                ;;
            9)
                show_logs
                ;;
            10)
                show_status
                ;;
            11)
                if ! is_installed; then
                    print_warning "尚未安装 OlivOS，请先添加账号。"
                else
                    if [ "$(has_qq_accounts)" = "true" ]; then
                        generate_napcat_compose
                    fi
                    generate_olivos_compose
                fi
                ;;
            12)
                if ! is_installed; then
                    print_warning "尚未安装 OlivOS，请先添加账号。"
                else
                    ensure_registry
                    select_image_version
                    generate_olivos_compose
                    read -p "是否立即拉取新镜像并重启？[y/N]: " apply
                    if [[ "$apply" =~ ^[Yy]$ ]]; then
                        pull_images && start_services
                    fi
                fi
                ;;
            13)
                cleanup_docker
                ;;
            14)
                complete_removal
                ;;
            0)
                print_info "再见！"
                exit 0
                ;;
            *)
                print_warning "无效选择，请重新输入。"
                ;;
        esac

        echo ""
        read -p "按 Enter 键继续..."
    done
}

# ==================== 主流程 ====================
main() {
    clear
    print_header "OlivOS Docker 一键安装与管理脚本"
    echo ""
    echo -e "${MAGENTA}  自动安装 Docker | 配置镜像加速 | 多平台多账号管理${NC}"
    echo ""

    check_root

    echo ""
    if is_installed; then
        print_info "检测到已有安装，可直接管理。"
    else
        print_info "首次使用请选择「添加账号」开始安装。"
    fi
    echo ""

    show_main_menu
}

main "$@"
