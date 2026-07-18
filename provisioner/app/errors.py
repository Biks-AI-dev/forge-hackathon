class ProvisionError(Exception):
    """Structured provisioning failure. Never carries secret values —
    only sandbox ids, slugs, step names, and non-secret messages."""

    status_code = 502
    error_code = "provision_failed"

    def __init__(self, message: str, sandbox_id: str | None = None):
        self.message = message
        self.sandbox_id = sandbox_id
        super().__init__(message)


class SandboxCreateError(ProvisionError):
    error_code = "sandbox_create_failed"


class InjectionError(ProvisionError):
    error_code = "injection_failed"


class StartError(ProvisionError):
    error_code = "start_failed"


class HealthCheckTimeoutError(ProvisionError):
    status_code = 504
    error_code = "health_check_timeout"


class PreviewUrlError(ProvisionError):
    error_code = "preview_url_failed"


class ProvisionTimeoutError(ProvisionError):
    status_code = 504
    error_code = "provision_timeout"
