"""Safe local certificate-material validation and storage errors."""


class CertificateMaterialError(RuntimeError):
    """Certificate material could not be validated or committed."""
