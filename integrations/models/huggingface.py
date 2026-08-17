# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Hugging Face text-classification implementation of the filter runtime."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Dict

from .contracts import FilterRuntimeEvaluationRequest, FilterRuntimeHandlerBase


def _text(value: Any) -> str:
    return str(value or "").strip()


def _classification_rows(value: Any) -> list[Dict[str, Any]]:
    if isinstance(value, dict):
        return [dict(value)]
    if not isinstance(value, list):
        return []
    rows: list[Dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            rows.append(dict(item))
        elif isinstance(item, list):
            rows.extend(_classification_rows(item))
    return rows


@dataclass
class HuggingFaceTextClassificationFilter(FilterRuntimeHandlerBase):
    """Evaluate a customer-selected text-classification model on input text.

    ``blocked_labels`` is intentionally explicit: labels are model-specific, so
    a runtime owner—not the client package—chooses the model and labels used
    for its own filter. The client supplies no built-in content classification.
    """

    model_id: str = ""
    blocked_labels: tuple[str, ...] = ()
    threshold: float = 0.5
    classifier: Any = None
    pipeline_kwargs: Dict[str, Any] = field(default_factory=dict)
    _resolved_classifier: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not _text(self.model_id):
            raise ValueError("Hugging Face filter requires an explicit model_id")
        if not 0.0 <= float(self.threshold) <= 1.0:
            raise ValueError("Hugging Face filter threshold must be between 0 and 1")
        normalized_labels = tuple(
            label.lower() for label in (_text(label) for label in self.blocked_labels) if label
        )
        if not normalized_labels:
            raise ValueError("Hugging Face filter requires at least one blocked label")
        self.blocked_labels = normalized_labels

    def _classifier(self) -> Any:
        if self.classifier is not None:
            return self.classifier
        if self._resolved_classifier is None:
            try:
                from transformers import pipeline
            except ImportError as exc:
                raise RuntimeError(
                    "Hugging Face filter support requires: "
                    "pip install 'atellagent-client[huggingface-filter]'"
                ) from exc
            self._resolved_classifier = pipeline(
                "text-classification",
                model=self.model_id,
                top_k=None,
                **dict(self.pipeline_kwargs),
            )
        return self._resolved_classifier

    async def evaluate_filter(
        self,
        request: FilterRuntimeEvaluationRequest,
    ) -> Dict[str, Any]:
        if request.mode != "input_check":
            raise ValueError("Hugging Face filter supports input_check only")
        content = request.content if isinstance(request.content, str) else ""
        classifier = self._classifier()
        if inspect.iscoroutinefunction(classifier) or inspect.iscoroutinefunction(
            type(classifier).__call__
        ):
            result = await classifier(content, truncation=True)
        else:
            # Transformers pipelines are synchronous and may perform local GPU
            # or CPU inference; keep the participant event loop responsive.
            result = await asyncio.to_thread(classifier, content, truncation=True)
        rows = _classification_rows(result)
        label_scores = {
            _text(row.get("label")).lower(): float(row.get("score") or 0.0)
            for row in rows
            if _text(row.get("label"))
        }
        blocked_scores = {
            label: label_scores[label]
            for label in self.blocked_labels
            if label in label_scores
        }
        score = max(blocked_scores.values(), default=0.0)
        allowed = score < float(self.threshold)
        return {
            "allowed": allowed,
            "score": score,
            "scores": {request.filter_id: score, **label_scores},
            "violations": sorted(label for label, value in blocked_scores.items() if value >= self.threshold),
            "evidence": {
                "labels": label_scores,
                "blocked_labels": list(self.blocked_labels),
            },
            "metadata": {
                "provider": "huggingface",
                "model_id": self.model_id,
                "mode": request.mode,
                "threshold": float(self.threshold),
            },
        }


__all__ = ["HuggingFaceTextClassificationFilter"]
