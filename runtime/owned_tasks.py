# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Join independent lifecycle work before returning or surfacing a failure."""

import asyncio


async def join_owned(*operations, cancel_on_interrupt=True):
    """Join every owner, including after repeated caller cancellation.

    Reconciliation work is cancelled once on interruption. Explicit cleanup
    uses cancel_on_interrupt=False so it runs to completion before cancellation
    propagates, including when interruption arrives before a close task starts.
    """
    owners = [asyncio.ensure_future(operation) for operation in operations]
    joined = asyncio.gather(*owners, return_exceptions=True)
    cancelled = None
    while True:
        try:
            results = await asyncio.shield(joined)
            break
        except asyncio.CancelledError as exc:
            if cancelled is None and cancel_on_interrupt:
                for owner in set(owners):
                    if not owner.done() and not getattr(owner, "cancelling", lambda: 0)():
                        owner.cancel()
            cancelled = exc
    # A known failure must survive a simultaneous shutdown/cancellation.
    for result in results:
        if isinstance(result, Exception):
            raise result
    if cancelled is not None:
        raise cancelled
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results
