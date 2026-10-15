"""Service-account certificate subject identity, independent of onboarding."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ServiceAccountCertificateIdentity:
    service_account_id: str
    tenant_id: str

    @property
    def subject_dns(self):
        return f"sa.{UUID(self.service_account_id).hex}.{UUID(self.tenant_id).hex}.identity.invalid"
