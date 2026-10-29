"""使用真实 HTTP 客户端和模拟 AstrBot 适配器验证插件边界。"""

import asyncio
import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from _support import example_payload
from aiohttp import web
from astrbot_plugin_registration_monitor.ranking import RankingError


class FakeStar:
    def __init__(self, context):
        self.context = context

    async def get_kv_data(self, key, default):
        return copy.deepcopy(self.context.disk.get(key, default))

    async def put_kv_data(self, key, value):
        self.context.disk[key] = copy.deepcopy(value)


class FakeChain:
    def message(self, text):
        self.text = text
        return self


def load_plugin_class():
    # 仅模拟 AstrBot 集成边界，HTTP、解析、持久化逻辑均运行实际插件代码。
    api = ModuleType("astrbot.api")
    api.AstrBotConfig = dict
    api.logger = MagicMock()
    event = ModuleType("astrbot.api.event")
    event.AstrMessageEvent = object
    event.MessageChain = FakeChain
    event.filter = SimpleNamespace(command=lambda _: lambda fn: fn)
    star = ModuleType("astrbot.api.star")
    star.Context = object
    star.Star = FakeStar
    stubs = {
        "astrbot": ModuleType("astrbot"),
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.star": star,
    }
    name = "astrbot_plugin_registration_monitor._tested_main"
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module.RegistrationMonitorPlugin


Plugin = load_plugin_class()


def platform(identity):
    return SimpleNamespace(meta=lambda: SimpleNamespace(name="aiocqhttp", id=identity))


def event(admin=True):
    return SimpleNamespace(
        stop_event=MagicMock(), is_admin=lambda: admin, plain_result=lambda text: text
    )


