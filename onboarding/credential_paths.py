# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Reject unsafe device credential paths; never repair or weaken permissions."""

import os
import stat


def require_device_credential_paths(certificate, private_key):
    if certificate == private_key:
        raise ValueError("distinct device certificate and key required")
    for path in (certificate, private_key):
        for target, directory in ((path.parent, True), (path, False)):
            info = target.lstat()
            if (target.is_symlink() or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
                raise ValueError("regular device credentials in a private directory required")
            if os.name == "nt":
                from .windows_credentials import require_current_user_acl
                require_current_user_acl(target)
            elif info.st_uid != os.getuid() or ((directory or path == private_key) and stat.S_IMODE(info.st_mode) & 0o077):
                raise ValueError("owner-private device credential paths required")
