# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""A no-replacement pointer commits a complete immutable credential generation."""

from pathlib import Path
import shutil
import json
from uuid import uuid4

from .atomic_private_file import create_private_file, sync_directory, read_private_file
from .private_paths import create_private_directory
from .proofs import canonical_json


def commit_credential_generation(root: Path, pointer: Path, *, kind: str, metadata: dict,
    certificate: bytes, private_key: bytes) -> None:
    if kind not in {"device", "integration"}:
        raise ValueError("closed credential generation purpose required")
    generation = root / f"{kind}-{uuid4().hex}"
    committed, created = False, False
    try:
        create_private_directory(generation, exclusive=True)
        created = True
        create_private_file(generation / "certificate.pem", certificate)
        create_private_file(generation / "private-key.pem", private_key)
        sync_directory(generation)
        create_private_file(pointer, canonical_json({**metadata, "generation": generation.name}))
        committed = True
    finally:
        # Only this call's uncommitted, newly created directory is removable.
        # A committed pointer is durable even if a later caller needs to retry.
        if not committed and created:
            try:
                record = json.loads(read_private_file(pointer, limit=4096))
                committed = isinstance(record, dict) and record.get("generation") == generation.name
            except FileNotFoundError:
                committed = False
            except (OSError, ValueError):
                committed = True  # Do not remove a possibly committed generation.
        if not committed and created:
            shutil.rmtree(generation, ignore_errors=True)
