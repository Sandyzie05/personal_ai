"""R3: batched + cached embeddings, index stamping, per-document replace, fail-closed dimension check."""

import os
import uuid

import pytest

from src.ai_engine.chroma_store import ChromaStore, ChromaStoreError, contextual_header
from src.ai_engine.embeddings import EmbeddingsGenerator, VaultEmbeddingCache
from src.ai_engine.rag_engine import RAGEngine
from evals.rag.run import _TMP  # chromadb allows one ephemeral system per process: share its dir
from src.data_vault import DataVault


class FakeClient:
    model = "fake-embed"

    def __init__(self, dim=8):
        self.dim, self.texts, self.batches = dim, 0, 0

    def generate_embeddings(self, texts):
        self.batches += 1
        self.texts += len(texts)
        return [[float((hash((t, i)) % 7) + 1) for i in range(self.dim)] for t in texts]

    def generate_embedding(self, t):
        return self.generate_embeddings([t])[0]


@pytest.fixture
def vault(tmp_path):
    return DataVault(vault_path=str(tmp_path / "v"), encryption_key=os.urandom(32))


def _store(client, tmp_path, **kw):
    gen = EmbeddingsGenerator(client=client)
    return gen, ChromaStore(embedding_generator=gen, persist_directory=_TMP,
                            collection_name=f"t_{uuid.uuid4().hex[:8]}", **kw)


def test_batches_by_config_size():
    client = FakeClient()
    EmbeddingsGenerator(client=client).generate_embeddings([f"t{i}" for i in range(70)])
    assert (client.batches, client.texts) == (3, 70)  # 32 + 32 + 6


def test_cache_means_zero_calls_on_second_index(vault, tmp_path):
    client = FakeClient()
    gen, s1 = _store(client, tmp_path)
    gen.cache = VaultEmbeddingCache(vault)
    docs = [f"bill number {i} total due ${i}.00" for i in range(5)]
    s1.add_documents(docs, [{"storage_key": f"k{i}"} for i in range(5)])
    first = client.texts
    gen2, s2 = _store(client, tmp_path)
    gen2.cache = VaultEmbeddingCache(vault)
    s2.add_documents(docs, [{"storage_key": f"k{i}"} for i in range(5)])
    assert first == 5 and client.texts == first


def test_cache_key_includes_salt_and_vector_roundtrips(vault):
    client = FakeClient()
    gen = EmbeddingsGenerator(client=client)
    gen.cache = VaultEmbeddingCache(vault)
    a = gen.generate_embeddings(["x"], salt="v1")[0]
    gen.generate_embeddings(["x"], salt="v1")
    assert client.texts == 1
    gen.generate_embeddings(["x"], salt="v2")
    assert client.texts == 2
    assert gen.generate_embeddings(["x"], salt="v1")[0] == pytest.approx(a)


def test_cache_entries_are_internal_vault_keys(vault):
    from src.data_vault import is_internal_vault_key

    VaultEmbeddingCache(vault).put("ab" * 32, [0.5, 1.5])
    assert all(is_internal_vault_key(k) for k in vault.list_keys())


def test_dimension_change_fails_closed(tmp_path):
    client = FakeClient(dim=8)
    gen, store = _store(client, tmp_path)
    store.add_documents(["one"], [{"storage_key": "a"}])
    client.dim = 16
    with pytest.raises(ChromaStoreError, match="dimension"):
        store.add_documents(["two"], [{"storage_key": "b"}])


def test_stale_reason_on_stamp_mismatch(tmp_path):
    gen, store = _store(FakeClient(), tmp_path)
    assert store.stale_reason() is None
    store.collection.modify(metadata={"chunker_version": "old", "embed_model": "fake-embed"})
    assert "chunker" in store.stale_reason()


def test_add_document_replaces_chunks_of_same_storage_key(tmp_path):
    gen, store = _store(FakeClient(), tmp_path)
    rag = RAGEngine(embedding_generator=gen, chroma_store=store)
    rag.add_document("old text", {"storage_key": "k"})
    rag.add_document("new text", {"storage_key": "k"})
    got = store.get_all()
    assert len(got) == 1 and "new text" in got[0]["content"]


def test_header_has_known_fields_only():
    h = contextual_header({"provider": "P", "category": "gas", "period_start": "2026-03-01", "period_end": "2026-03-31"})
    assert h == "Provider: P | Category: gas | Period: 2026-03-01..2026-03-31"
    assert contextual_header({}) == ""


def test_chunks_are_stored_with_header(tmp_path):
    gen, store = _store(FakeClient(), tmp_path)
    store.add_documents(["Total due $5.00"], [{"storage_key": "k", "provider": "P"}])
    assert store.get_all()[0]["content"].startswith("Provider: P\n")
