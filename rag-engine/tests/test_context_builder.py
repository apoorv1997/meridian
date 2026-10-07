from meridian_rag.retrieval.context_builder import build_context
from meridian_rag.retrieval.pgvector import RetrievedChunk


def make_chunk(content: str, doc_id: str = "d1") -> RetrievedChunk:
    return RetrievedChunk(
        id="c1", document_id=doc_id, content=content, similarity=0.9, metadata={}
    )


class TestBuildContext:
    def test_concatenates_in_order(self):
        ctx = build_context([make_chunk("first"), make_chunk("second")])
        assert "first" in ctx.text
        assert ctx.text.index("first") < ctx.text.index("second")
        assert ctx.chunk_count == 2

    def test_respects_max_chars(self):
        chunks = [make_chunk("x" * 100) for _ in range(10)]
        ctx = build_context(chunks, max_chars=350)
        assert len(ctx.text) <= 350
        assert ctx.chunk_count < 10

    def test_collects_unique_document_ids(self):
        chunks = [
            make_chunk("a", doc_id="doc-1"),
            make_chunk("b", doc_id="doc-2"),
            make_chunk("c", doc_id="doc-1"),
        ]
        ctx = build_context(chunks)
        assert ctx.document_ids == ["doc-1", "doc-2"]

    def test_empty_input(self):
        ctx = build_context([])
        assert ctx.text == ""
        assert ctx.document_ids == []
        assert ctx.chunk_count == 0

    def test_skipped_chunk_documents_not_tagged(self):
        # A chunk that doesn't fit must not contribute its document_id —
        # otherwise cache invalidation would be tied to content that isn't
        # actually in the context.
        chunks = [make_chunk("x" * 100, doc_id="doc-in"), make_chunk("y" * 100, doc_id="doc-out")]
        ctx = build_context(chunks, max_chars=110)
        assert ctx.document_ids == ["doc-in"]
