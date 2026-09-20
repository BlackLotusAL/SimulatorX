"""Shared control-message limits, numeric rules and environment errors."""
import math

MAX_MESSAGE = 8 * 1024 * 1024


def diagnostic_value(value):
    """Keep invalid native numeric inputs observable without producing invalid JSON."""
    if isinstance(value, dict):
        return {key: diagnostic_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [diagnostic_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def reject_constant(text):
    raise ValueError("Non-finite JSON number: " + text)


def parse_finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        reject_constant(text)
    return value


class EnvironmentError(RuntimeError):
    """An unusable test environment, rather than an injected device error."""
