#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import concurrent.futures
import html
import http.server
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_IDS = {int(x) for x in re.split(r"[,\s]+", os.getenv("OWNER_IDS", "")) if x.isdigit()}
DB_PATH = os.getenv("DB_PATH", "/opt/komari-tg-bot/bot.sqlite3")
TZ = dt.timezone(dt.timedelta(hours=8), "CST")
TG_API = f"https://api.telegram.org/bot{TOKEN}"
USER_AGENT = "komari-tg-bot/1.4.0"
BOT_USERNAME = ""
LAST_PROFILE_UPDATE = 0.0
GROUP_AUTO_DELETE_SECONDS = 120
INLINE_IMAGE_DIR = os.getenv("INLINE_IMAGE_DIR", "/tmp/komari-tg-bot-inline")
INLINE_IMAGE_PORT = int(os.getenv("INLINE_IMAGE_PORT", "80"))
default_inline_base = "http://127.0.0.1" if INLINE_IMAGE_PORT == 80 else f"http://127.0.0.1:{INLINE_IMAGE_PORT}"
INLINE_PUBLIC_BASE_URL = os.getenv("INLINE_PUBLIC_BASE_URL", default_inline_base).rstrip("/")
INLINE_IMAGE_TTL = int(os.getenv("INLINE_IMAGE_TTL", "1800"))
INLINE_IMAGE_CACHE_TTL = int(os.getenv("INLINE_IMAGE_CACHE_TTL", "300"))
INLINE_DELAY_RESULT_LIMIT = int(os.getenv("INLINE_DELAY_RESULT_LIMIT", "6"))
INLINE_TASK_SCAN_LIMIT = int(os.getenv("INLINE_TASK_SCAN_LIMIT", "3"))
INLINE_IMAGE_SERVER_ENABLED = os.getenv("INLINE_IMAGE_SERVER_ENABLED", "0").lower() in {"1", "true", "yes"}
INLINE_IMAGE_SERVER_STARTED = False
INLINE_DELAY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
INLINE_DELAY_FILE_CACHE: dict[str, tuple[float, str, str, int]] = {}
INLINE_DYNAMIC_IMAGES: dict[str, tuple[float, dict[str, Any], int]] = {}
INLINE_DELAY_JOBS: dict[str, tuple[float, dict[str, Any], int, str]] = {}
INLINE_TEXT_JOBS: dict[str, tuple[float, str, dict[str, Any], int | None, bool]] = {}
INLINE_IMAGE_LOCK = threading.Lock()
INLINE_RESULT_VERSION = "groupfast20260620"

PANEL_CACHE: dict[int, tuple[float, list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, int]]] = {}
PANEL_CACHE_TTL = int(os.getenv("PANEL_CACHE_TTL", "30"))
PANEL_CACHE_LOCK = threading.Lock()
NODE_METHOD_PREF: dict[str, str] = {}
CUSTOM_EMOJIS = {
    "console": ("💻", "5213323619911868787"),
    "stats": ("📊", "5190806721286657692"),
    "panel": ("💼", "5445221832074483553"),
    "server": ("🌎", "5224450179368767019"),
    "dot": ("⏺", "5372864346240065092"),
    "group": ("💻", "5193177581888755275"),
    "group_item": ("🪣", "5399909394525737759"),
    "cpu": ("🧮", "5303214794336125778"),
    "storage": ("🧮", "5190741648237161191"),
    "network": ("📥", "5443127283898405358"),
    "time": ("⏱", "5382194935057372936"),
    "node": ("💻", "5193177581888755275"),
    "renewal": ("💵", "5197434882321567830"),
    "delay": ("🔭", "5379999674193172777"),
    "users": ("👩‍💻", "5301201579955725162"),
    "info": ("🔭", "5379999674193172777"),
    "ok": ("✅", "5933852328535791206"),
    "warn": ("‼️", "5933752144128644756"),
}
BIND_USAGE = (
    "Komari 面板接入\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "状态：等待绑定\n\n"
    "用法\n"
    "<pre>/bind  面板URL  APIKEY  面板备注\n"
    "公开面板 APIKEY 可填写 -</pre>\n\n"
    "示例\n"
    "<pre>/bind https://komari.example.com - 我的面板</pre>"
)

ALIASES = {
    "start": "start",
    "help": "start",
    "menu": "menu",
    "allow": "unban",
    "deny": "ban",
    "admin": "admin",
    "promote": "admin",
    "unadmin": "unadmin",
    "demote": "unadmin",
    "ban": "ban",
    "unban": "unban",
    "users": "users",
    "bind": "bind",
    "panels": "panels",
    "panel": "panels",
    "use": "use",
    "unbind": "unbind",
    "all": "all",
    "test": "all",
    "search": "search",
    "sid": "sid",
}

PRIVATE_ONLY_COMMANDS = {"start", "menu", "bind", "panels", "use", "unbind", "search"}
PRIVATE_ONLY_CALLBACKS = {
    "menu",
    "panels",
    "bind_help",
    "unbind_help",
    "search_help",
    "nodes_active",
    "renewals",
    "delay_tasks",
    "users_panel",
}
PRIVATE_ONLY_CALLBACK_PREFIXES = ("delay_tasks:", "delay_task:")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("komari-tg-bot")


def now_text() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S CST+0800")


def parse_time(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(TZ)


def utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL DEFAULT 'user',
                note TEXT NOT NULL DEFAULT '',
                created_by INTEGER,
                created_at TEXT NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                first_name TEXT NOT NULL DEFAULT '',
                last_seen TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS panels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_panels_user ON panels(tg_id);
            CREATE TABLE IF NOT EXISTS node_aliases (
                panel_id INTEGER NOT NULL,
                uuid TEXT NOT NULL,
                sid INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(panel_id, uuid),
                UNIQUE(panel_id, sid)
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "username" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN username TEXT NOT NULL DEFAULT ''")
        if "first_name" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN first_name TEXT NOT NULL DEFAULT ''")
        if "last_seen" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN last_seen TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE users SET last_seen=created_at WHERE last_seen=''")
        for owner in OWNER_IDS:
            conn.execute(
                "INSERT INTO users(tg_id, role, note, created_by, created_at) "
                "VALUES(?, 'admin', 'owner', ?, ?) "
                "ON CONFLICT(tg_id) DO UPDATE SET role='admin', note='owner', last_seen=CASE WHEN last_seen='' THEN created_at ELSE last_seen END",
                (owner, owner, utcnow_iso()),
            )


def tg_call(method: str, payload: dict[str, Any] | None = None, timeout: int = 70) -> dict[str, Any]:
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{TG_API}/{method}",
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Telegram {method} HTTP {exc.code}: {detail[:300]}") from exc
    out = json.loads(body)
    if not out.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {out}")
    return out


def tg_call_multipart(method: str, fields: dict[str, Any], files: dict[str, tuple[str, bytes, str]], timeout: int = 70) -> dict[str, Any]:
    boundary = f"----komari{int(time.time() * 1000)}"
    body = bytearray()
    for key, value in fields.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")
    for key, (filename, content, content_type) in files.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
        )
        body.extend(content)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    req = urllib.request.Request(
        f"{TG_API}/{method}",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Telegram {method} HTTP {exc.code}: {detail[:300]}") from exc
    out = json.loads(raw)
    if not out.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {out}")
    return out


def code_block(text: str) -> str:
    return f"<pre>{html.escape(text)}</pre>"


def quote_block(text: str) -> str:
    return f"<blockquote>{text}</blockquote>"


def inline_rich_html(text: str) -> str:
    return text.replace("<blockquote>", "").replace("</blockquote>", "").replace("\n", "<br>")


def ce(key: str) -> str:
    emoji, custom_emoji_id = CUSTOM_EMOJIS[key]
    return f'<tg-emoji emoji-id="{custom_emoji_id}">{html.escape(emoji)}</tg-emoji>'


def with_custom_emoji(text: str, key: str | None, html_text: bool = False) -> tuple[str, str | None]:
    if not key or key not in CUSTOM_EMOJIS:
        return text, "HTML" if html_text else None
    emoji, custom_emoji_id = CUSTOM_EMOJIS[key]
    body = text if html_text else html.escape(text)
    prefix = f'<tg-emoji emoji-id="{custom_emoji_id}">{html.escape(emoji)}</tg-emoji>'
    return f"{prefix} {body}", "HTML"


def send_message(
    chat_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
    custom_emoji: str | None = None,
    html_text: bool = False,
) -> int | None:
    message_text, parse_mode = with_custom_emoji(text, custom_emoji, html_text)
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": message_text[:3900],
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        out = tg_call("sendMessage", payload, 30)
    except Exception:
        if not custom_emoji and not parse_mode:
            raise
        payload["text"] = text[:3900]
        if html_text:
            payload["parse_mode"] = "HTML"
        else:
            payload.pop("parse_mode", None)
        out = tg_call("sendMessage", payload, 30)
    message_id = (out.get("result") or {}).get("message_id")
    if not message_id:
        return None
    message_id = int(message_id)
    if chat_id < 0:
        schedule_delete(chat_id, message_id, GROUP_AUTO_DELETE_SECONDS)
    return message_id


def send_sticker(chat_id: int, sticker: str, reply_markup: dict[str, Any] | None = None) -> int | None:
    try:
        if os.path.isfile(sticker):
            with open(sticker, "rb") as file:
                content = file.read()
            fields: dict[str, Any] = {"chat_id": chat_id}
            if reply_markup:
                fields["reply_markup"] = reply_markup
            out = tg_call_multipart(
                "sendSticker",
                fields,
                {"sticker": (os.path.basename(sticker), content, "image/webp")},
                30,
            )
        else:
            payload: dict[str, Any] = {"chat_id": chat_id, "sticker": sticker}
            if reply_markup:
                payload["reply_markup"] = reply_markup
            out = tg_call("sendSticker", payload, 30)
        message_id = (out.get("result") or {}).get("message_id")
        if not message_id:
            return None
        message_id = int(message_id)
        if chat_id < 0:
            schedule_delete(chat_id, message_id, GROUP_AUTO_DELETE_SECONDS)
        return message_id
    except Exception:
        log.exception("sendSticker failed")
        return None


def delete_message(chat_id: int, message_id: int) -> None:
    try:
        tg_call("deleteMessage", {"chat_id": chat_id, "message_id": message_id}, 20)
    except Exception:
        log.exception("deleteMessage failed")


def schedule_delete(chat_id: int, message_id: int, seconds: int = 120) -> None:
    timer = threading.Timer(seconds, delete_message, args=(chat_id, message_id))
    timer.daemon = True
    timer.start()


def private_chat_url() -> str:
    return f"https://t.me/{BOT_USERNAME}" if BOT_USERNAME else "https://t.me"


def bot_mention() -> str:
    return f"@{BOT_USERNAME}" if BOT_USERNAME else "@your_bot"


def private_chat_keyboard() -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": "去私聊绑定/操作", "url": private_chat_url()}]]}


def send_group_private_hint(chat_id: int) -> None:
    text = (
        "Komari探针监控\n"
        "请去私聊绑定/操作。\n\n"
        "绑定面板、切换面板、搜索节点等操作请在机器人私聊完成。\n"
        "绑定好以后，再回群里使用 /all 或 /sid 编号。"
    )
    send_message(chat_id, text, private_chat_keyboard())


def send_photo(chat_id: int, path: str, caption: str = "") -> int | None:
    with open(path, "rb") as file:
        data = file.read()
    fields: dict[str, Any] = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption[:1000]
    out = tg_call_multipart("sendPhoto", fields, {"photo": (os.path.basename(path), data, "image/png")}, 70)
    message_id = (out.get("result") or {}).get("message_id")
    if not message_id:
        return None
    message_id = int(message_id)
    if chat_id < 0:
        schedule_delete(chat_id, message_id, GROUP_AUTO_DELETE_SECONDS)
    return message_id


def upload_photo_file_id(chat_id: int, path: str) -> str:
    with open(path, "rb") as file:
        data = file.read()
    fields: dict[str, Any] = {"chat_id": chat_id, "disable_notification": True}
    out = tg_call_multipart("sendPhoto", fields, {"photo": (os.path.basename(path), data, "image/jpeg")}, 70)
    result = out.get("result") or {}
    message_id = result.get("message_id")
    photos = result.get("photo") or []
    file_id = str((photos[-1] or {}).get("file_id") or "") if photos else ""
    if message_id:
        threading.Thread(target=delete_message, args=(chat_id, int(message_id)), daemon=True).start()
    if not file_id:
        raise RuntimeError("上传战报图片失败：Telegram 没有返回 file_id")
    return file_id


def inline_upload_chat_id(panel: sqlite3.Row | dict[str, Any]) -> int:
    try:
        tg_id = int(panel["tg_id"])
        if tg_id > 0:
            return tg_id
    except Exception:
        pass
    if OWNER_IDS:
        return sorted(OWNER_IDS)[0]
    raise RuntimeError("没有可用于上传临时图片的 Telegram 用户")


def cleanup_inline_images() -> None:
    os.makedirs(INLINE_IMAGE_DIR, exist_ok=True)
    cutoff = time.time() - INLINE_IMAGE_TTL
    for name in os.listdir(INLINE_IMAGE_DIR):
        if not re.fullmatch(r"(?:[a-f0-9]{32}|(?:stats|node|delay|bind)-placeholder)\.(?:jpg|png)", name):
            continue
        path = os.path.join(INLINE_IMAGE_DIR, name)
        try:
            if os.path.isfile(path) and "-placeholder" not in name and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except Exception:
            log.exception("cleanup inline image failed: %s", path)
    now = time.time()
    for token, (expires, _, _) in list(INLINE_DYNAMIC_IMAGES.items()):
        if expires < now:
            INLINE_DYNAMIC_IMAGES.pop(token, None)
    for token, (expires, _, _, _) in list(INLINE_DELAY_JOBS.items()):
        if expires < now:
            INLINE_DELAY_JOBS.pop(token, None)
    for token, (expires, _, _, _, _) in list(INLINE_TEXT_JOBS.items()):
        if expires < now:
            INLINE_TEXT_JOBS.pop(token, None)


def inline_image_url(name: str) -> str:
    return f"{INLINE_PUBLIC_BASE_URL}/inline/{urllib.parse.quote(name)}"


def ensure_inline_placeholder_image(kind: str, title: str, accent: str) -> str:
    os.makedirs(INLINE_IMAGE_DIR, exist_ok=True)
    filename = f"{kind}-placeholder.jpg"
    path = os.path.join(INLINE_IMAGE_DIR, filename)
    if os.path.isfile(path):
        return inline_image_url(filename)
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (160, 160), "#101827")
        draw = ImageDraw.Draw(img)
        for x in range(0, 160, 20):
            draw.line((x, 0, x, 160), fill="#1d2b42")
        for y in range(0, 160, 20):
            draw.line((0, y, 160, y), fill="#1d2b42")
        round_rect(draw, (20, 22, 140, 138), 24, "#172237")
        draw.ellipse((52, 38, 108, 94), fill=accent)
        font_path = find_report_font()
        font = ImageFont.truetype(font_path, 24) if font_path else ImageFont.load_default()
        label = title[:4]
        try:
            tw = draw.textlength(label, font=font)
        except Exception:
            tw = len(label) * 14
        draw.text(((160 - tw) / 2, 108), label, font=font, fill="#f4f8ff")
        img.save(path, "JPEG", quality=90, optimize=True)
    except Exception:
        log.exception("create inline placeholder failed")
    return inline_image_url(filename)


def inline_thumbnail(kind: str) -> str:
    if kind == "stats":
        return ensure_inline_placeholder_image("stats", "统计", "#39a7ff")
    if kind == "node":
        return ensure_inline_placeholder_image("node", "节点", "#6de08f")
    if kind == "delay":
        return ensure_inline_placeholder_image("delay", "延迟", "#8f7cff")
    return ensure_inline_placeholder_image("bind", "私聊", "#f4d35e")


def panel_snapshot(panel: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    if isinstance(panel, dict):
        return dict(panel)
    keys = ("id", "tg_id", "name", "base_url", "api_key", "active", "created_at")
    available = set(panel.keys())
    return {key: panel[key] for key in keys if key in available}


def generate_dynamic_inline_image(token: str, path: str) -> bool:
    item = INLINE_DYNAMIC_IMAGES.get(token)
    if not item:
        return False
    expires, panel, task_id = item
    if expires < time.time():
        INLINE_DYNAMIC_IMAGES.pop(token, None)
        return False
    with INLINE_IMAGE_LOCK:
        if os.path.isfile(path):
            return True
        png_path = os.path.join(INLINE_IMAGE_DIR, f"{token}.png")
        create_delay_report_image(panel, task_id, png_path)
        try:
            from PIL import Image

            with Image.open(png_path) as image:
                image.convert("RGB").save(path, "JPEG", quality=92, optimize=True)
        finally:
            try:
                if os.path.exists(png_path):
                    os.remove(png_path)
            except Exception:
                pass
    return os.path.isfile(path)


class InlineImageHandler(http.server.BaseHTTPRequestHandler):
    def serve_inline_image(self, send_body: bool) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if not parsed.path.startswith("/inline/"):
            self.send_error(404)
            return
        name = os.path.basename(urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1]))
        if not re.fullmatch(r"(?:[a-f0-9]{32}|(?:stats|node|delay|bind)-placeholder)\.jpg", name):
            self.send_error(404)
            return
        path = os.path.join(INLINE_IMAGE_DIR, name)
        if not os.path.isfile(path) and "-placeholder" not in name:
            token = name[:-4]
            try:
                generate_dynamic_inline_image(token, path)
            except Exception:
                log.exception("dynamic inline image generation failed: %s", token)
        if not os.path.isfile(path):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Cache-Control", "public, max-age=600")
        self.send_header("Content-Length", str(os.path.getsize(path)))
        self.end_headers()
        if send_body:
            with open(path, "rb") as file:
                shutil.copyfileobj(file, self.wfile)

    def do_GET(self) -> None:
        self.serve_inline_image(True)

    def do_HEAD(self) -> None:
        self.serve_inline_image(False)

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("inline image server: " + format, *args)


