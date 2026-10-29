# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Helpers for resolving bundled example assets from installed packages."""

from __future__ import annotations

import atexit
from contextlib import ExitStack
from importlib import resources


_RESOURCE_STACK = ExitStack()
atexit.register(_RESOURCE_STACK.close)


def bundled_example_path(relative_path: str) -> str:
    target = resources.files("atellagent_client.examples").joinpath(relative_path)
    resolved = _RESOURCE_STACK.enter_context(resources.as_file(target))
    return str(resolved)


__all__ = ["bundled_example_path"]

