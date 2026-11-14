# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# Licensed under LICENSE.md in this source tree.
"""Ephemeral native HTTP authority, never serialized into governed requests."""

from contextlib import contextmanager
from contextvars import ContextVar

_authorization=ContextVar("native_mcp_upstream_authorization",default=None)


def current_native_authorization():
    return _authorization.get()


@contextmanager
def bind_native_authorization(value):
    if value is not None and (not isinstance(value,str) or not value or len(value)>8192
                              or not value.isascii() or "\r" in value or "\n" in value):
        raise ValueError("A bounded native HTTP authorization value is required")
    token=_authorization.set(value)
    try: yield
    finally: _authorization.reset(token)
