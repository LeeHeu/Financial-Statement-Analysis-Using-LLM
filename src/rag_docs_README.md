# RAG Knowledge Base — Document Folder

Place documents here before running `rag_ingest.py`.

## Recommended document set (10–20 files)

| # | Doc type | Gợi ý nguồn |
|---|----------|-------------|
| 1–3 | `cfa_framework` | CFA Research Challenge guidelines (cfainstitute.org, public), IOSCO Principles for Sell-Side Research |
| 4–7 | `sample_report` | Báo cáo phân tích ngân hàng VN của SSI Research, VCSC, Mirae Asset (đã công bố công khai) |
| 8–11 | `industry` | Báo cáo ngành ngân hàng VN của World Bank, IMF, StoxPlus, FiinGroup |
| 12–14 | `regulatory` | Thông tư 41/2016/TT-NHNN (CAR), Thông tư 11/2021/TT-NHNN (NPL), Basel III summary |

## Supported formats

`.pdf`, `.md`, `.txt`

## Metadata sidecar (required for filtering)

For each document, create a JSON file with the same name + `.meta.json`:

```
annual_report_vcb_2024.pdf
annual_report_vcb_2024.meta.json   <-- sidecar
```

Example sidecar content:
```json
{
  "title": "VCB Annual Report 2024",
  "doc_type": "sample_report",
  "sector": "banking",
  "language": "vi",
  "publish_date": "2025-03-01",
  "jurisdiction": "VN",
  "url": "https://www.vietcombank.com.vn/...",
  "ticker_scope": "VCB",
  "as_of_date": "2024-12-31"
}
```

### doc_type values

| Value | Meaning |
|-------|---------|
| `cfa_framework` | CFA standards, equity research methodology |
| `sample_report` | Published broker / CTCK research reports |
| `industry` | Sector/industry analysis documents |
| `regulatory` | NHNN, SBV, Basel circulars and guidelines |

Without a sidecar, `doc_type` defaults to `"unknown"` and the document will not
be returned for queries that filter by doc_type.

## Run ingest (from `src/` directory)

```bash
uvx --python 3.12 --with chromadb --with openai --with pdfplumber --with pyyaml \
    python finance_deep_search/rag_ingest.py --docs-dir ./rag_docs --db-path ./rag_kb
```

The DB is written to `src/rag_kb/`. Re-running rebuilds the collection from scratch.

## Notes on document quality

- PDF files scanned as images may have poor OCR — prefer text-layer PDFs
- Vietnamese documents with VNI/TCVN3 encoding should be converted to UTF-8 first
- Remove boilerplate headers/footers if they repeat on every page (they pollute chunks)
- All docs in this folder use the same embedding model (`text-embedding-3-small`)
  — do NOT mix models between ingest runs
