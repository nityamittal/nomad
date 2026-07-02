from .assembler import ContextAssembler
from .embeddings import OllamaEmbeddings, SemanticIndex, VectorStore, chunk_text, cosine
from .indexer import FileIndex
from .retrieval import SearchHit, keyword_search

__all__ = [
    "ContextAssembler",
    "FileIndex",
    "SearchHit",
    "keyword_search",
    "OllamaEmbeddings",
    "SemanticIndex",
    "VectorStore",
    "chunk_text",
    "cosine",
    "build_assembler",
]


def build_assembler(cfg, trace=None) -> ContextAssembler:
    """Assembler from config: keyword always; semantic only if an index was
    built (`nomad --index`) so we never block on a missing embedding model."""
    index = FileIndex(cfg.project_root)
    semantic = None
    db_path = cfg.state_path / "index" / "vectors.db"
    if db_path.is_file():
        store = VectorStore(db_path)
        if store.count() > 0:
            embedder = OllamaEmbeddings(cfg.context.embedding_model, cfg.model.base_url)
            semantic = SemanticIndex(store, embedder)
    return ContextAssembler(
        index, cfg.context.retrieval_token_budget, semantic=semantic, trace=trace
    )
