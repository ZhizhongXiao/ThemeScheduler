"""Shared exception hierarchy for all ThemeScheduler domains."""


class ThemeSchedulerError(Exception):
    """Root for errors intentionally exposed by ThemeScheduler."""


class ContractError(ThemeSchedulerError, ValueError):
    """An input or operation violates a public product contract."""


class DataError(ThemeSchedulerError, ValueError):
    """Persisted or imported data is damaged, inconsistent, or untrusted."""


class ThemeSchedulerRuntimeError(ThemeSchedulerError, RuntimeError):
    """A required runtime, operating-system, or side-effect operation failed."""
