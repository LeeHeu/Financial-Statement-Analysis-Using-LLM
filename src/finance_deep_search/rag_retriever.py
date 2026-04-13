#!/usr/bin/env python
"""
RAG Retriever — query the ChromaDB knowledge base and write retrieved_context_{ticker}.json.

This is a pure Python script (no LLM). It runs per section defined in rag_sections.yaml,
merges + deduplicates results across multiple queries, re-ranks by cosine score, and
keeps only top_k chunks per section.

Output format:
  {
    "ticker": "VCB",
    "as_of_date": "2026-03-07",
    "sections": {
      "business_model": [{"source_id": "s_abc", "chunk": "...", "score": 0.87}],
      ...
    },
    "sources": {
      "s_abc": {"title": "...", "url": "...", "date": "...", "doc_type": "..."},
      ...
    }
  }

Scores are cosine similarity in [0, 1]. Scores are NOT comparable across sections
(each section uses different queries).

Usage (run from the src/ directory):
    uvx --python 3.12 --with chromadb --with openai --with pyyaml \\
        python finance_deep_search/rag_retriever.py <TICKER> <OUTPUT_DIR> <DB_PATH> <SECTIONS_YAML>
"""

import json
import os
import re
import sys
from datetime import date
from pathlib import Path


def load_sections(sections_yaml: str) -> dict:
    import yaml
    with open(sections_yaml, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("sections", {})


def _parse_scope_tokens(scope_value: object) -> set[str]:
    if scope_value is None:
        return set()
    raw = str(scope_value).strip()
    if not raw:
        return set()
    return {tok.strip().upper() for tok in re.split(r"[,\s;/|]+", raw) if tok.strip()}


def _scope_match(scope_value: object, ticker_upper: str, doc_type: str) -> bool:
    """
    Hard filter to prevent cross-ticker quantitative contamination.
    - sample_report requires explicit ticker scope match (or GENERIC)
    - industry/regulatory/cfa_framework can be generic even if scope is missing
    """
    tokens = _parse_scope_tokens(scope_value)
    if ticker_upper in tokens:
        return True
    if "GENERIC" in tokens or "ALL" in tokens or "*" in tokens:
        return True
    if not tokens and doc_type in {"industry", "regulatory", "cfa_framework"}:
        return True
    return False


def _scope_soft_fallback(scope_value: object, doc_type: str) -> bool:
    """
    Fallback policy if strict filtering returns too few chunks.
    Allow unknown scope only for framework/industry/regulatory.
    Never fallback sample_report with unknown or mismatched scope.
    """
    tokens = _parse_scope_tokens(scope_value)
    if tokens:
        return False
    return doc_type in {"industry", "regulatory", "cfa_framework"}


def retrieve(ticker: str, output_dir: str, db_path: str, sections_yaml: str):
    import chromadb
    from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[rag_retriever] ERROR: OPENAI_API_KEY environment variable is not set.")
        sys.exit(1)

    ef = OpenAIEmbeddingFunction(api_key=api_key, model_name="text-embedding-3-small")

    try:
        client = chromadb.PersistentClient(path=db_path)
        collection = client.get_collection("rag_kb", embedding_function=ef)
    except Exception as e:
        print(f"[rag_retriever] ERROR: could not open collection 'rag_kb' at '{db_path}': {e}")
        sys.exit(1)

    total_docs = collection.count()
    if total_docs == 0:
        print("[rag_retriever] WARNING: knowledge base is empty. Run rag_ingest.py first.")
        _write_empty(ticker, output_dir)
        return

    print(f"[rag_retriever] Collection has {total_docs} chunks.")

    # Load source map for citation metadata
    source_map: dict = {}
    source_map_path = Path(db_path) / "source_map.json"
    if source_map_path.exists():
        source_map = json.loads(source_map_path.read_text(encoding="utf-8"))

    sections_cfg = load_sections(sections_yaml)
    ticker_upper = ticker.upper()

    result = {
        "ticker": ticker,
        "as_of_date": str(date.today()),
        "sections": {},
        "sources": {},
    }

    for section_name, cfg in sections_cfg.items():
        queries: list[str] = cfg.get("queries", [])
        top_k: int = cfg.get("top_k", 3)
        filter_doc_types: list[str] = cfg.get("filter_doc_types", [])

        # Collect all candidate chunks across all queries, dedup by chunk id
        strict_candidates: dict[str, dict] = {}    # chunk_id -> {source_id, chunk, score}
        fallback_candidates: dict[str, dict] = {}  # chunk_id -> {source_id, chunk, score}

        for query in queries:
            where = {"doc_type": {"$in": filter_doc_types}} if filter_doc_types else None

            # ChromaDB raises if n_results > collection size
            n = min(max(top_k * 8, top_k + 12), total_docs)
            try:
                res = collection.query(
                    query_texts=[query],
                    n_results=n,
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
            except Exception as e:
                print(f"[rag_retriever]   Warning: query failed (section={section_name}): {e}")
                continue

            ids_batch = res.get("ids", [[]])[0]
            docs_batch = res.get("documents", [[]])[0]
            metas_batch = res.get("metadatas", [[]])[0]
            dists_batch = res.get("distances", [[]])[0]

            for chunk_id, doc, meta, dist in zip(ids_batch, docs_batch, metas_batch, dists_batch):
                source_id = meta.get("source_id", "unknown")
                source_meta = source_map.get(source_id, {})
                score = round(1.0 - dist, 4) if dist is not None else None
                doc_type = str(meta.get("doc_type") or source_meta.get("doc_type") or "unknown").lower()
                scope_value = meta.get("ticker_scope")
                if scope_value is None:
                    scope_value = source_meta.get("ticker_scope")

                entry = {
                    "source_id": source_id,
                    "chunk": doc[:1200],  # cap per chunk to control prompt size
                    "score": score,
                }

                target = None
                if _scope_match(scope_value, ticker_upper, doc_type):
                    target = strict_candidates
                elif _scope_soft_fallback(scope_value, doc_type):
                    target = fallback_candidates
                else:
                    continue

                if chunk_id in target:
                    existing_score = target[chunk_id]["score"] or 0
                    new_score = entry["score"] or 0
                    if new_score > existing_score:
                        target[chunk_id]["score"] = new_score
                else:
                    target[chunk_id] = entry

                if source_id not in result["sources"] and source_id in source_map:
                    result["sources"][source_id] = source_map[source_id]
                elif source_id not in result["sources"]:
                    result["sources"][source_id] = {
                        "title": meta.get("title", ""),
                        "url": "",
                        "date": "",
                        "doc_type": doc_type,
                        "language": meta.get("language", ""),
                        "jurisdiction": meta.get("jurisdiction", ""),
                        "sector": meta.get("sector", ""),
                        "ticker_scope": scope_value,
                    }

        # Sort strict candidates first; only use fallback if strict is insufficient
        ranked_strict = sorted(strict_candidates.values(), key=lambda x: x.get("score") or 0, reverse=True)
        selected = list(ranked_strict[:top_k])
        if len(selected) < top_k:
            ranked_fallback = sorted(fallback_candidates.values(), key=lambda x: x.get("score") or 0, reverse=True)
            needed = top_k - len(selected)
            selected.extend(ranked_fallback[:needed])

        result["sections"][section_name] = selected
        print(f"[rag_retriever]   {section_name}: {len(result['sections'][section_name])} chunks "
              f"(strict={len(strict_candidates)}, fallback={len(fallback_candidates)})")

    out_path = Path(output_dir) / f"retrieved_context_{ticker}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[rag_retriever] Saved to {out_path}")


def _write_empty(ticker: str, output_dir: str):
    """Write an empty context file so the pipeline can continue gracefully."""
    out_path = Path(output_dir) / f"retrieved_context_{ticker}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"ticker": ticker, "as_of_date": str(date.today()),
                    "sections": {}, "sources": {}},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[rag_retriever] Wrote empty context to {out_path}")


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: python rag_retriever.py <TICKER> <OUTPUT_DIR> <DB_PATH> <SECTIONS_YAML>")
        sys.exit(1)
    retrieve(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
