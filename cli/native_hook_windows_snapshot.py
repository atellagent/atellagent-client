"""Bounded Windows settings capture with pinned, non-reparse ancestry handles."""

from pathlib import Path
import stat

from .native_hook_snapshot import LIMIT, _unsafe
from .native_hook_windows_security import security_revision


def read(root, path):
    import pywintypes
    import ntsecuritycon
    import win32con as constants
    import win32file as files

    if root.anchor.startswith("\\\\") or root == Path(root.anchor):
        _unsafe()
    for part in path.parts[1:]:
        if (part.endswith((".", " ")) or ":" in part
            or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL",
                *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
            _unsafe()
    handles, identities = [], []
    try:
        for index in range(1, len(path.parts) + 1):
            candidate = Path(*path.parts[:index])
            last = index == len(path.parts)
            scoped = index >= len(root.parts)
            access = constants.GENERIC_READ if last else ntsecuritycon.FILE_READ_ATTRIBUTES
            if scoped:
                access |= constants.READ_CONTROL
            # Exclude write/delete sharing for ancestry too: a directory must
            # not become a junction between its inspection and the child open.
            share = constants.FILE_SHARE_READ
            try:
                handle = files.CreateFile(str(candidate), access, share, None,
                    constants.OPEN_EXISTING, files.FILE_FLAG_OPEN_REPARSE_POINT
                    | constants.FILE_FLAG_BACKUP_SEMANTICS, None)
            except pywintypes.error as error:
                if error.winerror in (2, 3) and index > len(root.parts):
                    return False, b"", identities + [("missing", *path.parts[index - 1:])]
                _unsafe()
            handles.append(handle)
            info = files.GetFileInformationByHandle(handle)
            attributes = info[0]
            directory = bool(attributes & constants.FILE_ATTRIBUTE_DIRECTORY)
            if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT or directory == last:
                _unsafe()
            if files.GetFileType(handle) != constants.FILE_TYPE_DISK:
                _unsafe()
            size = (info[5] << 32) | info[6]
            if last and (size > LIMIT or info[7] != 1):
                _unsafe()
            identity = [attributes, info[4], info[8], info[9]]
            if scoped:
                identity.append(security_revision(handle, profile_root=index == len(root.parts)))
            if last:
                identity.extend([size, str(info[1]), str(info[3])])
            identities.append(identity)
        content = bytearray()
        while len(content) <= LIMIT:
            try:
                _, chunk = files.ReadFile(handles[-1], min(65536, LIMIT + 1 - len(content)))
            except pywintypes.error as error:
                if error.winerror == 38:
                    break
                _unsafe()
            if not chunk:
                break
            content.extend(chunk)
        after = files.GetFileInformationByHandle(handles[-1])
        if len(content) > LIMIT or after[:2] + after[3:] != info[:2] + info[3:]:
            _unsafe()
        return True, bytes(content), identities
    except (pywintypes.error, ValueError, OverflowError):
        _unsafe()
    finally:
        for handle in reversed(handles):
            handle.Close()
