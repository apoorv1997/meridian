import pytest
from pydantic import ValidationError

from meridian_rag.consumer.idempotency import content_hash
from meridian_rag.embedding.model_registry import pad_to_dim
from meridian_rag.models import Chunk, DocumentEvent, DocumentEventType


class TestDocumentEvent:
    def test_parses_valid_event(self):
        event = DocumentEvent.model_validate_json(
            '{"type": "create", "document_id": "d1", "tenant_id": "t1", "content": "hello"}'
        )
        assert event.type == DocumentEventType.CREATE
        assert event.requires_content()

    def test_delete_does_not_require_content(self):
        event = DocumentEvent(type="delete", document_id="d1", tenant_id="t1")
        assert not event.requires_content()

    def test_rejects_unknown_type(self):
        with pytest.raises(ValidationError):
            DocumentEvent(type="upsert", document_id="d1", tenant_id="t1")

    def test_rejects_missing_tenant(self):
        with pytest.raises(ValidationError):
            DocumentEvent.model_validate_json('{"type": "create", "document_id": "d1"}')


class TestChunk:
    def test_adr008_requires_model_metadata(self):
        with pytest.raises(ValueError, match="ADR-008"):
            Chunk(
                tenant_id="t1",
                document_id="d1",
                content="text",
                content_hash="abc",
                chunk_index=0,
                embedding_model="",
                embedding_version="1",
            )

    def test_valid_chunk(self):
        chunk = Chunk(
            tenant_id="t1",
            document_id="d1",
            content="text",
            content_hash="abc",
            chunk_index=0,
            embedding_model="all-MiniLM-L6-v2",
            embedding_version="1",
        )
        assert chunk.id  # auto-generated UUID


class TestContentHash:
    def test_deterministic(self):
        assert content_hash("same content") == content_hash("same content")

    def test_distinct_content_distinct_hash(self):
        assert content_hash("version 1") != content_hash("version 2")

    def test_sha256_hex(self):
        assert len(content_hash("x")) == 64


class TestPadToDim:
    def test_pads_shorter_vector(self):
        padded = pad_to_dim([1.0, 2.0], 5)
        assert padded == [1.0, 2.0, 0.0, 0.0, 0.0]

    def test_exact_dim_unchanged(self):
        assert pad_to_dim([1.0, 2.0], 2) == [1.0, 2.0]

    def test_rejects_oversized_vector(self):
        with pytest.raises(ValueError):
            pad_to_dim([1.0, 2.0, 3.0], 2)

    def test_zero_padding_preserves_cosine_similarity(self):
        # cos(a, b) must equal cos(pad(a), pad(b)) — the property that makes
        # storing 384-dim vectors in a 1536-dim column legitimate.
        import math

        def cosine(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))

        a, b = [0.3, 0.4, 0.5], [0.1, 0.9, 0.2]
        assert cosine(a, b) == pytest.approx(cosine(pad_to_dim(a, 10), pad_to_dim(b, 10)))