async def collect(iterator):
    return [item async for item in iterator]


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.payload = example_payload()
        self.http_status = 200
        self.http_body = None

        async def handler(request):
            body = (
                self.http_body
                if self.http_body is not None
                else json.dumps(self.payload)
            )
            return web.Response(
                text=body, status=self.http_status, content_type="text/html"
            )

        app = web.Application()
        app.router.add_get("/api", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.api_url = f"http://127.0.0.1:{self.runner.addresses[0][1]}/api"
        self.platforms = [platform("napcat")]
        self.context = SimpleNamespace(
            disk={},
            platform_manager=SimpleNamespace(get_insts=lambda: self.platforms),
            send_message=AsyncMock(return_value=True),
        )
        self.config = {
            "enabled": False,
            "api_url": self.api_url,
            "group_ids": ["123456789", "987654321"],
            "target_school": "示例大学1",
            "event_name": "测试赛事",
            "page_url": "https://example.org/registration/",
        }
        self.plugin = Plugin(self.context, self.config)

    async def asyncTearDown(self):
        await self.plugin.terminate()
        await self.runner.cleanup()

    async def test_real_http_fetch_and_session_close(self):
        snapshot = await self.plugin._fetch()
        self.assertEqual(snapshot.schools[94].online_rank, 95)
        self.assertIsInstance(self.plugin._session, aiohttp.ClientSession)
        await self.plugin.terminate()
        self.assertTrue(self.plugin._session.closed)

    async def test_cancellation_broadcast_uses_latest_rankings_via_http(self):
        previous = await self.plugin._fetch()
        recipients = self.plugin._recipients()

        async def tick():
            await self.plugin.monitor.tick(
                self.plugin._fetch, self.api_url, recipients, "示例大学1", 95, False
            )

        await tick()
        self.context.send_message.assert_not_awaited()
        removed = previous.schools[0]
        self.payload["data"] = [
            row for row in self.payload["data"] if str(row["id"]) != removed.id
        ]
        await tick()
        self.assertEqual(self.context.send_message.await_count, 2)
        report = self.context.send_message.await_args_list[0].args[1].text
        self.assertIn("取消报名：1所学校", report)
        self.assertIn("示例大学1：上次检测网络赛第1名", report)
        self.assertIn("目标学校 · 示例大学1：当前报名名单中未找到", report)
        self.assertIn(f"报名第95位 · {previous.schools[95].name}：", report)
        self.assertIn("当前已报名：131所", report)
        await tick()
        self.assertEqual(self.context.send_message.await_count, 2)

    async def test_http_and_json_errors_are_handled(self):
        for code, body in [
            (503, "unavailable"),
            (200, "<html>error</html>"),
            (200, '{"success":false}'),
        ]:
            self.http_status, self.http_body = code, body
            with self.subTest(code=code, body=body), self.assertRaises(RankingError):
                await self.plugin._fetch()

    async def test_manual_broadcast_works_when_disabled_and_keeps_state(self):
        before = copy.deepcopy(self.context.disk)
        replies = await collect(self.plugin.broadcast(event()))
        self.assertIn("成功 2/2", replies[0])
        calls = self.context.send_message.await_args_list
        self.assertEqual(
            [c.args[0] for c in calls],
            ["napcat:GroupMessage:123456789", "napcat:GroupMessage:987654321"],
        )
        self.assertIn("【手动播报】", calls[0].args[1].text)
        self.assertIn("示例大学95", calls[0].args[1].text)
        self.assertIn("测试赛事报名排名", calls[0].args[1].text)
        self.assertIn("来源：https://example.org/registration/", calls[0].args[1].text)
        self.assertEqual(self.context.disk, before)

    async def test_manual_broadcast_rejects_non_admin_before_fetch(self):
        self.plugin._fetch = AsyncMock()
        replies = await collect(self.plugin.broadcast(event(admin=False)))
        self.assertIn("仅限 AstrBot 管理员", replies[0])
        self.plugin._fetch.assert_not_awaited()
        self.context.send_message.assert_not_awaited()

    async def test_partial_manual_failure_is_reported(self):
        self.context.send_message.side_effect = [False, True]
        replies = await collect(self.plugin.broadcast(event()))
        self.assertIn("成功 1/2", replies[0])
        self.assertIn("失败QQ群：123456789", replies[0])
        self.assertEqual(self.context.send_message.await_count, 2)

    async def test_preview_only_replies_in_current_session(self):
        replies = await collect(self.plugin.ranking(event(admin=False)))
        self.assertIn("示例大学1：网络赛第1名；报名学校第1位", replies[0])
        self.assertIn("测试赛事报名排名", replies[0])
        self.context.send_message.assert_not_awaited()
        self.assertEqual(self.context.disk, {})

    async def test_no_group_returns_configuration_hint_without_fetch(self):
        self.config["group_ids"] = []
        self.plugin._fetch = AsyncMock()
        replies = await collect(self.plugin.broadcast(event()))
        self.assertIn("group_ids", replies[0])
        self.plugin._fetch.assert_not_awaited()

    async def test_multi_platform_requires_explicit_instance_id(self):
        self.platforms.append(platform("other"))
        with self.assertRaises(RankingError):
            self.plugin._recipients()
        self.config["platform_id"] = "other"
        self.assertEqual(self.plugin._recipients()[0], "other:GroupMessage:123456789")
        self.config["platform_id"] = "missing"
        with self.assertRaises(RankingError):
            self.plugin._recipients()

    async def test_group_dedup_validation_and_invalid_api_url(self):
        self.config["group_ids"] = ["123456789", 123456789, ""]
        self.assertEqual(self.plugin._recipients(), ["napcat:GroupMessage:123456789"])
        self.config["group_ids"] = ["bad-group"]
        with self.assertRaises(RankingError):
            self.plugin._recipients()
        self.config["api_url"] = "file:///etc/passwd"
        with self.assertRaises(RankingError):
            await self.plugin._fetch()

    async def test_initialize_and_terminate_cancel_background_task(self):
        await self.plugin.initialize()
        task = self.plugin._task
        await self.plugin.terminate()
        self.assertTrue(task.cancelled())

    async def test_blank_api_is_rejected_without_http_request(self):
        self.config["api_url"] = ""
        with self.assertRaisesRegex(RankingError, "接口地址"):
            await self.plugin._fetch()
        self.config.pop("api_url")
        with self.assertRaises(RankingError):
            await self.plugin._fetch()

    async def test_optional_page_url_and_custom_cutoff(self):
        self.config["page_url"] = ""
        self.config["cutoff_position"] = 2
        replies = await collect(self.plugin.ranking(event()))
        self.assertIn("报名第2位 · 示例大学2", replies[0])
        self.assertNotIn("来源：", replies[0])
        self.config["page_url"] = "file:///tmp/page"
        replies = await collect(self.plugin.ranking(event()))
        self.assertIn("来源页面地址", replies[0])

    async def test_scheduler_passes_custom_options_to_automatic_reports(self):
        self.config["enabled"] = True
        self.config["notify_on_first_run"] = True
        self.config["cutoff_position"] = 2
        self.payload = example_payload(2)
        with (
            patch("asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)),
            self.assertRaises(asyncio.CancelledError),
        ):
            await self.plugin._scheduler()
        self.assertEqual(self.context.send_message.await_count, 2)
        report = self.context.send_message.await_args_list[0].args[1].text
        self.assertTrue(report.startswith("测试赛事新增报名"))
        self.assertIn("来源：https://example.org/registration/", report)
        self.assertIn("报名第2位 · 示例大学2", report)
        self.assertEqual(self.plugin.last_error, "")


if __name__ == "__main__":
    unittest.main()
