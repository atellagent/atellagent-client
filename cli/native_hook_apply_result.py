"""Settings application failures identify committed paths, never file contents."""

from atellagent_client.onboarding.errors import OnboardingClientError


class ApplyError(OnboardingClientError):
    def __init__(self, code, written):
        super().__init__(code)
        self.written = tuple(str(path) for path in written)