def start_inline_image_server() -> None:
    global INLINE_IMAGE_SERVER_STARTED
    if INLINE_IMAGE_SERVER_STARTED:
        return
    cleanup_inline_images()
    server = http.server.ThreadingHTTPServer(("0.0.0.0", INLINE_IMAGE_PORT), InlineImageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    INLINE_IMAGE_SERVER_STARTED = True
    log.info("inline image server listening on 0.0.0.0:%s", INLINE_IMAGE_PORT)


def create_inline_delay_image(panel: sqlite3.Row | dict[str, Any], task_id: int) -> tuple[str, int, str]:
    cleanup_inline_images()
    token = secrets.token_hex(16)
    png_path = os.path.join(INLINE_IMAGE_DIR, f"{token}.png")
    jpg_path = os.path.join(INLINE_IMAGE_DIR, f"{token}.jpg")
    task_name, count = create_delay_report_image(panel, task_id, png_path)
    try:
        from PIL import Image

        with Image.open(png_path) as image:
            image.convert("RGB").save(jpg_path, "JPEG", quality=92, optimize=True)
    finally:
        try:
            if os.path.exists(png_path):
                os.remove(png_path)
        except Exception:
            pass
    return task_name, count, jpg_path


def edit_inline_message_text(inline_message_id: str, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "inline_message_id": inline_message_id,
        "text": text[:3900],
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        tg_call("editMessageText", payload, 30)
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def edit_inline_message_rich(inline_message_id: str, html_text: str, reply_markup: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "inline_message_id": inline_message_id,
        "rich_message": {
            "html": inline_rich_html(html_text)[:3900],
            "skip_entity_detection": True,
        },
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        tg_call("editMessageText", payload, 30)
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def edit_inline_message_media(inline_message_id: str, media: dict[str, Any], reply_markup: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {"inline_message_id": inline_message_id, "media": media}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    tg_call("editMessageMedia", payload, 30)


def draw_report_icon(draw: Any, image: Any, key: str, xy: tuple[int, int], size: int, fallback: str, font: Any) -> None:
    path = custom_emoji_image_path(key)
    if path:
        try:
            from PIL import Image

            with Image.open(path) as icon:
                icon = icon.convert("RGBA").resize((size, size))
                image.paste(icon, xy, icon)
                return
        except Exception:
            pass
    draw.text(xy, fallback, font=font, fill="#111827")


def custom_emoji_image_path(key: str) -> str | None:
    if key not in CUSTOM_EMOJIS:
        return None
    _, custom_emoji_id = CUSTOM_EMOJIS[key]
    cache_path = os.path.join(INLINE_IMAGE_DIR, f"emoji-{custom_emoji_id}.webp")
    if os.path.isfile(cache_path):
        return cache_path
    try:
        os.makedirs(INLINE_IMAGE_DIR, exist_ok=True)
        stickers = tg_call("getCustomEmojiStickers", {"custom_emoji_ids": [custom_emoji_id]}, 30).get("result") or []
        if not stickers:
            return None
        sticker = stickers[0]
        file_id = ((sticker.get("thumbnail") or {}).get("file_id") or sticker.get("file_id"))
        if not file_id:
            return None
        file_info = tg_call("getFile", {"file_id": file_id}, 30).get("result") or {}
        file_path = file_info.get("file_path")
        if not file_path:
            return None
        url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp, open(cache_path, "wb") as out:
            shutil.copyfileobj(resp, out)
        return cache_path
    except Exception:
        log.exception("custom emoji image fetch failed: %s", key)
        return None


def report_image_fonts() -> tuple[Any, Any, Any]:
    try:
        from PIL import ImageFont
    except Exception as exc:
        raise RuntimeError("VPS 缺少图片绘制库 python3-pil") from exc
    font_path = find_report_font()
    if not font_path:
        raise RuntimeError("VPS 缺少中文字体")
    return (
        ImageFont.truetype(font_path, 30),
        ImageFont.truetype(font_path, 24),
        ImageFont.truetype(font_path, 20),
    )


def draw_report_section(
    draw: Any,
    image: Any,
    y: int,
    icon_key: str,
    fallback: str,
    title: str,
    lines: list[str],
    title_font: Any,
    body_font: Any,
    emoji_font: Any,
) -> int:
    draw_report_icon(draw, image, icon_key, (42, y + 2), 30, fallback, emoji_font)
    draw.text((82, y), title, font=title_font, fill="#111827")
    y += 42
    for line in lines:
        draw.text((82, y), line, font=body_font, fill="#111827")
        y += 31
    return y + 26


def create_inline_text_image(panel: sqlite3.Row | dict[str, Any], kind: str, sid: int | None) -> tuple[str, str]:
    try:
        from PIL import Image, ImageDraw
    except Exception as exc:
        raise RuntimeError("VPS 缺少图片绘制库 python3-pil") from exc
    cleanup_inline_images()
    token = secrets.token_hex(16)
    output_path = os.path.join(INLINE_IMAGE_DIR, f"{token}.jpg")
    title_font, body_font, small_font = report_image_fonts()
    emoji_font = title_font

    if kind == "stats":
        nodes, latest, _ = load_panel(panel)
        online = sum(1 for node in nodes if latest.get(node_uuid(node), {}).get("online"))
        cores = sum(as_int(node.get("cpu_cores")) or as_int(node.get("cpu_physical_cores")) for node in nodes)
        ram_used = ram_total = swap_used = swap_total = disk_used = disk_total = 0
        net_in = net_out = total_down = total_up = 0
        for node in nodes:
            status = latest.get(node_uuid(node), {})
            ram_used += as_int(status.get("ram"))
            ram_total += as_int(status.get("ram_total") or node.get("mem_total"))
            swap_used += as_int(status.get("swap"))
            swap_total += as_int(status.get("swap_total") or node.get("swap_total"))
            disk_used += as_int(status.get("disk"))
            disk_total += as_int(status.get("disk_total") or node.get("disk_total"))
            net_in += as_int(status.get("net_in"))
            net_out += as_int(status.get("net_out"))
            total_down += as_int(status.get("net_total_down"))
            total_up += as_int(status.get("net_total_up"))
        balance = min(total_down, total_up) / max(total_down, total_up) * 100 if total_down and total_up else 0
        sections = [
            ("server", "🌎", "服务器", [f"在线 {online}/{len(nodes)}", f"CPU核心 {cores}"]),
            (
                "storage",
                "🧮",
                "资源占用",
                [
                    f"内存 {fmt_percent(ram_used, ram_total)}  {fmt_bytes(ram_used)} / {fmt_bytes(ram_total)}",
                    f"交换 {fmt_percent(swap_used, swap_total)}  {fmt_bytes(swap_used)} / {fmt_bytes(swap_total)}",
                    f"磁盘 {fmt_percent(disk_used, disk_total)}  {fmt_bytes(disk_used)} / {fmt_bytes(disk_total)}",
                ],
            ),
            (
                "network",
                "📥",
                "网络流量",
                [
                    f"下行 ↓ {fmt_bytes(net_in, '/s')} ｜ 累计 {fmt_bytes(total_down)}",
                    f"上行 ↑ {fmt_bytes(net_out, '/s')} ｜ 累计 {fmt_bytes(total_up)}",
                    f"对等 {balance:.2f}%",
                ],
            ),
        ]
        heading_key = "stats"
        heading = f"统计信息 · {panel['name']}"
        status = "状态  实时同步"
        caption = f"{panel['name']} · 统计信息"
    elif kind == "node" and sid is not None:
        nodes, latest, _ = load_panel(panel)
        uuid = sid_to_uuid(int(panel["id"]), sid)
        node = next((item for item in nodes if node_uuid(item) == uuid), None)
        if not node:
            raise RuntimeError("节点不存在，可能已经删除")
        status_map = latest.get(uuid, {})
        name = node_name(node)
        cpu_name = str(node.get("cpu_name") or "未知")
        ram_used = as_int(status_map.get("ram"))
        ram_total = as_int(status_map.get("ram_total") or node.get("mem_total"))
        swap_used = as_int(status_map.get("swap"))
        swap_total = as_int(status_map.get("swap_total") or node.get("swap_total"))
        disk_used = as_int(status_map.get("disk"))
        disk_total = as_int(status_map.get("disk_total") or node.get("disk_total"))
        sections = [
            (
                "info",
                "🔭",
                "基础信息",
                [
                    f"编号 ID-{sid:02d}",
                    f"IPv4 {mask_ip(node.get('ipv4')) or '无'}",
                    f"IPv6 {'有' if node.get('ipv6') else '无'}",
                    f"平台 {node.get('os') or node.get('kernel_version') or '未知'}",
                    f"架构 {node.get('arch') or '未知'}",
                    f"运行 {fmt_uptime(status_map.get('uptime'))}",
                ],
            ),
            ("cpu", "🧮", "处理器", [cpu_name, f"CPU {as_float(status_map.get('cpu')):.2f}%", f"负载 {as_float(status_map.get('load')):.2f} / {as_float(status_map.get('load5')):.2f} / {as_float(status_map.get('load15')):.2f}"]),
            (
                "storage",
                "🧮",
                "资源占用",
                [
                    f"内存 {fmt_percent(ram_used, ram_total)}  {fmt_bytes(ram_used)} / {fmt_bytes(ram_total)}",
                    f"交换 {fmt_percent(swap_used, swap_total)}  {fmt_bytes(swap_used)} / {fmt_bytes(swap_total)}",
                    f"磁盘 {fmt_percent(disk_used, disk_total)}  {fmt_bytes(disk_used)} / {fmt_bytes(disk_total)}",
                ],
            ),
            (
                "network",
                "📥",
                "网络",
                [
                    f"网速 ↓ {fmt_bytes(status_map.get('net_in'), '/s')}    ↑ {fmt_bytes(status_map.get('net_out'), '/s')}",
                    f"流量 ↓ {fmt_bytes(status_map.get('net_total_down'))}    ↑ {fmt_bytes(status_map.get('net_total_up'))}",
                ],
            ),
        ]
        heading_key = "node"
        heading = f"{name} · {'在线' if status_map.get('online') else '离线'}"
        status = ""
        caption = f"{name} · 服务器详情"
    else:
        raise RuntimeError("未知的内联图片任务")

    height = 170 + sum(52 + len(item[3]) * 31 + 26 for item in sections) + 70
    image = Image.new("RGB", (760, height), "#f7fbf8")
    draw = ImageDraw.Draw(image)
    round_rect(draw, (22, 22, 738, height - 22), 18, "#ffffff")
    draw_report_icon(draw, image, heading_key, (44, 42), 34, "📊", emoji_font)
    draw.text((88, 42), heading, font=title_font, fill="#111827")
    draw.line((44, 91, 716, 91), fill="#111827", width=4)
    y = 116
    if status:
        draw.text((44, y), status, font=body_font, fill="#111827")
        y += 54
    for icon_key, fallback, title, lines in sections:
        y = draw_report_section(draw, image, y, icon_key, fallback, title, lines, title_font, body_font, emoji_font)
    draw_report_icon(draw, image, "time", (44, height - 68), 28, "⏱", emoji_font)
    draw.text((82, height - 68), f"更新时间  {now_text()}", font=small_font, fill="#111827")
    image.save(output_path, "JPEG", quality=94, optimize=True)
    return caption, output_path


def edit_message(
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
    custom_emoji: str | None = None,
    html_text: bool = False,
) -> None:
    message_text, parse_mode = with_custom_emoji(text, custom_emoji, html_text)
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": message_text[:3900],
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        try:
            tg_call("editMessageText", payload, 30)
        except Exception:
            if not custom_emoji and not parse_mode:
                raise
            payload["text"] = text[:3900]
            if html_text:
                payload["parse_mode"] = "HTML"
            else:
                payload.pop("parse_mode", None)
            tg_call("editMessageText", payload, 30)
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def answer_callback(callback_id: str, text: str = "", alert: bool = False) -> None:
    payload: dict[str, Any] = {"callback_query_id": callback_id}
    if text:
        payload.update(text=text[:180], show_alert=alert)
    try:
        tg_call("answerCallbackQuery", payload, 20)
    except Exception:
        log.exception("answerCallbackQuery failed")


def keyboard(rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row] for row in rows]}


def menu_keyboard(tg_id: int | None = None, *, private_chat: bool = True) -> dict[str, Any]:
    rows = [
        [("📊 统计信息", "all_active"), ("🗂 面板列表", "panels")],
        [("🔗 绑定面板", "bind_help"), ("🗑 删除面板", "unbind_help")],
        [("🔎 搜索节点", "search_help"), ("🖥 节点列表", "nodes_active")],
    ]
    if private_chat:
        rows.append([("💳 续费查询", "renewals"), ("📡 延迟战报", "delay_tasks")])
    if private_chat and tg_id is not None and is_admin(tg_id):
        rows.append([("👥 用户列表", "users_panel")])
    return keyboard(rows)


def start_keyboard() -> dict[str, Any]:
    return keyboard([[("📊 统计信息", "all_active")], [("🗂 面板列表", "panels")]])


def panel_keyboard(panel_id: int, sid: int | None = None) -> dict[str, Any]:
    refresh = f"node:{panel_id}:{sid}" if sid else f"all:{panel_id}"
    return keyboard([[("刷新", refresh)], [("返回主菜单", "menu")]])


def touch_user(tg_id: int, profile: dict[str, Any] | None = None) -> None:
    if tg_id <= 0:
        return
    profile = profile or {}
    username = str(profile.get("username") or "")
    first_name = str(profile.get("first_name") or profile.get("first_name") or "")
    now = utcnow_iso()
    with db() as conn:
        conn.execute(
            "INSERT INTO users(tg_id, role, note, created_by, created_at, username, first_name, last_seen) "
            "VALUES(?, 'user', '', NULL, ?, ?, ?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET "
            "last_seen=excluded.last_seen, "
            "username=CASE WHEN excluded.username!='' THEN excluded.username ELSE users.username END, "
            "first_name=CASE WHEN excluded.first_name!='' THEN excluded.first_name ELSE users.first_name END",
            (tg_id, now, username, first_name, now),
        )


def is_banned(tg_id: int) -> bool:
    if tg_id in OWNER_IDS:
        return False
    with db() as conn:
        row = conn.execute("SELECT role FROM users WHERE tg_id=?", (tg_id,)).fetchone()
    return bool(row and row["role"] == "banned")


def is_admin(tg_id: int) -> bool:
    if tg_id in OWNER_IDS:
        return True
    with db() as conn:
        row = conn.execute("SELECT role FROM users WHERE tg_id=?", (tg_id,)).fetchone()
    return bool(row and row["role"] == "admin")


def is_allowed(tg_id: int) -> bool:
    return not is_banned(tg_id)


def require_allowed(chat_id: int, tg_id: int) -> bool:
    if is_allowed(tg_id):
        return True
    send_message(chat_id, f"你已被管理员拉黑，无法使用此机器人。\n你的 Telegram ID: {tg_id}")
    return False


def user_stats() -> tuple[int, int, int, int]:
    month_ago = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).isoformat(timespec="seconds")
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role!='banned'").fetchone()["c"]
        active = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role!='banned' AND last_seen>=?", (month_ago,)).fetchone()["c"]
        admins = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role='admin'").fetchone()["c"]
        banned = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role='banned'").fetchone()["c"]
    return int(total), int(active), int(admins), int(banned)


def user_label(row: sqlite3.Row) -> str:
    username = f"@{row['username']}" if row["username"] else ""
    first_name = row["first_name"] or ""
    note = row["note"] or ""
    parts = [str(row["tg_id"])]
    if username:
        parts.append(username)
    elif first_name:
        parts.append(first_name)
    parts.append(row["role"])
    if note:
        parts.append(note)
    return "  ".join(parts)


def bot_profile_texts() -> tuple[str, str]:
    total, active, admins, banned = user_stats()
    short = f"Komari 探针机器人｜{total} 人使用｜30天活跃 {active} 人"
    description = (
        "Komari 探针机器人\n"
        f"总用户 {total} 人 · 30天活跃 {active} 人\n"
        f"管理员 {admins} 人 · 黑名单 {banned} 人\n"
        "公开可用，每个用户的面板数据独立保存。"
    )
    return short[:120], description[:512]


def update_bot_profile(force: bool = False) -> None:
    global LAST_PROFILE_UPDATE
    now = time.time()
    if not force and now - LAST_PROFILE_UPDATE < 60:
        return
    LAST_PROFILE_UPDATE = now
    short, description = bot_profile_texts()
    try:
        tg_call("setMyShortDescription", {"short_description": short}, 30)
        tg_call("setMyDescription", {"description": description}, 30)
    except Exception:
        log.exception("update bot profile failed")


def touch_user_async(tg_id: int, profile: dict[str, Any] | None = None) -> None:
    if tg_id <= 0:
        return
    threading.Thread(target=touch_user, args=(tg_id, profile), daemon=True).start()


def update_bot_profile_async(force: bool = False) -> None:
    threading.Thread(target=update_bot_profile, args=(force,), daemon=True).start()


def normalize_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("面板地址必须以 http:// 或 https:// 开头")
    parsed = urllib.parse.urlsplit(url)
    if not parsed.netloc:
        raise ValueError("面板地址不正确")
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path.rstrip("/"), "", "")).rstrip("/")


