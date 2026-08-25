#!/usr/bin/env bash
# ============================================================================ #
# 川流（chuan-os）— WSL Redis 自启 + IP 检测
#
# 背景：
#   缓存旁路（cache-aside，ADR-039）的 Redis 跑在 WSL 里。WSL2 使用 NAT，
#   Windows 侧要直连，必须知道 WSL 当前 IP（`wsl hostname -I`），且 WSL 重启后
#   IP 会变、Redis 也不会自动拉起。本脚本一站式解决：
#     1. 确保 redis-server 已安装、已启动（systemd/service 优先，兜底 --daemonize）
#     2. 确保监听 0.0.0.0 且 protected-mode off（否则 Windows 侧连不上；用运行时 CONFIG SET，无需 root）
#     3. 检测 WSL 当前 IP，回写 chuan-os/config/config.yaml 的 cache.host
#     4. 自启：优先 systemd 服务（redis-server.service enabled 即已覆盖）；否则写 /etc/wsl.conf [boot]
#        command（需 root）；再不行自动装 ~/.bashrc 兜底（无需密码）
#
# 用法（在 Windows PowerShell 里执行）：
#   wsl bash /mnt/<盘符>/Dev/Active/chuan-os/scripts/wsl_redis_autostart_and_ip_check.sh
#   # 只检测 IP 并回写配置，不碰 Redis 服务 / 自启（安全快速）：
#   wsl bash <同路径>/wsl_redis_autostart_and_ip_check.sh --ip-only
# ============================================================================ #

set -euo pipefail

IP_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --ip-only) IP_ONLY=1 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -n 30
      exit 0 ;;
    *) echo "[WARN] 忽略未知参数: $arg" >&2 ;;
  esac
done

# 项目根：脚本位于 <root>/scripts/，向上两级即项目根（不依赖固定盘符挂载点）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CONFIG="$PROJECT_ROOT/config/config.yaml"

log()  { printf '\033[1;32m[OK]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

echo "======================================================"
echo " 川流 · WSL Redis 自启 + IP 检测"
echo " 项目根: $PROJECT_ROOT"
echo " 配置文件: $CONFIG"
echo "======================================================"

# ---------------------------------------------------------------------------- #
# 1) 确保 redis 已安装
# ---------------------------------------------------------------------------- #
if ! command -v redis-server >/dev/null 2>&1; then
  fail "未检测到 redis-server。请先在 WSL 里安装：sudo apt-get update && sudo apt-get install -y redis-server"
fi

redis_running() {
  redis-cli ping 2>/dev/null | grep -q PONG
}

# redis 配置文件：优先取运行实例实际使用的（redis 未跑时为空），其次常见路径
REDIS_CONF="$(redis-cli CONFIG GET configfile 2>/dev/null | tail -n1 | xargs)"
if [ -z "$REDIS_CONF" ] && [ -f /etc/redis/redis.conf ]; then
  REDIS_CONF=/etc/redis/redis.conf
fi

# 直接拉起 redis（有配置用配置，无配置用默认参数；外部放通见第 3 步）
start_redis() {
  if [ -n "$REDIS_CONF" ]; then
    redis-server "$REDIS_CONF" --daemonize yes
  else
    redis-server --daemonize yes
  fi
}

# ---------------------------------------------------------------------------- #
# 2) 确保 redis 已启动（非 --ip-only 才执行）
# ---------------------------------------------------------------------------- #
if [ "$IP_ONLY" -eq 0 ]; then
  if redis_running; then
    log "redis 已在运行（$(redis-cli ping)）"
  else
    log "redis 未运行，尝试启动……"
    # systemd（WSL 启用了 systemd）优先
    if [ "$(ps -p 1 -o comm= 2>/dev/null)" = "systemd" ]; then
      if sudo -n systemctl start redis-server 2>/dev/null || sudo -n service redis-server start 2>/dev/null; then
        log "已通过 systemd/service 启动 redis"
      else
        warn "systemd/service 启动失败（可能需要密码），改用直接拉起："
        start_redis && log "已用 redis-server --daemonize 拉起" \
          || warn "直接拉起失败，请手动执行：sudo service redis-server start"
      fi
    else
      # 默认 WSL（无 systemd）：直接后台拉起，无需 root
      start_redis && log "已用 redis-server --daemonize 拉起" \
        || warn "直接拉起失败，请手动执行：sudo service redis-server start"
    fi
    sleep 0.5
    redis_running || fail "redis 启动后仍 ping 不通，请检查日志：journalctl -u redis-server 或 /var/log/redis/"
  fi

  # -------------------------------------------------------------------------- #
  # 3) 确保可被 Windows 直连：bind 0.0.0.0 + protected-mode no（运行时 CONFIG SET，无需 root）
  # -------------------------------------------------------------------------- #
  CUR_BIND="$(redis-cli CONFIG GET bind 2>/dev/null | tail -n1)"
  CUR_PROT="$(redis-cli CONFIG GET protected-mode 2>/dev/null | tail -n1)"
  CHANGED=0
  case " $CUR_BIND " in
    *'0.0.0.0'*) : ;;
    *) redis-cli CONFIG SET bind "0.0.0.0 -::1" >/dev/null 2>&1 && CHANGED=1 || warn "CONFIG SET bind 失败" ;;
  esac
  if [ "$CUR_PROT" != "no" ]; then
    redis-cli CONFIG SET protected-mode no >/dev/null 2>&1 && CHANGED=1 || warn "CONFIG SET protected-mode 失败"
  fi
  if [ "$CHANGED" -eq 1 ]; then
    log "已放通外部访问（bind 0.0.0.0 / protected-mode no）"
    if [ -n "$REDIS_CONF" ]; then
      redis-cli CONFIG REWRITE >/dev/null 2>&1 \
        && log "已 CONFIG REWRITE 持久化到 $REDIS_CONF" \
        || warn "CONFIG REWRITE 持久化失败（无配置文件或权限不足；WSL 重启后需重跑本脚本）"
    else
      warn "redis 无配置文件，CONFIG SET 仅本次生效；WSL 重启后重跑本脚本即可"
    fi
  else
    log "redis 已放通外部访问（bind $CUR_BIND / protected-mode $CUR_PROT）"
  fi

  # -------------------------------------------------------------------------- #
  # 4) root 级自启：优先 systemd 服务（redis-server.service enabled → WSL 重启自动拉起）；
  #    否则写 /etc/wsl.conf [boot]（需 root）；再不行 ~/.bashrc 兜底（无需密码）
  # -------------------------------------------------------------------------- #
  WSL_CONF=/etc/wsl.conf
  if [ "$(ps -p 1 -o comm= 2>/dev/null)" = "systemd" ] && \
     [ "$(systemctl is-enabled redis-server 2>/dev/null)" = "enabled" ]; then
    log "root 级自启已由 systemd 覆盖（redis-server.service enabled，WSL 重启自动拉起）"
    if [ -f "$WSL_CONF" ] && grep -q 'redis-server' "$WSL_CONF" 2>/dev/null; then
      warn "检测到 $WSL_CONF 里已有 redis [boot] 命令，与 systemd 服务重复，建议移除以免启动时抢 6379 端口"
    fi
  elif sudo -n test -w /etc 2>/dev/null; then
    if ! grep -q 'redis-server' "$WSL_CONF" 2>/dev/null; then
      if [ -n "$REDIS_CONF" ]; then
        BOOT_LINE="command = service redis-server start || redis-server $REDIS_CONF --daemonize yes"
      else
        BOOT_LINE='command = redis-server --daemonize yes'
      fi
      if [ -f "$WSL_CONF" ] && grep -q '^\[boot\]' "$WSL_CONF"; then
        sudo -n sed -i "/^\[boot\]/a $BOOT_LINE" "$WSL_CONF"
      else
        printf '\n[boot]\n%s\n' "$BOOT_LINE" | sudo -n tee -a "$WSL_CONF" >/dev/null
      fi
      log "已写入 $WSL_CONF：[boot] command 拉起 redis（下次 WSL 启动生效）"
      warn "注意：WSL 需在 Windows 侧执行 wsl --shutdown 后重启才生效"
    else
      log "$WSL_CONF 已包含 redis 自启配置"
    fi
  else
    warn "root 级自启未覆盖（既无 systemd 服务，又写不了 $WSL_CONF）"
    # 无 root 兜底：~/.bashrc 打开 WSL 交互 shell 时自动拉起 redis（用户级，无需密码）
    BASHRC="$HOME/.bashrc"
    if grep -q 'redis-cli ping' "$BASHRC" 2>/dev/null; then
      log "$BASHRC 已包含 redis 自启兜底"
    else
      {
        echo ""
        echo "# chuan-os: WSL 交互 shell 时自动拉起 redis（无 root 兜底，wsl_redis_autostart 脚本写入）"
        if [ -n "$REDIS_CONF" ]; then
          echo "command -v redis-server >/dev/null 2>&1 && redis-cli ping >/dev/null 2>&1 || redis-server '$REDIS_CONF' --daemonize yes >/dev/null 2>&1 || true"
        else
          echo "command -v redis-server >/dev/null 2>&1 && redis-cli ping >/dev/null 2>&1 || redis-server --daemonize yes >/dev/null 2>&1 || true"
        fi
      } >> "$BASHRC"
      log "已在 $BASHRC 追加 redis 自启兜底（下次打开 WSL 终端生效）"
    fi
  fi
