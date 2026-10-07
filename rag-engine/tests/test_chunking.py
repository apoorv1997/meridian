import pytest

from meridian_rag.chunking import fixed_size_chunks, semantic_chunks
from meridian_rag.chunking.semantic import split_sentences


class TestFixedSizeChunks:
    def test_short_text_single_chunk(self):
        assert fixed_size_chunks("hello world", chunk_size=100, overlap=20) == ["hello world"]

    def test_empty_text(self):
        assert fixed_size_chunks("") == []
        assert fixed_size_chunks("   ") == []

    def test_chunks_respect_size(self):
        text = "x" * 2500
        chunks = fixed_size_chunks(text, chunk_size=1000, overlap=200)
        assert all(len(c) <= 1000 for c in chunks)

    def test_overlap_preserves_boundary_content(self):
        text = "abcdefghij" * 100  # 1000 chars
        chunks = fixed_size_chunks(text, chunk_size=400, overlap=100)
        # Each chunk's first 100 chars equal the previous chunk's last 100.
        for prev, curr in zip(chunks, chunks[1:]):
            assert prev[-100:] == curr[:100]

    def test_full_coverage(self):
        text = "0123456789" * 50
        chunks = fixed_size_chunks(text, chunk_size=120, overlap=20)
        # Strip overlap from all but the first chunk and verify reassembly.
        reassembled = chunks[0] + "".join(c[20:] for c in chunks[1:])
        assert reassembled == text

    def test_invalid_params(self):
        with pytest.raises(ValueError):
            fixed_size_chunks("text", chunk_size=0)
        with pytest.raises(ValueError):
            fixed_size_chunks("text", chunk_size=100, overlap=100)


class TestSemanticChunks:
    def test_sentences_not_split(self):
        text = "First sentence here. Second sentence follows. Third one ends it."
        chunks = semantic_chunks(text, max_chars=50)
        # Every chunk must end at a sentence boundary (or be the whole text).
        for chunk in chunks:
            assert chunk.rstrip()[-1] in ".!?"

    def test_packs_sentences_up_to_limit(self):
        text = "Aaa bbb. Ccc ddd. Eee fff."
        chunks = semantic_chunks(text, max_chars=20)
        assert chunks == ["Aaa bbb. Ccc ddd.", "Eee fff."]

    def test_oversized_sentence_falls_back_to_fixed(self):
        text = "x" * 500 + "."
        chunks = semantic_chunks(text, max_chars=100)
        assert len(chunks) > 1
        assert all(len(c) <= 100 for c in chunks)

    def test_empty(self):
        assert semantic_chunks("") == []

    def test_split_sentences(self):
        assert split_sentences("Hello there. How are you? Fine!") == [
            "Hello there.",
            "How are you?",
            "Fine!",
        ]
