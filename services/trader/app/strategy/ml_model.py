from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import exp
from pathlib import Path
from statistics import mean
from typing import TYPE_CHECKING, Any

from app.contracts import MlModelSnapshot
from app.training.dataset_loader import LocalTrainingDatasetLoader

if TYPE_CHECKING:
    from app.config import Settings
    from app.contracts import SignalBias
    from app.pacifica.client import PacificaClient


INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


@dataclass(slots=True)
class MlAssessment:
    ready: bool
    longProbability: float = 0.5
    shortProbability: float = 0.5
    selectedProbability: float = 0.5
    opposingProbability: float = 0.5
    approved: bool = True
    reason: str = "ML classifier is not ready yet."


@dataclass(slots=True)
class _StandardScaler:
    means: list[float] = field(default_factory=list)
    scales: list[float] = field(default_factory=list)

    def fit(self, samples: list[list[float]]) -> None:
        feature_count = len(samples[0]) if samples else 0
        self.means = []
        self.scales = []
        for index in range(feature_count):
            column = [sample[index] for sample in samples]
            average = mean(column)
            variance = mean([(value - average) ** 2 for value in column])
            self.means.append(average)
            self.scales.append(max(variance**0.5, 1e-6))

    def transform(self, sample: list[float]) -> list[float]:
        if not self.means:
            return sample
        return [
            (value - self.means[index]) / self.scales[index]
            for index, value in enumerate(sample)
        ]


@dataclass(slots=True)
class _BinaryLogisticModel:
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0

    def fit(
        self,
        samples: list[list[float]],
        labels: list[int],
        *,
        epochs: int = 220,
        learning_rate: float = 0.08,
        l2: float = 0.0008,
    ) -> None:
        feature_count = len(samples[0]) if samples else 0
        self.weights = [0.0] * feature_count
        self.bias = 0.0

        positives = sum(labels)
        negatives = max(len(labels) - positives, 1)
        positives = max(positives, 1)
        positive_weight = len(labels) / (2 * positives)
        negative_weight = len(labels) / (2 * negatives)

        for _ in range(epochs):
            weight_gradients = [0.0] * feature_count
            bias_gradient = 0.0

            for sample, label in zip(samples, labels, strict=True):
                probability = self.predict_proba(sample)
                error = probability - label
                class_weight = positive_weight if label == 1 else negative_weight
                weighted_error = error * class_weight

                for index in range(feature_count):
                    weight_gradients[index] += weighted_error * sample[index]
                bias_gradient += weighted_error

            sample_count = max(len(samples), 1)
            for index in range(feature_count):
                regularized_gradient = (weight_gradients[index] / sample_count) + (
                    l2 * self.weights[index]
                )
                self.weights[index] -= learning_rate * regularized_gradient
            self.bias -= learning_rate * (bias_gradient / sample_count)

    def predict_proba(self, sample: list[float]) -> float:
        z_score = self.bias + sum(
            weight * value for weight, value in zip(self.weights, sample, strict=False)
        )
        z_score = max(min(z_score, 35.0), -35.0)
        return 1.0 / (1.0 + exp(-z_score))


@dataclass(slots=True)
class _ValidationMetrics:
    sampleCount: int = 0
    decisionCount: int = 0
    decisionCoverage: float = 0.0
    decisionPrecision: float | None = None
    longPrecision: float | None = None
    shortPrecision: float | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "sampleCount": self.sampleCount,
            "decisionCount": self.decisionCount,
            "decisionCoverage": self.decisionCoverage,
            "decisionPrecision": self.decisionPrecision,
            "longPrecision": self.longPrecision,
            "shortPrecision": self.shortPrecision,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "_ValidationMetrics":
        payload = payload or {}
        return cls(
            sampleCount=int(payload.get("sampleCount", 0)),
            decisionCount=int(payload.get("decisionCount", 0)),
            decisionCoverage=float(payload.get("decisionCoverage", 0.0)),
            decisionPrecision=_optional_float(payload.get("decisionPrecision")),
            longPrecision=_optional_float(payload.get("longPrecision")),
            shortPrecision=_optional_float(payload.get("shortPrecision")),
        )