fi

# ---------------------------------------------------------------------------- #
# 5) 检测 WSL 当前 IP 并回写 config.yaml 的 cache.host
# ---------------------------------------------------------------------------- #
WSL_IP="$(hostname -I | awk '{print $1}')"
[ -n "$WSL_IP" ] || fail "无法获取 WSL IP（hostname -I 为空）"
[ -f "$CONFIG" ] || fail "未找到配置文件：$CONFIG"

OLD_HOST="$(grep -A6 '^cache:' "$CONFIG" | sed -n 's/^[[:space:]]*host:[[:space:]]*["'\'']\?\([^"'\'' ]*\).*/\1/p' | head -n1)"

if [ "$WSL_IP" = "$OLD_HOST" ]; then
  log "config.yaml cache.host 已是最新：$WSL_IP"
else
  # 只改 cache 段内的 host，不碰 hud.host 等其他 host 键（不 drop 掉 cache: 头行）
  awk -v ip="$WSL_IP" '
    /^cache:/ { in_cache = 1 }
    /^[a-z]/ && !/^cache:/ { in_cache = 0 }
    in_cache && /^[[:space:]]*host:/ {
      sub(/host:[[:space:]]*.*/, "host: \"" ip "\"")
    }
    { print }
  ' "$CONFIG" > "$CONFIG.tmp" && mv "$CONFIG.tmp" "$CONFIG"
  log "已回写 cache.host：$OLD_HOST → $WSL_IP"
fi

# ---------------------------------------------------------------------------- #
# 6) 从 Windows 侧视角验证
# ---------------------------------------------------------------------------- #
echo "------------------------------------------------------"
log "WSL Redis 地址：$WSL_IP:6379"
log "本机回环验证：$(redis-cli ping)"
printf '\033[1;36m[INFO]\033[0m Windows 侧验证（PowerShell）：redis-cli -h %s ping\n' "$WSL_IP"
echo "------------------------------------------------------"
