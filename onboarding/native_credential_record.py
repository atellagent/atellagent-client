# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Stored integration candidates are not activation or hook readiness."""

from dataclasses import dataclass, field
from pathlib import Path

from .service_account_certificate_verify import VerifiedServiceAccountCredential


@dataclass(frozen=True)
class StoredNativeCredential:
    credential: VerifiedServiceAccountCredential
    certificate_path: Path = field(repr=False)
    private_key_path: Path = field(repr=False)
