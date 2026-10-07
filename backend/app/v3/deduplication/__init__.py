"""V3 typed, scoped, time-aware deduplication."""

from .contracts import (
    DeduplicationDecision,
    DeduplicationPolicy,
    DeduplicationResult,
    DeduplicationScope,
    FingerprintSignal,
    FingerprintType,
)
from .fingerprints import FingerprintBuilderV3
from .service import DeduplicationCoordinator, DeduplicationError, DeduplicationPolicyResolver

__all__ = [
    "DeduplicationCoordinator",
    "DeduplicationDecision",
    "DeduplicationError",
    "DeduplicationPolicy",
    "DeduplicationPolicyResolver",
    "DeduplicationResult",
    "DeduplicationScope",
    "FingerprintBuilderV3",
    "FingerprintSignal",
    "FingerprintType",
]
