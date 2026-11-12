"""
Demo-only settings for Infriqa PR Change Pre-Flight Checks.
Intentional CORS + RAG signals so Control Center can detect this PR.
Do not use these values in production.
"""

# CORS / cross-origin policy change (pattern: CORS_ORIGINS / allowed_origins / Access-Control-Allow-Origin)
CORS_ORIGINS = [
    "https://app.infriqa.ai",
    "https://test.aignosticlabs.ai",
    "http://localhost:5173",
]
allowed_origins = CORS_ORIGINS
# Extra explicit CORS header token for Change Intel matcher.
DEFAULT_ACCESS_CONTROL_ALLOW_ORIGIN = "Access-Control-Allow-Origin: https://app.infriqa.ai"

# RAG / vector store dependency (pattern: vector_store / embeddings / chroma / AzureAISearch)
RAG_VECTOR_BACKEND = "chroma"
AzureAISearch = {
    "endpoint": "https://example.search.windows.net",
    "index": "triage-knowledge",
}
vector_store = {
    "provider": "chroma",
    "collection": "triage-knowledge",
    "azure_ai_search": AzureAISearch,
}
embeddings = {
    "model": "text-embedding-3-small",
    "dimensions": 1536,
}

# Sync bump — forces GitHub pull_request synchronize so Change Intel re-ingests.
PREFLIGHT_SYNC_MARKER = "sync-2"
