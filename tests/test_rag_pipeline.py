"""R2/R4/R5/R6: filters, hybrid retrieval, dedupe, context assembly, grounding, grounded chat turns."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from src.ai_engine import grounding as g
from src.ai_engine.chat_engine import NOT_FOUND, ChatEngine
from src.ai_engine.chroma_store import dedupe_versions
from src.ai_engine.embeddings import EmbeddingsGenerator
from src.ai_engine.hybrid import LexicalIndex, query_terms, rrf
from src.ai_engine.omlx_client import OmlxClient
from src.ai_engine.query_analysis import build_rag_filter, parse_relative_date_range
from src.ai_engine.rag_engine import RAGEngine, evidence_id

CTX = ("[Source 1 | Aurora | electricity | 2026-03 | id=ev_0123abcd]\n| Total due | $1,234.50 |\nDue date: 2026-04-24"
       "\n\n[Source 2 | Basalt | gas | 2026-03 | id=ev_89abcdef]\nTotal due $88.04")


# --- query analysis -------------------------------------------------------------------------------
def test_filter_multi_key_uses_and_and_period_overlap():
    assert build_rag_filter("gas", "B") == {"$and": [{"category": "gas"}, {"provider": "B"}]}
    assert build_rag_filter(None, None) is None
    w = build_rag_filter("gas", None, ("2026-01-01", "2026-03-31"))
    assert {"period_end_n": {"$gte": 20260101}} in w["$and"] and {"period_start_n": {"$lte": 20260331}} in w["$and"]


def test_filter_out_of_scope_category_falls_back_to_scope():
    assert build_rag_filter("gas", None, None, ["mobile"]) == {"category": {"$in": ["mobile"]}}


def test_two_months_span_both():
    assert parse_relative_date_range("higher in January 2026 or March 2026?") == ("2026-01-01", "2026-03-31")


# --- hybrid ---------------------------------------------------------------------------------------
def test_bm25_finds_exact_identifier_and_rrf_fuses():
    lex = LexicalIndex(["a", "b", "c"], ["total due 88.04 gas", "total due 12.00 gas", "water bill"])
    assert lex.search("88.04")[0] == "a"
    fused = rrf([["a", "b"], ["b", "c"]])
    assert max(fused, key=fused.get) == "b"


def test_coverage_ignores_months_years_and_generic_words_but_not_unknown_terms():
    lex = LexicalIndex(["a"], ["aurora electric total due"])
    assert query_terms("What was my Aurora Electric bill for March 2026 higher?") == ["aurora", "electric"]
    assert lex.coverage("Aurora Electric bill for March 2026", "a") == 1.0
    assert lex.coverage("passport number", "a") == 0.0


# --- dedupe ---------------------------------------------------------------------------------------
def _d(key, ts, h="h", acct="1", prov="P"):
    return {"content": key, "metadata": {"storage_key": key, "upload_timestamp": ts, "content_hash": h,
                                          "provider": prov, "period_start": "a", "period_end": "b", "account_label": acct}}


def test_dedupe_identical_and_superseded_keep_newest():
    out = dedupe_versions([_d("old", "1", h="x"), _d("re", "2", h="x"), _d("fix", "3", h="y")])
    assert [d["content"] for d in out] == ["fix"]


def test_dedupe_never_merges_different_accounts_or_unlabelled():
    docs = [_d("a", "1", h="x", acct="1"), _d("b", "2", h="y", acct="2"), _d("c", "3", h="z", acct="")]
    docs.append(_d("d", "4", h="w", acct=""))
    assert [d["content"] for d in dedupe_versions(docs)] == ["a", "b", "c", "d"]


# --- context assembly (R5) ------------------------------------------------------------------------
class _Store:
    def __init__(self, chunks):
        self.chunks = chunks

    def retrieve_relevant(self, q, k, where=None):
        return self.chunks

    def last_retrieval(self):
        return self.chunks


def _chunk(key, text, period_end, chunk="1", provider="P"):
    return {"content": text, "score": 0.5, "metadata": {"storage_key": key, "provider": provider, "category": "gas",
                                                           "period_start": period_end[:7] + "-01", "period_end": period_end,
                                                           "chunk": chunk}}


def test_context_blocks_grouped_labelled_ranked_and_shown_newest_first():
    rag = RAGEngine(embedding_generator=MagicMock(), chroma_store=_Store([
        _chunk("old", "OLD1", "2025-01-31"), _chunk("new", "NEW2", "2026-03-31", chunk="2"),
        _chunk("new", "NEW1", "2026-03-31", chunk="1")]))
    ctx = rag.get_context_for_query("q")
    assert ctx.index(evidence_id("new")) < ctx.index(evidence_id("old"))  # newest period first
    assert "Source 1 | P | gas | 2025-01-01..2025-01-31" in ctx or "Source 1 | P | gas | 2025-01-01" in ctx
    assert "NEW1\nNEW2" in ctx  # chunks of one document in chunk order, one block
    assert ctx.count("[Source ") == 2


def test_empty_retrieval_gives_empty_context():
    assert RAGEngine(embedding_generator=MagicMock(), chroma_store=_Store([])).get_context_for_query("q") == ""


@pytest.fixture
def engine(tmp_path):
    from tests.test_chat_engine import make_engine

    return make_engine(tmp_path)


def test_budget_drops_whole_least_relevant_blocks_not_mid_table(engine):
    blocks = [f"[Source {i} | P | id=ev_0000000{i}]\n" + "| row | $1.00 |\n" * 20 for i in (1, 2, 3)]
    out = engine._fit_context_to_budget("\n\n".join(blocks), "q", budget_chars=len(blocks[0]) * 2 + 10)
    assert "Source 1" in out and "Source 2" in out and "Source 3" not in out and "truncated" in out
    assert out.split("[...")[0].rstrip().endswith("|")  # ends on a whole table row


# --- grounding (R6) -------------------------------------------------------------------------------
def test_grounded_answer_passes_with_normalised_figures_and_dates():
    r = g.check("Total $1234.50 [ev_0123abcd], due April 24, 2026.", CTX)
    assert r.ok, r.flagged()


@pytest.mark.parametrize("answer", [
    "Total $1,300.00 [ev_0123abcd].",  # fabricated figure
    "Due 2026-04-25 [ev_0123abcd].",  # fabricated date
    "Total $88.04 [ev_ffffffff].",  # citation that does not exist
    "Total $1,234.50.",  # figure with no citation
])
def test_fabrication_fails_closed(answer):
    assert not g.check(answer, CTX).ok


def test_figure_must_occur_in_the_cited_source():
    assert not g.check("Total $88.04 [ev_0123abcd].", CTX).ok  # exists, but in the other bill


def test_small_counts_ignored_and_no_context_means_nothing_to_check():
    assert g.check("You have 3 bills [ev_0123abcd].", CTX).ok
    assert g.check("anything $5.00", "").ok


# --- chat turn ------------------------------------------------------------------------------------
class _Rag:
    def __init__(self, ctx):
        self.ctx = ctx

    def get_context_for_query(self, query, where=None):
        return self.ctx


class _Client:
    context_window_tokens = 8192
    temperature = 0.7

    def __init__(self, replies):
        self.replies, self.calls, self.temps = list(replies), [], []

    def model_exists(self, m):
        return True

    def chat(self, messages, stream=False, format=None):
        self.calls.append(messages)
        self.temps.append(self.temperature)
        text = self.replies.pop(0)
        if stream:
            return iter([{"message": {"content": text}}])
        return {"message": {"content": text}}


def _engine(tmp_path, ctx, replies):
    e = ChatEngine(ollama_client=_Client(replies), vault_path=str(tmp_path / "v"), encryption_key=os.urandom(32))
    e.rag_engine = _Rag(ctx)
    return e


def test_empty_context_returns_template_without_calling_model(tmp_path):
    e = _engine(tmp_path, "", [])
    assert e.query_vault("q")["response"] == NOT_FOUND
    assert e.ollama_client.calls == []
    assert "".join(e.query_vault_stream("q")) == NOT_FOUND


def test_good_answer_passes_once_at_cold_temperature_and_restores_it(tmp_path):
    e = _engine(tmp_path, CTX, ["It was $88.04 [ev_89abcdef]."])
    out = e.query_vault("q")
    assert out["grounding"]["ok"] and len(e.ollama_client.calls) == 1
    assert e.ollama_client.temps == [0.1] and e.ollama_client.temperature == 0.7


def test_fabricated_answer_regenerates_once_with_flagged_claims(tmp_path):
    e = _engine(tmp_path, CTX, ["It was $99.99 [ev_89abcdef].", "It was $88.04 [ev_89abcdef]."])
    out = e.query_vault("q")
    assert out["grounding"] == {"ok": True, "flagged": [], "regenerated": True}
    assert "$99.99" in e.ollama_client.calls[1][-1]["content"] and "Could not verify" not in out["response"]


def test_still_fabricated_after_retry_gets_visible_note(tmp_path):
    e = _engine(tmp_path, CTX, ["$99.99 [ev_89abcdef]", "$77.77 [ev_89abcdef]"])
    out = e.query_vault("q")
    assert len(e.ollama_client.calls) == 2 and "Could not verify" in out["response"] and "$77.77" in out["response"]


def test_stream_appends_note_after_fabricated_answer(tmp_path):
    e = _engine(tmp_path, CTX, ["It was $99.99 [ev_89abcdef]."])
    text = "".join(e.query_vault_stream("q"))
    assert text.startswith("It was $99.99") and "Could not verify" in text


def test_turn_diagnostics_hold_ids_and_scores_not_text(tmp_path):
    e = _engine(tmp_path, CTX, ["$99.99 [ev_89abcdef]", "$99.99 [ev_89abcdef]"])
    e.query_vault("what is my secret question")
    from src.agents.store import list_activity

    entry = [a for a in list_activity(e.vault) if a.get("kind") == "rag_turn"][0]
    assert entry["grounded"] is False and entry["regenerated"] is True
    assert "secret" not in json.dumps(entry) and "99.99" not in json.dumps(entry)


# --- embeddings client / prefix -------------------------------------------------------------------
@patch("urllib.request.urlopen")
def test_omlx_batch_embeddings_one_call_ordered_by_index(mock_urlopen, monkeypatch):
    for var in ("omlx_api_key", "OMLX_API_KEY"):  # a key switches the client to its own opener (no urlopen)
        monkeypatch.delenv(var, raising=False)
    resp = MagicMock()
    resp.read.return_value = json.dumps({"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}).encode()
    mock_urlopen.return_value.__enter__.return_value = resp
    out = OmlxClient(model="m").generate_embeddings(["a", "b"])
    assert out == [[1.0], [2.0]] and mock_urlopen.call_count == 1
    assert json.loads(mock_urlopen.call_args.args[0].data)["input"] == ["a", "b"]


def test_qwen_queries_get_instruction_documents_do_not():
    c = MagicMock(model="mlx-community/Qwen3-VL-Embedding-2B-8bit")
    gen = EmbeddingsGenerator(client=c)
    assert gen.query_prefix.startswith("Instruct:") and gen.query_prefix.endswith("Query: ")
    assert gen.document_prefix == ""
