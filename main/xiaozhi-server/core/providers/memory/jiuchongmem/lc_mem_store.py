"""
lc_mem_store.py
封装 PGVector + ArkEmbedding 的最小实现，提供 4 个外部可用函数：
    ▸ init_store(pg_url, ark_client, ark_model_id, chunk_size, role_id)
    ▸ add_text(text: str) -> int
    ▸ similarity_search(query: str, k: int = 5)
    ▸ clear_all(pg_url: str, role_id: str) -> int
"""

from __future__ import annotations
import logging, re
from typing import List, Tuple, Any
import time
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from langchain.docstore.document import Document
from langchain.embeddings.base import Embeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.vectorstores.pgvector import PGVector
from volcenginesdkarkruntime import Ark
from volcenginesdkarkruntime._exceptions import ArkRateLimitError

# ───────────────────────────── Logger
_logger = logging.getLogger("lc_mem")
_logger.setLevel(logging.DEBUG)
if not _logger.handlers:
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _logger.addHandler(sh)

# ───────────────────────────── 全局状态
TABLE      = "jiuchongmemory"
_vs        = None          # type: PGVector | None
_splitter  = None          # type: RecursiveCharacterTextSplitter | None
_ROLE      = None          # 当前 user / device id

# ───────────────────────────── Embedding 适配
class ArkEmbedding(Embeddings):
    def __init__(self, ark_client: Ark, model_id: str):
        self.client = ark_client
        self.model_id = str(model_id)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        resp = self.client.embeddings.create(model=self.model_id, input=texts)
        return [d.embedding for d in resp.data]

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]

# ───────────────────────────── 初始化
class MemoryStore:
    def __init__(
        self,
        pg_url: str,
        ark_client: Ark,
        ark_model_id: str,
        chunk_size: int,
        role_id: str,
    ):
        engine = create_engine(pg_url)
        with engine.begin() as conn:
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector";'))
            try:
                PGVector.initialize(pg_url, collection_name="jiuchongmemory")
            except Exception:
                pass

        self.role_id = str(role_id)
        self.embedder = ArkEmbedding(ark_client, ark_model_id)
        self.vs = PGVector(
            connection_string=pg_url,
            collection_name="jiuchongmemory",
            embedding_function=self.embedder,
        )
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=0
        )
        _logger.info("MemoryStore 初始化完毕，role_id=%s", self.role_id)

    # ────────────────── 工具函数 ──────────────────
    def _clean(self, text: str) -> str:
        text = re.sub(r"\r\n|\r", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()

    def _retry_backoff(self, fn, *args, retries=5, base_delay=1, **kwargs):
        for attempt in range(1, retries + 1):
            try:
                return fn(*args, **kwargs)
            except ArkRateLimitError:
                if attempt == retries:
                    raise
                delay = base_delay * (2 ** (attempt - 1))
                _logger.warning(
                    "ArkRateLimitError, retry #%d in %.1fs…", attempt, delay
                )
                time.sleep(delay)

    # ────────────────── 外部 API ──────────────────
    def add_text(self, text: str, batch_size: int = 156) -> int:
        raw = self._clean(text)
        docs = [
            Document(page_content=seg, metadata={"user_id": self.role_id})
            for seg in self.splitter.split_text(raw)
            if len(seg) >= 60
        ]

        total = 0
        for i in range(0, len(docs), batch_size):
            batch = docs[i : i + batch_size]
            if not batch:
                continue
            _logger.info("写入第 %d 批，共 %d 段", i // batch_size + 1, len(batch))
            self._retry_backoff(self.vs.add_documents, batch)
            total += len(batch)
        return total

    def similarity_search(self, query: str, k: int):
        return self.vs.max_marginal_relevance_search_with_score(
            query,
            k=k,
            fetch_k=30,
            lambda_mult=0.6,
            filter={"user_id": self.role_id},
        )

    def similarity_search_by_name(self, query: str, role_id: str, k: int):
        return self.vs.max_marginal_relevance_search_with_score(
            query,
            k=k,
            fetch_k=30,
            lambda_mult=0.6,
            filter={"user_id": str(role_id)},
        )

    @staticmethod
    def clear_all(pg_url: str, role_id: str) -> int:
        engine = create_engine(pg_url)
        with engine.begin() as conn:
            res = conn.execute(
                text(
                    "DELETE FROM langchain_pg_embedding "
                    "WHERE cmetadata->>'user_id' = :uid"
                ),
                {"uid": role_id},
            )
        return res.rowcount