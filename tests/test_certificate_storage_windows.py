# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Native Windows coverage for certificate credential file permissions."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from atellagent_client.sdk.certificate_storage import stage_file


@unittest.skipUnless(os.name == "nt", "Windows ACL test")
class WindowsCertificateStorageTests(unittest.TestCase):
    def test_staged_private_key_has_current_user_only_acl(self) -> None:
        import win32api
        import win32con
        import win32security

        with tempfile.TemporaryDirectory(prefix="atellagent-certificate-acl-") as root:
            staged = stage_file(Path(root) / "client-key.pem", b"test-only-key", 0o600)
            self.assertEqual(staged.read_bytes(), b"test-only-key")
            descriptor = win32security.GetFileSecurity(
                str(staged), win32security.DACL_SECURITY_INFORMATION
            )
            dacl = descriptor.GetSecurityDescriptorDacl()
            self.assertIsNotNone(dacl)
            self.assertEqual(dacl.GetAceCount(), 1)
            token = win32security.OpenProcessToken(
                win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
            )
            try:
                user_sid = win32security.GetTokenInformation(
                    token, win32security.TokenUser
                )[0]
            finally:
                token.Close()
            self.assertEqual(
                win32security.ConvertSidToStringSid(dacl.GetAce(0)[2]),
                win32security.ConvertSidToStringSid(user_sid),
            )


if __name__ == "__main__":
    unittest.main()
