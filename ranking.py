"""兼容学校报名 JSON 接口的解析与排名播报，不依赖 AstrBot。"""

import re
import unicodedata
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

CHINA_TZ = timezone(timedelta(hours=8))


class RankingError(ValueError):
    """接口或配置异常，不能用于更新监控基线。"""


def normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


@dataclass(frozen=True)
class School:
    id: str
    name_zh: str
    name_en: str
    online_rank: int | None

    @property
    def name(self) -> str:
        return self.name_zh or self.name_en


@dataclass(frozen=True)
class Snapshot:
    schools: tuple[School, ...]
    updated_at: str

    def school_line(self, index: int) -> str:
        school = self.schools[index]
        rank = f"第{school.online_rank}名" if school.online_rank else "暂无排名"
        return f"{school.name}：网络赛{rank}；报名学校第{index + 1}位"


def parse_payload(payload: object) -> Snapshot:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise RankingError("报名接口返回失败，请查看站点或稍后重试。")
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        raise RankingError("报名接口数据为空或格式异常，本次不更新监控记录。")
    schools = []
    ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RankingError("报名接口学校条目格式异常。")
        identity = row.get("id")
        zh, en = row.get("nameZh") or "", row.get("nameEn") or ""
        if (
            isinstance(identity, bool)
            or not isinstance(identity, (str, int))
            or not str(identity).strip()
            or not isinstance(zh, str)
            or not isinstance(en, str)
            or not (zh.strip() or en.strip())
        ):
            raise RankingError("报名接口缺少学校 ID 或名称。")
        identity = str(identity).strip()
        if identity in ids:
            raise RankingError("报名接口包含重复学校 ID，本次不更新监控记录。")
        ids.add(identity)
        value = row.get("rank")
        if isinstance(value, bool):
            raise RankingError("报名接口网络赛排名格式异常。")
        if value is None or value == "" or value == 0 or value == "0":
            rank = None
        elif not re.fullmatch(r"[1-9]\d*", str(value)):
            raise RankingError("报名接口网络赛排名格式异常。")
        else:
            rank = int(value)
        schools.append(School(identity, zh.strip(), en.strip(), rank))
    # 稳定排序：网络赛名次相同的学校沿用接口顺序，与网页位次口径一致。
    schools.sort(key=lambda s: (s.online_rank is None, s.online_rank or 0))
    timestamp = payload.get("timestamp")
    updated_at = "接口未提供更新时间"
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        with suppress(ValueError, OverflowError, OSError):
            updated_at = datetime.fromtimestamp(timestamp / 1000, CHINA_TZ).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
    return Snapshot(tuple(schools), updated_at)


def target_line(snapshot: Snapshot, target: str) -> str:
    if not target.strip():
        return "目标学校：尚未在插件设置中填写。"
    key = normalize(target)
    matches = [
        i
        for i, school in enumerate(snapshot.schools)
        if key in {normalize(school.name_zh), normalize(school.name_en), school.id}
    ]
    if len(matches) == 1:
        return "目标学校 · " + snapshot.school_line(matches[0])
    if len(matches) > 1:
        return "目标学校匹配多条记录，请在设置中填写学校 ID。"
    return (
        f"目标学校 · {target}：当前报名名单中未找到；报名位次暂无，"
        "网络赛排名无法从此接口获取。请核对完整中英文校名或学校 ID。"
    )


def build_reports(
    snapshot: Snapshot,
    target: str,
    cutoff: int = 95,
    new_ids: set[str] | None = None,
    cancelled_schools: tuple[School, ...] = (),
    *,
    event_name: str = "赛事",
    page_url: str = "",
) -> list[str]:
    """报名变化分批发送，每批均带目标学校及第 N 位学校的信息。"""
    indices = [i for i, s in enumerate(snapshot.schools) if new_ids and s.id in new_ids]
    entries = [("新报名学校", snapshot.school_line(i)) for i in indices]
    for school in cancelled_schools:
        rank = f"第{school.online_rank}名" if school.online_rank else "暂无排名"
        entries.append(
            (
                "取消报名学校",
                f"{school.name}：上次检测网络赛{rank}；当前已不在报名名单",
            )
        )
    batches = [entries[i : i + 10] for i in range(0, len(entries), 10)] or [[]]
    result = []
    for number, batch in enumerate(batches, 1):
        title = f"{event_name}报名排名"
        if cancelled_schools:
            title = f"{event_name}取消报名：{len(cancelled_schools)}所学校"
            if indices:
                title = f"{event_name}新增报名：{len(indices)}所学校；取消报名：{len(cancelled_schools)}所学校"
        elif new_ids is not None:
            title = f"{event_name}新增报名：{len(indices)}所学校"
        if len(batches) > 1:
            title += f"（{number}/{len(batches)}）"
        lines = [title, f"当前已报名：{len(snapshot.schools)}所"]
        if batch:
            section = None
            for label, text in batch:
                if label != section:
                    lines.append(f"\n{label}：")
                    section = label
                lines.append(text)
        lines.append("\n" + target_line(snapshot, target))
        if len(snapshot.schools) >= cutoff:
            lines.append(f"\n报名第{cutoff}位 · " + snapshot.school_line(cutoff - 1))
        else:
            lines.append(f"\n报名第{cutoff}位：当前不足{cutoff}所，暂无对应学校。")
        lines.extend(
            [
                "\n位次按网络赛排名升序排列；同名次沿用接口顺序。",
                f"数据更新时间（北京时间）：{snapshot.updated_at}",
            ]
        )
        if page_url:
            lines.append(f"来源：{page_url}")
        result.append("\n".join(lines))
    return result
