# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Read-only current-user credential ACL checks, without service/task imports."""


def require_current_user_acl(path):
    try:
        _require_current_user_acl(path)
    except Exception as exc:
        raise ValueError("current-user-only credential ACL unavailable") from exc


def _require_current_user_acl(path):
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        owner = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    descriptor = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
    acl = descriptor.GetSecurityDescriptorDacl()
    if acl is None or acl.GetAceCount() != 1:
        raise ValueError("current-user-only credential ACL required")
    ace = acl.GetAce(0)
    if (ace[0][0] != win32security.ACCESS_ALLOWED_ACE_TYPE
        or win32security.ConvertSidToStringSid(ace[2]) != win32security.ConvertSidToStringSid(owner)):
        raise ValueError("current-user-only credential ACL required")