@dataclass(slots=True)
class _CapturedModelState:
    scalerMeans: list[float]
    scalerScales: list[float]
    longWeights: list[float]
    longBias: float
    shortWeights: list[float]
    shortBias: float
    ready: bool
    lastError: str | None
    lastSummary: str
    trainingSamples: int
    trainingSymbols: list[str]
    trainingSource: str | None
    lastTrainedAt: datetime | None
    validation: _ValidationMetrics


class MlSignalModel:
    def __init__(self, settings: "Settings", client: "PacificaClient") -> None:
        self.settings = settings
        self.client = client
        self.datasetLoader = LocalTrainingDatasetLoader(settings)
        self.scaler = _StandardScaler()
        self.longModel = _BinaryLogisticModel()
        self.shortModel = _BinaryLogisticModel()
        self.ready = False
        self.lastError: str | None = None
        self.lastSummary: str = "ML classifier has not been trained yet."
        self.trainingSamples = 0
        self.trainingSymbols: list[str] = []
        self.trainingSource: str | None = None
        self.lastTrainedAt: datetime | None = None
        self.lastAttemptAt: datetime | None = None
        self.validation = _ValidationMetrics()
        self.artifactPath = self._resolve_artifact_path(settings.mlModelArtifactPath)

        if self.settings.mlLoadArtifactOnStartup:
            self.load_artifact()

    @property
    def minimum_points(self) -> int:
        return max(48, self.settings.mlPredictionHorizonBars + 34)

    async def refresh_if_due(self, symbols: list[str], force: bool = False) -> bool:
        if not self.settings.mlEnabled:
            self.ready = False
            self.lastSummary = "ML classifier is disabled in settings."
            self.lastError = None
            self.validation = _ValidationMetrics()
            return False

        now = datetime.now(timezone.utc)
        if (
            not force
            and self.lastAttemptAt is not None
            and (now - self.lastAttemptAt).total_seconds() < self.settings.mlRetrainIntervalSec
        ):
            return False

        had_ready_model = self.ready
        previous_state = self._capture_model_state() if had_ready_model else None
        prior_training_source = self.trainingSource
        self.lastAttemptAt = now
        local_notes: list[str] = []

        if self.settings.mlPreferLocalDataset:
            local_close_history, local_notes = self.datasetLoader.load_close_history(
                symbols=symbols,
                interval=self.settings.mlCandleInterval,
                max_candles=self.settings.mlTrainingLookbackCandles,
            )
            if self.train_from_close_history(local_close_history, source="local dataset"):
                self.lastTrainedAt = now
                self._save_artifact_with_notice()
                if local_notes:
                    self.lastSummary += f" Local dataset notes: {'; '.join(local_notes)}."
                return True

        interval_ms = INTERVAL_MS.get(self.settings.mlCandleInterval, INTERVAL_MS["1m"])
        end_time = int(now.timestamp() * 1000)
        start_time = end_time - (interval_ms * self.settings.mlTrainingLookbackCandles)

        candle_sets = await asyncio.gather(
            *[
                self.client.get_candles(
                    symbol=symbol,
                    interval=self.settings.mlCandleInterval,
                    start_time=start_time,
                    end_time=end_time,
                )
                for symbol in symbols
            ],
            return_exceptions=True,
        )

        close_history: dict[str, list[float]] = {}
        failures: list[str] = []
        for symbol, result in zip(symbols, candle_sets, strict=True):
            if isinstance(result, Exception):
                failures.append(f"{symbol}: {result}")
                continue
            closes = self._extract_close_series(result)
            if len(closes) >= self.minimum_points:
                close_history[symbol] = closes

        trained = self.train_from_close_history(close_history, source="Pacifica REST candles")
        if trained:
            self.lastTrainedAt = now
            self._save_artifact_with_notice()
            if failures:
                self.lastSummary += f" Some symbols failed to load: {', '.join(failures)}."
            if local_notes:
                self.lastSummary += f" Local dataset notes: {'; '.join(local_notes)}."
            return True

        detail_parts = []
        if local_notes:
            detail_parts.append(f"local dataset: {'; '.join(local_notes)}")
        if failures:
            detail_parts.append(f"api: {', '.join(failures)}")
        if self.lastError:
            detail_parts.append(self.lastError)
        if not detail_parts:
            detail_parts.append("Not enough candle data.")
        failure_details = " | ".join(detail_parts)

        if had_ready_model and previous_state is not None:
            self._restore_model_state(previous_state)
            self.trainingSource = prior_training_source
            self.lastError = failure_details
            self.lastSummary = (
                "ML retraining did not clear the validation gates, so the bot is still using "
                f"the previously loaded model. {failure_details}"
            )
            return False

        self.ready = False
        self.lastError = failure_details
        self.lastSummary = f"ML training fallback active. {failure_details}"
        self.validation = _ValidationMetrics()
        return False

    def train_from_close_history(
        self,
        close_history: dict[str, list[float]],
        *,
        source: str,
    ) -> bool:
        feature_rows: list[list[float]] = []
        long_labels: list[int] = []
        short_labels: list[int] = []

        for symbol, closes in close_history.items():
            symbol_samples, symbol_long, symbol_short = self._build_dataset(symbol, closes)
            feature_rows.extend(symbol_samples)
            long_labels.extend(symbol_long)
            short_labels.extend(symbol_short)

        if len(feature_rows) < 120:
            self._fail_training(
                error="Not enough training samples to fit the ML classifier.",
                summary="ML training skipped because candle history is too short.",
            )
            return False

        validation_size = max(
            int(len(feature_rows) * self.settings.mlValidationSplitPct),
            self.settings.mlMinValidationSamples,
        )
        training_size = len(feature_rows) - validation_size
        if training_size < 80 or validation_size < self.settings.mlMinValidationSamples:
            self._fail_training(
                error="The dataset is too small for a stable train/validation split.",
                summary=(
                    "ML training skipped because there are not enough samples for "
                    "out-of-sample validation."
                ),
            )
            return False

        training_rows = feature_rows[:training_size]
        validation_rows = feature_rows[training_size:]
        training_long_labels = long_labels[:training_size]
        validation_long_labels = long_labels[training_size:]
        training_short_labels = short_labels[:training_size]
        validation_short_labels = short_labels[training_size:]

        if sum(training_long_labels) == 0 or sum(training_short_labels) == 0:
            self._fail_training(
                error="Training labels are too one-sided to fit a usable classifier.",
                summary="ML training skipped because label diversity is too low.",
            )
            return False

        self.scaler.fit(training_rows)
        transformed_rows = [self.scaler.transform(row) for row in training_rows]
        self.longModel.fit(transformed_rows, training_long_labels)
        self.shortModel.fit(transformed_rows, training_short_labels)

        self.validation = self._evaluate_validation_split(
            validation_rows=validation_rows,
            validation_long_labels=validation_long_labels,
            validation_short_labels=validation_short_labels,
        )

        if self.validation.decisionCount < self.settings.mlMinValidationDecisionCount:
            self._fail_training(
                error=(
                    "Validation set did not produce enough approved trade decisions to judge the "
                    "model safely."
                ),
                summary=(
                    "ML training completed but the validation window did not produce enough "
                    "approved trade decisions, so the bot is staying on rules only."
                ),
            )
            return False

        if (
            self.validation.decisionPrecision is None
            or self.validation.decisionPrecision
            < self.settings.mlMinValidationDecisionPrecision
        ):
            precision_text = self._format_ratio(self.validation.decisionPrecision)
            self._fail_training(
                error=(
                    "Validation decision precision was below the safety threshold. "
                    f"Observed {precision_text} over {self.validation.decisionCount} decisions."
                ),
                summary=(
                    "ML training completed but holdout precision was not strong enough, so the "
                    "bot is keeping rule-based approvals active."
                ),
            )
            return False

        self.ready = True
        self.lastError = None
        self.trainingSamples = len(feature_rows)
        self.trainingSymbols = sorted(close_history.keys())
        self.trainingSource = source
        self.lastSummary = (
            f"Validated logistic classifiers from {source} on {self.trainingSamples} Pacifica "
            f"candle windows across {len(self.trainingSymbols)} symbols. Holdout precision "
            f"{self._format_ratio(self.validation.decisionPrecision)} across "
            f"{self.validation.decisionCount} approved decisions "
            f"({self.validation.decisionCoverage:.0%} coverage)."
        )
        return True

    def load_artifact(self) -> bool:
        if not self.artifactPath.exists():
            return False

        try:
            payload = json.loads(self.artifactPath.read_text(encoding="utf-8"))
            scaler_payload = payload["scaler"]
            long_model_payload = payload["longModel"]
            short_model_payload = payload["shortModel"]

            self.scaler.means = [float(value) for value in scaler_payload["means"]]
            self.scaler.scales = [float(value) for value in scaler_payload["scales"]]
            self.longModel.weights = [float(value) for value in long_model_payload["weights"]]
            self.longModel.bias = float(long_model_payload["bias"])
            self.shortModel.weights = [float(value) for value in short_model_payload["weights"]]
            self.shortModel.bias = float(short_model_payload["bias"])
            self.trainingSamples = int(payload.get("trainingSamples", 0))
            self.trainingSymbols = [
                str(symbol) for symbol in payload.get("trainingSymbols", [])
            ]
            self.trainingSource = str(payload.get("trainingSource") or "artifact")
            trained_at = payload.get("trainedAt")
            self.lastTrainedAt = (
                datetime.fromisoformat(trained_at) if isinstance(trained_at, str) else None
            )
            self.validation = _ValidationMetrics.from_payload(payload.get("validation"))
            self.ready = bool(payload.get("ready", True))
            self.lastError = None
            self.lastSummary = (
                f"Loaded persisted ML model from {self.artifactPath} trained on "
                f"{self.trainingSamples} samples."
            )
            return True
        except Exception as exc:
            self.ready = False
            self.lastError = f"Failed to load ML artifact: {exc}"
            self.lastSummary = self.lastError
            self.validation = _ValidationMetrics()
            return False

    def save_artifact(self) -> Path:
        payload = {
            "version": 2,
            "ready": self.ready,
            "trainedAt": (
                self.lastTrainedAt.isoformat()
                if self.lastTrainedAt is not None
                else datetime.now(timezone.utc).isoformat()
            ),
            "trainingSamples": self.trainingSamples,
            "trainingSymbols": self.trainingSymbols,
            "trainingSource": self.trainingSource,
            "candleInterval": self.settings.mlCandleInterval,
            "predictionHorizonBars": self.settings.mlPredictionHorizonBars,
            "targetMovePct": self.settings.mlTargetMovePct,
            "validation": self.validation.to_payload(),
            "scaler": {
                "means": self.scaler.means,
                "scales": self.scaler.scales,
            },
            "longModel": {
                "weights": self.longModel.weights,
                "bias": self.longModel.bias,
            },
            "shortModel": {
                "weights": self.shortModel.weights,
                "bias": self.shortModel.bias,
            },
        }
        self.artifactPath.parent.mkdir(parents=True, exist_ok=True)
        self.artifactPath.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return self.artifactPath

    def assess(self, prices: list[float], bias: "SignalBias") -> MlAssessment:
        if not self.ready:
            return MlAssessment(
                ready=False,
                reason=self.lastSummary,
            )

        if len(prices) < self.minimum_points:
            return MlAssessment(
                ready=False,
                reason="ML classifier needs a deeper local price history before it can score trades.",
            )

        features = self._extract_features(prices)
        transformed = self.scaler.transform(features)
        long_probability = self.longModel.predict_proba(transformed)
        short_probability = self.shortModel.predict_proba(transformed)
        selected_probability = long_probability if bias == "long" else short_probability
        opposing_probability = short_probability if bias == "long" else long_probability
        edge = selected_probability - opposing_probability
        approved = (
            selected_probability >= self.settings.mlMinTradeProbability
            and edge >= self.settings.mlProbabilityEdge
        )

        return MlAssessment(
            ready=True,
            longProbability=round(long_probability, 4),
            shortProbability=round(short_probability, 4),
            selectedProbability=round(selected_probability, 4),
            opposingProbability=round(opposing_probability, 4),
            approved=approved,
            reason=(
                f"ML {bias} probability {selected_probability:.0%} versus "
                f"{opposing_probability:.0%} opposite probability. Holdout precision "
                f"{self._format_ratio(self.validation.decisionPrecision)}."
            ),
        )

    def snapshot(self) -> MlModelSnapshot:
        return MlModelSnapshot(
            ready=self.ready,
            summary=self.lastSummary,
            trainingSource=self.trainingSource,
            trainingSamples=self.trainingSamples,
            trainingSymbols=self.trainingSymbols,
            lastTrainedAt=self.lastTrainedAt,
            validationSamples=self.validation.sampleCount,
            decisionSamples=self.validation.decisionCount,
            decisionCoverage=round(self.validation.decisionCoverage, 4),
            decisionPrecision=_round_optional(self.validation.decisionPrecision),
            longPrecision=_round_optional(self.validation.longPrecision),
            shortPrecision=_round_optional(self.validation.shortPrecision),
        )

    def _build_dataset(
        self,
        symbol: str,
        closes: list[float],
    ) -> tuple[list[list[float]], list[int], list[int]]:
        del symbol
        horizon = self.settings.mlPredictionHorizonBars
        threshold = self.settings.mlTargetMovePct / 100
        samples: list[list[float]] = []
        long_labels: list[int] = []
        short_labels: list[int] = []

        for index in range(self.minimum_points - 1, len(closes) - horizon):
            history = closes[: index + 1]
            current_close = closes[index]
            future_close = closes[index + horizon]
            future_return = (future_close - current_close) / max(current_close, 1.0)

            samples.append(self._extract_features(history))
            long_labels.append(1 if future_return > threshold else 0)
            short_labels.append(1 if future_return < -threshold else 0)

        return samples, long_labels, short_labels

    def _extract_features(self, prices: list[float]) -> list[float]:
        current = prices[-1]
        fast_mean = mean(prices[-8:])
        slow_mean = mean(prices[-21:])
        trend_mean = mean(prices[-34:])
        range_window = prices[-20:]
        range_high = max(range_window)
        range_low = min(range_window)
        range_span = max(range_high - range_low, current * 0.0001)
        recent_returns = [
            (prices[index] - prices[index - 1]) / max(prices[index - 1], 1.0)
            for index in range(len(prices) - 12, len(prices))
        ]
        positive_returns = sum(1 for value in recent_returns if value > 0)
        negative_returns = sum(1 for value in recent_returns if value < 0)
        realized_volatility = mean(abs(value) for value in recent_returns)

        return [
            self._pct_change(prices, 1),
            self._pct_change(prices, 3),
            self._pct_change(prices, 5),
            self._pct_change(prices, 8),
            self._pct_change(prices, 13),
            (current / max(fast_mean, 1.0)) - 1,
            (current / max(slow_mean, 1.0)) - 1,
            (fast_mean / max(slow_mean, 1.0)) - 1,
            (current / max(trend_mean, 1.0)) - 1,
            (current - range_high) / max(range_high, 1.0),
            (current - range_low) / max(range_low, 1.0),
            ((current - mean(range_window)) / range_span),
            realized_volatility,
            (positive_returns - negative_returns) / max(len(recent_returns), 1),
            self._pct_change(prices, 21) - self._pct_change(prices, 5),
        ]

    def _extract_close_series(self, candles: list[dict[str, Any]]) -> list[float]:
        closes: list[float] = []
        for candle in candles:
            close_value = candle.get("c")
            if close_value is None:
                close_value = candle.get("close")
            if close_value is None:
                continue
            closes.append(float(close_value))
        return closes

    def _pct_change(self, prices: list[float], lookback: int) -> float:
        anchor = prices[-(lookback + 1)]
        current = prices[-1]
        return (current - anchor) / max(anchor, 1.0)

    def _resolve_artifact_path(self, artifact_path: Path) -> Path:
        if artifact_path.is_absolute():
            return artifact_path
        return (Path(__file__).resolve().parents[2] / artifact_path).resolve()

    def _save_artifact_with_notice(self) -> None:
        try:
            path = self.save_artifact()
            self.lastSummary += f" Artifact saved to {path}."
        except Exception as exc:
            self.lastSummary += f" Artifact save failed: {exc}."

    def _evaluate_validation_split(
        self,
        *,
        validation_rows: list[list[float]],
        validation_long_labels: list[int],
        validation_short_labels: list[int],
    ) -> _ValidationMetrics:
        if not validation_rows:
            return _ValidationMetrics()

        decision_count = 0
        decision_correct = 0
        long_predictions = 0
        long_true_positives = 0
        short_predictions = 0
        short_true_positives = 0

        for row, long_label, short_label in zip(
            validation_rows,
            validation_long_labels,
            validation_short_labels,
            strict=True,
        ):
            transformed = self.scaler.transform(row)
            long_probability = self.longModel.predict_proba(transformed)
            short_probability = self.shortModel.predict_proba(transformed)
            long_edge = long_probability - short_probability
            short_edge = short_probability - long_probability
            long_approved = (
                long_probability >= self.settings.mlMinTradeProbability
                and long_edge >= self.settings.mlProbabilityEdge
            )
            short_approved = (
                short_probability >= self.settings.mlMinTradeProbability
                and short_edge >= self.settings.mlProbabilityEdge
            )

            if long_approved:
                long_predictions += 1
                if long_label == 1:
                    long_true_positives += 1

            if short_approved:
                short_predictions += 1
                if short_label == 1:
                    short_true_positives += 1

            decision: str | None = None
            if long_approved and (not short_approved or long_probability >= short_probability):
                decision = "long"
            elif short_approved:
                decision = "short"

            if decision is None:
                continue

            decision_count += 1
            if (decision == "long" and long_label == 1) or (
                decision == "short" and short_label == 1
            ):
                decision_correct += 1

        sample_count = len(validation_rows)
        return _ValidationMetrics(
            sampleCount=sample_count,
            decisionCount=decision_count,
            decisionCoverage=(decision_count / sample_count) if sample_count else 0.0,
            decisionPrecision=_safe_ratio(decision_correct, decision_count),
            longPrecision=_safe_ratio(long_true_positives, long_predictions),
            shortPrecision=_safe_ratio(short_true_positives, short_predictions),
        )

    def _capture_model_state(self) -> _CapturedModelState:
        return _CapturedModelState(
            scalerMeans=list(self.scaler.means),
            scalerScales=list(self.scaler.scales),
            longWeights=list(self.longModel.weights),
            longBias=self.longModel.bias,
            shortWeights=list(self.shortModel.weights),
            shortBias=self.shortModel.bias,
            ready=self.ready,
            lastError=self.lastError,
            lastSummary=self.lastSummary,
            trainingSamples=self.trainingSamples,
            trainingSymbols=list(self.trainingSymbols),
            trainingSource=self.trainingSource,
            lastTrainedAt=self.lastTrainedAt,
            validation=_ValidationMetrics(
                sampleCount=self.validation.sampleCount,
                decisionCount=self.validation.decisionCount,
                decisionCoverage=self.validation.decisionCoverage,
                decisionPrecision=self.validation.decisionPrecision,
                longPrecision=self.validation.longPrecision,
                shortPrecision=self.validation.shortPrecision,
            ),
        )

    def _restore_model_state(self, state: _CapturedModelState) -> None:
        self.scaler.means = list(state.scalerMeans)
        self.scaler.scales = list(state.scalerScales)
        self.longModel.weights = list(state.longWeights)
        self.longModel.bias = state.longBias
        self.shortModel.weights = list(state.shortWeights)
        self.shortModel.bias = state.shortBias
        self.ready = state.ready
        self.lastError = state.lastError
        self.lastSummary = state.lastSummary
        self.trainingSamples = state.trainingSamples
        self.trainingSymbols = list(state.trainingSymbols)
        self.trainingSource = state.trainingSource
        self.lastTrainedAt = state.lastTrainedAt
        self.validation = _ValidationMetrics(
            sampleCount=state.validation.sampleCount,
            decisionCount=state.validation.decisionCount,
            decisionCoverage=state.validation.decisionCoverage,
            decisionPrecision=state.validation.decisionPrecision,
            longPrecision=state.validation.longPrecision,
            shortPrecision=state.validation.shortPrecision,
        )

    def _fail_training(self, *, error: str, summary: str) -> None:
        self.ready = False
        self.lastError = error
        self.lastSummary = summary

    def _format_ratio(self, value: float | None) -> str:
        if value is None:
            return "n/a"
        return f"{value:.0%}"


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _round_optional(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 4)
