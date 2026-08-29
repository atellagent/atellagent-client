# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Atomic local storage for enrolled client certificates and keys."""

from __future__ import annotations

from contextlib import suppress
import os
import tempfile
from pathlib import Path
from typing import Optional

from .enrollment_types import CertificateEnrollmentError


def credential_paths(
    config_path: os.PathLike[str] | str,
    certificate_path: Optional[os.PathLike[str] | str],
    private_key_path: Optional[os.PathLike[str] | str],
) -> tuple[Path, Path]:
    config_dir = Path(config_path).expanduser().resolve().parent
    certificate = Path(
        certificate_path
        or os.getenv("ATELLAGENT_CERT_PATH")
        or config_dir / "certs" / "client-cert.pem"
    ).expanduser()
    private_key = Path(
        private_key_path
        or os.getenv("ATELLAGENT_KEY_PATH")
        or config_dir / "certs" / "client-key.pem"
    ).expanduser()
    if certificate.resolve() == private_key.resolve():
        raise CertificateEnrollmentError("Credential output paths must be distinct")
    return certificate, private_key


def stage_file(path: Path, content: bytes, mode: int) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(staged_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return staged
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        staged.unlink(missing_ok=True)
        raise


def commit_credential_set(
    *,
    certificate_path: Path,
    certificate_pem: bytes,
    private_key_path: Path,
    private_key_pem: bytes,
    replace: bool,
) -> None:
    for path in (certificate_path, private_key_path):
        if path.exists() and not replace:
            raise CertificateEnrollmentError(
                f"Credential path already exists: {path}; use explicit rotation replacement"
            )
    staged_certificate = stage_file(certificate_path, certificate_pem, 0o644)
    staged_private_key = stage_file(private_key_path, private_key_pem, 0o600)
    try:
        if replace:
            os.replace(staged_certificate, certificate_path)
            os.replace(staged_private_key, private_key_path)
        else:
            os.link(staged_certificate, certificate_path)
            try:
                os.link(staged_private_key, private_key_path)
            except Exception:
                certificate_path.unlink(missing_ok=True)
                private_key_path.unlink(missing_ok=True)
                raise
            staged_certificate.unlink()
            staged_private_key.unlink()
        os.chmod(certificate_path, 0o644)
        os.chmod(private_key_path, 0o600)
    except CertificateEnrollmentError:
        raise
    except Exception as exc:
        raise CertificateEnrollmentError("Unable to store enrolled credentials") from exc
    finally:
        staged_certificate.unlink(missing_ok=True)
        staged_private_key.unlink(missing_ok=True)


__all__ = ["commit_credential_set", "credential_paths", "stage_file"]
