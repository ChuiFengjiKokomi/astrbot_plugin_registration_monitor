import unittest

from _support import example_payload
from astrbot_plugin_registration_monitor.ranking import (
    RankingError,
    build_reports,
    parse_payload,
    target_line,
)


def payload(*ranks):
    return {
        "success": True,
        "data": [
            {"id": str(i), "nameZh": f"学校{i}", "nameEn": f"School {i}", "rank": rank}
            for i, rank in enumerate(ranks, 1)
        ],
        "timestamp": 1792932019000,
    }


class RankingTests(unittest.TestCase):
    def test_synthetic_fixture_and_95th_school(self):
        snapshot = parse_payload(example_payload())
        self.assertEqual(len(snapshot.schools), 132)
        self.assertEqual(snapshot.schools[0].name, "示例大学1")
        self.assertEqual(snapshot.schools[94].name, "示例大学95")
        self.assertEqual(snapshot.schools[94].online_rank, 95)
        report = build_reports(snapshot, "示例大学1")[0]
        self.assertIn("目标学校 · 示例大学1：网络赛第1名；报名学校第1位", report)
        self.assertIn("报名第95位 · 示例大学95：网络赛第95名；报名学校第95位", report)

    def test_custom_event_and_source_apply_to_every_change_batch(self):
        snapshot = parse_payload(example_payload(24))
        reports = build_reports(
            snapshot,
            "",
            new_ids={school.id for school in snapshot.schools},
            event_name="测试赛事",
            page_url="https://example.org/registration/",
        )
        self.assertEqual(len(reports), 3)
        for report in reports:
            self.assertTrue(report.startswith("测试赛事新增报名"))
            self.assertIn("来源：https://example.org/registration/", report)
            self.assertNotIn("香港", report)
            self.assertNotIn("非港澳", report)
        cancellation = build_reports(
            parse_payload(example_payload(1)),
            "",
            cancelled_schools=snapshot.schools[1:],
            event_name="测试赛事",
        )
        self.assertTrue(all(r.startswith("测试赛事取消报名") for r in cancellation))

    def test_blank_source_does_not_leak_a_default_site(self):
        report = build_reports(parse_payload(payload(1)), "")[0]
        self.assertTrue(report.startswith("赛事报名排名"))
        self.assertNotIn("来源：", report)
        self.assertNotIn("https://", report)

    def test_sort_new_schools_and_missing_cutoff(self):
        snapshot = parse_payload(payload(40, 10, 20))
        report = build_reports(snapshot, "学校1", new_ids={"1", "3"})[0]
        self.assertIn("新增报名：2所学校", report)
        self.assertIn("学校3：网络赛第20名；报名学校第2位", report)
        self.assertIn("学校1：网络赛第40名；报名学校第3位", report)
        self.assertIn("当前不足95所", report)

    def test_ties_keep_api_order_and_null_ranks_go_last(self):
        snapshot = parse_payload(payload(None, 12, 12, 0, 2))
        self.assertEqual([s.id for s in snapshot.schools], ["5", "2", "3", "1", "4"])
        self.assertIn("暂无排名", snapshot.school_line(3))

    def test_exact_english_normalization_id_and_missing_target(self):
        snapshot = parse_payload(payload(1, 2))
        self.assertIn("学校1：", target_line(snapshot, " ＳＣＨＯＯＬ １ "))
        self.assertIn("学校2：", target_line(snapshot, "2"))
        self.assertIn("未找到", target_line(snapshot, "学校"))
        self.assertIn("无法从此接口获取", target_line(snapshot, "不存在大学"))
        self.assertIn("尚未", target_line(snapshot, ""))

    def test_invalid_payload_does_not_look_like_empty_registration(self):
        invalid = [None, [], {}, {"success": False}, {"success": True, "data": []}]
        for obj in invalid:
            with self.subTest(obj=obj), self.assertRaises(RankingError):
                parse_payload(obj)
        for mutate in (
            lambda p: p["data"][0].pop("id"),
            lambda p: p["data"][0].update(rank="oops"),
            lambda p: p["data"][0].update(rank=-1),
            lambda p: p["data"][0].update(rank=True),
            lambda p: p["data"][0].update(rank=False),
            lambda p: p["data"].append(p["data"][0]),
        ):
            obj = payload(1)
            mutate(obj)
            with self.subTest(obj=obj), self.assertRaises(RankingError):
                parse_payload(obj)

    def test_large_batch_is_split_without_omissions(self):
        snapshot = parse_payload(payload(*range(1, 24)))
        reports = build_reports(
            snapshot, "学校1", new_ids={s.id for s in snapshot.schools}
        )
        self.assertEqual(len(reports), 3)
        for school in snapshot.schools:
            text = f"\n{school.name}：网络赛"
            self.assertEqual(sum(report.count(text) for report in reports), 1)
        self.assertTrue(all("目标学校" in r and "报名第95位" in r for r in reports))

    def test_cancellations_split_and_do_not_claim_current_positions(self):
        previous = parse_payload(payload(*range(1, 24), None))
        current = parse_payload(payload(1))
        reports = build_reports(
            current, "学校2", cancelled_schools=previous.schools[1:]
        )
        self.assertEqual(len(reports), 3)
        self.assertIn("取消报名：23所学校（1/3）", reports[0])
        for school in previous.schools[1:]:
            self.assertEqual(
                sum(r.count(f"\n{school.name}：上次检测网络赛") for r in reports), 1
            )
        self.assertIn("学校24：上次检测网络赛暂无排名", reports[-1])
        self.assertTrue(all("目标学校" in r and "报名第95位" in r for r in reports))
        self.assertTrue(all("当前报名名单中未找到" in r for r in reports))
        self.assertNotIn("学校2：网络赛第2名；报名学校第2位", "\n".join(reports))

    def test_mixed_changes_share_ten_school_batch_limit(self):
        previous = parse_payload(payload(*range(1, 8)))
        updated = payload(*range(1, 14))
        updated["data"] = [row for row in updated["data"] if int(row["id"]) >= 8]
        current = parse_payload(updated)
        reports = build_reports(
            current,
            "学校1",
            new_ids={str(i) for i in range(8, 14)},
            cancelled_schools=previous.schools,
        )
        self.assertEqual(len(reports), 2)
        self.assertIn("新增报名：6所学校；取消报名：7所学校", reports[0])
        self.assertIn("新报名学校：", reports[0])
        self.assertIn("取消报名学校：", reports[0])
        self.assertEqual(reports[0].count("\n学校"), 10)
        self.assertEqual(reports[1].count("\n学校"), 3)


if __name__ == "__main__":
    unittest.main()
