"""Native ReplaceFileW with the documented destination/source argument order."""


def replace_existing(destination, replacement):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    replace = kernel.ReplaceFileW
    replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID]
    replace.restype = wintypes.BOOL
    if not replace(str(destination), str(replacement), None, 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())
