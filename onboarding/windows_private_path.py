# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Protect newly created setup paths before writing private material."""


def protect_new_path(path):
    try:
        _protect_new_path(path)
    except Exception as exc:
        raise ValueError("current-user-only setup path protection unavailable") from exc


def _protect_new_path(path):
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        owner = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    acl = win32security.ACL()
    acl.AddAccessAllowedAceEx(win32security.ACL_REVISION,
        win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE,
        win32con.GENERIC_ALL, owner)
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(1, acl, 0)
    win32security.SetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION, descriptor)
    from .windows_credentials import require_current_user_acl
    require_current_user_acl(path)