def list_panels(tg_id: int) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute("SELECT * FROM panels WHERE tg_id=? ORDER BY active DESC, id ASC", (tg_id,)).fetchall()


def get_panel(tg_id: int, panel_id: int | None = None) -> sqlite3.Row | None:
    with db() as conn:
        if panel_id is not None:
            return conn.execute("SELECT * FROM panels WHERE tg_id=? AND id=?", (tg_id, panel_id)).fetchone()
        row = conn.execute("SELECT * FROM panels WHERE tg_id=? AND active=1 ORDER BY id LIMIT 1", (tg_id,)).fetchone()
        if row:
            return row
        return conn.execute("SELECT * FROM panels WHERE tg_id=? ORDER BY id LIMIT 1", (tg_id,)).fetchone()


def set_active_panel(tg_id: int, panel_id: int) -> bool:
    with db() as conn:
        if not conn.execute("SELECT id FROM panels WHERE tg_id=? AND id=?", (tg_id, panel_id)).fetchone():
            return False
        conn.execute("UPDATE panels SET active=0 WHERE tg_id=?", (tg_id,))
        conn.execute("UPDATE panels SET active=1 WHERE tg_id=? AND id=?", (tg_id, panel_id))
    return True


def unwrap_response(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get("error"):
            raise RuntimeError(str(value["error"]))
        if "result" in value:
            return value["result"]
        if "data" in value:
            return value["data"]
    return value


def komari_request(panel: sqlite3.Row | dict[str, Any], path: str, body: Any | None = None) -> Any:
    url = panel["base_url"].rstrip("/") + path
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if panel["api_key"]:
        headers["Authorization"] = "Bearer " + panel["api_key"]
    data = None
    method = "GET"
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"{path} HTTP {exc.code}: {detail[:250]}") from exc
    return json.loads(raw) if raw else None


def komari_rpc(panel: sqlite3.Row | dict[str, Any], method: str, params: dict[str, Any] | None = None) -> Any:
    payload = {"jsonrpc": "2.0", "id": int(time.time() * 1000) % 99999999, "method": method, "params": params or {}}
    return unwrap_response(komari_request(panel, "/api/rpc2", payload))


def normalize_nodes(value: Any) -> list[dict[str, Any]]:
    value = unwrap_response(value)
    if isinstance(value, dict):
        value = value.get("clients") or value.get("nodes") or list(value.values())
    if not isinstance(value, list):
        raise RuntimeError("节点列表返回格式无法识别")
    nodes = [dict(item) for item in value if isinstance(item, dict)]
    nodes.sort(key=lambda n: (int(n.get("weight") or 0), str(n.get("name") or "").lower(), str(n.get("uuid") or "")))
    return nodes


def normalize_latest(value: Any) -> dict[str, dict[str, Any]]:
    value = unwrap_response(value)
    out: dict[str, dict[str, Any]] = {}
    if isinstance(value, dict):
        data = value.get("data") if isinstance(value.get("data"), dict) else value
        for key, item in data.items():
            if isinstance(item, dict):
                uuid = item.get("client") or item.get("uuid") or key
                out[str(uuid)] = dict(item, client=str(uuid))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and (item.get("client") or item.get("uuid")):
                out[str(item.get("client") or item.get("uuid"))] = dict(item)
    return out


