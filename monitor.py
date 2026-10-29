"""持久化的报名变化检测与逐群重试队列。"""

import asyncio
import copy
from collections.abc import Awaitable, Callable
from datetime import datetime

from .ranking import CHINA_TZ, RankingError, Snapshot, build_reports, parse_payload


class RegistrationMonitor:
    def __init__(
        self,
        load: Callable[[], Awaitable[dict]],
        save: Callable[[dict], Awaitable[None]],
        send: Callable[[str, str], Awaitable[None]],
        on_error: Callable[[str], None],
    ):
        self.load = load
        self.save = save
        self.send = send
        self.on_error = on_error
        self.lock = asyncio.Lock()
        self.state: dict | None = None

    async def ready(self):
        if self.state is None:
            state = await self.load()
            if not isinstance(state, dict) or (state and state.get("version") != 1):
                raise RankingError(
                    "报名监控存储格式异常，请检查 AstrBot 插件 KV 数据。"
                )
            if state and (
                not isinstance(state.get("seen"), list)
                or not isinstance(state.get("pending"), list)
                or not isinstance(state.get("initialized"), bool)
            ):
                raise RankingError("报名监控记录损坏，本次停止检测以避免重复播报。")
            if "current_schools" in state:
                # 兼容旧版仅保存 seen 的记录；新版名单损坏时停止检测。
                parse_payload({"success": True, "data": state["current_schools"]})
            self.state = copy.deepcopy(state)

    async def commit(self, state: dict):
        # 先持久化再更新内存，写入失败时保留旧基线以便下轮重试。
        await self.save(copy.deepcopy(state))
        self.state = state

    async def tick(
        self,
        fetch: Callable[[], Awaitable[Snapshot]],
        source: str,
        recipients: list[str],
        target: str,
        cutoff: int,
        notify_initial: bool,
        *,
        event_name: str = "赛事",
        page_url: str = "",
    ):
        async with self.lock:
            await self.ready()
            if not recipients:
                return
            if not self.state or self.state.get("source") != source:
                await self.commit(
                    {
                        "version": 1,
                        "source": source,
                        "initialized": False,
                        "seen": [],
                        "pending": [],
                    }
                )
            # 即使接口暂时故障，也优先重试此前已经入队的播报。
            await self.flush(recipients)
            snapshot = await fetch()
            updated = copy.deepcopy(self.state)
            seen = set(updated["seen"])
            current_ids = {s.id for s in snapshot.schools}
            new_ids = current_ids - seen
            cancelled = ()
            if updated["initialized"] and "current_schools" in updated:
                previous = parse_payload(
                    {"success": True, "data": updated["current_schools"]}
                )
                cancelled = tuple(
                    s for s in previous.schools if s.id not in current_ids
                )
            if (new_ids or cancelled) and (updated["initialized"] or notify_initial):
                updated["pending"].append(
                    {
                        "messages": build_reports(
                            snapshot,
                            target,
                            cutoff,
                            new_ids,
                            cancelled,
                            event_name=event_name,
                            page_url=page_url,
                        ),
                        "recipients": {origin: 0 for origin in recipients},
                    }
                )
            updated["seen"] = sorted(seen | current_ids)
            updated["current_schools"] = [
                {
                    "id": s.id,
                    "nameZh": s.name_zh,
                    "nameEn": s.name_en,
                    "rank": s.online_rank,
                }
                for s in snapshot.schools
            ]
            updated["initialized"] = True
            updated["last_check"] = datetime.now(CHINA_TZ).isoformat(timespec="seconds")
            updated["count"] = len(snapshot.schools)
            # 当前名单和待发队列一同保存，防止发送失败后遗漏变化。
            await self.commit(updated)
            await self.flush(recipients)

    async def flush(self, allowed: list[str]):
        updated = copy.deepcopy(self.state)
        for job in updated["pending"]:
            job["recipients"] = {
                origin: cursor
                for origin, cursor in job["recipients"].items()
                if origin in allowed
            }
        updated["pending"] = [j for j in updated["pending"] if j["recipients"]]
        if updated != self.state:
            await self.commit(updated)
        # 一个群失败不阻塞其他群；失败群本轮不越过旧消息发送新消息。
        blocked = set()
        for index in range(len(self.state["pending"])):
            job = self.state["pending"][index]
            for origin in list(job["recipients"]):
                if origin in blocked:
                    continue
                cursor = self.state["pending"][index]["recipients"][origin]
                while cursor < len(job["messages"]):
                    try:
                        await self.send(origin, job["messages"][cursor])
                    except Exception as exc:
                        self.on_error(f"报名播报发送至 {origin} 失败，将重试：{exc}")
                        blocked.add(origin)
                        break
                    cursor += 1
                    updated = copy.deepcopy(self.state)
                    updated["pending"][index]["recipients"][origin] = cursor
                    await self.commit(updated)
                if cursor == len(job["messages"]):
                    updated = copy.deepcopy(self.state)
                    del updated["pending"][index]["recipients"][origin]
                    await self.commit(updated)
        updated = copy.deepcopy(self.state)
        updated["pending"] = [j for j in updated["pending"] if j["recipients"]]
        if updated != self.state:
            await self.commit(updated)
