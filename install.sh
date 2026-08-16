#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Tweakl/komari-tg-bot.git}"
APP_DIR="${APP_DIR:-/opt/komari-tg-bot}"
SERVICE_NAME="${SERVICE_NAME:-komari-tg-bot}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "请使用 root 运行：sudo bash install.sh"
    exit 1
  fi
}

prompt_required() {
  local var_name="$1"
  local prompt="$2"
  local current="${!var_name:-}"
  if [ -n "$current" ]; then
    return
  fi
  while [ -z "${current}" ]; do
    read -r -p "$prompt" current
  done
  export "$var_name=$current"
}

install_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y git python3 python3-venv python3-pip
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y git python3 python3-pip
  elif command -v yum >/dev/null 2>&1; then
    yum install -y git python3 python3-pip
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache git python3 py3-pip
  else
    echo "未识别的系统包管理器，请先安装 git/python3/pip。"
    exit 1
  fi
}

install_bot() {
  need_root
  echo "Komari TG Bot 安装"
  echo "━━━━━━━━━━━━━━━━━━━━"
  prompt_required TELEGRAM_BOT_TOKEN "请输入机器人 Token："
  prompt_required OWNER_IDS "请输入你的 Telegram 数字 ID："

  DB_PATH="${DB_PATH:-${APP_DIR}/bot.sqlite3}"
  INLINE_IMAGE_SERVER_ENABLED="${INLINE_IMAGE_SERVER_ENABLED:-0}"
  INLINE_IMAGE_PORT="${INLINE_IMAGE_PORT:-80}"
  INLINE_PUBLIC_BASE_URL="${INLINE_PUBLIC_BASE_URL:-}"

  install_packages

  mkdir -p "$APP_DIR"
  if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull --ff-only
  else
    rm -rf "$APP_DIR"/*
    git clone "$REPO_URL" "$APP_DIR"
  fi

  cd "$APP_DIR"
  "$PYTHON_BIN" -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt

  cat > "${APP_DIR}/.env" <<EOF
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
OWNER_IDS=${OWNER_IDS}
DB_PATH=${DB_PATH}
INLINE_IMAGE_SERVER_ENABLED=${INLINE_IMAGE_SERVER_ENABLED}
INLINE_IMAGE_PORT=${INLINE_IMAGE_PORT}
INLINE_PUBLIC_BASE_URL=${INLINE_PUBLIC_BASE_URL}
EOF
  chmod 600 "${APP_DIR}/.env"

  cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Komari Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable --now "${SERVICE_NAME}.service"
  echo
  echo "安装完成。"
  systemctl status "${SERVICE_NAME}.service" --no-pager
}

uninstall_bot() {
  need_root
  echo "Komari TG Bot 卸载"
  echo "━━━━━━━━━━━━━━━━━━━━"
  systemctl disable --now "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
  rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
  systemctl daemon-reload

  read -r -p "是否删除程序目录 ${APP_DIR}？这会删除数据库和绑定信息。[y/N] " confirm
  case "$confirm" in
    y|Y|yes|YES)
      rm -rf "$APP_DIR"
      echo "已删除程序目录。"
      ;;
    *)
      echo "已保留程序目录：${APP_DIR}"
      ;;
  esac
  echo "卸载完成。"
}

show_menu() {
  echo "Komari TG Bot 管理脚本"
  echo "━━━━━━━━━━━━━━━━━━━━"
  echo "1) 安装 / 更新"
  echo "2) 卸载"
  echo "0) 退出"
  echo
  read -r -p "请选择操作 [1/2/0]：" choice
  case "$choice" in
    1) install_bot ;;
    2) uninstall_bot ;;
    0) exit 0 ;;
    *) echo "无效选择"; exit 1 ;;
  esac
}

case "${1:-menu}" in
  install) install_bot ;;
  uninstall) uninstall_bot ;;
  menu) show_menu ;;
  *) echo "用法：bash install.sh [install|uninstall]"; exit 1 ;;
esac
