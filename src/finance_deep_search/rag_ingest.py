#!/usr/bin/env python
"""
Ingest documents into ChromaDB for RAG-enhanced CFA report generation.

Each document can have a sidecar metadata file: <filename>.meta.json
Required metadata fields:
  title         - human-readable document title
  doc_type      - one of: cfa_framework | financial_statement | sample_report | industry | regulatory
  sector        - e.g. "banking"
  language      - "vi" | "en"
  publish_date  - "YYYY-MM-DD" or "YYYY"
  jurisdiction  - "VN" | "global"

Optional metadata fields:
  url           - source URL
  ticker_scope  - "generic" or specific ticker e.g. "VCB"
  as_of_date    - data as of date

Supported document types: .pdf, .md, .txt

Usage (run from the src/ directory):
    uvx --python 3.12 --with chromadb --with openai --with pdfplumber --with pyyaml \\
        python finance_deep_search/rag_ingest.py --docs-dir ./rag_docs --db-path ./rag_kb

Re-running rebuilds the collection from scratch (safe to re-run).
"""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

CHUNK_SIZE_CHARS = 2800   # ~700 tokens at 4 chars/token
CHUNK_OVERLAP_CHARS = 336  # ~12% overlap
CHUNK_MIN_CHARS = 600      # skip chunks shorter than this

VALID_DOC_TYPES = {"cfa_framework", "financial_statement", "sample_report", "industry", "regulatory"}


def chunk_text(text: str) -> list[str]:
    """Split text into overlapping chunks, skipping very short ones."""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE_CHARS, len(text))
        chunk = text[start:end].strip()
        if len(chunk) >= CHUNK_MIN_CHARS:
            chunks.append(chunk)
        start += CHUNK_SIZE_CHARS - CHUNK_OVERLAP_CHARS
    return chunks


def extract_text_pdf(path: Path) -> str:
    import pdfplumber
    parts = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                parts.append(text)
    return "\n\n".join(parts)


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_text_pdf(path)
    if suffix in (".md", ".markdown", ".txt"):
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def read_meta(path: Path) -> dict:
    """Load sidecar .meta.json, falling back to minimal defaults."""
    meta_path = path.parent / (path.stem + ".meta.json")
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[ingest] Warning: bad metadata for {path.name}: {e}")
    return {
        "title": path.stem,
        "doc_type": "unknown",
        "sector": "banking",
        "language": "unknown",
        "publish_date": "unknown",
        "jurisdiction": "unknown",
        "ticker_scope": "GENERIC",
    }


def stable_source_id(path_key: str) -> str:
    return "s_" + hashlib.md5(path_key.encode("utf-8")).hexdigest()[:8]


def normalize_ticker_scope(value: object) -> str:
    """
    Normalize sidecar ticker scope into uppercase CSV tokens.
    Examples:
      None -> "GENERIC"
      "VCB, TCB" -> "VCB,TCB"
      "generic" -> "GENERIC"
    """
    if value is None:
        return "GENERIC"

    raw = ""
    if isinstance(value, list):
        raw = ",".join([str(v) for v in value])
    else:
        raw = str(value)

    tokens = [t.strip().upper() for t in re.split(r"[,\s;/|]+", raw) if t.strip()]
    if not tokens:
        return "GENERIC"

    normalized = []
    for tok in tokens:
        if tok in {"GENERIC", "ALL", "*"}:
            normalized.append("GENERIC")
        else:
            normalized.append(tok)

    # stable order + dedupe
    deduped = []
    seen = set()
    for tok in normalized:
        if tok not in seen:
            deduped.append(tok)
            seen.add(tok)
    return ",".join(deduped)