def fetch_nodes(panel: sqlite3.Row | dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[str] = []
    base_url = str(panel["base_url"])
    pref = NODE_METHOD_PREF.get(base_url)
    rpc_methods = []
    if pref and pref not in ("admin_list", "public_nodes"):
        rpc_methods.append(pref)
    for method in ("getNodes", "common:getNodes"):
        if method not in rpc_methods:
            rpc_methods.append(method)
    for method in rpc_methods:
        try:
            nodes = normalize_nodes(komari_rpc(panel, method))
            NODE_METHOD_PREF[base_url] = method
            return nodes
        except Exception as exc:
            errors.append(str(exc))
    if panel["api_key"]:
        try:
            nodes = normalize_nodes(komari_request(panel, "/api/admin/client/list"))
            NODE_METHOD_PREF[base_url] = "admin_list"
            return nodes
        except Exception as exc:
            errors.append(str(exc))
    try:
        nodes = normalize_nodes(komari_request(panel, "/api/nodes"))
        NODE_METHOD_PREF[base_url] = "public_nodes"
        return nodes
    except Exception as exc:
        errors.append(str(exc))
    raise RuntimeError("; ".join(errors[-3:]))


def fetch_latest(panel: sqlite3.Row | dict[str, Any]) -> dict[str, dict[str, Any]]:
    for method in ("getNodesLatestStatus", "common:getNodesLatestStatus"):
        try:
            return normalize_latest(komari_rpc(panel, method))
        except Exception:
            pass
    return {}


def node_uuid(node: dict[str, Any]) -> str:
    return str(node.get("uuid") or node.get("UUID") or "")


def node_name(node: dict[str, Any]) -> str:
    return str(node.get("name") or node.get("Name") or "未命名")


def ensure_sids(panel_id: int, nodes: list[dict[str, Any]]) -> dict[str, int]:
    uuids = [node_uuid(node) for node in nodes if node_uuid(node)]
    with db() as conn:
        rows = conn.execute("SELECT uuid, sid FROM node_aliases WHERE panel_id=?", (panel_id,)).fetchall()
        mapping = {row["uuid"]: int(row["sid"]) for row in rows}
        next_sid = max(mapping.values(), default=0) + 1
        for uuid in uuids:
            if uuid in mapping:
                continue
            mapping[uuid] = next_sid
            conn.execute(
                "INSERT OR IGNORE INTO node_aliases(panel_id, uuid, sid, created_at) VALUES(?,?,?,?)",
                (panel_id, uuid, next_sid, utcnow_iso()),
            )
            next_sid += 1
    return mapping


def sid_to_uuid(panel_id: int, sid: int) -> str | None:
    with db() as conn:
        row = conn.execute("SELECT uuid FROM node_aliases WHERE panel_id=? AND sid=?", (panel_id, sid)).fetchone()
    return row["uuid"] if row else None


def as_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def fmt_price(value: Any) -> str:
    amount = as_float(value)
    if amount <= 0:
        return "未填写"
    return f"{amount:.2f}".rstrip("0").rstrip(".")


def billing_period(value: Any) -> str:
    days = as_int(value)
    if days in (28, 29, 30, 31):
        return "月"
    if days in (89, 90, 91, 92, 93):
        return "季"
    if days in (364, 365, 366):
        return "年"
    if days in (729, 730, 731):
        return "两年"
    if days <= 0:
        return "次"
    return f"{days}天"


def fmt_bytes(value: Any, suffix: str = "") -> str:
    num = float(as_int(value))
    units = ["B", "K", "M", "G", "T", "P"]
    idx = 0
    while abs(num) >= 1024 and idx < len(units) - 1:
        num /= 1024
        idx += 1
    text = f"{int(num)}{units[idx]}" if idx == 0 else f"{num:.1f}{units[idx]}".replace(".0", "")
    return text + suffix


def fmt_percent(used: Any, total: Any) -> str:
    total_i = as_int(total)
    return "0.00%" if total_i <= 0 else f"{as_int(used) / total_i * 100:.2f}%"


def fmt_uptime(seconds: Any) -> str:
    days, rem = divmod(as_int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    return f"{minutes} 分钟"


def mask_ip(ip: Any) -> str:
    value = str(ip or "")
    if "." in value:
        parts = value.split(".")
        return parts[0] + "." + parts[1] + ".**.**" if len(parts) > 1 else value
    if ":" in value:
        return value.split(":", 1)[0] + ":*:*:*:*:*:*:*"
    return value


def flag_for(node: dict[str, Any]) -> str:
    text = " ".join(str(node.get(k) or "") for k in ("region", "name", "group", "tags")).lower()
    flags = {
        "香港": "🇭🇰",
        "hk": "🇭🇰",
        "台湾": "🇹🇼",
        "tw": "🇹🇼",
        "日本": "🇯🇵",
        "jp": "🇯🇵",
        "韩国": "🇰🇷",
        "kr": "🇰🇷",
        "新加坡": "🇸🇬",
        "sg": "🇸🇬",
        "美国": "🇺🇸",
        "us": "🇺🇸",
        "中国": "🇨🇳",
        "cn": "🇨🇳",
    }
    for key, flag in flags.items():
        if key.lower() in text:
            return flag
    return "🚩"


def invalidate_panel_cache(panel_id: int) -> None:
    with PANEL_CACHE_LOCK:
        PANEL_CACHE.pop(panel_id, None)


def load_panel(panel: sqlite3.Row | dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, int]]:
    panel_id = int(panel["id"])
    now = time.time()
    with PANEL_CACHE_LOCK:
        cached = PANEL_CACHE.get(panel_id)
        if cached and cached[0] > now:
            return cached[1], cached[2], cached[3]
    nodes = fetch_nodes(panel)
    latest = fetch_latest(panel)
    sid_map = ensure_sids(panel_id, nodes)
    with PANEL_CACHE_LOCK:
        PANEL_CACHE[panel_id] = (now + PANEL_CACHE_TTL, nodes, latest, sid_map)
    return nodes, latest, sid_map


def node_group(node: dict[str, Any]) -> str:
    group = str(node.get("group") or "").strip()
    return group or "默认分组"


def node_groups(nodes: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        grouped.setdefault(node_group(node), []).append(node)
    groups = sorted(grouped.items(), key=lambda item: (item[0] == "默认分组", item[0].lower()))
    for _, items in groups:
        items.sort(key=lambda node: node_name(node).lower())
    return groups


def rows_of(items: list[tuple[str, str]], size: int) -> list[list[tuple[str, str]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def node_group_text(panel: sqlite3.Row, groups: list[tuple[str, list[dict[str, Any]]]]) -> str:
    panel_name = html.escape(str(panel["name"]))
    lines = [
        f"{ce('group')} {panel_name} · 节点分组",
        "━━━━━━━━━━━━━━━━━━━━",
        "状态    等待选择",
        "提示    点击下方按钮进入分组",
        "",
    ]
    lines.extend(
        quote_block(f"{ce('group_item')} [{idx + 1:02d}] {html.escape(str(name))}    {len(items)} 台")
        for idx, (name, items) in enumerate(groups)
    )
    return "\n".join(lines)


def node_group_keyboard(panel_id: int, groups: list[tuple[str, list[dict[str, Any]]]]) -> dict[str, Any]:
    buttons = [(f"{name} · {len(items)}", f"grp:{panel_id}:{idx}") for idx, (name, items) in enumerate(groups)]
    rows = rows_of(buttons, 2)
    rows.append([("返回主菜单", "menu")])
    return keyboard(rows)


def node_list_text(panel: sqlite3.Row, group_name: str, items: list[dict[str, Any]], latest: dict[str, dict[str, Any]], sid_map: dict[str, int]) -> str:
    panel_name = html.escape(str(panel["name"]))
    group_title = html.escape(str(group_name))
    lines = [
        f"{ce('node')} {panel_name} · {group_title}",
        "━━━━━━━━━━━━━━━━━━━━",
        "状态    节点列表",
        "提示    点击下方 ID 按钮查看详情",
        "",
    ]
    for node in items:
        sid = sid_map.get(node_uuid(node), 0)
        lines.append(quote_block(f"{ce('dot')} ID-{sid:02d}  {html.escape(node_name(node))}"))
    return "\n".join(lines)


def node_list_keyboard(panel_id: int, items: list[dict[str, Any]], sid_map: dict[str, int]) -> dict[str, Any]:
    buttons = []
    for node in items:
        sid = sid_map.get(node_uuid(node), 0)
        if sid:
            buttons.append((f"ID-{sid:02d}", f"node:{panel_id}:{sid}"))
    rows = rows_of(buttons, 4)
    rows.append([("返回分组", f"nodes:{panel_id}"), ("返回主菜单", "menu")])
    return keyboard(rows)


def renewal_text(panel: sqlite3.Row) -> str:
    nodes = fetch_nodes(panel)
    today = dt.datetime.now(TZ).date()
    items: list[tuple[int, dt.datetime, dict[str, Any]]] = []
    for node in nodes:
        expired = parse_time(node.get("expired_at") or node.get("expire_time") or node.get("due_time"))
        if not expired:
            continue
        days = (expired.date() - today).days
        items.append((days, expired, node))
    items.sort(key=lambda item: (item[0], item[1], node_name(item[2]).lower()))

    panel_name = html.escape(str(panel["name"]))
    lines = [
        f"{ce('renewal')} {panel_name} · 续费查询",
        "━━━━━━━━━━━━━━━━━━━━",
        "状态    按到期时间排序",
        "范围    最近到期 10 台",
        "",
    ]
    if not items:
        lines.append("没有读取到到期时间。")
        lines.append("")
        lines.append(f"{ce('time')} 更新时间  {now_text()}")
        return "\n".join(lines)

    for idx, (days, expired, node) in enumerate(items[:10], 1):
        if days < 0:
            due_text = f"逾期 {abs(days)} 天"
        elif days == 0:
            due_text = "今天到期"
        elif days <= 7:
            due_text = f"{days} 天后"
        elif days <= 15:
            due_text = f"{days} 天后"
        else:
            due_text = f"{days} 天后"
        currency = str(node.get("currency") or "")
        price = fmt_price(node.get("price"))
        period = billing_period(node.get("billing_cycle"))
        price_text = price if price == "未填写" else f"{currency}{price}/{period}"
        auto = "｜自动续费" if node.get("auto_renewal") else ""
        date_text = expired.strftime("%m-%d %H:%M")
        lines.append(
            quote_block(
                f"{ce('dot')} [{idx:02d}] {html.escape(node_name(node).strip())}\n"
                f"到期  {due_text}｜{date_text}\n"
                f"费用  {price_text}{auto}"
            )
        )
        lines.append("")
    lines.append(f"{ce('time')} 更新时间  {now_text()}")
    return "\n".join(lines)


def fetch_ping_data(panel: sqlite3.Row | dict[str, Any], uuid: str, hours: int = 1) -> dict[str, Any]:
    query = urllib.parse.urlencode({"uuid": uuid, "hours": hours})
    value = unwrap_response(komari_request(panel, f"/api/records/ping?{query}"))
    return dict(value) if isinstance(value, dict) else {}


def fetch_ping_tasks(panel: sqlite3.Row | dict[str, Any], nodes: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    nodes = nodes if nodes is not None else fetch_nodes(panel)
    tasks: dict[int, dict[str, Any]] = {}

    def load_node_tasks(node: dict[str, Any]) -> list[dict[str, Any]]:
        uuid = node_uuid(node)
        if not uuid:
            return []
        try:
            data = fetch_ping_data(panel, uuid, 1)
        except Exception:
            return []
        return data.get("tasks") or []

    worker_count = min(8, max(1, len(nodes)))
    if worker_count == 1:
        node_task_lists = [load_node_tasks(nodes[0])] if nodes else []
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            node_task_lists = list(executor.map(load_node_tasks, nodes))

    for task_list in node_task_lists:
        for task in task_list:
            if not isinstance(task, dict):
                continue
            task_id = as_int(task.get("id"))
            if task_id:
                tasks.setdefault(task_id, dict(task))
    return sorted(tasks.values(), key=lambda item: (str(item.get("name") or "").lower(), as_int(item.get("id"))))


def delay_tasks_text(panel: sqlite3.Row, tasks: list[dict[str, Any]]) -> str:
    panel_name = html.escape(str(panel["name"]))
    lines = [
        f"{ce('delay')} {panel_name} · 延迟战报",
        "━━━━━━━━━━━━━━━━━━━━",
        "状态    选择监测任务",
        "提示    点击按钮生成图片战报",
        "",
    ]
    if tasks:
        for idx, task in enumerate(tasks, 1):
            task_type = str(task.get("type") or "icmp").upper()
            lines.append(quote_block(f"{ce('dot')} {html.escape(str(task.get('name') or '未命名'))}    {task_type}"))
    else:
        lines.append("没有读取到延迟监测任务。")
    return "\n".join(lines)


def delay_tasks_keyboard(panel_id: int, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    buttons = []
    for task in tasks:
        task_id = as_int(task.get("id"))
        if task_id:
            buttons.append((str(task.get("name") or f"任务 {task_id}")[:24], f"delay_task:{panel_id}:{task_id}"))
    rows = rows_of(buttons, 2)
    rows.append([("返回主菜单", "menu")])
    return keyboard(rows)


def delay_report_keyboard(panel_id: int, task_id: int) -> dict[str, Any]:
    return keyboard(
        [
            [("刷新战报", f"delay_task:{panel_id}:{task_id}")],
            [("返回任务", f"delay_tasks:{panel_id}"), ("返回主菜单", "menu")],
        ]
    )


def latency_value(value: Any) -> float | None:
    try:
        num = float(value)
    except Exception:
        return None
    return num if num >= 0 else None


def raw_latency_value(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def latency_display(value: float) -> str:
    return "超时" if value < 0 else f"{value:.0f}ms"


def latency_bar(value: float, max_value: float, width: int = 14) -> str:
    if value < 0:
        return "▱" * width
    if max_value <= 0:
        filled = 1
    else:
        filled = int(round(value / max_value * width))
        filled = max(1, min(width, filled))
    return "▰" * filled + "▱" * (width - filled)


def rank_badge(rank: int) -> str:
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    return medals.get(rank, f"{rank:02d}")


def delay_report_rows(panel: sqlite3.Row, task_id: int) -> tuple[str, str, list[dict[str, Any]]]:
    nodes = fetch_nodes(panel)
    rows: list[dict[str, Any]] = []
    task_name = f"任务 {task_id}"
    task_type = "ICMP"

    def load_node_ping(node: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
        uuid = node_uuid(node)
        if not uuid:
            return None
        try:
            return node, fetch_ping_data(panel, uuid, 1)
        except Exception:
            return None

    worker_count = min(8, max(1, len(nodes)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        node_data = list(executor.map(load_node_ping, nodes))

    for item in node_data:
        if item is None:
            continue
        node, data = item
        for task in data.get("tasks") or []:
            if isinstance(task, dict) and as_int(task.get("id")) == task_id:
                task_name = str(task.get("name") or task_name)
                task_type = str(task.get("type") or task_type).upper()
                break
        records = [
            record
            for record in (data.get("records") or [])
            if isinstance(record, dict) and as_int(record.get("task_id")) == task_id and raw_latency_value(record.get("value")) is not None
        ]
        if not records:
            continue
        records.sort(key=lambda record: str(record.get("time") or ""), reverse=True)
        raw_values = [raw_latency_value(record.get("value")) for record in records]
        raw_values = [value for value in raw_values if value is not None]
        values = [value for value in raw_values if value >= 0]
        if values:
            latest = raw_latency_value(records[0].get("value"))
            if latest is None or latest < 0:
                latest = values[0]
            avg = sum(values) / len(values)
            count = len(values)
        else:
            latest = -1.0
            avg = -1.0
            count = len(raw_values)
        rows.append({"name": node_name(node).strip(), "latest": latest, "avg": avg, "count": count})

    rows.sort(key=lambda item: (item["avg"] < 0, item["avg"] if item["avg"] >= 0 else 999999, item["latest"] if item["latest"] >= 0 else 999999, item["name"].lower()))
    return task_name, task_type, rows


def delay_report_text(panel: sqlite3.Row, task_id: int) -> str:
    task_name, task_type, rows = delay_report_rows(panel, task_id)
    lines = [
        f"📡 {task_name} · 网络延迟排名",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{panel['name']} · 最近 1 小时 · 按均值排序",
        "",
    ]
    if not rows:
        lines.append("没有读取到这个任务的延迟数据。")
        lines.append("")
        lines.append(f"🕒 {now_text()}")
        return "\n".join(lines)

    latest_max = max(item["latest"] for item in rows) or 1
    avg_max = max(item["avg"] for item in rows) or 1
    for idx, item in enumerate(rows[:25], 1):
        latest = float(item["latest"])
        avg = float(item["avg"])
        lines.append(f"{rank_badge(idx)} {item['name']}")
        lines.append(f"   实时 {latency_bar(latest, latest_max)} {latency_display(latest)}")
        lines.append(f"   均值 {latency_bar(avg, avg_max)} {latency_display(avg)}")
    if len(rows) > 25:
        lines.append(f"... 还有 {len(rows) - 25} 台未显示")
    lines.append("")
    lines.append(f"📍 节点 {len(rows)} 台 · {task_type} · {now_text()}")
    return "\n".join(lines)


def find_report_font() -> str | None:
    candidates = [
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    return next((path for path in candidates if os.path.exists(path)), None)


def draw_text_fit(draw: Any, xy: tuple[int, int], text: str, font: Any, fill: str, max_width: int) -> None:
    value = str(text)
    if not value:
        return
    try:
        while value and draw.textlength(value, font=font) > max_width:
            value = value[:-1]
        if value != str(text):
            value = value[:-1] + "…"
    except Exception:
        pass
    draw.text(xy, value, font=font, fill=fill)


def draw_bold_text(draw: Any, xy: tuple[int, int], text: str, font: Any, fill: str, strength: int = 1) -> None:
    x, y = xy
    offsets = [(0, 0)]
    if strength >= 1:
        offsets.extend([(1, 0), (0, 1)])
    if strength >= 2:
        offsets.extend([(1, 1), (2, 0), (0, 2)])
    for dx, dy in offsets:
        draw.text((x + dx, y + dy), text, font=font, fill=fill)


def text_width(draw: Any, text: str, font: Any) -> int:
    try:
        return int(draw.textlength(str(text), font=font))
    except Exception:
        return len(str(text)) * 20


def round_rect(draw: Any, box: tuple[int, int, int, int], radius: int, fill: str) -> None:
    if hasattr(draw, "rounded_rectangle"):
        draw.rounded_rectangle(box, radius=radius, fill=fill)
        return
    x1, y1, x2, y2 = box
    radius = max(0, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    draw.rectangle((x1 + radius, y1, x2 - radius, y2), fill=fill)
    draw.rectangle((x1, y1 + radius, x2, y2 - radius), fill=fill)
    if radius:
        draw.pieslice((x1, y1, x1 + radius * 2, y1 + radius * 2), 180, 270, fill=fill)
        draw.pieslice((x2 - radius * 2, y1, x2, y1 + radius * 2), 270, 360, fill=fill)
        draw.pieslice((x2 - radius * 2, y2 - radius * 2, x2, y2), 0, 90, fill=fill)
        draw.pieslice((x1, y2 - radius * 2, x1 + radius * 2, y2), 90, 180, fill=fill)


def rounded_bar(draw: Any, box: tuple[int, int, int, int], fraction: float, color: str, bg: str) -> None:
    x1, y1, x2, y2 = box
    radius = max(2, (y2 - y1) // 2)
    round_rect(draw, box, radius, bg)
    width = max(4, int((x2 - x1) * max(0.0, min(1.0, fraction))))
    round_rect(draw, (x1, y1, x1 + width, y2), radius, color)


def avg_color(value: float) -> tuple[str, str]:
    if value <= 80:
        return "#75d98c", "#234d36"
    if value <= 130:
        return "#6ec6ff", "#204862"
    if value <= 180:
        return "#f4d35e", "#55491f"
    return "#ff8f70", "#5b3029"


def latency_level(value: float) -> tuple[str, str, str]:
    if value < 0:
        return "超时", "#ffb3a3", "#542b24"
    if value <= 30:
        return "极速", "#69e58d", "#183f2a"
    if value <= 80:
        return "优秀", "#55c7ff", "#18364e"
    if value <= 150:
        return "良好", "#f3d565", "#4f421b"
    return "偏高", "#ff8c6d", "#542b24"


def metric_card(draw: Any, box: tuple[int, int, int, int], title: str, value: str, subtitle: str, accent: str, fonts: tuple[Any, Any, Any]) -> None:
    title_font, value_font, small_font = fonts
    x1, y1, x2, y2 = box
    round_rect(draw, box, 20, "#161f31")
    round_rect(draw, (x1, y1, x1 + 7, y2), 4, accent)
    draw.text((x1 + 24, y1 + 14), title, font=title_font, fill="#8c9ab1")
    draw_text_fit(draw, (x1 + 24, y1 + 42), subtitle, small_font, accent, max(40, x2 - x1 - 48))
    draw_bold_text(draw, (x1 + 24, y1 + 66), value, value_font, "#f4f8ff", 1)


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def rgb_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def blend_rgb(a: tuple[int, int, int], b: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    amount = max(0.0, min(1.0, amount))
    return tuple(int(a[idx] + (b[idx] - a[idx]) * amount) for idx in range(3))


def glow_layer(Image: Any, ImageDraw: Any, img: Any, center: tuple[int, int], radius: int, color: str, alpha: int) -> Any:
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    rgb = hex_rgb(color)
    for step in range(18, 0, -1):
        r = int(radius * step / 18)
        a = int(alpha * (step / 18) ** 2)
        draw.ellipse((center[0] - r, center[1] - r, center[0] + r, center[1] + r), fill=(*rgb, a))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def make_tech_background(Image: Any, ImageDraw: Any, width: int, height: int) -> Any:
    top = hex_rgb("#171a24")
    mid = hex_rgb("#101827")
    right = hex_rgb("#132742")
    img = Image.new("RGB", (width, height), "#10131d")
    draw = ImageDraw.Draw(img)
    for y in range(height):
        base = blend_rgb(top, mid, y / max(1, height))
        right_mix = blend_rgb(base, right, 0.28)
        draw.line((0, y, width, y), fill=rgb_hex(blend_rgb(base, right_mix, y / max(1, height) * 0.35)))
    img = glow_layer(Image, ImageDraw, img, (width - 140, 90), 360, "#1f7aff", 60)
    img = glow_layer(Image, ImageDraw, img, (120, height - 140), 280, "#6b5cff", 26)
    img = glow_layer(Image, ImageDraw, img, (width - 210, height // 2), 240, "#18d6a3", 18)
    draw = ImageDraw.Draw(img)
    for x in range(0, width, 42):
        color = "#202838" if x % 210 else "#2a3850"
        draw.line((x, 0, x, height), fill=color)
    for y in range(0, height, 42):
        color = "#202838" if y % 210 else "#2a3850"
        draw.line((0, y, width, y), fill=color)
    for offset in range(-140, 260, 80):
        draw.line((width - 420 + offset, 0, width + offset, 420), fill="#173a61")
    return img


def create_delay_report_image(panel: sqlite3.Row | dict[str, Any], task_id: int, output_path: str) -> tuple[str, int]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as exc:
        raise RuntimeError("VPS 缺少图片绘制库 python3-pil") from exc

    task_name, task_type, rows = delay_report_rows(panel, task_id)
    if not rows:
        raise RuntimeError("没有读取到这个任务的延迟数据")

    font_path = find_report_font()
    if not font_path:
        raise RuntimeError("VPS 缺少中文字体")

    title_font = ImageFont.truetype(font_path, 64)
    chip_font = ImageFont.truetype(font_path, 24)
    sub_font = ImageFont.truetype(font_path, 30)
    name_font = ImageFont.truetype(font_path, 36)
    row_font = ImageFont.truetype(font_path, 30)
    value_font = ImageFont.truetype(font_path, 40)
    small_font = ImageFont.truetype(font_path, 22)
    badge_font = ImageFont.truetype(font_path, 34)

    width = 1600
    row_h = 92
    top = 430
    bottom = 126
    height = top + len(rows) * row_h + bottom
    img = make_tech_background(Image, ImageDraw, width, height)
    draw = ImageDraw.Draw(img)

    avg_values = [float(item["avg"]) for item in rows]
    latest_values = [float(item["latest"]) for item in rows]
    reachable_avg_values = [value for value in avg_values if value >= 0]
    reachable_latest_values = [value for value in latest_values if value >= 0]
    fastest = min(reachable_avg_values) if reachable_avg_values else -1.0
    slowest = max(reachable_avg_values) if reachable_avg_values else -1.0
    global_avg = sum(reachable_avg_values) / len(reachable_avg_values) if reachable_avg_values else -1.0
    fastest_node = next((item["name"][:18] for item in rows if float(item["avg"]) == fastest), "全部超时")
    slowest_node = next((item["name"][:18] for item in reversed(rows) if float(item["avg"]) == slowest), "全部超时")

    round_rect(draw, (64, 52, 370, 104), 26, "#292e3d")
    draw.ellipse((88, 72, 106, 90), fill="#31e8a5")
    draw.text((124, 63), "Komari Delay Radar", font=chip_font, fill="#e2eaff")
    draw_bold_text(draw, (64, 132), task_name, title_font, "#2f8cff", 2)
    title_x = 64 + text_width(draw, task_name, title_font) + 24
    draw_bold_text(draw, (title_x, 132), "网络延迟排名", title_font, "#f8fbff", 1)
    draw.text((68, 214), f"{panel['name']} · VPS 实时探测 · 最近 1 小时均值排序", font=sub_font, fill="#aeb9cc")

    round_rect(draw, (1068, 50, 1538, 192), 26, "#151d2d")
    draw_bold_text(draw, (1110, 76), f"{len(rows)} 台 VPS", value_font, "#f5f9ff", 1)
    draw.text((1112, 132), f"{task_type} / 采样 {rows[0]['count']} 次", font=small_font, fill="#8f9bb2")
    round_rect(draw, (1328, 82, 1362, 114), 8, "#39a7ff")
    draw.text((1384, 82), "实时延迟", font=small_font, fill="#dce6f7")
    round_rect(draw, (1328, 130, 1362, 162), 8, "#6de08f")
    draw.text((1384, 130), "1小时均值", font=small_font, fill="#dce6f7")

    card_fonts = (small_font, value_font, small_font)
    metric_card(draw, (64, 278, 420, 392), "最快节点", latency_display(fastest), fastest_node, "#31e8a5", card_fonts)
    metric_card(draw, (440, 278, 796, 392), "全局均值", latency_display(global_avg), "可达节点平均表现" if reachable_avg_values else "没有可达采样", "#39a7ff", card_fonts)
    metric_card(draw, (816, 278, 1172, 392), "最高延迟", latency_display(slowest), slowest_node, "#f4d35e", card_fonts)
    metric_card(draw, (1192, 278, 1536, 392), "实时峰值", latency_display(max(reachable_latest_values) if reachable_latest_values else -1.0), "当前采样最大值" if reachable_latest_values else "没有可达采样", "#ff8c6d", card_fonts)

    draw.arc((1080, 0, 1700, 620), 205, 345, fill="#1f6ca8")
    draw.arc((1160, 58, 1630, 528), 200, 330, fill="#1a4f79")

    latest_max = max([item["latest"] for item in rows if item["latest"] >= 0] or [1])
    avg_max = max([item["avg"] for item in rows if item["avg"] >= 0] or [1])
    for idx, item in enumerate(rows, 1):
        y = top + (idx - 1) * row_h
        card_fill = "#1a2233" if idx % 2 else "#151e2e"
        round_rect(draw, (48, y, width - 48, y + 80), 20, card_fill)
        accent = "#ffd34d" if idx == 1 else "#dbe7fb" if idx == 2 else "#d39150" if idx == 3 else "#53637c"
        round_rect(draw, (48, y, 60, y + 80), 6, accent)
        draw.ellipse((76, y + 14, 140, y + 78), fill=accent)
        draw.text((100 if idx < 10 else 90, y + 24), str(idx), font=badge_font, fill="#121826")
        draw_text_fit(draw, (160, y + 22), item["name"], name_font, "#f0f6ff", 260)

        latest = float(item["latest"])
        avg = float(item["avg"])
        draw.text((430, y + 16), "LIVE", font=small_font, fill="#74839c")
        draw.text((438, y + 52), "AVG", font=small_font, fill="#74839c")
        rounded_bar(draw, (516, y + 15, 1112, y + 42), 0 if latest < 0 else latest / latest_max, "#39a7ff", "#27344e")
        draw.text((1138, y + 8), latency_display(latest), font=row_font, fill="#9bd3ff")
        rounded_bar(draw, (516, y + 51, 1112, y + 78), 0 if avg < 0 else avg / avg_max, "#6de08f", "#27344e")
        draw.text((1138, y + 44), latency_display(avg), font=row_font, fill="#a2ecb8")

        level, pill_fill, pill_text = latency_level(avg)
        round_rect(draw, (1360, y + 20, 1538, y + 60), 20, pill_text)
        draw.text((1382, y + 21), f"{level} {latency_display(avg)}", font=row_font, fill=pill_fill)

    footer_time = now_text()
    draw.text((68, height - 58), f"节点 {len(rows)} 台 · 测试 {rows[0]['count']} 次 · {task_type}", font=small_font, fill="#8792a7")
    try:
        time_x = width - 52 - int(draw.textlength(footer_time, font=small_font))
    except Exception:
        time_x = 920
    draw.text((time_x, height - 58), footer_time, font=small_font, fill="#8792a7")
    img.save(output_path, "PNG", optimize=True)
    return task_name, len(rows)


def aggregate_text_from_data(panel: sqlite3.Row | dict[str, Any], nodes: list[dict[str, Any]], latest: dict[str, dict[str, Any]]) -> str:
    online = cores = ram_used = ram_total = swap_used = swap_total = disk_used = disk_total = 0
    net_in = net_out = total_down = total_up = 0
    for node in nodes:
        status = latest.get(node_uuid(node), {})
        online += 1 if status.get("online") else 0
        cores += as_int(node.get("cpu_cores")) or as_int(node.get("cpu_physical_cores"))
        ram_used += as_int(status.get("ram"))
        ram_total += as_int(status.get("ram_total") or node.get("mem_total"))
        swap_used += as_int(status.get("swap"))
        swap_total += as_int(status.get("swap_total") or node.get("swap_total"))
        disk_used += as_int(status.get("disk"))
        disk_total += as_int(status.get("disk_total") or node.get("disk_total"))
        net_in += as_int(status.get("net_in"))
        net_out += as_int(status.get("net_out"))
        total_down += as_int(status.get("net_total_down"))
        total_up += as_int(status.get("net_total_up"))
    balance = min(total_down, total_up) / max(total_down, total_up) * 100 if total_down and total_up else 0
    panel_name = html.escape(str(panel["name"]))
    return (
        f"{ce('stats')} 统计信息 · {panel_name}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "状态    实时同步\n\n"
        f"{ce('server')} 服务器\n"
        f"在线    {online}/{len(nodes)}\n"
        f"{ce('cpu')} CPU核心  {cores}\n\n"
        f"{ce('storage')} 资源占用\n"
        f"内存    {fmt_percent(ram_used, ram_total):>8}   {fmt_bytes(ram_used)} / {fmt_bytes(ram_total)}\n"
        f"交换    {fmt_percent(swap_used, swap_total):>8}   {fmt_bytes(swap_used)} / {fmt_bytes(swap_total)}\n"
        f"磁盘    {fmt_percent(disk_used, disk_total):>8}   {fmt_bytes(disk_used)} / {fmt_bytes(disk_total)}\n\n"
        f"{ce('network')} 网络流量\n"
        f"下行    ↓ {fmt_bytes(net_in, '/s')}  ｜累计 {fmt_bytes(total_down)}\n"
        f"上行    ↑ {fmt_bytes(net_out, '/s')}  ｜累计 {fmt_bytes(total_up)}\n"
        f"对等    {balance:.2f}%\n\n"
        f"{ce('time')} 更新时间  {now_text()}"
    )


def aggregate_text(panel: sqlite3.Row) -> str:
    nodes, latest, _ = load_panel(panel)
    return aggregate_text_from_data(panel, nodes, latest)


def aggregate_plain_text_from_data(panel: sqlite3.Row | dict[str, Any], nodes: list[dict[str, Any]], latest: dict[str, dict[str, Any]]) -> str:
    online = cores = ram_used = ram_total = swap_used = swap_total = disk_used = disk_total = 0
    net_in = net_out = total_down = total_up = 0
    for node in nodes:
        status = latest.get(node_uuid(node), {})
        online += 1 if status.get("online") else 0
        cores += as_int(node.get("cpu_cores")) or as_int(node.get("cpu_physical_cores"))
        ram_used += as_int(status.get("ram"))
        ram_total += as_int(status.get("ram_total") or node.get("mem_total"))
        swap_used += as_int(status.get("swap"))
        swap_total += as_int(status.get("swap_total") or node.get("swap_total"))
        disk_used += as_int(status.get("disk"))
        disk_total += as_int(status.get("disk_total") or node.get("disk_total"))
        net_in += as_int(status.get("net_in"))
        net_out += as_int(status.get("net_out"))
        total_down += as_int(status.get("net_total_down"))
        total_up += as_int(status.get("net_total_up"))
    balance = min(total_down, total_up) / max(total_down, total_up) * 100 if total_down and total_up else 0
    panel_name = str(panel["name"])
    return (
        f"📊 统计信息 · {panel_name}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🖥 服务器    {online}/{len(nodes)} 在线\n"
        f"🧠 CPU核心    {cores}\n\n"
        "💾 资源占用\n"
        f"内存    {fmt_percent(ram_used, ram_total):>8}   {fmt_bytes(ram_used)} / {fmt_bytes(ram_total)}\n"
        f"交换    {fmt_percent(swap_used, swap_total):>8}   {fmt_bytes(swap_used)} / {fmt_bytes(swap_total)}\n"
        f"磁盘    {fmt_percent(disk_used, disk_total):>8}   {fmt_bytes(disk_used)} / {fmt_bytes(disk_total)}\n\n"
        "🌐 网络流量\n"
        f"实时下行    ↓ {fmt_bytes(net_in, '/s')}\n"
        f"实时上行    ↑ {fmt_bytes(net_out, '/s')}\n"
        f"累计下行    ↓ {fmt_bytes(total_down)}\n"
        f"累计上行    ↑ {fmt_bytes(total_up)}\n"
        f"流量对等    {balance:.2f}%\n\n"
        f"🕘 {now_text()}"
    )


def aggregate_plain_text(panel: sqlite3.Row | dict[str, Any]) -> str:
    nodes, latest, _ = load_panel(panel)
    return aggregate_plain_text_from_data(panel, nodes, latest)


def detail_text_from_node(panel: sqlite3.Row | dict[str, Any], sid: int, node: dict[str, Any], status: dict[str, Any]) -> str:
    name = html.escape(node_name(node))
    cpu_name = html.escape(str(node.get("cpu_name") or "未知"))
    physical = as_int(node.get("cpu_physical_cores"))
    if physical and "physical" not in cpu_name.lower():
        cpu_name += f" {physical} Physical Core"
    ram_used = as_int(status.get("ram"))
    ram_total = as_int(status.get("ram_total") or node.get("mem_total"))
    swap_used = as_int(status.get("swap"))
    swap_total = as_int(status.get("swap_total") or node.get("swap_total"))
    disk_used = as_int(status.get("disk"))
    disk_total = as_int(status.get("disk_total") or node.get("disk_total"))
    online_text = "在线" if status.get("online") else "离线"
    return (
        f"{ce('node')} {name} · {online_text}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        + quote_block(
            f"编号  ID-{sid:02d}\n"
            f"IPv4  {mask_ip(node.get('ipv4')) or '无'}\n"
            f"IPv6  {'有' if node.get('ipv6') else '无'}\n"
            f"平台  {html.escape(str(node.get('os') or node.get('kernel_version') or '未知'))}\n"
            f"架构  {html.escape(str(node.get('arch') or '未知'))}\n"
            f"运行  {fmt_uptime(status.get('uptime'))}"
        )
        + "\n\n"
        + quote_block(
            f"{ce('cpu')} 处理器\n"
            f"{cpu_name}\n"
            f"CPU   {as_float(status.get('cpu')):.2f}%\n"
            f"负载  {as_float(status.get('load')):.2f} / {as_float(status.get('load5')):.2f} / {as_float(status.get('load15')):.2f}"
        )
        + "\n\n"
        + quote_block(
            f"{ce('storage')} 资源占用\n"
            f"内存  {fmt_percent(ram_used, ram_total):>8}   {fmt_bytes(ram_used)} / {fmt_bytes(ram_total)}\n"
            f"交换  {fmt_percent(swap_used, swap_total):>8}   {fmt_bytes(swap_used)} / {fmt_bytes(swap_total)}\n"
            f"磁盘  {fmt_percent(disk_used, disk_total):>8}   {fmt_bytes(disk_used)} / {fmt_bytes(disk_total)}"
        )
        + "\n\n"
        + quote_block(
            f"{ce('network')} 网络\n"
            f"网速  ↓ {fmt_bytes(status.get('net_in'), '/s')}    ↑ {fmt_bytes(status.get('net_out'), '/s')}\n"
            f"流量  ↓ {fmt_bytes(status.get('net_total_down'))}    ↑ {fmt_bytes(status.get('net_total_up'))}"
        )
        + "\n\n"
        f"{ce('time')} 更新时间  {now_text()}"
    )


def detail_text(panel: sqlite3.Row, sid: int) -> str:
    nodes, latest, _ = load_panel(panel)
    uuid = sid_to_uuid(int(panel["id"]), sid)
    if not uuid:
        raise RuntimeError("没有找到这个编号，请先 /search 关键词 刷新节点索引")
    node = next((item for item in nodes if node_uuid(item) == uuid), None)
    if not node:
        raise RuntimeError("节点不存在，可能已经删除")
    return detail_text_from_node(panel, sid, node, latest.get(uuid, {}))


def detail_plain_text_from_node(panel: sqlite3.Row | dict[str, Any], sid: int, node: dict[str, Any], status: dict[str, Any]) -> str:
    name = node_name(node)
    cpu_name = str(node.get("cpu_name") or "未知")
    physical = as_int(node.get("cpu_physical_cores"))
    if physical and "physical" not in cpu_name.lower():
        cpu_name += f" {physical} Physical Core"
    ram_used = as_int(status.get("ram"))
    ram_total = as_int(status.get("ram_total") or node.get("mem_total"))
    swap_used = as_int(status.get("swap"))
    swap_total = as_int(status.get("swap_total") or node.get("swap_total"))
    disk_used = as_int(status.get("disk"))
    disk_total = as_int(status.get("disk_total") or node.get("disk_total"))
    online_text = "在线" if status.get("online") else "离线"
    return (
        f"🖥 {name} · {online_text}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"编号  ID-{sid:02d}\n"
        f"IPv4  {mask_ip(node.get('ipv4')) or '无'}\n"
        f"IPv6  {'有' if node.get('ipv6') else '无'}\n"
        f"平台  {node.get('os') or node.get('kernel_version') or '未知'}\n"
        f"架构  {node.get('arch') or '未知'}\n"
        f"运行  {fmt_uptime(status.get('uptime'))}\n\n"
        "🧠 处理器\n"
        f"{cpu_name}\n"
        f"CPU   {as_float(status.get('cpu')):.2f}%\n"
        f"负载  {as_float(status.get('load')):.2f} / {as_float(status.get('load5')):.2f} / {as_float(status.get('load15')):.2f}\n\n"
        "💾 资源占用\n"
        f"内存  {fmt_percent(ram_used, ram_total):>8}   {fmt_bytes(ram_used)} / {fmt_bytes(ram_total)}\n"
        f"交换  {fmt_percent(swap_used, swap_total):>8}   {fmt_bytes(swap_used)} / {fmt_bytes(swap_total)}\n"
        f"磁盘  {fmt_percent(disk_used, disk_total):>8}   {fmt_bytes(disk_used)} / {fmt_bytes(disk_total)}\n\n"
        "🌐 网络\n"
        f"网速  ↓ {fmt_bytes(status.get('net_in'), '/s')}    ↑ {fmt_bytes(status.get('net_out'), '/s')}\n"
        f"流量  ↓ {fmt_bytes(status.get('net_total_down'))}    ↑ {fmt_bytes(status.get('net_total_up'))}\n\n"
        f"🕘 {now_text()}"
    )


def detail_plain_text(panel: sqlite3.Row | dict[str, Any], sid: int) -> str:
    nodes, latest, _ = load_panel(panel)
    uuid = sid_to_uuid(int(panel["id"]), sid)
    if not uuid:
        raise RuntimeError("没有找到这个编号，请先 /search 关键词 刷新节点索引")
    node = next((item for item in nodes if node_uuid(item) == uuid), None)
    if not node:
        raise RuntimeError("节点不存在，可能已经删除")
    return detail_plain_text_from_node(panel, sid, node, latest.get(uuid, {}))


def search_text(panel: sqlite3.Row, keyword: str) -> str:
    nodes, latest, sid_map = load_panel(panel)
    kw = keyword.lower()
    matches = []
    for node in nodes:
        haystack = " ".join(str(node.get(k) or "") for k in ("name", "group", "tags", "region", "os", "ipv4", "ipv6")).lower()
        if kw in haystack:
            matches.append((sid_map.get(node_uuid(node), 0), node, latest.get(node_uuid(node), {})))
    if not matches:
        return f"没有找到包含 {keyword!r} 的节点。"
    matches.sort(key=lambda item: item[0])
    lines = [f"{ce('info')} 搜索结果 · {html.escape(keyword)}", "━━━━━━━━━━━━━━━━━━━━", "状态    命中节点"]
    lines.extend(quote_block(f"{ce('dot')} ID-{sid:02d}  {html.escape(node_name(node))}") for sid, node, status in matches[:80])
    if len(matches) > 80:
        lines.append(f"... 还有 {len(matches) - 80} 个结果")
    return "\n".join(lines)


def menu_text(tg_id: int) -> str:
    panel = get_panel(tg_id)
    current = f"当前面板: {html.escape(str(panel['name']))} (#{panel['id']})" if panel else "当前面板: 未绑定"
    last_line = "节点查询    用户权限" if is_admin(tg_id) else "节点查询    快捷操作"
    return (
        f"{ce('panel')} Komari 功能菜单\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{current}\n\n"
        "请选择下面的功能按钮\n"
        + code_block(
            "状态统计    面板管理\n"
            "续费提醒    延迟战报\n"
            f"{last_line}"
        )
    )


def start_text(tg_id: int) -> str:
    panel = get_panel(tg_id)
    current = f"当前面板: {html.escape(str(panel['name']))} (#{panel['id']})" if panel else "当前面板: 未绑定"
    group_hint = html.escape(f"/all@{BOT_USERNAME} 或 @{BOT_USERNAME} /all" if BOT_USERNAME else "@机器人 /all")
    admin_help = (
        f"\n\n{ce('users')} 管理员命令\n"
        + code_block(
            "/admin    用户ID 备注   升级管理员\n"
            "/unadmin  用户ID        取消管理员\n"
            "/ban      用户ID 原因   拉黑用户\n"
            "/unban    用户ID        解除拉黑\n"
            "/users                 用户列表"
        )
        if is_admin(tg_id)
        else ""
    )
    return (
        f"{ce('console')} Komari 探针机器人\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{current}\n\n"
        f"{ce('info')} 常用命令\n"
        + code_block(
            "/bind    面板URL APIKEY 备注\n"
            "/panels                  面板列表\n"
            "/use     面板ID           切换面板\n"
            "/all                     统计信息\n"
            "/search  关键词           搜索节点\n"
            "/sid     编号             节点详情"
        )
        + "\n\n"
        f"{ce('network')} 群组用法\n{group_hint}"
        f"{admin_help}"
    )


def panels_text(rows: list[sqlite3.Row]) -> str:
    lines = ["状态  ID    名称                 模式          地址"]
    for row in rows:
        mark = "*" if row["active"] else " "
        mode = "管理员Token" if row["api_key"] else "公开模式"
        name = str(row["name"])[:18]
        lines.append(f"{mark}     #{row['id']:<4} {name:<18} {mode:<12} {row['base_url']}")
    return f"{ce('panel')} 面板列表\n━━━━━━━━━━━━━━━━━━━━\n" + code_block("\n".join(lines))


def users_text(rows: list[sqlite3.Row], total: int, active: int, admins: int, banned: int) -> str:
    lines = [
        f"{ce('users')} 用户列表",
        "━━━━━━━━━━━━━━━━━━━━",
        f"总人数 {total} · 30天活跃 {active} · 管理员 {admins} · 黑名单 {banned}",
        "",
    ]
    table = ["用户ID        角色       备注"]
    table.extend(user_label(row) for row in rows)
    return "\n".join(lines) + code_block("\n".join(table))


def parse_command(message: dict[str, Any]) -> tuple[str, str] | None:
    text = (message.get("text") or "").strip()
    if not text:
        return None
    chat_type = (message.get("chat") or {}).get("type", "private")
    addressed = chat_type == "private"
    bot_mention = "@" + BOT_USERNAME.lower() if BOT_USERNAME else ""
    if bot_mention and text.lower().startswith(bot_mention):
        text = text[len(bot_mention) :].strip()
        addressed = True
    reply_from = ((message.get("reply_to_message") or {}).get("from") or {})
    if BOT_USERNAME and str(reply_from.get("username") or "").lower() == BOT_USERNAME.lower():
        addressed = True
    if text.startswith("/"):
        rest = text[1:]
        first, _, args = rest.partition(" ")
        command, at, target = first.partition("@")
        if at and BOT_USERNAME and target.lower() != BOT_USERNAME.lower():
            return None
        return ALIASES.get(command.lower()) or ALIASES.get(command) or command.lower(), args.strip()
    if addressed:
        first, _, args = text.partition(" ")
        command = ALIASES.get(first.lower()) or ALIASES.get(first)
        if command:
            return command, args.strip()
    return None


def answer_inline_query(inline_query_id: str, results: list[dict[str, Any]], cache_time: int = 0) -> None:
    payload = {
        "inline_query_id": inline_query_id,
        "results": results[:50],
        "cache_time": cache_time,
        "is_personal": True,
    }
    tg_call("answerInlineQuery", payload, 30)


def inline_article_result(
    result_id: str,
    title: str,
    description: str,
    message_text: str,
    reply_markup: dict[str, Any] | None = None,
    thumbnail_url: str | None = None,
    parse_mode: str | None = None,
) -> dict[str, Any]:
    if parse_mode == "HTML" and "<tg-emoji" in message_text:
        input_message_content: dict[str, Any] = {
            "rich_message": {
                "html": inline_rich_html(message_text)[:3900],
                "skip_entity_detection": True,
            }
        }
    else:
        input_message_content = {"message_text": message_text[:3900]}
    if parse_mode and "message_text" in input_message_content:
        input_message_content["parse_mode"] = parse_mode
    inline_result_id = result_id if result_id.startswith("delay:") else f"{result_id}:{INLINE_RESULT_VERSION}"
    result: dict[str, Any] = {
        "type": "article",
        "id": inline_result_id[:64],
        "title": title[:96],
        "description": description[:160],
        "input_message_content": input_message_content,
    }
    if reply_markup:
        result["reply_markup"] = reply_markup
    return result


def inline_private_prompt_result(result_id: str = "private-bind") -> dict[str, Any]:
    return inline_article_result(
        result_id,
        "请去私聊绑定/操作",
        "先在机器人私聊绑定或切换 Komari 面板，再使用内联查询。",
        "Komari探针监控\n请去私聊绑定/操作。",
        private_chat_keyboard(),
        inline_thumbnail("bind"),
    )


def inline_node_matches(
    nodes: list[dict[str, Any]], latest: dict[str, dict[str, Any]], sid_map: dict[str, int], query: str
) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
    keyword = query.strip().lower()
    sid_query = re.fullmatch(r"(?:id[-_\s]*)?0*(\d+)", keyword or "")
    if not sid_query:
        return []
    wanted_sid = int(sid_query.group(1))
    matches: list[tuple[int, int, dict[str, Any], dict[str, Any]]] = []
    for node in nodes:
        uuid = node_uuid(node)
        sid = sid_map.get(uuid, 0)
        if not sid:
            continue
        if sid != wanted_sid:
            continue
        matches.append((0, sid, node, latest.get(uuid, {})))
    matches.sort(key=lambda item: (item[0], item[1], node_name(item[2]).lower()))
    return [(sid, node, status) for _, sid, node, status in matches[:1]]


def inline_node_detail_result(
    panel: sqlite3.Row | dict[str, Any],
    nodes: list[dict[str, Any]],
    latest: dict[str, dict[str, Any]],
    sid_map: dict[str, int],
    query: str,
    show_refresh: bool = True,
) -> dict[str, Any]:
    sid = inline_sid_query(query)
    if sid is None:
        return inline_article_result(
            f"node-help:{panel['id']}",
            "🖥 服务器详情",
            "请输入",
            f"🖥 服务器详情\n━━━━━━━━━━━━━━━━━━━━\n请在 {bot_mention()} 后面输入节点编号。\n\n示例：{bot_mention()} 01",
            thumbnail_url=inline_thumbnail("node"),
        )
    matches = inline_node_matches(nodes, latest, sid_map, query)
    if not matches:
        return inline_article_result(
            f"node-missing:{panel['id']}:{sid}",
            "🖥 服务器详情",
            "请输入",
            f"没有找到编号 ID-{sid:02d}。\n请先在机器人私聊菜单里打开节点列表确认编号。",
            thumbnail_url=inline_thumbnail("node"),
        )
    matched_sid, node, status = matches[0]
    if not show_refresh:
        return inline_article_result(
            f"node-static:{panel['id']}:{matched_sid}",
            "🖥 服务器详情",
            f"{node_name(node)} · {'在线' if status.get('online') else '离线'}",
            detail_plain_text_from_node(panel, matched_sid, node, status),
            thumbnail_url=inline_thumbnail("node"),
        )
    return inline_text_job_result(
        "node",
        panel,
        matched_sid,
        "🖥 服务器详情",
        f"{node_name(node)} · {'在线' if status.get('online') else '离线'}",
        show_refresh,
    )


def inline_text_keyboard(token: str, label: str = "刷新数据") -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": label, "callback_data": f"inline_text:{token}"}]]}


def register_inline_text_job(kind: str, panel: sqlite3.Row | dict[str, Any], sid: int | None = None, show_refresh: bool = True) -> str:
    cleanup_inline_images()
    token = secrets.token_hex(12)
    INLINE_TEXT_JOBS[token] = (time.time() + INLINE_IMAGE_TTL, kind, panel_snapshot(panel), sid, show_refresh)
    return token


def inline_loading_text(title: str, description: str) -> str:
    return f"{title}\n━━━━━━━━━━━━━━━━━━━━\n{description}\n正在读取数据，请稍等。"


def inline_loading_rich_text(kind: str, title: str) -> str:
    icon_key = "stats" if kind == "stats" else "node"
    return (
        f"{ce(icon_key)} {html.escape(title)}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{ce('info')} 点击发送数据\n"
        "正在读取数据，请稍等。"
    )


def inline_text_job_result(
    kind: str,
    panel: sqlite3.Row | dict[str, Any],
    sid: int | None,
    title: str,
    description: str,
    show_refresh: bool = True,
) -> dict[str, Any]:
    token = register_inline_text_job(kind, panel, sid, show_refresh)
    loading_text = inline_loading_text(title, "点击发送数据")
    return inline_article_result(
        f"inline_text:{token}",
        title,
        description,
        loading_text,
        inline_text_keyboard(token),
        thumbnail_url=inline_thumbnail("stats" if kind == "stats" else "node"),
    )


def finish_inline_text_job(inline_message_id: str, token: str) -> None:
    job = INLINE_TEXT_JOBS.get(token)
    if not job:
        edit_inline_message_text(inline_message_id, f"这条内联数据已经过期，请重新打开 {bot_mention()} 发送。")
        return
    expires, kind, panel, sid, show_refresh = job
    if expires < time.time():
        INLINE_TEXT_JOBS.pop(token, None)
        edit_inline_message_text(inline_message_id, f"这条内联数据已经过期，请重新打开 {bot_mention()} 发送。")
        return
    try:
        if show_refresh:
            if kind == "stats":
                html_text = aggregate_text(panel)
            elif kind == "node" and sid is not None:
                html_text = detail_text(panel, sid)
            else:
                raise RuntimeError("未知的内联任务")
            edit_inline_message_rich(inline_message_id, html_text, inline_text_keyboard(token, "刷新数据"))
        else:
            if kind == "stats":
                plain_text = aggregate_plain_text(panel)
            elif kind == "node" and sid is not None:
                plain_text = detail_plain_text(panel, sid)
            else:
                raise RuntimeError("未知的内联任务")
            edit_inline_message_text(inline_message_id, plain_text)
    except Exception as exc:
        log.exception("finish inline text job failed")
        edit_inline_message_text(
            inline_message_id,
            f"读取失败：{exc}\n请稍后再试，或回机器人私聊菜单重新打开。",
            inline_text_keyboard(token, "再试一次"),
        )


def start_inline_text_job(inline_message_id: str, token: str) -> None:
    thread = threading.Thread(target=finish_inline_text_job, args=(inline_message_id, token), daemon=True)
    thread.start()


def inline_sid_query(query: str) -> int | None:
    match = re.fullmatch(r"(?:id[-_\s]*)?0*(\d+)", query.strip().lower())
    return int(match.group(1)) if match else None


def inline_task_matches(task: dict[str, Any], query: str) -> bool:
    keyword = query.strip().lower()
    if not keyword:
        return True
    special = {"delay", "ping", "icmp", "tcp", "延迟", "战报", "测速", "延迟检测"}
    if keyword in special:
        return True
    haystack = f"{task.get('name') or ''} {task.get('type') or ''} {task.get('id') or ''}".lower()
    return keyword in haystack


def delay_loading_text(panel: sqlite3.Row | dict[str, Any], task_name: str) -> str:
    return (
        "📡 Komari Delay Radar\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"面板  {panel['name']}\n"
        f"任务  {task_name}\n\n"
        "▰▰▰▱▱  雷达阵列正在点亮\n"
        "正在读取节点延迟、整理排名、渲染战报。\n\n"
        "如果图片没有自动出现，请点击下面的按钮。"
    )


def inline_delay_keyboard(token: str, label: str = "生成延迟战报") -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": label, "callback_data": f"inline_delay:{token}"}]]}


def register_inline_delay_job(panel: sqlite3.Row | dict[str, Any], task_id: int, task_name: str) -> str:
    cleanup_inline_images()
    token = secrets.token_hex(12)
    INLINE_DELAY_JOBS[token] = (time.time() + INLINE_IMAGE_TTL, panel_snapshot(panel), task_id, task_name)
    return token


def inline_delay_photo_result(panel: sqlite3.Row | dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    panel_id = int(panel["id"])
    task_id = as_int(task.get("id"))
    if not task_id:
        raise RuntimeError("延迟任务缺少 ID")
    task_name = str(task.get("name") or f"任务 {task_id}")
    token = register_inline_delay_job(panel, task_id, task_name)
    return inline_article_result(
        f"delay:{token}",
        f"📡 {task_name}",
        "点击发送加载提示，随后生成延迟排名图片",
        delay_loading_text(panel, task_name),
        inline_delay_keyboard(token),
        inline_thumbnail("delay"),
    )


def finish_inline_delay_report(inline_message_id: str, token: str) -> None:
    job = INLINE_DELAY_JOBS.get(token)
    if not job:
        edit_inline_message_text(
            inline_message_id,
            f"📡 Komari Delay Radar\n━━━━━━━━━━━━━━━━━━━━\n这个延迟战报任务已经过期，请重新打开 {bot_mention()} 菜单选择。",
        )
        return
    expires, panel, task_id, task_name = job
    if expires < time.time():
        INLINE_DELAY_JOBS.pop(token, None)
        edit_inline_message_text(
            inline_message_id,
            f"📡 Komari Delay Radar\n━━━━━━━━━━━━━━━━━━━━\n这个延迟战报任务已经过期，请重新打开 {bot_mention()} 菜单选择。",
        )
        return
    try:
        total_started = time.perf_counter()
        edit_inline_message_text(
            inline_message_id,
            (
                "📡 Komari Delay Radar\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"任务  {task_name}\n\n"
                "▰▰▰▰▱  数据已接入，正在绘制排名图。\n"
                "请稍等，马上把战报贴上来。"
            ),
            inline_delay_keyboard(token, "重新生成"),
        )
        progress_seconds = time.perf_counter() - total_started
        render_started = time.perf_counter()
        generated_name, count, image_path = create_inline_delay_image(panel, task_id)
        render_seconds = time.perf_counter() - render_started
        try:
            upload_started = time.perf_counter()
            file_id = upload_photo_file_id(inline_upload_chat_id(panel), image_path)
            upload_seconds = time.perf_counter() - upload_started
            edit_started = time.perf_counter()
            edit_inline_message_media(
                inline_message_id,
                {
                    "type": "photo",
                    "media": file_id,
                    "caption": f"{generated_name} · 延迟排名 · {count} 台 VPS",
                },
            )
            edit_seconds = time.perf_counter() - edit_started
            log.info(
                "inline delay report task=%s progress=%.3fs render=%.3fs upload=%.3fs edit=%.3fs total=%.3fs",
                task_id,
                progress_seconds,
                render_seconds,
                upload_seconds,
                edit_seconds,
                time.perf_counter() - total_started,
            )
        finally:
            try:
                if os.path.exists(image_path):
                    os.remove(image_path)
            except Exception:
                pass
    except Exception as exc:
        log.exception("finish inline delay report failed")
        edit_inline_message_text(
            inline_message_id,
            (
                "📡 Komari Delay Radar\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"任务  {task_name}\n\n"
                f"生成失败：{exc}\n"
                "可以稍后再试，或回私聊菜单重新打开延迟战报。"
            ),
            inline_delay_keyboard(token, "再试一次"),
        )


def start_inline_delay_report(inline_message_id: str, token: str) -> None:
    thread = threading.Thread(target=finish_inline_delay_report, args=(inline_message_id, token), daemon=True)
    thread.start()


def strip_inline_version(result_id: str) -> str:
    suffix = f":{INLINE_RESULT_VERSION}"
    return result_id[: -len(suffix)] if result_id.endswith(suffix) else result_id


def handle_chosen_inline_result(result: dict[str, Any]) -> None:
    result_id = strip_inline_version(str(result.get("result_id") or ""))
    inline_message_id = str(result.get("inline_message_id") or "")
    from_user = result.get("from") or {}
    tg_id = int(from_user.get("id") or 0)
    touch_user_async(tg_id, from_user)
    update_bot_profile_async()
    if not inline_message_id:
        return
    if not is_allowed(tg_id):
        return
    if result_id.startswith("delay:"):
        token = result_id.split(":", 1)[1]
        start_inline_delay_report(inline_message_id, token)
        return
    if result_id.startswith("inline_text:"):
        token = result_id.split(":", 1)[1]
        start_inline_text_job(inline_message_id, token)


def handle_inline_query(query: dict[str, Any]) -> None:
    inline_query_id = str(query.get("id") or "")
    if not inline_query_id:
        return
    from_user = query.get("from") or {}
    tg_id = int(from_user.get("id") or 0)
    keyword = str(query.get("query") or "").strip()
    chat_type = str(query.get("chat_type") or "")
    show_inline_refresh = chat_type not in {"group", "supergroup", "channel"}
    touch_user_async(tg_id, from_user)
    update_bot_profile_async()
    if not is_allowed(tg_id):
        answer_inline_query(
            inline_query_id,
            [
                inline_article_result(
                    "banned",
                    "无法使用此机器人",
                    "你已被管理员拉黑。",
                    f"你已被管理员拉黑，无法使用此机器人。\n你的 Telegram ID: {tg_id}",
                )
            ],
        )
        return
    panel = get_panel(tg_id)
    if not panel:
        answer_inline_query(inline_query_id, [inline_private_prompt_result()])
        return

    try:
        nodes, latest, sid_map = load_panel(panel)
        if show_inline_refresh:
            stats_result = inline_text_job_result(
                "stats",
                panel,
                None,
                f"📊 统计信息 · {panel['name']}",
                "点击发送当前面板统计信息",
                True,
            )
        else:
            stats_result = inline_article_result(
                f"stats-static:{panel['id']}",
                f"📊 统计信息 · {panel['name']}",
                "点击发送当前面板统计信息",
                aggregate_plain_text_from_data(panel, nodes, latest),
                thumbnail_url=inline_thumbnail("stats"),
            )
        results: list[dict[str, Any]] = [stats_result]
        results.append(inline_node_detail_result(panel, nodes, latest, sid_map, keyword, show_inline_refresh))

        sid_query = inline_sid_query(keyword)
        if sid_query is None:
            tasks = [task for task in fetch_ping_tasks(panel, nodes[:INLINE_TASK_SCAN_LIMIT]) if inline_task_matches(task, keyword)]
            for task in tasks[:INLINE_DELAY_RESULT_LIMIT]:
                try:
                    results.append(inline_delay_photo_result(panel, task))
                except Exception as exc:
                    task_name = str(task.get("name") or f"任务 {task.get('id') or ''}").strip()
                    results.append(
                        inline_article_result(
                            f"delay-error:{task.get('id') or len(results)}",
                            f"📡 {task_name} 生成失败",
                            str(exc)[:120],
                            f"{task_name} 延迟战报生成失败：{exc}",
                        )
                    )

        answer_inline_query(inline_query_id, results)
    except Exception as exc:
        answer_inline_query(
            inline_query_id,
            [inline_article_result("inline-error", "读取失败", str(exc)[:120], f"读取失败：{exc}")],
        )


def handle_admin_command(chat_id: int, tg_id: int, command: str, args: str) -> bool:
    if command not in {"admin", "unadmin", "ban", "unban", "users"}:
        return False
    if chat_id < 0:
        send_group_private_hint(chat_id)
        return True
    if not is_admin(tg_id):
        send_message(chat_id, "只有管理员可以使用这个命令。", custom_emoji="warn")
        return True
    if command == "admin":
        parts = args.split(maxsplit=1)
        if not parts or not parts[0].isdigit():
            send_message(chat_id, "用法：/admin 用户ID 备注", custom_emoji="info")
            return True
        target = int(parts[0])
        note = parts[1] if len(parts) > 1 else ""
        now = utcnow_iso()
        with db() as conn:
            conn.execute(
                "INSERT INTO users(tg_id, role, note, created_by, created_at, last_seen) VALUES(?, 'admin', ?, ?, ?, ?) "
                "ON CONFLICT(tg_id) DO UPDATE SET role='admin', note=excluded.note",
                (target, note, tg_id, now, now),
            )
        update_bot_profile_async(True)
        send_message(chat_id, f"已升级为管理员：{target}", custom_emoji="ok")
        return True
    if command == "unadmin":
        parts = args.split()
        if not parts or not parts[0].isdigit():
            send_message(chat_id, "用法：/unadmin 用户ID", custom_emoji="info")
            return True
        target = int(parts[0])
        if target in OWNER_IDS:
            send_message(chat_id, "不能取消 owner 管理员。", custom_emoji="warn")
            return True
        with db() as conn:
            conn.execute("UPDATE users SET role='user' WHERE tg_id=? AND role='admin'", (target,))
        update_bot_profile_async(True)
        send_message(chat_id, f"已取消管理员：{target}", custom_emoji="ok")
        return True
    if command == "ban":
        parts = args.split(maxsplit=1)
        if not parts or not parts[0].isdigit():
            send_message(chat_id, "用法：/ban 用户ID 原因", custom_emoji="info")
            return True
        target = int(parts[0])
        if target in OWNER_IDS:
            send_message(chat_id, "不能拉黑 owner。", custom_emoji="warn")
            return True
        note = parts[1] if len(parts) > 1 else ""
        now = utcnow_iso()
        with db() as conn:
            conn.execute(
                "INSERT INTO users(tg_id, role, note, created_by, created_at, last_seen) VALUES(?, 'banned', ?, ?, ?, ?) "
                "ON CONFLICT(tg_id) DO UPDATE SET role='banned', note=excluded.note",
                (target, note, tg_id, now, now),
            )
        update_bot_profile_async(True)
        send_message(chat_id, f"已拉黑用户：{target}", custom_emoji="ok")
        return True
    if command == "unban":
        parts = args.split(maxsplit=1)
        if not parts or not parts[0].isdigit():
            send_message(chat_id, "用法：/unban 用户ID", custom_emoji="info")
            return True
        target = int(parts[0])
        note = parts[1] if len(parts) > 1 else ""
        now = utcnow_iso()
        with db() as conn:
            conn.execute(
                "INSERT INTO users(tg_id, role, note, created_by, created_at, last_seen) VALUES(?, 'user', ?, ?, ?, ?) "
                "ON CONFLICT(tg_id) DO UPDATE SET role=CASE WHEN role='banned' THEN 'user' ELSE role END, note=excluded.note",
                (target, note, tg_id, now, now),
            )
        update_bot_profile_async(True)
        send_message(chat_id, f"已解除拉黑：{target}", custom_emoji="ok")
        return True
    with db() as conn:
        rows = conn.execute("SELECT tg_id, role, note, username, first_name, last_seen FROM users ORDER BY role, last_seen DESC, tg_id LIMIT 80").fetchall()
    total, active, admins, banned = user_stats()
    send_message(chat_id, users_text(rows, total, active, admins, banned), html_text=True)
    return True


def handle_message(message: dict[str, Any]) -> None:
    parsed = parse_command(message)
    if not parsed:
        return
    command, args = parsed
    chat_id = int(message["chat"]["id"])
    tg_id = int((message.get("from") or {}).get("id", chat_id))
    touch_user_async(tg_id, message.get("from") or {})
    update_bot_profile_async()
    if chat_id < 0 and command in PRIVATE_ONLY_COMMANDS:
        send_group_private_hint(chat_id)
        return
    if command == "start":
        if require_allowed(chat_id, tg_id):
            send_message(chat_id, start_text(tg_id), start_keyboard(), html_text=True)
        return
    if command == "menu":
        if require_allowed(chat_id, tg_id):
            send_message(chat_id, menu_text(tg_id), menu_keyboard(tg_id, private_chat=chat_id > 0), html_text=True)
        return
    if handle_admin_command(chat_id, tg_id, command, args):
        return
    if not require_allowed(chat_id, tg_id):
        return

    if command == "bind":
        parts = args.split(maxsplit=2)
        if len(parts) < 2:
            send_message(chat_id, BIND_USAGE, custom_emoji="info", html_text=True)
            return
        try:
            url = normalize_url(parts[0])
            api_key = "" if parts[1].strip() == "-" else parts[1].strip()
            name = parts[2].strip() if len(parts) > 2 else urllib.parse.urlsplit(url).netloc
            probe = {"id": 0, "base_url": url, "api_key": api_key, "name": name}
            nodes = fetch_nodes(probe)
            fetch_latest(probe)
            with db() as conn:
                first = conn.execute("SELECT COUNT(*) AS c FROM panels WHERE tg_id=?", (tg_id,)).fetchone()["c"] == 0
                cur = conn.execute(
                    "INSERT INTO panels(tg_id, name, base_url, api_key, active, created_at) VALUES(?,?,?,?,?,?)",
                    (tg_id, name, url, api_key, 1 if first else 0, utcnow_iso()),
            )
            panel_id = int(cur.lastrowid)
            ensure_sids(panel_id, nodes)
            invalidate_panel_cache(panel_id)
            send_message(chat_id, f"绑定成功：#{panel_id} {name}\n当前读取到 {len(nodes)} 个节点。", custom_emoji="ok")
        except Exception as exc:
            send_message(chat_id, f"绑定失败：{exc}", custom_emoji="warn")
        return

    if command == "panels":
        rows = list_panels(tg_id)
        if not rows:
            send_message(chat_id, "还没有绑定面板。\n\n" + BIND_USAGE, custom_emoji="warn", html_text=True)
            return
        send_message(chat_id, panels_text(rows), html_text=True)
        return

    if command == "use":
        if not args.isdigit():
            send_message(chat_id, "用法：/use 面板ID", custom_emoji="info")
            return
        ok = set_active_panel(tg_id, int(args))
        send_message(chat_id, "已切换当前面板。" if ok else "没有找到这个面板。", custom_emoji="ok" if ok else "warn")
        return

    if command == "unbind":
        if not args.isdigit():
            send_message(chat_id, "用法：/unbind 面板ID", custom_emoji="info")
            return
        panel_id = int(args)
        with db() as conn:
            ok = bool(conn.execute("SELECT id FROM panels WHERE tg_id=? AND id=?", (tg_id, panel_id)).fetchone())
            if ok:
                conn.execute("DELETE FROM node_aliases WHERE panel_id=?", (panel_id,))
                conn.execute("DELETE FROM panels WHERE tg_id=? AND id=?", (tg_id, panel_id))
                row = conn.execute("SELECT id FROM panels WHERE tg_id=? ORDER BY id LIMIT 1", (tg_id,)).fetchone()
                if row:
                    conn.execute("UPDATE panels SET active=1 WHERE tg_id=? AND id=?", (tg_id, row["id"]))
        invalidate_panel_cache(panel_id)
        send_message(chat_id, "已解绑。" if ok else "没有找到这个面板。", custom_emoji="ok" if ok else "warn")
        return

    if command == "all":
        parts = args.split()
        panel = get_panel(tg_id, int(parts[0])) if parts and parts[0].isdigit() else get_panel(tg_id)
        if not panel:
            if chat_id < 0:
                send_group_private_hint(chat_id)
                return
            send_message(chat_id, "还没有绑定面板。\n\n" + BIND_USAGE, custom_emoji="warn", html_text=True)
            return
        try:
            reply_markup = panel_keyboard(int(panel["id"])) if chat_id > 0 else None
            send_message(chat_id, aggregate_text(panel), reply_markup, html_text=True)
        except Exception as exc:
            send_message(chat_id, f"读取失败：{exc}", custom_emoji="warn")
        return

    if command == "search":
        if not args:
            send_message(chat_id, "用法：/search 关键词", custom_emoji="info")
            return
        panel = get_panel(tg_id)
        if not panel:
            send_message(chat_id, "还没有绑定面板。\n\n" + BIND_USAGE, custom_emoji="warn", html_text=True)
            return
        try:
            send_message(chat_id, search_text(panel, args), html_text=True)
        except Exception as exc:
            send_message(chat_id, f"搜索失败：{exc}", custom_emoji="warn")
        return

    if command == "sid":
        parts = args.split()
        if not parts or not parts[0].isdigit():
            if chat_id < 0:
                send_group_private_hint(chat_id)
                return
            send_message(chat_id, "用法：/sid 编号", custom_emoji="info")
            return
        panel = get_panel(tg_id)
        if not panel:
            if chat_id < 0:
                send_group_private_hint(chat_id)
                return
            send_message(chat_id, "还没有绑定面板。\n\n" + BIND_USAGE, custom_emoji="warn", html_text=True)
            return
        sid = int(parts[0])
        try:
            reply_markup = panel_keyboard(int(panel["id"]), sid) if chat_id > 0 else None
            send_message(chat_id, detail_text(panel, sid), reply_markup, html_text=True)
        except Exception as exc:
            send_message(chat_id, f"读取失败：{exc}", custom_emoji="warn")
        return

    if chat_id < 0:
        send_group_private_hint(chat_id)
        return
    send_message(chat_id, "未知命令。发送 /start 查看命令，或 /menu 打开功能菜单。", menu_keyboard(tg_id, private_chat=chat_id > 0), custom_emoji="info")


def handle_callback(query: dict[str, Any]) -> None:
    query_id = query.get("id", "")
    message = query.get("message") or {}
    chat_id = int(message.get("chat", {}).get("id", 0))
    message_id = int(message.get("message_id", 0))
    inline_message_id = str(query.get("inline_message_id") or "")
    tg_id = int((query.get("from") or {}).get("id", chat_id))
    data = query.get("data") or ""
    touch_user_async(tg_id, query.get("from") or {})
    update_bot_profile_async()
    if inline_message_id and data.startswith("inline_delay:"):
        if not is_allowed(tg_id):
            answer_callback(query_id, f"未授权。你的ID: {tg_id}", True)
            return
        token = data.split(":", 1)[1]
        answer_callback(query_id, "正在生成延迟战报...")
        start_inline_delay_report(inline_message_id, token)
        return
    if inline_message_id and data.startswith("inline_text:"):
        if not is_allowed(tg_id):
            answer_callback(query_id, f"未授权。你的ID: {tg_id}", True)
            return
        token = data.split(":", 1)[1]
        answer_callback(query_id, "正在刷新数据...")
        start_inline_text_job(inline_message_id, token)
        return
    if not chat_id or not message_id:
        answer_callback(query_id)
        return
    if not is_allowed(tg_id):
        answer_callback(query_id, f"未授权。你的ID: {tg_id}", True)
        return
    if chat_id < 0 and (data in PRIVATE_ONLY_CALLBACKS or any(data.startswith(prefix) for prefix in PRIVATE_ONLY_CALLBACK_PREFIXES)):
        answer_callback(query_id, "这个功能只能在机器人私聊中使用", True)
        return
    try:
        if data == "menu":
            answer_callback(query_id)
            edit_message(chat_id, message_id, menu_text(tg_id), menu_keyboard(tg_id, private_chat=chat_id > 0), html_text=True)
            return
        if data == "panels":
            answer_callback(query_id)
            rows = list_panels(tg_id)
            if not rows:
                edit_message(chat_id, message_id, "还没有绑定面板。\n\n" + BIND_USAGE, menu_keyboard(tg_id, private_chat=chat_id > 0), custom_emoji="warn", html_text=True)
                return
            edit_message(chat_id, message_id, panels_text(rows), menu_keyboard(tg_id, private_chat=chat_id > 0), html_text=True)
            return
        if data == "bind_help":
            answer_callback(query_id)
            edit_message(chat_id, message_id, BIND_USAGE, menu_keyboard(tg_id, private_chat=chat_id > 0), custom_emoji="info", html_text=True)
            return
        if data == "unbind_help":
            answer_callback(query_id)
            rows = list_panels(tg_id)
            panel_lines = "\n".join(f"#{r['id']} {r['name']}" for r in rows) if rows else "暂无面板"
            edit_message(chat_id, message_id, f"用法：\n/unbind 【面板ID】\n\n当前面板：\n{panel_lines}", menu_keyboard(tg_id, private_chat=chat_id > 0), custom_emoji="info")
            return
        if data == "search_help":
            answer_callback(query_id)
            edit_message(chat_id, message_id, "用法：\n/search 【关键词】\n\n示例：\n/search hk", menu_keyboard(tg_id, private_chat=chat_id > 0), custom_emoji="info")
            return
        if data == "renewals":
            panel = get_panel(tg_id)
            if not panel:
                answer_callback(query_id, "你还没有绑定面板", True)
                return
            answer_callback(query_id)
            edit_message(chat_id, message_id, renewal_text(panel), menu_keyboard(tg_id, private_chat=chat_id > 0), html_text=True)
            return
        if data == "delay_tasks" or data.startswith("delay_tasks:"):
            panel_id = int(data.split(":", 1)[1]) if data.startswith("delay_tasks:") else None
            panel = get_panel(tg_id, panel_id)
            if not panel:
                answer_callback(query_id, "你还没有绑定面板", True)
                return
            answer_callback(query_id)
            edit_message(chat_id, message_id, "正在读取延迟监测任务...", menu_keyboard(tg_id, private_chat=chat_id > 0), custom_emoji="delay")
            nodes = fetch_nodes(panel)
            tasks = fetch_ping_tasks(panel, nodes)
            edit_message(chat_id, message_id, delay_tasks_text(panel, tasks), delay_tasks_keyboard(int(panel["id"]), tasks), html_text=True)
            return
        if data == "nodes_active":
            panel = get_panel(tg_id)
            if not panel:
                answer_callback(query_id, "你还没有绑定面板", True)
                return
            answer_callback(query_id)
            nodes, _, _ = load_panel(panel)
            groups = node_groups(nodes)
            if not groups:
                edit_message(chat_id, message_id, "当前面板没有节点。", menu_keyboard(tg_id, private_chat=chat_id > 0), custom_emoji="warn")
                return
            edit_message(chat_id, message_id, node_group_text(panel, groups), node_group_keyboard(int(panel["id"]), groups), html_text=True)
            return
        if data == "sid_help":
            answer_callback(query_id)
            edit_message(chat_id, message_id, "用法：\n/sid 【节点编号】\n\n先用 /search 关键词 找到节点编号。", menu_keyboard(tg_id, private_chat=chat_id > 0), custom_emoji="info")
            return
        if data == "users_panel":
            if not is_admin(tg_id):
                answer_callback(query_id, "只有管理员可以查看", True)
                return
            answer_callback(query_id)
            with db() as conn:
                rows = conn.execute("SELECT tg_id, role, note, username, first_name, last_seen FROM users ORDER BY role, last_seen DESC, tg_id LIMIT 80").fetchall()
            total, active, admins, banned = user_stats()
            edit_message(chat_id, message_id, users_text(rows, total, active, admins, banned), menu_keyboard(tg_id, private_chat=chat_id > 0), html_text=True)
            return
        if data == "all_active":
            panel = get_panel(tg_id)
            if not panel:
                answer_callback(query_id, "你还没有绑定面板", True)
                return
            answer_callback(query_id)
            edit_message(chat_id, message_id, aggregate_text(panel), panel_keyboard(int(panel["id"])), html_text=True)
            return
        if data.startswith("all:"):
            panel_id = int(data.split(":", 1)[1])
            panel = get_panel(tg_id, panel_id)
            if not panel:
                answer_callback(query_id, "这条消息不是你的面板", True)
                return
            answer_callback(query_id)
            edit_message(chat_id, message_id, aggregate_text(panel), panel_keyboard(panel_id), html_text=True)
            return
        if data.startswith("delay_task:"):
            _, panel_id_text, task_id_text = data.split(":", 2)
            panel_id = int(panel_id_text)
            task_id = int(task_id_text)
            panel = get_panel(tg_id, panel_id)
            if not panel:
                answer_callback(query_id, "这条消息不是你的面板", True)
                return
            answer_callback(query_id, "正在生成图片战报...")
            nodes = fetch_nodes(panel)
            tasks = fetch_ping_tasks(panel, nodes)
            edit_message(chat_id, message_id, delay_tasks_text(panel, tasks), delay_tasks_keyboard(panel_id, tasks), html_text=True)
            image_path = f"/tmp/komari-delay-{chat_id}-{panel_id}-{task_id}-{int(time.time())}.png"
            try:
                task_name, count = create_delay_report_image(panel, task_id, image_path)
                send_photo(chat_id, image_path, f"{task_name} · 延迟排名 · {count} 台 VPS")
            finally:
                try:
                    if os.path.exists(image_path):
                        os.remove(image_path)
                except Exception:
                    pass
            return
        if data.startswith("nodes:"):
            panel_id = int(data.split(":", 1)[1])
            panel = get_panel(tg_id, panel_id)
            if not panel:
                answer_callback(query_id, "这条消息不是你的面板", True)
                return
            answer_callback(query_id)
            nodes, _, _ = load_panel(panel)
            groups = node_groups(nodes)
            edit_message(chat_id, message_id, node_group_text(panel, groups), node_group_keyboard(panel_id, groups), html_text=True)
            return
        if data.startswith("grp:"):
            _, panel_id_text, group_index_text = data.split(":", 2)
            panel_id = int(panel_id_text)
            group_index = int(group_index_text)
            panel = get_panel(tg_id, panel_id)
            if not panel:
                answer_callback(query_id, "这条消息不是你的面板", True)
                return
            nodes, latest, sid_map = load_panel(panel)
            groups = node_groups(nodes)
            if group_index < 0 or group_index >= len(groups):
                answer_callback(query_id, "分组已变化，请重新打开节点列表", True)
                return
            group_name, items = groups[group_index]
            answer_callback(query_id)
            edit_message(chat_id, message_id, node_list_text(panel, group_name, items, latest, sid_map), node_list_keyboard(panel_id, items, sid_map), html_text=True)
            return
        if data.startswith("node:"):
            _, panel_id_text, sid_text = data.split(":", 2)
            panel_id = int(panel_id_text)
            sid = int(sid_text)
            panel = get_panel(tg_id, panel_id)
            if not panel:
                answer_callback(query_id, "这条消息不是你的面板", True)
                return
            answer_callback(query_id)
            reply_markup = panel_keyboard(panel_id, sid) if chat_id > 0 else {"inline_keyboard": []}
            edit_message(chat_id, message_id, detail_text(panel, sid), reply_markup, html_text=True)
            return
        answer_callback(query_id)
    except Exception as exc:
        answer_callback(query_id, str(exc), True)


def setup_bot() -> None:
    global BOT_USERNAME
    tg_call("deleteWebhook", {"drop_pending_updates": False}, 30)
    private_commands = [
        {"command": "start", "description": "命令说明"},
        {"command": "menu", "description": "功能按钮菜单"},
        {"command": "bind", "description": "绑定 Komari 面板"},
        {"command": "panels", "description": "面板列表"},
        {"command": "use", "description": "切换面板"},
        {"command": "all", "description": "统计信息"},
        {"command": "search", "description": "搜索节点"},
        {"command": "sid", "description": "查看节点详情"},
        {"command": "admin", "description": "管理员升级用户"},
        {"command": "unadmin", "description": "管理员取消升级"},
        {"command": "ban", "description": "管理员拉黑用户"},
        {"command": "unban", "description": "管理员解除拉黑"},
        {"command": "users", "description": "管理员查看用户列表"},
    ]
    group_commands = [
        {"command": "all", "description": "统计信息"},
        {"command": "sid", "description": "查看节点详情"},
    ]
    tg_call("setMyCommands", {"commands": group_commands}, 30)
    tg_call("setMyCommands", {"scope": {"type": "all_private_chats"}, "commands": private_commands}, 30)
    tg_call("setMyCommands", {"scope": {"type": "all_group_chats"}, "commands": group_commands}, 30)
    me = tg_call("getMe", {}, 30).get("result", {})
    BOT_USERNAME = str(me.get("username") or "")
    if INLINE_IMAGE_SERVER_ENABLED:
        try:
            start_inline_image_server()
        except Exception:
            log.exception("inline image server failed to start")
    update_bot_profile(True)
    log.info("started @%s", BOT_USERNAME)


def dispatch_update(update: dict[str, Any]) -> None:
    try:
        if "message" in update:
            handle_message(update["message"])
        elif "callback_query" in update:
            handle_callback(update["callback_query"])
        elif "inline_query" in update:
            handle_inline_query(update["inline_query"])
        elif "chosen_inline_result" in update:
            handle_chosen_inline_result(update["chosen_inline_result"])
    except Exception:
        log.error("update failed: %s", traceback.format_exc())


UPDATE_WORKER_LIMIT = int(os.getenv("UPDATE_WORKER_LIMIT", "8"))
UPDATE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=UPDATE_WORKER_LIMIT)


def run_loop() -> None:
    offset = 0
    while True:
        try:
            updates = tg_call(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 50,
                    "allowed_updates": ["message", "callback_query", "inline_query", "chosen_inline_result"],
                },
                70,
            ).get("result", [])
            for update in updates:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                UPDATE_EXECUTOR.submit(dispatch_update, update)
        except Exception as exc:
            log.warning("poll error: %s", exc)
            time.sleep(5)


def main() -> int:
    if not TOKEN:
        print("TELEGRAM_BOT_TOKEN empty", file=sys.stderr)
        return 2
    init_db()
    setup_bot()
    run_loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
