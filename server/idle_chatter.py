"""Occasional small talk that Amadeus opens on its own.

Scope is deliberately narrow: weekdays only, 10:00-18:00 local time, and only
after the conversation has been quiet for a randomized stretch. Delivery reuses
the source-neutral narration boundary, so a line reaches exactly the same
subtitle + TTS pipeline as AUIP and Work narration instead of inventing a second
speech path.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import inspect
import logging
import random
import time
from typing import Any, Awaitable, Callable

from server.event_bus import bus
from server.narration_delivery import NarrationRequest, NarrationSink, deliver_narration
from server.protocol import Method

logger = logging.getLogger("idle_chatter")

ACTIVE_START_HOUR = 10
ACTIVE_END_HOUR = 18
IDLE_MIN_SECONDS = 12 * 60
IDLE_MAX_SECONDS = 28 * 60
TICK_SECONDS = 45

IDLE_CHATTER_PROMPT = (
    "你是牧濑红莉栖（Makise Kurisu），18 岁，维克多·孔多利亚大学脑科学研究所的研究员。"
    "现在用户就坐在电脑前，但已经有一段时间没有和你说话了。"
    "请主动开口说一句很短的话。语气保持她的风格：聪明、略带吐槽、不肉麻、不客套。"
    "硬性要求：只输出这句日语本身（一到两句、口语化），不要中文、不要引号、不要解释、"
    "不要问'在吗'，不要使用敬语套话，也不要在开头加问候语。"
)

# Most openers continue the previous thread; only occasionally start something new.
NEW_TOPIC_RATE = 0.2
CONTEXT_LINES = 8


def build_turn_prompt(context: str, fresh_topic: bool) -> str:
    """Explicit instruction for this opener, so the model cannot drift to either
    extreme (always rehashing history, or always ignoring it)."""
    if fresh_topic or not context.strip():
        return (
            "（这次请开一个新话题，不要接着刚才对话里已经聊过的内容。"
            "只输出那句话本身。）"
        )
    return (
        "（下面是你们最近的对话，请顺着这个上下文自然地接一句：可以是追问、补充、"
        "或者对他刚才状态的关心，不要另起新话题，也不要重复他或你说过的原句。"
        "只输出那句话本身。）\n\n"
        f"{context}"
    )


class IdleChatter:
    """Timed, idle-gated, weekday-only proactive lines."""

    def __init__(
        self,
        *,
        sink: NarrationSink | None,
        author: Callable[[str], Awaitable[str]],
        context: Callable[[], str] | None = None,
        busy: Callable[[], bool] | None = None,
        clock: Callable[[], dt.datetime] | None = None,
        random_source: random.Random | None = None,
        record: Callable[[str], None] | None = None,
    ) -> None:
        self._sink = sink
        self._author = author
        self._context = context or (lambda: "")
        self._busy = busy or (lambda: False)
        self._record = record
        self._clock = clock or dt.datetime.now
        self._random = random_source or random.Random()
        self._last_activity = time.monotonic()
        self._next_delay = self._pick_delay()
        self._delivering = False
        self._task: asyncio.Task[None] | None = None

    def _pick_delay(self) -> float:
        return self._random.uniform(IDLE_MIN_SECONDS, IDLE_MAX_SECONDS)

    async def note_activity(self, *_args: Any, **_kwargs: Any) -> None:
        """Any chat traffic resets the quiet window and re-randomizes it."""
        self._last_activity = time.monotonic()
        self._next_delay = self._pick_delay()

    def within_window(self) -> bool:
        now = self._clock()
        return now.weekday() < 5 and ACTIVE_START_HOUR <= now.hour < ACTIVE_END_HOUR

    def quiet_for(self) -> float:
        return time.monotonic() - self._last_activity

    def due(self) -> bool:
        return (
            self._sink is not None
            and self.within_window()
            and not self._delivering
            and self.quiet_for() >= self._next_delay
            and not self._busy()
        )

    async def tick(self) -> bool:
        """One scheduling step; returns True when a line was delivered."""
        if not self.due():
            return False
        self._delivering = True
        try:
            fresh_topic = self._random.random() < NEW_TOPIC_RATE
            try:
                context = "" if fresh_topic else str(self._context() or "").strip()
            except Exception:
                logger.exception("idle chatter context lookup failed")
                context = ""
            prompt = build_turn_prompt(context, fresh_topic)
            try:
                raw = await self._author(prompt)
            except Exception:
                logger.exception("idle chatter author failed")
                await self.note_activity()
                return False
            text = " ".join(str(raw or "").split()).strip()
            if not text:
                await self.note_activity()
                return False
            request = NarrationRequest(
                request_id=f"idle-{time.time_ns()}",
                source_kind="host",
                source_id="idle-chatter",
                session_id="",
                payload={
                    "text": text,
                    "voice_text_ja": text,
                    "display_text": text,
                    "line_id": f"idle-{time.time_ns()}",
                },
            )
            receipt = await deliver_narration(request, self._sink)
            logger.info(
                "idle chatter delivered status=%s reason=%s text=%r",
                receipt.status,
                receipt.diagnostics.get("reason", ""),
                text[:48],
            )
            # Keep the spoken line in the Session dialog: the wallpaper shows it
            # for a moment only, so a missed opener must still be readable later.
            if self._record is not None and receipt.status not in {"error", "skipped"}:
                try:
                    result = self._record(text)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    logger.exception("idle chatter could not be recorded in the session")
            return receipt.accepted
        finally:
            self._delivering = False
            await self.note_activity()

    async def run(self) -> None:
        while True:
            try:
                await asyncio.sleep(TICK_SECONDS)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("idle chatter loop error")
                await asyncio.sleep(TICK_SECONDS)

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        bus.on(Method.CHAT_USER, self.note_activity)
        bus.on(Method.CHAT_COMPLETE, self.note_activity)
        self._last_activity = time.monotonic()
        self._next_delay = self._pick_delay()
        self._task = asyncio.create_task(self.run(), name="idle-chatter")
        logger.info(
            "idle chatter armed: weekdays %02d:00-%02d:00, quiet %d-%d min",
            ACTIVE_START_HOUR,
            ACTIVE_END_HOUR,
            IDLE_MIN_SECONDS // 60,
            IDLE_MAX_SECONDS // 60,
        )