def ingest(docs_dir: str, db_path: str, collection_name: str = "rag_kb"):
    import chromadb
    from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[ingest] ERROR: OPENAI_API_KEY environment variable is not set.")
        sys.exit(1)

    ef = OpenAIEmbeddingFunction(api_key=api_key, model_name="text-embedding-3-small")
    client = chromadb.PersistentClient(path=db_path)

    # Rebuild from scratch on each ingest run
    try:
        client.delete_collection(collection_name)
        print(f"[ingest] Deleted existing collection '{collection_name}'.")
    except Exception:
        pass

    collection = client.create_collection(
        collection_name,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    docs_root = Path(docs_dir)
    if not docs_root.exists():
        print(f"[ingest] ERROR: docs_dir '{docs_dir}' does not exist.")
        sys.exit(1)

    supported = {".pdf", ".md", ".markdown", ".txt"}
    skip_names = {"readme.md", "readme.txt", "readme"}
    files = sorted([
        f for f in docs_root.rglob("*")
        if f.suffix.lower() in supported
        and not f.name.endswith(".meta.json")
        and f.name.lower() not in skip_names
        and not f.name.startswith(".")
    ])

    if not files:
        print(f"[ingest] No supported files found in {docs_dir}.")
        sys.exit(0)

    total_chunks = 0
    source_map: dict[str, dict] = {}
    ingest_quality: dict[str, object] = {
        "files_total": len(files),
        "files_processed": 0,
        "files_skipped_empty": 0,
        "files_failed_extract": 0,
        "unknown_doc_type_files": [],
    }

    for file_path in files:
        print(f"[ingest] Processing: {file_path.name}")
        try:
            text = extract_text(file_path)
        except Exception as e:
            print(f"[ingest]   ERROR extracting text: {e}")
            ingest_quality["files_failed_extract"] = int(ingest_quality["files_failed_extract"]) + 1
            continue

        if not text.strip():
            print(f"[ingest]   Skipping (empty after extraction).")
            ingest_quality["files_skipped_empty"] = int(ingest_quality["files_skipped_empty"]) + 1
            continue

        meta = read_meta(file_path)
        rel_key = str(file_path.relative_to(docs_root)).replace("\\", "/")
        source_id = stable_source_id(rel_key)
        doc_type = str(meta.get("doc_type", "unknown")).strip().lower()
        raw_scope = meta.get("ticker_scope")
        if raw_scope is None and doc_type == "sample_report":
            # sample reports without explicit scope are risky for cross-ticker contamination
            ticker_scope = "UNKNOWN"
        else:
            ticker_scope = normalize_ticker_scope(raw_scope)

        if doc_type not in VALID_DOC_TYPES:
            ingest_quality["unknown_doc_type_files"].append(file_path.name)

        source_map[source_id] = {
            "title": meta.get("title", file_path.stem),
            "url": meta.get("url", ""),
            "date": meta.get("publish_date", ""),
            "doc_type": doc_type,
            "language": meta.get("language", "unknown"),
            "jurisdiction": meta.get("jurisdiction", "unknown"),
            "sector": meta.get("sector", "banking"),
            "ticker_scope": ticker_scope,
        }

        chunks = chunk_text(text)
        if not chunks:
            print(f"[ingest]   Skipping (no valid chunks).")
            continue

        ids, documents, metadatas = [], [], []
        for i, chunk in enumerate(chunks):
            chunk_id = hashlib.md5(f"{rel_key}|{i}".encode("utf-8")).hexdigest()[:16]
            ids.append(chunk_id)
            documents.append(chunk)
            metadatas.append({
                "source_id": source_id,
                "doc_type": doc_type,
                "sector": meta.get("sector", "banking"),
                "language": meta.get("language", "unknown"),
                "jurisdiction": meta.get("jurisdiction", "unknown"),
                "title": meta.get("title", file_path.stem),
                "ticker_scope": ticker_scope,
                "chunk_index": i,
            })

        # Upsert in batches of 100
        for j in range(0, len(ids), 100):
            collection.upsert(
                ids=ids[j:j + 100],
                documents=documents[j:j + 100],
                metadatas=metadatas[j:j + 100],
            )

        total_chunks += len(ids)
        ingest_quality["files_processed"] = int(ingest_quality["files_processed"]) + 1
        print(f"[ingest]   {len(ids)} chunks indexed.")

    # Persist source map alongside the DB
    source_map_path = Path(db_path) / "source_map.json"
    source_map_path.parent.mkdir(parents=True, exist_ok=True)
    source_map_path.write_text(
        json.dumps(source_map, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    quality_path = Path(db_path) / "_ingest_quality.json"
    quality_path.write_text(
        json.dumps(ingest_quality, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n[ingest] Done.")
    print(f"[ingest]   Total chunks : {total_chunks}")
    print(f"[ingest]   Sources      : {len(source_map)}")
    print(f"[ingest]   DB path      : {db_path}")
    print(f"[ingest]   Source map   : {source_map_path}")
    print(f"[ingest]   Quality      : {quality_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest documents into RAG knowledge base.")
    parser.add_argument("--docs-dir", default="./rag_docs",
                        help="Directory containing documents to ingest (default: ./rag_docs)")
    parser.add_argument("--db-path", default="./rag_kb",
                        help="ChromaDB persistent storage path (default: ./rag_kb)")
    parser.add_argument("--collection", default="rag_kb",
                        help="ChromaDB collection name (default: rag_kb)")
    args = parser.parse_args()
    ingest(args.docs_dir, args.db_path, args.collection)
