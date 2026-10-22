#!/usr/bin/env python3
# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# Licensed under the terms in the repository's LICENSE.md.
"""Offline image gate for the October 2026 eMudhra CA incident.

Fingerprint sources: https://emudhra.we-have-a-problem.com/roots.json and
https://access.redhat.com/security/vulnerabilities/RHSB-2026-012.
All chain inputs are untrusted public certificates; no private keys are used.
"""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import ssl
import subprocess

AFFECTED_ROOTS = {
    '86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b',
    'd7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4',
    '7dd78d5f4f13459a83dff9abbba62edbaf6f2d102bf257fd712f4d9f2746ed8d',
    'f2edaaff3d9b701fb4d14c9f285f8c0bad90fc3bcc1fa07d47f4718edaba34a5',
}
CERTIFICATE = re.compile(br'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', re.S)


def fingerprints(data: bytes) -> set[str]:
    matches = CERTIFICATE.findall(data)
    if not matches or len(matches) != data.count(b'-----BEGIN CERTIFICATE-----'):
        raise ValueError('certificate store is empty or malformed')
    return {hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem.decode('ascii'))).hexdigest() for pem in matches}


def verify(chains_directory: Path) -> dict:
    context = ssl.create_default_context()
    roots = {hashlib.sha256(c).hexdigest() for c in context.get_ca_certs(binary_form=True)}
    defaults = ssl.get_default_verify_paths()
    bundle = Path('/etc/ssl/certs/ca-certificates.crt')
    stores = {bundle.resolve(): fingerprints(bundle.read_bytes())}
    if defaults.cafile:
        p = Path(defaults.cafile).resolve()
        stores[p] = fingerprints(p.read_bytes())
    # OpenSSL may load hash-directory certificates lazily. Inspect the actual
    # PEM files as well as the context's eagerly enumerated roots.
    if defaults.capath:
        for path in Path(defaults.capath).iterdir():
            if path.is_file() and b'-----BEGIN CERTIFICATE-----' in path.read_bytes():
                stores[path.resolve()] = fingerprints(path.read_bytes())
    try:
        distribution = metadata.distribution('certifi')
    except metadata.PackageNotFoundError:
        distribution = None
    if distribution is not None:
        p = Path(distribution.locate_file('certifi/cacert.pem')).resolve()
        stores[p] = fingerprints(p.read_bytes())
    if not roots:
        raise ValueError('expected nonempty default image trust')
    affected = roots & AFFECTED_ROOTS
    for values in stores.values():
        affected |= values & AFFECTED_ROOTS
    if affected:
        raise ValueError('affected root remains: ' + ','.join(sorted(affected)))
    expected = {
        f'{kind}-{generation}{suffix}-emudhra.we-have-a-problem.com.pem'
        for kind in ('dv', 'ov', 'ev') for generation in ('g3b', 'g3c') for suffix in ('', '-aaa')
    }
    files = {p.name: p for p in chains_directory.glob('*.pem')}
    if files.keys() != expected:
        raise ValueError('expected all twelve incident chains')
    results = []
    for name, path in sorted(files.items()):
        fingerprints(path.read_bytes())
        result = subprocess.run(
            ['openssl', 'verify', '-purpose', 'sslserver', '-verify_hostname', path.stem,
             '-CAfile', str(bundle), '-untrusted', str(path), str(path)],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout + result.stderr
        if result.returncode == 0 or 'unable to get local issuer certificate' not in output:
            raise ValueError('chain was accepted or failed for an unexpected reason: ' + name + ': ' + output)
        results.append({'chain': name, 'issuer_rejected': True})
    return {'passed': True, 'default_root_count': len(roots), 'checked_store_files': len(stores),
            'affected_roots_present': [], 'chains': results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chains-directory', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.chains_directory), sort_keys=True))


if __name__ == '__main__':
    main()
