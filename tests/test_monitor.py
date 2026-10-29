import copy
import unittest

import _support  # noqa: F401
from astrbot_plugin_registration_monitor.monitor import RegistrationMonitor
from astrbot_plugin_registration_monitor.ranking import (
    RankingError,
    parse_payload,
)


def snapshot(*ids):
    return parse_payload(
        {
            "success": True,
            "data": [
                {"id": s, "nameZh": f"大学{s}", "nameEn": "", "rank": int(s)}
                for s in ids
            ],
        }
    )


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.disk = {}
        self.sent = []
        self.errors = []
        self.fail_groups = set()
        self.fail_fetch = False
        self.fail_save = False
        self.current = snapshot("1")
        self.monitor = self.create_monitor()

    def create_monitor(self):
        async def load():
            return copy.deepcopy(self.disk)

        async def save(state):
            if self.fail_save:
                raise OSError("disk full")
            self.disk = copy.deepcopy(state)

        async def send(origin, text):
            if origin in self.fail_groups:
                raise RuntimeError("offline")
            self.sent.append((origin, text))

        return RegistrationMonitor(load, save, send, self.errors.append)

    async def fetch(self):
        if self.fail_fetch:
            raise RankingError("timeout")
        return self.current

    async def tick(self, groups=None, initial=False, source="api1"):
        await self.monitor.tick(
            self.fetch,
            source,
            ["group1", "group2"] if groups is None else groups,
            "大学1",
            95,
            initial,
        )

    async def test_first_run_baseline_new_school_and_restart_dedup(self):
        await self.tick()
        self.assertEqual(self.sent, [])
        self.current = snapshot("1", "2")
        await self.tick()
        self.assertEqual(len(self.sent), 2)
        self.assertIn("大学2：网络赛第2名；报名学校第2位", self.sent[0][1])
        self.monitor = self.create_monitor()
        await self.tick()
        self.assertEqual(len(self.sent), 2)

    async def test_departed_then_returned_school_is_not_new(self):
        self.current = snapshot("1", "2")
        await self.tick()
        self.current = snapshot("1")
        await self.tick()
        self.current = snapshot("1", "2")
        await self.tick()
        self.assertEqual(len(self.sent), 2)
        self.assertIn("取消报名：1所学校", self.sent[0][1])
        self.assertIn("大学2：上次检测网络赛第2名；当前已不在报名名单", self.sent[0][1])
        self.assertNotIn("新增报名", self.sent[0][1])

    async def test_cancellation_after_restart_is_reported_once(self):
        self.current = snapshot("1", "2")
        await self.tick()
        self.monitor = self.create_monitor()
        self.current = snapshot("1")
        await self.tick()
        self.assertEqual(len(self.sent), 2)
        self.assertIn("当前已报名：1所", self.sent[0][1])
        self.assertIn("目标学校 · 大学1", self.sent[0][1])
        self.assertIn("当前不足95所", self.sent[0][1])
        self.monitor = self.create_monitor()
        await self.tick()
        self.assertEqual(len(self.sent), 2)
        # 重新出现仍沿用历史 ID 去重，但再次取消应再次通知。
        self.current = snapshot("1", "2")
        await self.tick()
        self.current = snapshot("1")
        await self.tick()
        self.assertEqual(len(self.sent), 4)

    async def test_simultaneous_addition_and_cancellation_with_same_count(self):
        self.current = snapshot("1", "2")
        await self.tick()
        self.current = snapshot("1", "3")
        await self.tick()
        self.assertEqual(len(self.sent), 2)
        report = self.sent[0][1]
        self.assertIn("新增报名：1所学校；取消报名：1所学校", report)
        self.assertIn("大学3：网络赛第3名；报名学校第2位", report)
        self.assertIn("大学2：上次检测网络赛第2名", report)

    async def test_cancellation_retries_after_restart_and_fetch_failure(self):
        self.current = snapshot("1", "2")
        await self.tick()
        self.fail_groups.add("group2")
        self.current = snapshot("1")
        await self.tick()
        self.assertEqual([g for g, _ in self.sent], ["group1"])
        self.monitor = self.create_monitor()
        self.fail_groups.clear()
        self.fail_fetch = True
        with self.assertRaises(RankingError):
            await self.tick()
        self.assertEqual([g for g, _ in self.sent], ["group1", "group2"])
        self.assertEqual(self.sent[0][1], self.sent[1][1])
        self.assertEqual(self.disk["pending"], [])

    async def test_cancellation_save_failure_preserves_previous_school(self):
        self.current = snapshot("1", "2")
        await self.tick()
        before = copy.deepcopy(self.disk)
        self.fail_save = True
        self.current = snapshot("1")
        with self.assertRaises(OSError):
            await self.tick()
        self.assertEqual(self.disk, before)
        self.assertEqual(self.monitor.state, before)
        self.assertEqual(self.sent, [])
        self.fail_save = False
        await self.tick()
        self.assertEqual(len(self.sent), 2)
        self.assertIn("大学2：上次检测", self.sent[0][1])

    async def test_legacy_state_builds_cancellation_baseline_and_keeps_pending(self):
        self.disk = {
            "version": 1,
            "source": "api1",
            "initialized": True,
            "seen": ["1", "2", "3"],
            "pending": [{"messages": ["旧版待发消息"], "recipients": {"group2": 0}}],
        }
        self.current = snapshot("1", "2")
        await self.tick()
        self.assertEqual(self.sent, [("group2", "旧版待发消息")])
        self.assertEqual(self.disk["seen"], ["1", "2", "3"])
        self.monitor = self.create_monitor()
        self.current = snapshot("1")
        await self.tick()
        self.assertEqual(len(self.sent), 3)
        self.assertIn("大学2：上次检测", self.sent[1][1])
        self.assertNotIn("大学3", self.sent[1][1])

    async def test_name_and_rank_changes_update_details_without_broadcast(self):
        self.current = snapshot("1", "2")
        await self.tick()
        self.current = parse_payload(
            {
                "success": True,
                "data": [
                    {"id": "1", "nameZh": "大学1", "rank": 1},
                    {"id": "2", "nameZh": "新校名", "rank": 100},
                ],
            }
        )
        await self.tick()
        self.assertEqual(self.sent, [])
        self.current = snapshot("1")
        await self.tick()
        self.assertIn("新校名：上次检测网络赛第100名", self.sent[0][1])

    async def test_group_failure_retries_after_restart_without_duplicate_success(self):
        await self.tick()
        self.fail_groups.add("group2")
        self.current = snapshot("1", "2")
        await self.tick()
        self.assertEqual([origin for origin, _ in self.sent], ["group1"])
        self.assertTrue(self.disk["pending"])
        self.monitor = self.create_monitor()
        self.fail_groups.clear()
        await self.tick()
        self.assertEqual([origin for origin, _ in self.sent], ["group1", "group2"])
        self.assertEqual(self.disk["pending"], [])

    async def test_pending_delivered_even_when_upstream_is_down(self):
        await self.tick()
        self.fail_groups.add("group2")
        self.current = snapshot("1", "2")
        await self.tick()
        self.fail_groups.clear()
        self.fail_fetch = True
        with self.assertRaises(RankingError):
            await self.tick()
        self.assertEqual([g for g, _ in self.sent], ["group1", "group2"])
        self.assertEqual(self.disk["pending"], [])

    async def test_invalid_fetch_and_save_failure_preserve_baseline(self):
        await self.tick()
        before = copy.deepcopy(self.disk)
        self.fail_fetch = True
        with self.assertRaises(RankingError):
            await self.tick()
        self.assertEqual(self.disk, before)
        self.fail_fetch = False
        self.fail_save = True
        self.current = snapshot("1", "2")
        with self.assertRaises(OSError):
            await self.tick()
        self.assertEqual(self.monitor.state, before)
        self.assertEqual(self.sent, [])
        self.fail_save = False
        await self.tick()
        self.assertEqual(len(self.sent), 2)

    async def test_no_group_does_not_consume_new_registrations(self):
        await self.tick(groups=[])
        self.assertEqual(self.disk, {})
        await self.tick()
        self.current = snapshot("1", "2")
        await self.tick(groups=[])
        await self.tick()
        self.assertEqual(len(self.sent), 2)

    async def test_removed_recipient_is_not_sent_pending_message(self):
        await self.tick()
        self.fail_groups.add("group2")
        self.current = snapshot("1", "2")
        await self.tick()
        self.fail_groups.clear()
        await self.tick(groups=["group1"])
        self.assertEqual([g for g, _ in self.sent], ["group1"])
        self.assertEqual(self.disk["pending"], [])

    async def test_initial_opt_in_and_source_change_rebaseline(self):
        await self.tick(initial=True)
        self.assertEqual(len(self.sent), 2)
        self.current = snapshot("1", "2")
        await self.tick(source="api2")
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.disk["seen"], ["1", "2"])

    async def test_custom_presentation_reaches_automatic_queue(self):
        await self.tick()
        self.current = snapshot("1", "2")
        await self.monitor.tick(
            self.fetch,
            "api1",
            ["group1"],
            "大学1",
            2,
            False,
            event_name="区域赛",
            page_url="https://example.org/registration/",
        )
        self.assertTrue(self.sent[0][1].startswith("区域赛新增报名"))
        self.assertIn("报名第2位 · 大学2", self.sent[0][1])
        self.assertIn("来源：https://example.org/registration/", self.sent[0][1])

    async def test_ordered_batched_retry_and_more_new_schools(self):
        await self.tick()
        self.fail_groups.add("group2")
        self.current = snapshot(*map(str, range(1, 23)))
        await self.tick()
        self.assertEqual(len(self.sent), 3)
        self.current = snapshot(*map(str, range(1, 24)))
        await self.tick()
        self.assertEqual(len(self.sent), 4)
        self.fail_groups.clear()
        await self.tick()
        sent_group2 = [text for origin, text in self.sent if origin == "group2"]
        self.assertEqual(len(sent_group2), 4)
        self.assertIn("（1/3）", sent_group2[0])
        self.assertIn("（3/3）", sent_group2[2])
        self.assertIn("新增报名：1所", sent_group2[3])

    async def test_corrupted_storage_stops_instead_of_sending_all_as_new(self):
        self.disk = {"version": 99}
        with self.assertRaises(RankingError):
            await self.tick()
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
