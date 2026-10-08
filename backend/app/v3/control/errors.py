"""Control-plane errors safe to surface to the owner."""

class ControlServiceError(RuntimeError):
    """Expected V3 control operation failure with a safe operator message."""
