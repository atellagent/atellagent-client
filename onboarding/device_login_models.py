# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Interactive login material is separate from durable machine authority."""

from dataclasses import dataclass, field
import re
from urllib.parse import urlencode

from .errors import OnboardingClientError


@dataclass(frozen=True)
class DeviceLoginChallenge:
    browser_url: str
    user_code: str
    interval: int
    deadline: float
    device_code: str = field(repr=False)
    code_verifier: str = field(repr=False)


def login_challenge(payload: dict, *, browser_url: str, verifier: str,
    started_at: float) -> DeviceLoginChallenge:
    code, user_code = payload.get("device_code"), payload.get("user_code")
    interval, expires = payload.get("interval", 5), payload.get("expires_in")
    if (not isinstance(code, str) or not 1 <= len(code) <= 4096 or any(ord(c) < 33 for c in code)
        or not isinstance(user_code, str) or not re.fullmatch(r"[A-Za-z0-9-]{4,32}", user_code)
        or type(interval) is not int or not 1 <= interval <= 60
        or type(expires) is not int or not 1 <= expires <= 900):
        raise OnboardingClientError("onboarding_oauth_response_invalid")
    # Never open a verification URI supplied by an OAuth response.
    return DeviceLoginChallenge(browser_url + "?" + urlencode({"user_code": user_code}),
        user_code, interval, started_at + expires, code, verifier)


@dataclass(frozen=True)
class ManagementTokens:
    expires_in: int
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)

    @classmethod
    def from_payload(cls, payload: dict) -> "ManagementTokens":
        access, refresh, expires = payload.get("access_token"), payload.get("refresh_token"), payload.get("expires_in")
        if (payload.get("token_type") not in {"Bearer", "bearer"}
            or not isinstance(access, str) or not 1 <= len(access) <= 32768
            or any(ord(c) < 33 for c in access) or type(expires) is not int or not 1 <= expires <= 86400
            or (refresh is not None and (not isinstance(refresh, str) or not 1 <= len(refresh) <= 32768
                or any(ord(c) < 33 for c in refresh)))):
            raise OnboardingClientError("onboarding_oauth_token_invalid")
        return cls(expires, access, refresh)
