"""Typed bounded setup progress; stopping a wait never cancels durable intent."""

import asyncio
from dataclasses import dataclass, field
from time import monotonic
from typing import AsyncIterator, Protocol

from .device_setup_flow import DeviceSetupStep
from .native_setup_flow import NativeSetupStep
from .workload_setup_flow import WorkloadSetupStep

from .errors import OnboardingClientError

SetupStep = DeviceSetupStep | NativeSetupStep | WorkloadSetupStep


class SetupFlow(Protocol):
    async def advance(self) -> SetupStep: ...


@dataclass(frozen=True)
class OnboardingProgress:
    stage: str
    step: SetupStep | None = field(default=None, repr=False)
    error_code: str | None = None


async def setup_progress(flow: SetupFlow, *, seconds: int = 60,
                         stop_at=frozenset({"device_ready", "awaiting_owner_consent",
                                            "awaiting_registration", "registered"})) -> AsyncIterator[OnboardingProgress]:
    """Advance retained setup without login; consumers own rendering and interaction."""
    if type(seconds) is not int or not 1 <= seconds <= 900:
        raise ValueError("setup wait must be between 1 and 900 seconds")
    deadline = monotonic() + seconds
    try:
        async with asyncio.timeout(seconds):
            while True:
                try:
                    step = await flow.advance()
                except OnboardingClientError as exc:
                    if not exc.retryable:
                        raise
                    yield OnboardingProgress("temporarily_unavailable", error_code=exc.code)
                else:
                    yield OnboardingProgress(step.stage, step=step)
                    if step.stage in stop_at:
                        return
                await asyncio.sleep(min(5, max(0, deadline - monotonic())))
    except TimeoutError:
        yield OnboardingProgress("retained")
