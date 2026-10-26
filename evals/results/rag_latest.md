# RAG eval (synthetic corpus, hashed-BoW stand-in embedder, offline)

44 docs, 81 questions. `baseline` = frozen pre-fix run (evals/rag/baseline.json). Absolutes are not the real model's; read deltas.

| metric | baseline | legacy | r0_packed | r2_header_period | r4_hybrid | final |
|---|---|---|---|---|---|---|
| recall@5 | 0.4453 | 0.4453 | 0.5622 | 1.0 | 1.0 | 1.0 |
| mrr | 0.241 | 0.241 | 0.3348 | 0.9627 | 0.9925 | 0.9925 |
| context_precision | 0.1134 | 0.1134 | 0.146 | 0.9129 | 0.9303 | 0.9303 |
| context_has_answer | 0.0 | 0.0 | 0.5075 | 1.0 | 1.0 | 1.0 |
| abstention | 0.2857 | 0.2857 | 0.4286 | 0.7143 | 1.0 | 1.0 |
| false_abstain | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| stale_in_context | 0.0 | 0.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| dup_in_context | 0.0 | 0.0 | 0.6667 | 1.0 | 0.0 | 0.0 |
| faithfulness | - | - | 1.0 | 1.0 | 1.0 | 1.0 |
| fabrication_caught | - | - | 1.0 | 0.9851 | 0.9851 | 0.9851 |
| avg_retrieval_ms | 1.46 | 1.77 | 0.81 | 0.64 | 0.8 | 0.86 |
| avg_context_chars | 444 | 528 | 2944 | 915 | 861 | 861 |
| index_embed_calls | 573 | 573 | 44 | 44 | 44 | 44 |
| index_http_calls | - | 18 | 2 | 2 | 2 | 2 |

Gates on `final`: recall@5 >= 0.95, faithfulness >= 0.98, fabrication_caught >= 0.98, abstention >= 0.95, context_has_answer >= 0.95, false_abstain <= 0.05, stale_in_context <= 0.0, dup_in_context <= 0.0
**Overall: PASS**
