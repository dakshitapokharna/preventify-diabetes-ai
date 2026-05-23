# ingestion/embedder/
# Reads chunks from data/chunks/*.jsonl, embeds with bge-large-en-v1.5,
# upserts to pgvector table preventify_corpus on Neon (PostgreSQL).
