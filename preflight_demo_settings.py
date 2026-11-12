"""
Demo-only settings for Infriqa PR Change Pre-Flight Checks.
Intentional CORS + RAG signals so Control Center can detect this PR.
Do not use these values in production.
"""

# CORS / cross-origin policy change (pattern: CORS_ORIGINS / allowed_origins)
CORS_ORIGINS = [
    "https://app.infriqa.ai",
    "https://test.aignosticlabs.ai",
    "http://localhost:5173",
]
allowed_origins = CORS_ORIGINS

# RAG / vector store dependency (pattern: vector_store / embeddings / chroma)
RAG_VECTOR_BACKEND = "chroma"
vector_store = {
    "provider": "chroma",
    "collection": "triage-knowledge",
}
embeddings = {
    "model": "text-embedding-3-small",
    "dimensions": 1536,
}
