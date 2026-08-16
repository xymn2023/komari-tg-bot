import unittest
import os
import tempfile
import time
from unittest.mock import patch

import bot


class GroupPrivateHintTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.photos = []
        self.scheduled = []
        self.next_message_id = 321

    def next_id(self):
        message_id = self.next_message_id
        self.next_message_id += 1
        return message_id

    def fake_tg_call(self, method, payload=None, timeout=70):
        payload = payload or {}
        if method == "sendMessage":
            message_id = self.next_id()
            self.sent.append((payload["chat_id"], payload["text"], payload.get("reply_markup"), message_id))
            return {"ok": True, "result": {"message_id": message_id}}
        return {"ok": True, "result": {}}

    def fake_tg_call_multipart(self, method, fields, files, timeout=70):
        message_id = self.next_id()
        self.photos.append((fields["chat_id"], fields.get("caption", ""), files, message_id))
        return {"ok": True, "result": {"message_id": message_id}}

    def fake_schedule_delete(self, chat_id, message_id, seconds=120):
        self.scheduled.append((chat_id, message_id, seconds))

    def run_message(self, message):
        with (
            patch.object(bot, "BOT_USERNAME", "example_bot"),
            patch.object(bot, "touch_user_async", lambda *args, **kwargs: None),
            patch.object(bot, "update_bot_profile_async", lambda *args, **kwargs: None),
            patch.object(bot, "require_allowed", lambda *args, **kwargs: True),
            patch.object(bot, "get_panel", lambda *args, **kwargs: None),
            patch.object(bot, "tg_call", self.fake_tg_call),
            patch.object(bot, "schedule_delete", self.fake_schedule_delete, create=True),
        ):
            bot.handle_message(message)

    def test_group_sid_without_panel_prompts_private_chat_and_auto_delete(self):
        self.run_message(
            {
                "text": "/sid 32",
                "chat": {"id": -100123, "type": "supergroup"},
                "from": {"id": 42, "username": "guest"},
            }
        )

        self.assertEqual(len(self.sent), 1)
        chat_id, text, reply_markup, _message_id = self.sent[0]
        self.assertEqual(chat_id, -100123)
        self.assertIn("请去私聊绑定/操作", text)
        self.assertNotIn("这条提示将在", text)
        self.assertNotIn(bot.BIND_USAGE, text)
        self.assertEqual(reply_markup["inline_keyboard"][0][0]["url"], "https://t.me/example_bot")
        self.assertEqual(self.scheduled, [(-100123, 321, 120)])

    def test_group_private_only_command_uses_same_private_chat_hint(self):
        self.run_message(
            {
                "text": "/bind https://example.com abc test",
                "chat": {"id": -100456, "type": "group"},
                "from": {"id": 42, "username": "guest"},
            }
        )

        self.assertEqual(len(self.sent), 1)
        self.assertIn("请去私聊绑定/操作", self.sent[0][1])
        self.assertEqual(self.scheduled, [(-100456, 321, 120)])

    def test_private_sid_without_panel_keeps_bind_usage(self):
        self.run_message(
            {
                "text": "/sid 32",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 42, "username": "guest"},
            }
        )

        self.assertEqual(len(self.sent), 1)
        self.assertIn(bot.BIND_USAGE, self.sent[0][1])
        self.assertEqual(self.scheduled, [])

    def test_every_group_text_message_auto_deletes_from_send_layer(self):
        with (
            patch.object(bot, "tg_call", self.fake_tg_call),
            patch.object(bot, "schedule_delete", self.fake_schedule_delete),
        ):
            message_id = bot.send_message(-100789, "群组统计信息")

        self.assertEqual(message_id, 321)
        self.assertEqual(self.scheduled, [(-100789, 321, 120)])

    def test_private_text_message_does_not_auto_delete(self):
        with (
            patch.object(bot, "tg_call", self.fake_tg_call),
            patch.object(bot, "schedule_delete", self.fake_schedule_delete),
        ):
            bot.send_message(42, "私聊消息")

        self.assertEqual(self.scheduled, [])

    def test_group_photo_message_auto_deletes(self):
        fd, path = tempfile.mkstemp(suffix=".png")
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(b"fakepng")
            with (
                patch.object(bot, "tg_call_multipart", self.fake_tg_call_multipart),
                patch.object(bot, "schedule_delete", self.fake_schedule_delete),
            ):
                message_id = bot.send_photo(-100999, path, "延迟战报")
        finally:
            if os.path.exists(path):
                os.remove(path)

        self.assertEqual(message_id, 321)
        self.assertEqual(self.scheduled, [(-100999, 321, 120)])

    def test_inline_query_without_panel_prompts_private_binding(self):
        calls = []

        def fake_tg_call(method, payload=None, timeout=70):
            calls.append((method, payload or {}))
            return {"ok": True, "result": {}}

        with (
            patch.object(bot, "BOT_USERNAME", "example_bot"),
            patch.object(bot, "touch_user_async", lambda *args, **kwargs: None),
            patch.object(bot, "update_bot_profile_async", lambda *args, **kwargs: None),
            patch.object(bot, "is_allowed", lambda *args, **kwargs: True),
            patch.object(bot, "get_panel", lambda *args, **kwargs: None),
            patch.object(bot, "tg_call", fake_tg_call),
        ):
            bot.handle_inline_query({"id": "inline-1", "query": "", "from": {"id": 42}})

        method, payload = calls[-1]
        self.assertEqual(method, "answerInlineQuery")
        self.assertTrue(payload["is_personal"])
        self.assertEqual(payload["results"][0]["type"], "article")
        self.assertIn("请去私聊绑定/操作", payload["results"][0]["title"])
        self.assertEqual(payload["results"][0]["reply_markup"]["inline_keyboard"][0][0]["url"], "https://t.me/example_bot")

    def test_inline_query_returns_stats_node_prompt_and_delay_options(self):
        calls = []
        panel = {"id": 2, "name": "Tweak"}
        node = {"uuid": "node-1", "name": "JP-2", "os": "Debian", "arch": "amd64", "ipv4": "38.207.1.2"}
        status = {"online": True, "cpu": 0, "uptime": 3600}

        def fake_tg_call(method, payload=None, timeout=70):
            calls.append((method, payload or {}))
            return {"ok": True, "result": {}}

        with (
            patch.object(bot, "BOT_USERNAME", "example_bot"),
            patch.object(bot, "touch_user_async", lambda *args, **kwargs: None),
            patch.object(bot, "update_bot_profile_async", lambda *args, **kwargs: None),
            patch.object(bot, "is_allowed", lambda *args, **kwargs: True),
            patch.object(bot, "get_panel", lambda *args, **kwargs: panel),
            patch.object(bot, "aggregate_text", lambda panel_arg: "统计信息正文"),
            patch.object(bot, "load_panel", lambda panel_arg: ([node], {"node-1": status}, {"node-1": 27})),
            patch.object(bot, "fetch_ping_tasks", lambda panel_arg, nodes=None: [{"id": 7, "name": "山东联通", "type": "icmp"}]),
            patch.object(bot, "create_delay_report_image", side_effect=AssertionError("menu must not generate image")),
            patch.object(bot, "tg_call", fake_tg_call),
        ):
            bot.handle_inline_query({"id": "inline-2", "query": "", "from": {"id": 42}})

        method, payload = calls[-1]
        self.assertEqual(method, "answerInlineQuery")
        results = payload["results"]
        self.assertEqual(results[0]["title"], "📊 统计信息 · Tweak")
        stats_content = results[0]["input_message_content"]
        self.assertIn("message_text", stats_content)
        self.assertIn("点击发送数据", stats_content["message_text"])
        self.assertNotIn("状态    实时同步", stats_content["message_text"])
        self.assertTrue(results[0]["id"].startswith("inline_text:"))
        self.assertTrue(results[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].startswith("inline_text:"))
        self.assertEqual(results[1]["title"], "🖥 服务器详情")
        self.assertEqual(results[1]["description"], "请输入")
        self.assertIn("请在 @example_bot 后面输入节点编号", results[1]["input_message_content"]["message_text"])
        delay_results = [item for item in results if item["type"] == "article" and item["title"] == "📡 山东联通"]
        self.assertEqual(len(delay_results), 1)
        self.assertTrue(delay_results[0]["id"].startswith("delay:"))
        self.assertIn("雷达阵列正在点亮", delay_results[0]["input_message_content"]["message_text"])
        self.assertIn("渲染战报", delay_results[0]["input_message_content"]["message_text"])
        self.assertNotIn("科技风", delay_results[0]["input_message_content"]["message_text"])
        self.assertTrue(delay_results[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].startswith("inline_delay:"))
        self.assertNotIn("thumbnail_url", delay_results[0])

    def test_inline_query_with_sid_uses_fixed_node_detail_option(self):
        calls = []
        panel = {"id": 2, "name": "Tweak"}
        nodes = [
            {"uuid": "node-1", "name": "JP-2", "os": "Debian", "arch": "amd64", "ipv4": "38.207.1.2"},
            {"uuid": "node-2", "name": "HK-1", "os": "Debian", "arch": "amd64", "ipv4": "1.1.1.1"},
        ]
        latest = {"node-1": {"online": True, "cpu": 0, "uptime": 3600}, "node-2": {"online": True}}
        sid_map = {"node-1": 1, "node-2": 2}

        def fake_tg_call(method, payload=None, timeout=70):
            calls.append((method, payload or {}))
            return {"ok": True, "result": {}}

        with (
            patch.object(bot, "BOT_USERNAME", "example_bot"),
            patch.object(bot, "touch_user_async", lambda *args, **kwargs: None),
            patch.object(bot, "update_bot_profile_async", lambda *args, **kwargs: None),
            patch.object(bot, "is_allowed", lambda *args, **kwargs: True),
            patch.object(bot, "get_panel", lambda *args, **kwargs: panel),
            patch.object(bot, "load_panel", lambda panel_arg: (nodes, latest, sid_map)),
            patch.object(bot, "fetch_ping_tasks", side_effect=AssertionError("numeric query should not load delay tasks")),
            patch.object(bot, "tg_call", fake_tg_call),
        ):
            bot.handle_inline_query({"id": "inline-3", "query": "1", "from": {"id": 42}})

        results = calls[-1][1]["results"]
        node_results = [item for item in results if "服务器详情" in item["title"]]
        self.assertEqual(len(node_results), 1)
        self.assertEqual(node_results[0]["title"], "🖥 服务器详情")
        self.assertEqual(node_results[0]["description"], "JP-2 · 在线")
        self.assertNotIn("thumbnail_url", node_results[0])
        node_content = node_results[0]["input_message_content"]
        self.assertIn("message_text", node_content)
        self.assertIn("点击发送数据", node_content["message_text"])
        self.assertNotIn("JP-2", node_content["message_text"])
        self.assertTrue(node_results[0]["id"].startswith("inline_text:"))
        self.assertTrue(node_results[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].startswith("inline_text:"))

    def test_chosen_inline_delay_result_starts_background_generation(self):
        started = []

        with (
            patch.object(bot, "touch_user_async", lambda *args, **kwargs: None),
            patch.object(bot, "update_bot_profile_async", lambda *args, **kwargs: None),
            patch.object(bot, "is_allowed", lambda *args, **kwargs: True),
            patch.object(bot, "start_inline_delay_report", lambda inline_message_id, token: started.append((inline_message_id, token))),
        ):
            bot.handle_chosen_inline_result(
                {
                    "result_id": "delay:abc123",
                    "inline_message_id": "inline-message-1",
                    "from": {"id": 42},
                    "query": "",
                }
            )

        self.assertEqual(started, [("inline-message-1", "abc123")])

    def test_chosen_inline_text_result_starts_background_refresh(self):
        started = []

        with (
            patch.object(bot, "touch_user_async", lambda *args, **kwargs: None),
            patch.object(bot, "update_bot_profile_async", lambda *args, **kwargs: None),
            patch.object(bot, "is_allowed", lambda *args, **kwargs: True),
            patch.object(bot, "start_inline_text_job", lambda inline_message_id, token: started.append((inline_message_id, token))),
        ):
            bot.handle_chosen_inline_result(
                {
                    "result_id": f"inline_text:abc123:{bot.INLINE_RESULT_VERSION}",
                    "inline_message_id": "inline-message-2",
                    "from": {"id": 42},
                    "query": "",
                }
            )

        self.assertEqual(started, [("inline-message-2", "abc123")])

    def test_group_inline_stats_returns_final_plain_text_without_refresh_button(self):
        calls = []
        panel = {"id": 2, "name": "Tweak", "base_url": "https://example.com", "api_key": ""}

        def fake_tg_call(method, payload=None, timeout=70):
            calls.append((method, payload or {}))
            return {"ok": True, "result": {}}

        with (
            patch.object(bot, "touch_user_async", lambda *args, **kwargs: None),
            patch.object(bot, "update_bot_profile_async", lambda *args, **kwargs: None),
            patch.object(bot, "is_allowed", lambda *args, **kwargs: True),
            patch.object(bot, "get_panel", lambda *args, **kwargs: panel),
            patch.object(bot, "load_panel", lambda panel_arg: ([], {}, {})),
            patch.object(bot, "fetch_ping_tasks", lambda panel_arg, nodes=None: []),
            patch.object(bot, "tg_call", fake_tg_call),
        ):
            bot.handle_inline_query({"id": "inline-group", "query": "", "from": {"id": 42}, "chat_type": "supergroup"})
            result_id = calls[-1][1]["results"][0]["id"]
            result = calls[-1][1]["results"][0]
            content = result["input_message_content"]
            self.assertTrue(result_id.startswith("stats-static:"))
            self.assertNotIn("reply_markup", result)
            self.assertIn("📊 统计信息 · Tweak", content["message_text"])
            self.assertIn("🖥 服务器", content["message_text"])
            self.assertNotIn("<tg-emoji", content["message_text"])
            bot.handle_chosen_inline_result(
                {
                    "result_id": result_id,
                    "inline_message_id": "inline-message-group",
                    "from": {"id": 42},
                    "query": "",
                }
            )
            time.sleep(0.05)

        self.assertFalse([payload for method, payload in calls if method == "editMessageText"])

    def test_group_inline_node_detail_returns_final_plain_text_without_refresh_button(self):
        calls = []
        panel = {"id": 2, "name": "Tweak", "base_url": "https://example.com", "api_key": ""}
        node = {
            "uuid": "node-1",
            "name": "Xboard",
            "os": "Debian GNU/Linux 12 (bookworm)",
            "arch": "amd64",
            "ipv4": "154.21.1.2",
            "cpu_name": "AMD EPYC",
            "cpu_physical_cores": 1,
            "mem_total": 1024,
            "swap_total": 2048,
            "disk_total": 4096,
        }
        status = {
            "online": True,
            "uptime": 3600,
            "cpu": 3.2,
            "load": 0.1,
            "load5": 0.2,
            "load15": 0.3,
            "ram": 512,
            "swap": 0,
            "disk": 1024,
            "net_in": 2048,
            "net_out": 4096,
            "net_total_down": 8192,
            "net_total_up": 16384,
        }

        def fake_tg_call(method, payload=None, timeout=70):
            calls.append((method, payload or {}))
            return {"ok": True, "result": {}}

        with (
            patch.object(bot, "BOT_USERNAME", "example_bot"),
            patch.object(bot, "touch_user_async", lambda *args, **kwargs: None),
            patch.object(bot, "update_bot_profile_async", lambda *args, **kwargs: None),
            patch.object(bot, "is_allowed", lambda *args, **kwargs: True),
            patch.object(bot, "get_panel", lambda *args, **kwargs: panel),
            patch.object(bot, "load_panel", lambda panel_arg: ([node], {"node-1": status}, {"node-1": 1})),
            patch.object(bot, "sid_to_uuid", lambda panel_id, sid: "node-1" if sid == 1 else None),
            patch.object(bot, "fetch_ping_tasks", side_effect=AssertionError("numeric query should not load delay tasks")),
            patch.object(bot, "tg_call", fake_tg_call),
        ):
            bot.handle_inline_query({"id": "inline-group-node", "query": "01", "from": {"id": 42}, "chat_type": "supergroup"})
            node_result = next(item for item in calls[-1][1]["results"] if "服务器详情" in item["title"])
            result_id = node_result["id"]
            content = node_result["input_message_content"]
            self.assertTrue(result_id.startswith("node-static:"))
            self.assertNotIn("reply_markup", node_result)
            self.assertIn("🖥 Xboard · 在线", content["message_text"])
            self.assertIn("🧠 处理器", content["message_text"])
            self.assertNotIn("<tg-emoji", content["message_text"])
            bot.handle_chosen_inline_result(
                {
                    "result_id": result_id,
                    "inline_message_id": "inline-message-group-node",
                    "from": {"id": 42},
                    "query": "01",
                }
            )
            time.sleep(0.05)

        self.assertFalse([payload for method, payload in calls if method == "editMessageText"])

    def test_inline_delay_button_starts_background_generation(self):
        calls = []
        started = []

        def fake_answer(callback_id, text="", alert=False):
            calls.append((callback_id, text, alert))

        with (
            patch.object(bot, "touch_user_async", lambda *args, **kwargs: None),
            patch.object(bot, "update_bot_profile_async", lambda *args, **kwargs: None),
            patch.object(bot, "is_allowed", lambda *args, **kwargs: True),
            patch.object(bot, "answer_callback", fake_answer),
            patch.object(bot, "start_inline_delay_report", lambda inline_message_id, token: started.append((inline_message_id, token))),
        ):
            bot.handle_callback(
                {
                    "id": "cb-1",
                    "inline_message_id": "inline-message-2",
                    "data": "inline_delay:def456",
                    "from": {"id": 42},
                }
            )

        self.assertEqual(started, [("inline-message-2", "def456")])
        self.assertEqual(calls, [("cb-1", "正在生成延迟战报...", False)])

    def test_finish_inline_delay_report_edits_media_with_file_id(self):
        texts = []
        medias = []
        token = "token789"
        bot.INLINE_DELAY_JOBS[token] = (9999999999, {"id": 2, "tg_id": 42, "name": "Tweak"}, 7, "Cloudflare")

        try:
            with (
                patch.object(bot, "edit_inline_message_text", lambda inline_message_id, text, reply_markup=None: texts.append((inline_message_id, text))),
                patch.object(bot, "create_inline_delay_image", lambda panel, task_id: ("Cloudflare", 3, "report.jpg")),
                patch.object(bot, "upload_photo_file_id", lambda chat_id, path: "telegram-file-id"),
                patch.object(bot, "edit_inline_message_media", lambda inline_message_id, media, reply_markup=None: medias.append((inline_message_id, media))),
                patch.object(os.path, "exists", lambda path: False),
            ):
                bot.finish_inline_delay_report("inline-message-3", token)
        finally:
            bot.INLINE_DELAY_JOBS.pop(token, None)

        self.assertTrue(any("正在绘制排名图" in text for _, text in texts))
        self.assertEqual(medias, [("inline-message-3", {"type": "photo", "media": "telegram-file-id", "caption": "Cloudflare · 延迟排名 · 3 台 VPS"})])


if __name__ == "__main__":
    unittest.main()
