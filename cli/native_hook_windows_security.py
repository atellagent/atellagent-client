"""Handle-bound ownership/DACL checks for existing Windows user settings."""


def security_revision(handle, *, profile_root=False):
    import win32api
    import win32con
    import win32security as security
    from .native_hook_snapshot import _unsafe

    token = security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        current = security.GetTokenInformation(token, security.TokenUser)[0]
    finally:
        token.Close()
    sid = security.ConvertSidToStringSid
    descriptor = security.GetSecurityInfo(handle, security.SE_FILE_OBJECT,
        security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION)
    owners = {sid(current)}
    # Windows may create the user profile under SYSTEM ownership. This does
    # not relax ownership checks for host directories or settings files.
    if profile_root:
        owners.add("S-1-5-18")
    if sid(descriptor.GetSecurityDescriptorOwner()) not in owners:
        _unsafe()
    acl = descriptor.GetSecurityDescriptorDacl()
    if acl is None:
        _unsafe()
    trusted = {sid(current), "S-1-5-18", "S-1-5-32-544"}  # user, SYSTEM, administrators
    # Data/append/EA/attribute/delete-child/delete/DACL/owner/generic write or all.
    write = 0x500D0156
    for index in range(acl.GetAceCount()):
        ace = acl.GetAce(index)
        kind, flags = ace[0]
        if flags & security.INHERIT_ONLY_ACE:
            continue
        if kind == security.ACCESS_DENIED_ACE_TYPE:
            continue
        if kind != security.ACCESS_ALLOWED_ACE_TYPE:
            _unsafe()  # No permissive interpretation of object/callback ACEs.
        if ace[1] & write and sid(ace[2]) not in trusted:
            _unsafe()
    return security.ConvertSecurityDescriptorToStringSecurityDescriptor(descriptor,
        security.SDDL_REVISION_1, security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION)
