# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in LICENSE.md.

"""Record cryptography's statically linked OpenSSL from the installed runtime."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import re

from cryptography.hazmat.bindings import _rust
from cryptography.hazmat.backends.openssl.backend import backend


def inventory(created: str) -> dict:
    # Read the library actually loaded by cryptography, not Python ssl's
    # separately linked system OpenSSL or a version inferred from release notes.
    openssl_text = backend.openssl_version_text()
    match = re.fullmatch(r"OpenSSL (\d+\.\d+\.\d+) .+", openssl_text)
    if match is None:
        raise ValueError(f"unsupported bundled OpenSSL version: {openssl_text}")
    openssl_version = match.group(1)
    cryptography_version = version("cryptography")
    carrier = Path(_rust.__file__).resolve()
    carrier_sha256 = hashlib.sha256(carrier.read_bytes()).hexdigest()
    timestamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("inventory creation time must include a timezone")
    created_utc = timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    document_id = hashlib.sha256(
        f"{cryptography_version}:{openssl_version}:{carrier_sha256}".encode()
    ).hexdigest()
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "Atellagent client bundled native components",
        "documentNamespace": f"https://atellagent.com/spdx/client-native/{document_id}",
        "creationInfo": {"created": created_utc, "creators": ["Organization: Atellagent"]},
        "packages": [
            {
                "SPDXID": "SPDXRef-cryptography",
                "name": "cryptography",
                "versionInfo": cryptography_version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseDeclared": "Apache-2.0 OR BSD-3-Clause",
                "licenseConcluded": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "externalRefs": [{"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl", "referenceLocator": f"pkg:pypi/cryptography@{cryptography_version}"}],
            },
            {
                "SPDXID": "SPDXRef-bundled-OpenSSL",
                "name": "openssl",
                "versionInfo": openssl_version,
                "supplier": "Organization: OpenSSL Project",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseDeclared": "Apache-2.0",
                "licenseConcluded": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "sourceInfo": f"Statically bundled in {carrier}; runtime reports {openssl_text}",
                "externalRefs": [
                    {"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl", "referenceLocator": f"pkg:generic/openssl@{openssl_version}"},
                    {"referenceCategory": "SECURITY", "referenceType": "cpe23Type", "referenceLocator": f"cpe:2.3:a:openssl:openssl:{openssl_version}:*:*:*:*:*:*:*"},
                ],
            },
        ],
        "files": [{
            "SPDXID": "SPDXRef-native-carrier",
            "fileName": str(carrier),
            "checksums": [{"algorithm": "SHA256", "checksumValue": carrier_sha256}],
            "licenseConcluded": "NOASSERTION",
            "licenseInfoInFiles": ["NOASSERTION"],
            "copyrightText": "NOASSERTION",
        }],
        "relationships": [
            {"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": "SPDXRef-cryptography"},
            {"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": "SPDXRef-bundled-OpenSSL"},
            {"spdxElementId": "SPDXRef-cryptography", "relationshipType": "STATIC_LINK", "relatedSpdxElement": "SPDXRef-bundled-OpenSSL"},
            {"spdxElementId": "SPDXRef-cryptography", "relationshipType": "CONTAINS", "relatedSpdxElement": "SPDXRef-native-carrier"},
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--created", required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(inventory(args.created), indent=2) + "\n", encoding="utf-8")
