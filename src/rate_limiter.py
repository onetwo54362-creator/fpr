"""Rate limiter with random cooldown for anti-detection."""

from __future__ import annotations
import asyncio
import logging
import random

log = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, min_batch: int = 2, max_batch: int = 5, min_delay: float = 3.0, max_delay: float = 10.0):
        self.min_batch = max(1, min_batch)
        self.max_batch = max(self.min_batch, max_batch)
        self.min_delay = max(0.5, min_delay)
        self.max_delay = max(self.min_delay, max_delay)
        self._count = 0
        self._target = self._new_target()
        self._total_cooldowns = 0
        self._total_time = 0.0
        log.info(f"⏱️  Rate limiter: cooldown every {self.min_batch}-{self.max_batch} reqs, {self.min_delay}-{self.max_delay}s")

    def _new_target(self) -> int:
        return random.randint(self.min_batch, self.max_batch)

    async def on_request_complete(self):
        self._count += 1
        if self._count >= self._target:
            await self._cooldown()

    async def _cooldown(self):
        base = random.uniform(self.min_delay, self.max_delay)
        jitter = base * random.uniform(-0.25, 0.25)
        delay = max(0.5, base + jitter)
        self._total_cooldowns += 1
        self._total_time += delay
        log.info(f"🛑 Cooldown #{self._total_cooldowns}: {delay:.1f}s after {self._count} reqs")
        await asyncio.sleep(delay)
        self._count = 0
        self._target = self._new_target()

    async def page_delay(self):
        await asyncio.sleep(random.uniform(1.5, 3.5))

    async def section_delay(self):
        await asyncio.sleep(random.uniform(0.8, 2.0))

    def get_stats(self) -> dict:
        return {"total_cooldowns": self._total_cooldowns, "total_cooldown_time": round(self._total_time, 1)}
