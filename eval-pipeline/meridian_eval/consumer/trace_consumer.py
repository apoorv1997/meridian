"""Kafka OTel trace consumer — the eval pipeline entry point.

Consumption path per span:
    1. Parse JSON → TraceSpan
    2. Tier 1 (synchronous, 100%) — latency, format, length, toxicity
    3. Persist Tier 1 result to PostgreSQL
    4. Tier 2 (async, 10% sample) — LLM-as-judge via Ollama
    5. If sampled and scored: persist Tier 2 result, record score in FeedbackWindow
    6. If output_text present: embed and check for semantic drift
    7. Manual offset commit after all processing (never auto-commit)
    Failures → span logged and skipped; offset still committed so the consumer
    does not stall. Add a DLQ producer here if replay is needed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime

import redis.asyncio as aioredis
from aiokafka import AIOKafkaConsumer
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from meridian_eval.consumer.models import TraceSpan
from meridian_eval.drift.detector import DriftDetector
from meridian_eval.drift.embedder import DriftEmbedder
from meridian_eval.feedback.weight_updater import WeightUpdater
from meridian_eval.feedback.window import FeedbackWindow
from meridian_eval.judge.ensemble import EnsembleJudge
from meridian_eval.judge.llm_judge import LLMJudge
from meridian_eval.tiers import tier1, tier2

logger = logging.getLogger(__name__)


class TraceConsumer:
    def __init__(
        self,
        *,
        kafka_brokers: str,
        traces_topic: str,
        consumer_group: str,
        database_url: str,
        redis_url: str,
        ollama_base_url: str,
        ollama_model: str,
        tier2_sample_rate: float,
        feedback_window_seconds: float,
        feedback_min_samples: int,
        feedback_max_shift: float,
    ) -> None:
        self._brokers = kafka_brokers
        self._topic = traces_topic
        self._group = consumer_group
        self._db_url = database_url
        self._redis_url = redis_url
        self._ollama_url = ollama_base_url
        self._ollama_model = ollama_model
        self._sample_rate = tier2_sample_rate
        self._window_seconds = feedback_window_seconds
        self._min_samples = feedback_min_samples
        self._max_shift = feedback_max_shift

        self._sessions: async_sessionmaker[AsyncSession] | None = None
        self._consumer: AIOKafkaConsumer | None = None
        self._feedback_window: FeedbackWindow | None = None
        self._judge: LLMJudge | None = None
        self._drift_embedder = DriftEmbedder()
        self._drift_detector = DriftDetector()

    async def run(self) -> None:
        # ── DB ──────────────────────────────────────────────────────────────────
        db_url = self._db_url
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        db_url = db_url.split("?")[0]
        engine = create_async_engine(db_url, pool_size=5)
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

        # ── Redis ───────────────────────────────────────────────────────────────
        redis_client = await aioredis.from_url(self._redis_url, decode_responses=True)

        # ── Feedback loop ───────────────────────────────────────────────────────
        updater = WeightUpdater(
            redis_client,
            max_shift=self._max_shift,
            min_floor=0.05,
        )
        self._feedback_window = FeedbackWindow(
            updater,
            window_seconds=self._window_seconds,
            min_samples=self._min_samples,
            max_shift=self._max_shift,
        )
        self._feedback_window.start()

        # ── LLM judge ───────────────────────────────────────────────────────────
        self._judge = LLMJudge(base_url=self._ollama_url, model=self._ollama_model)
        ensemble = EnsembleJudge(self._judge)

        # ── Kafka consumer ──────────────────────────────────────────────────────
        self._consumer = AIOKafkaConsumer(
            self._topic,
            bootstrap_servers=self._brokers,
            group_id=self._group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        await self._consumer.start()
        logger.info(
            "trace consumer started",
            extra={"topic": self._topic, "group": self._group},
        )

        try:
            async for message in self._consumer:
                try:
                    await self._process(message.value, ensemble)
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "span processing failed — skipping",
                        extra={"error": str(exc)},
                    )
                await self._consumer.commit()
        finally:
            await self._feedback_window.stop()
            await self._judge.close()
            await self._consumer.stop()
            await redis_client.aclose()
            await engine.dispose()

    async def _process(self, raw: bytes, ensemble: EnsembleJudge) -> None:
        try:
            span = TraceSpan.model_validate_json(raw)
        except ValidationError as exc:
            logger.warning("invalid trace span: %s", exc)
            return

        # ── Tier 1 ──────────────────────────────────────────────────────────────
        t1 = tier1.evaluate(span)
        await self._persist_tier1(span, t1)

        # ── Tier 2 (async, sampled) ──────────────────────────────────────────────
        t2 = await tier2.evaluate(span, ensemble, sample_rate=self._sample_rate)
        if t2.sampled and t2.aggregate_score >= 0.0:
            await self._persist_tier2(span, t2)
            assert self._feedback_window is not None
            await self._feedback_window.record(
                span.route_id, span.provider, t2.aggregate_score
            )

        # ── Drift detection (best-effort) ────────────────────────────────────────
        output_text = span.attributes.get("output_text", "").strip()
        if output_text:
            try:
                [embedding] = await self._drift_embedder.embed([output_text])
                alert = self._drift_detector.add(span.tenant_id, span.route_id, embedding)
                if alert:
                    logger.warning(
                        "drift alert",
                        extra={
                            "tenant_id": alert.tenant_id,
                            "route_id": alert.route_id,
                            "distance": alert.distance,
                            "threshold": alert.threshold,
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                # Best-effort, but visible: a broken drift check must not fail silently.
                logger.warning("drift check failed: %s", exc)

    async def _persist_tier1(self, span: TraceSpan, result: tier1.Tier1Result) -> None:
        assert self._sessions is not None
        async with self._sessions() as session:
            await session.execute(
                _INSERT_EVAL,
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": span.tenant_id,
                    "trace_id": span.trace_id,
                    "route_id": span.route_id,
                    "provider": span.provider,
                    "tier": 1,
                    "latency_ms": span.latency_ms,
                    "tier1_passed": result.passed,
                    "tier2_score": None,
                    "tier2_rubrics": None,
                    "raw_judge_output": None,
                    "created_at": datetime.now(UTC),
                },
            )
            await session.commit()

    async def _persist_tier2(self, span: TraceSpan, result: tier2.Tier2Result) -> None:
        import json
        assert self._sessions is not None
        rubrics_json = json.dumps(
            {name: r.score for name, r in result.scores.items()}
        )
        async with self._sessions() as session:
            await session.execute(
                _INSERT_EVAL,
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": span.tenant_id,
                    "trace_id": span.trace_id,
                    "route_id": span.route_id,
                    "provider": span.provider,
                    "tier": 2,
                    "latency_ms": span.latency_ms,
                    "tier1_passed": None,
                    "tier2_score": result.aggregate_score,
                    "tier2_rubrics": rubrics_json,
                    "raw_judge_output": None,
                    "created_at": datetime.now(UTC),
                },
            )
            await session.commit()


from sqlalchemy import text as _text  # noqa: E402 — after class definition

_INSERT_EVAL = _text(
    """
    SET app.tenant_id = :tenant_id;
    INSERT INTO evaluation_results
        (id, tenant_id, trace_id, route_id, provider, tier,
         latency_ms, tier1_passed, tier2_score, tier2_rubrics,
         raw_judge_output, created_at)
    VALUES
        (:id, :tenant_id, :trace_id, :route_id, :provider, :tier,
         :latency_ms, :tier1_passed, :tier2_score, :tier2_rubrics::jsonb,
         :raw_judge_output, :created_at)
    """
)


def _settings_from_env() -> dict:
    return {
        "kafka_brokers": os.environ.get("KAFKA_BROKERS", "localhost:9094"),
        "traces_topic": os.environ.get("KAFKA_TOPIC_TRACES", "meridian.traces"),
        "consumer_group": os.environ.get("EVAL_CONSUMER_GROUP", "meridian-eval"),
        "database_url": os.environ["DATABASE_URL"],
        "redis_url": os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        "ollama_base_url": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        "ollama_model": os.environ.get("OLLAMA_JUDGE_MODEL", "llama3.1:8b"),
        "tier2_sample_rate": float(os.environ.get("EVAL_TIER2_SAMPLE_RATE", "0.10")),
        "feedback_window_seconds": float(os.environ.get("EVAL_FEEDBACK_WINDOW_MINUTES", "5")) * 60,
        "feedback_min_samples": int(os.environ.get("EVAL_FEEDBACK_MIN_SAMPLES", "50")),
        "feedback_max_shift": float(os.environ.get("EVAL_FEEDBACK_MAX_WEIGHT_SHIFT", "0.10")),
    }


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    consumer = TraceConsumer(**_settings_from_env())
    asyncio.run(consumer.run())


if __name__ == "__main__":
    main()
