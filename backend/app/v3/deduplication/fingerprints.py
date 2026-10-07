"""Typed fingerprint generation for V3 deduplication."""

from __future__ import annotations

import hashlib
import json
import uuid

from app.v3.content import ProcessedContent

from .contracts import DeduplicationPolicy, FingerprintSignal, FingerprintType


class FingerprintBuilderV3:
    """Build only meaningful fingerprint signals; never hash empty components."""

    @staticmethod
    def build(
        *,
        source_id: uuid.UUID,
        event_key: str,
        content: ProcessedContent,
        policy: DeduplicationPolicy,
    ) -> tuple[FingerprintSignal, ...]:
        if not policy.enabled:
            return ()

        signals: list[FingerprintSignal] = []
        content_signals: list[FingerprintSignal] = []

        if policy.compare_telegram_identity:
            identity = f"telegram:{source_id}:{event_key}"
            signals.append(
                FingerprintSignal(
                    FingerprintType.TELEGRAM_IDENTITY,
                    _sha256(identity),
                )
            )

        normalized_text = content.normalized_text.strip()
        if policy.compare_text and normalized_text:
            text_signal = FingerprintSignal(
                FingerprintType.TEXT,
                _sha256(normalized_text),
            )
            signals.append(text_signal)
            content_signals.append(text_signal)

        if policy.compare_media and content.media:
            media_payload = json.dumps(
                [item.identity for item in content.media],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            media_signal = FingerprintSignal(
                FingerprintType.MEDIA,
                _sha256(media_payload),
            )
            signals.append(media_signal)
            content_signals.append(media_signal)

        if content_signals:
            combined_payload = json.dumps(
                {signal.kind.value: signal.value for signal in content_signals},
                sort_keys=True,
                separators=(",", ":"),
            )
            signals.append(
                FingerprintSignal(
                    FingerprintType.COMBINED,
                    _sha256(combined_payload),
                )
            )

        return tuple(signals)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
