"""Domain-specific errors used by provider orchestration."""


class ProviderInputError(ValueError):
    """Raised when a provider cannot process this input but fallback may."""
