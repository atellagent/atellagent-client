"""Pin existing non-reparse Windows ancestry while staging settings."""

from contextlib import contextmanager
from pathlib import Path
import stat

from .native_hook_snapshot import _unsafe
from .native_hook_windows_security import security_revision


@contextmanager
def pinned_directory(root, path):
    import win32file as files
    import win32con as constants
    import ntsecuritycon
    handles = []
    try:
        for index in range(1, len(path.parts) + 1):
            access = ntsecuritycon.FILE_READ_ATTRIBUTES
            if index >= len(root.parts):
                access |= constants.READ_CONTROL
            handle = files.CreateFile(str(Path(*path.parts[:index])), access,
                constants.FILE_SHARE_READ, None, constants.OPEN_EXISTING,
                files.FILE_FLAG_OPEN_REPARSE_POINT | constants.FILE_FLAG_BACKUP_SEMANTICS, None)
            handles.append(handle)
            info = files.GetFileInformationByHandle(handle)
            if (info[0] & stat.FILE_ATTRIBUTE_REPARSE_POINT
                or not info[0] & constants.FILE_ATTRIBUTE_DIRECTORY):
                _unsafe()
            if index >= len(root.parts):
                security_revision(handle, profile_root=index == len(root.parts))
        yield
    finally:
        for handle in reversed(handles):
            handle.Close()


def protect_created(path):
    """Set permissions/owner only on a path this operation exclusively created."""
    import win32api
    import win32con
    import win32security as security
    from atellagent_client.onboarding.windows_private_path import protect_new_path
    protect_new_path(path)
    token = security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        owner = security.GetTokenInformation(token, security.TokenUser)[0]
    finally:
        token.Close()
    security.SetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
        security.OWNER_SECURITY_INFORMATION, owner, None, None, None)
