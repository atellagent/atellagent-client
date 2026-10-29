"""OS registration files must be bounded, owner-private and not linked."""

import os

import pytest

from atellagent_client.cli.service_errors import ServiceCommandError
from atellagent_client.cli.service_files import _read_owned_file, _write_private_file


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership fixture; Windows requires actual ACL acceptance")
def test_private_commit_and_missing_record(tmp_path):
    target = tmp_path / "services" / "registration.plist"
    assert _read_owned_file(target) is None
    _write_private_file(target, b"registration")
    assert _read_owned_file(target) == b"registration"
    assert target.stat().st_mode & 0o077 == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership fixture; Windows requires actual ACL acceptance")
@pytest.mark.parametrize("kind", ["link", "public", "oversized"])
def test_unsafe_existing_registration_is_not_repaired(tmp_path, kind):
    target = tmp_path / "registration.plist"
    if kind == "link":
        target.symlink_to(tmp_path / "missing")
    else:
        target.write_bytes(b"x" * (65537 if kind == "oversized" else 1))
        target.chmod(0o644 if kind == "public" else 0o600)
    before = target.lstat()
    with pytest.raises(ServiceCommandError):
        _read_owned_file(target)
    after = target.lstat()
    assert (before.st_ino, before.st_mode, before.st_size) == (after.st_ino, after.st_mode, after.st_size)
