"""定时检测学校报名变化并向配置的QQ群播报。"""

import asyncio
import re
from contextlib import suppress
from urllib.parse import urlsplit

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

from .monitor import RegistrationMonitor
from .ranking import RankingError, build_reports, parse_payload

HELP = """赛事报名监控
/报名排名：预览目标学校和设置中关注位次的学校排名
/报名播报：立即向插件设置中的所有QQ群播报（AstrBot 管理员）
/报名状态：查看监控状态（AstrBot 管理员）
赛事名称、接口链接、来源页面、目标学校、QQ群号等均在 AstrBot 插件设置中修改。
手动播报不改变自动检测记录；有新增或取消报名时自动播报变化学校、目标学校和关注位次的学校。"""


class RegistrationMonitorPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._request_lock = asyncio.Lock()
        self._manual_lock = asyncio.Lock()
        self.last_error = ""
        self.monitor = RegistrationMonitor(
            self._load, self._save, self._send, logger.warning
        )

    async def _load(self):
        return await self.get_kv_data("registration_monitor_v1", {})

    async def _save(self, value):
        await self.put_kv_data("registration_monitor_v1", value)

    async def initialize(self):
        await self.monitor.ready()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._scheduler(), name="registration-monitor"
            )

    async def terminate(self):
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        if self._session:
            await self._session.close()

    def _api_url(self) -> str:
        # LINK-1：数据接口在插件设置 api_url 中填写，不在代码中绑定某个站点。
        url = str(self.config.get("api_url", "")).strip()
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise RankingError("请在插件设置中填写有效的 HTTP/HTTPS 数据接口地址。")
        return url

    def _recipients(self) -> list[str]:
        groups = self.config.get("group_ids", [])
        if not isinstance(groups, list):
            raise RankingError("QQ群号须在插件设置中填写为列表，每项一个群号。")
        groups = list(dict.fromkeys(str(g).strip() for g in groups if str(g).strip()))
        if not groups:
            return []
        if any(not re.fullmatch(r"[1-9]\d{4,14}", g) for g in groups):
            raise RankingError("QQ群号格式错误，请逐项填写纯数字群号。")
        platform_id = str(self.config.get("platform_id", "")).strip()
        # UMO 需要适配器实例 ID，不是 QQ 号或适配器类型名。
        platforms = [
            p
            for p in self.context.platform_manager.get_insts()
            if p.meta().name == "aiocqhttp"
        ]
        if platform_id:
            platforms = [p for p in platforms if p.meta().id == platform_id]
        if len(platforms) != 1:
            raise RankingError(
                "未找到唯一的 OneBot v11 平台。请先连接 NapCat/Lagrange 等，"
                "多个连接时在插件设置中填写平台 ID。"
            )
        platform_id = platforms[0].meta().id
        return [f"{platform_id}:GroupMessage:{group}" for group in groups]

    async def _fetch(self):
        async with self._request_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    headers={
                        "User-Agent": "AstrBot-Registration-Monitor/1.0",
                        "Accept": "application/json",
                    },
                    trust_env=False,
                )
            timeout = max(5, min(120, int(self.config.get("request_timeout", 60))))
            try:
                async with self._session.get(
                    self._api_url(), timeout=aiohttp.ClientTimeout(total=timeout)
                ) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
                return parse_payload(payload)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                if isinstance(exc, RankingError):
                    raise
                raise RankingError(
                    f"获取报名数据失败（{type(exc).__name__}），请稍后重试。"
                ) from exc

    async def _send(self, origin: str, text: str):
        sent = await self.context.send_message(origin, MessageChain().message(text))
        if sent is False:
            raise RuntimeError("AstrBot 未找到目标平台，消息未发送")

    def _report_options(self):
        target = str(self.config.get("target_school", "")).strip()
        cutoff = max(1, int(self.config.get("cutoff_position", 95)))
        event_name = str(self.config.get("event_name", "")).strip() or "赛事"
        # LINK-2：消息底部的来源页面独立于 api_url，留空则隐藏。
        page_url = str(self.config.get("page_url", "")).strip()
        if page_url:
            parsed = urlsplit(page_url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise RankingError("请填写有效的 HTTP/HTTPS 来源页面地址，或留空隐藏。")
        return target, cutoff, event_name, page_url

    def _build_reports(self, snapshot):
        target, cutoff, event_name, page_url = self._report_options()
        return build_reports(
            snapshot, target, cutoff, event_name=event_name, page_url=page_url
        )

    async def _scheduler(self):
        while True:
            interval = 300
            try:
                interval = max(60, int(self.config.get("check_interval_seconds", 300)))
                if self.config.get("enabled", True):
                    recipients = self._recipients()
                    if not recipients:
                        self.last_error = (
                            "尚未在插件设置中填写接收QQ群号，自动监控等待配置。"
                        )
                    else:
                        target, cutoff, event_name, page_url = self._report_options()
                        await self.monitor.tick(
                            self._fetch,
                            self._api_url(),
                            recipients,
                            target,
                            cutoff,
                            bool(self.config.get("notify_on_first_run", False)),
                            event_name=event_name,
                            page_url=page_url,
                        )
                        self.last_error = ""
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("报名监控本轮失败，将在下轮重试")
            await asyncio.sleep(interval)

    @filter.command("报名排名")
    async def ranking(self, event: AstrMessageEvent):
        """预览当前排名，不更改监控状态。"""
        event.stop_event()
        try:
            snapshot = await self._fetch()
            yield event.plain_result(self._build_reports(snapshot)[0])
        except RankingError as exc:
            yield event.plain_result(str(exc))
        except Exception:
            logger.exception("报名排名查询失败")
            yield event.plain_result("报名排名查询失败，请查看 AstrBot 日志。")

    @filter.command("报名播报")
    async def broadcast(self, event: AstrMessageEvent):
        """管理员手动触发，发送到设置中的全部QQ群。"""
        event.stop_event()
        if not event.is_admin():
            yield event.plain_result("手动播报仅限 AstrBot 管理员使用。")
            return
        async with self._manual_lock:
            try:
                recipients = self._recipients()
                if not recipients:
                    yield event.plain_result(
                        "请先在插件设置的 group_ids 中填写接收QQ群号。"
                    )
                    return
                text = self._build_reports(await self._fetch())[0]
                failed = []
                for origin in recipients:
                    try:
                        await self._send(origin, "【手动播报】\n" + text)
                    except Exception:
                        logger.exception(f"手动报名播报至 {origin} 失败")
                        failed.append(origin.rsplit(":", 1)[-1])
                result = f"手动播报完成：成功 {len(recipients) - len(failed)}/{len(recipients)} 个群。"
                if failed:
                    result += "\n失败QQ群：" + "、".join(failed) + "，可稍后重新触发。"
                yield event.plain_result(result)
            except RankingError as exc:
                yield event.plain_result(str(exc))
            except Exception:
                logger.exception("手动报名播报失败")
                yield event.plain_result("手动播报失败，请查看 AstrBot 日志。")

    @filter.command("报名状态")
    async def status(self, event: AstrMessageEvent):
        event.stop_event()
        if not event.is_admin():
            yield event.plain_result("监控状态仅限 AstrBot 管理员查看。")
            return
        async with self.monitor.lock:
            await self.monitor.ready()
            state = self.monitor.state or {}
            lines = [
                f"自动监控：{'开启' if self.config.get('enabled', True) else '关闭'}",
                f"赛事名称：{self.config.get('event_name', '') or '赛事'}",
                f"数据接口：{'已设置' if self.config.get('api_url', '').strip() else '未设置，请填写 api_url'}",
                f"来源页面：{'已设置' if self.config.get('page_url', '').strip() else '未设置，消息不显示来源链接'}",
                f"关注位次：{self.config.get('cutoff_position', 95)}",
                f"检查间隔：{self.config.get('check_interval_seconds', 300)} 秒（最低60秒）",
                f"接收QQ群：{self.config.get('group_ids', [])}",
                f"目标学校：{self.config.get('target_school', '') or '未设置'}",
                f"监控基线：{'已建立' if state.get('initialized') else '尚未建立'}",
                f"最近成功检查：{state.get('last_check', '无')}",
                f"最近报名数：{state.get('count', '未知')}",
                f"待发送批次：{len(state.get('pending', []))}",
                f"最近检查异常：{self.last_error or '无'}",
                "\n" + HELP,
            ]
        yield event.plain_result("\n".join(lines))
