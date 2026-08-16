# Komari TG Bot

Komari TG Bot 是一个对接 Komari 面板的 Telegram 探针机器人。它支持私聊菜单、群组查询、内联模式、节点详情、续费查询、延迟战报图片、管理员权限和黑名单，适合把多套 Komari 面板集中到 Telegram 里查看。

## 功能

- 多用户使用，数据按 Telegram 用户隔离
- Owner 可升级管理员、取消管理员、拉黑/解除拉黑用户
- 每个用户可绑定多个 Komari 面板并自由切换
- 私聊按钮菜单：统计信息、面板管理、节点查询、续费查询、延迟战报、用户权限
- 群组命令：`/all` 查看统计，`/sid 编号` 查看节点详情
- 内联模式：在任意聊天输入 `@你的机器人` 后选择统计、服务器详情或延迟战报
- 群内统计与服务器详情直接发送，减少二次编辑等待
- 延迟战报并发读取节点数据，并正确显示超时节点
- 群组里的机器人消息自动清理，减少刷屏
- 延迟战报可生成科技风排序图片

## 一键部署

先去 [@BotFather](https://t.me/BotFather) 创建机器人并拿到 Token，然后在 VPS 上运行下面的一键命令。

脚本会让你选择：

```text
1) 安装 / 更新
2) 卸载
0) 退出
```

选择安装后，会提示你输入机器人 Token 和 Telegram 数字 ID：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/xymn2023/komari-tg-bot/main/install.sh)
```

直接进入安装：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/xymn2023/komari-tg-bot/main/install.sh) install
```

直接卸载：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/xymn2023/komari-tg-bot/main/install.sh) uninstall
```

安装完成后查看状态和日志：

```bash
systemctl status komari-tg-bot --no-pager
journalctl -u komari-tg-bot -f
```

## 配置

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | 是 | BotFather 给你的机器人 Token |
| `OWNER_IDS` | 是 | 机器人拥有者 Telegram 数字 ID，多个用逗号分隔 |
| `DB_PATH` | 否 | SQLite 数据库路径，默认 `/opt/komari-tg-bot/bot.sqlite3` |
| `INLINE_IMAGE_SERVER_ENABLED` | 否 | 是否启用内置图片 HTTP 服务，默认 `0` |
| `INLINE_IMAGE_PORT` | 否 | 内置图片服务端口，默认 `80` |
| `INLINE_PUBLIC_BASE_URL` | 否 | 图片服务的公网地址；不配置时使用本地地址 |

如需启用内置图片服务，请自行配置公网 HTTPS 地址：

```bash
INLINE_IMAGE_SERVER_ENABLED=1
INLINE_PUBLIC_BASE_URL=https://your-domain.example.com
```

## BotFather 设置

开启内联模式：

```text
/setinline
```

占位提示可以填：

```text
输入节点ID、关键词或延迟任务
```

建议开启内联反馈：

```text
/setinlinefeedback
```

## 常用命令

```text
/start                 命令说明
/menu                  功能按钮菜单
/bind 面板URL APIKEY 备注
/panels                面板列表
/use 面板ID            切换面板
/all                   统计信息
/search 关键词         搜索节点
/sid 编号              节点详情
/admin 用户ID 备注     升级管理员
/unadmin 用户ID        取消管理员
/ban 用户ID 原因       拉黑用户
/unban 用户ID          解除拉黑
/users                 用户列表
```

公开 Komari 面板没有 API Key 时，`APIKEY` 可以填 `-`：

```text
/bind https://komari.example.com - 我的面板
```

## 更新

再次运行安装命令并选择“安装 / 更新”，或手动执行：

```bash
cd /opt/komari-tg-bot
git pull --ff-only
systemctl restart komari-tg-bot
```

## 安全提醒

- 不要把 `TELEGRAM_BOT_TOKEN`、Komari API Key、`.env` 或数据库文件提交到 GitHub
- 示例配置仅使用占位值；请在服务器本地 `.env` 中填写真实值
- 如果机器人 Token 泄露，请立即到 BotFather 重置

版本变化见 [CHANGELOG.md](CHANGELOG.md)。
