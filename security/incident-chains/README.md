# Negative TLS trust fixtures

Public certificate chains from https://emudhra.we-have-a-problem.com/chains.zip,
captured by the incident reporters on 2026-10-20. These contain public
certificates only. They are untrusted verification inputs, never CA bundles.
Six direct and six AAA cross-signed chains must all fail issuer verification.
The container build checks them offline, then removes the fixtures and checker.
