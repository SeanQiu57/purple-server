"""Jiuchongmem provider entry.

Only keeps the three runtime paths used by the framework:
- init_memory
- query_memory
- save_memory
"""
import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Sequence
from urllib.parse import quote_plus
from sqlalchemy import (
    DateTime, Text, create_engine, String, select, func, text, delete
)
from sqlalchemy.dialects.postgresql import JSON, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from langchain.docstore.document import Document
from langchain.embeddings.base import Embeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.vectorstores.pgvector import PGVector
from pgvector.sqlalchemy import Vector
from volcenginesdkarkruntime import Ark
from volcenginesdkarkruntime._exceptions import ArkRateLimitError
from ..base import MemoryProviderBase, logger

TAG = "[JiuchongMem]"
DEFAULT_KNOWLEDGE_USER_ID = "sdt_cleaned"
SUMMARY_MEM_TYPES = ("summary", "reflect")

# ────────────────────────  SQLAlchemy Base ────────────────────────
class Base(DeclarativeBase): ...


class MemoryDoc(Base):
    __tablename__ = "memory_doc"
    id:        Mapped[int]      = mapped_column(primary_key=True)
    user_id:   Mapped[str]      = mapped_column(Text)
    mem_type:  Mapped[str]      = mapped_column(String)
    content:   Mapped[str]      = mapped_column(Text)
    created_at:Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    perception: Mapped[str | None] = mapped_column(Text, nullable=True)


class PromptProfile(Base):
    __tablename__ = "prompt_profile"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    user_id: Mapped[str] = mapped_column(Text)
    conv_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    wm_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    chat_short_keep: Mapped[int | None] = mapped_column(nullable=True)
    chat_kb_k: Mapped[int | None] = mapped_column(nullable=True)
    chat_long_k: Mapped[int | None] = mapped_column(nullable=True)
    wm_short_keep: Mapped[int | None] = mapped_column(nullable=True)
    wm_kb_k: Mapped[int | None] = mapped_column(nullable=True)
    wm_long_k: Mapped[int | None] = mapped_column(nullable=True)
    group: Mapped[str | None] = mapped_column("group", Text, nullable=True)
    pet_nick_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_nick_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    important_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    notifications: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)


class LangchainPgCollection(Base):
    __tablename__ = "langchain_pg_collection"
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    cmetadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    uuid: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)


class LangchainPgEmbedding(Base):
    __tablename__ = "langchain_pg_embedding"
    collection_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(), nullable=True)
    document: Mapped[str | None] = mapped_column(String, nullable=True)
    cmetadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    custom_id: Mapped[str | None] = mapped_column(String, nullable=True)
    uuid: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)


class ArkEmbedding(Embeddings):
    def __init__(self, ark_client: Ark, model_id: str):
        self.client = ark_client
        self.model_id = str(model_id)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        resp = self.client.embeddings.create(model=self.model_id, input=texts)
        return [d.embedding for d in resp.data]

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]


@dataclass
class RetrievedItem:
    document: str
    distance: float
    metadata: dict[str, Any]
    collection_name: str #postgres vector里面的集合名称

# ────────────────────────  Provider 核心 ────────────────────────
class MemoryProvider(MemoryProviderBase):
    provider_name = "jiuchongmem"
    PROFILE_FUNCTION_HANDLERS = {
        "save_profile_names": "_save_profile_names",
        "save_important_info": "_save_important_info",
    }

    def __init__(
        self,
        config: dict,
        summary_memory: str | None = None
    ):
        super().__init__(config)
        self._summary = summary_memory or ""

        selected_llm_name = str(config.get("_selected_llm_name") or "")
        selected_llm_config = config.get("_selected_llm_config") or {}
        selected_llm_url = str(
            selected_llm_config.get("base_url")
            or selected_llm_config.get("url")
            or ""
        )
        can_reuse_ark_llm = (
            "volces" in selected_llm_url
            or "ark" in selected_llm_url
            or selected_llm_name.lower().startswith("doubao")
        )

        self.db_url = self._build_db_url(config)
        self.vector_collection = str(config.get("collection_name") or "jiuchongmemory")
        self.chunk_size = int(config.get("chunk_size") or 300)
        self.vector_batch_size = int(config.get("vector_batch_size") or 156)
        self.min_chunk_length = int(config.get("min_chunk_length") or 60)

        self.ark_api_key = (
            config.get("ark_api_key")
            or config.get("api_key")
            or config.get("ARK_API_KEY")
            or (selected_llm_config.get("api_key") if can_reuse_ark_llm else None)
        )
        self.perception_model_id = (
            config.get("perception_model_id")
            or config.get("model_name")
            or config.get("perception_model_id")
            or (selected_llm_config.get("model_name") if can_reuse_ark_llm else None)
        )
        self.perception_model_id = (
            config.get("perception_model_id")
            or config.get("memory_refine_model_id")
            or self.perception_model_id
        )
        self.function_call_model_id = (
            config.get("function_call_model_id")
            or self.perception_model_id
            or self.perception_model_id
        )
        self.embedding_model_id = config.get("embedding_model_id", 10)
        self.default_kb_user_ids = self._normalize_user_ids(
            config.get("kb_user_ids")
            or config.get("knowledge_user_ids")
            or [config.get("knowledge_user_id", DEFAULT_KNOWLEDGE_USER_ID)]
        )
        self.default_kb_min_items = int(config.get("kb_min_items") or 0)
        self.default_kb_max_distance = float(config.get("kb_max_distance") or 0.25)
        self.default_long_mem_min_items = int(config.get("long_mem_min_items") or 0)
        self.default_long_mem_max_distance = float(config.get("long_mem_max_distance") or 0.25)
        self.default_chat_short_keep = int(config.get("default_chat_short_keep") or 5)
        self.default_chat_kb_k = int(config.get("default_chat_kb_k") or 3)
        self.default_chat_long_k = int(config.get("default_chat_long_k") or 5)
        self.default_compact_pop_batch = int(config.get("compact_pop_batch") or 10)
        self.default_important_notice = str(config.get("important_notice") or "")
        self.function_call_list_path = Path(__file__).with_name("function_call_list.json")
        self._short_migration_running = False

        self.engine = create_engine(self.db_url, pool_pre_ping=True)
        Base.metadata.create_all(self.engine)

        if not self.ark_api_key or not self.perception_model_id:
            raise RuntimeError("jiuchongmem 缺少 ark_api_key/perception_model_id，且无法从当前 LLM 配置复用")
        self.ark_client = Ark(api_key=self.ark_api_key)

    @staticmethod
    def _build_db_url(config: dict) -> str:
        explicit_db_url = config.get("db_url")
        if explicit_db_url:
            return str(explicit_db_url)

        db_config = config.get("db") or {}
        user = str(db_config.get("user") or "postgres")
        password = quote_plus(str(db_config.get("password") or "sean"))
        host = str(db_config.get("host") or "127.0.0.1")
        port = int(db_config.get("port") or 5432)
        database = str(db_config.get("database") or "azi_db")
        return (
            f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"
        )

    def init_memory(self, role_id, llm, **kwargs):
        super().init_memory(role_id, llm, **kwargs)
        self._init_vector_runtime(chunk_size=self.chunk_size)
        logger.info(f"{TAG} init_store ✅ role_id={self.role_id}")

    def _init_vector_runtime(self, chunk_size: int):
        with self.engine.begin() as conn:
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector";'))
            try:
                PGVector.initialize(self.db_url, collection_name=self.vector_collection)
            except Exception:
                pass

        self._mem_embedder = ArkEmbedding(self.ark_client, self.embedding_model_id)
        self._mem_vs = PGVector(
            connection_string=self.db_url,
            collection_name=self.vector_collection,
            embedding_function=self._mem_embedder,
        )
        self._mem_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=0,
        )

    @staticmethod
    def _clean_text(value: str) -> str:
        value = re.sub(r"\r\n|\r", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        value = re.sub(r"[ \t]+", " ", value)
        return value.strip()

    @staticmethod
    def _retry_backoff(fn, *args, retries=5, base_delay=1, **kwargs):
        for attempt in range(1, retries + 1):
            try:
                return fn(*args, **kwargs)
            except ArkRateLimitError:
                if attempt == retries:
                    raise
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(f"{TAG} ArkRateLimitError retry={attempt} delay={delay:.1f}s")
                time.sleep(delay)

    def _add_text_to_long_memory(
        self,
        text_value: str,
        batch_size: int | None = None,
        original_text: str | None = None,
        created_at: datetime | str | None = None,
    ) -> int:
        raw = self._clean_text(text_value)
        original = self._clean_text(original_text or text_value)
        created_at_text = ""
        if isinstance(created_at, datetime):
            created_at_text = created_at.isoformat()
        elif created_at is not None:
            created_at_text = str(created_at).strip()

        metadata = {
            "user_id": str(self.role_id),
            "original": original,
        }
        if created_at_text:
            metadata["created_at"] = created_at_text

        effective_batch_size = batch_size or self.vector_batch_size
        docs = [
            Document(page_content=seg, metadata=metadata)
            for seg in self._mem_splitter.split_text(raw)
            if len(seg) >= self.min_chunk_length
        ]

        total = 0
        for i in range(0, len(docs), effective_batch_size):
            batch = docs[i : i + effective_batch_size]
            if not batch:
                continue
            self._retry_backoff(self._mem_vs.add_documents, batch)
            total += len(batch)
        return total

    def _similarity_search(self, query: str, k: int):
        return self._mem_vs.max_marginal_relevance_search_with_score(
            query,
            k=k,
            fetch_k=30,
            lambda_mult=0.6,
            filter={"user_id": str(self.role_id)},
        )

    def _similarity_search_by_user_id(self, query: str, user_id: str, k: int):
        return self._mem_vs.max_marginal_relevance_search_with_score(
            query,
            k=k,
            fetch_k=30,
            lambda_mult=0.6,
            filter={"user_id": str(user_id)},
        )

    @staticmethod
    def _format_vector(vector: Sequence[float]) -> str:
        return "[" + ",".join(str(item) for item in vector) + "]"

    def _direct_vector_search_by_user_id(self, query: str, user_id: str, k: int):
        embedding = self._retry_backoff(self._mem_embedder.embed_query, query)
        distance_expr = LangchainPgEmbedding.embedding.cosine_distance(embedding).label("distance")

        with Session(self.engine) as s:
            rows = s.execute(
                select(
                    LangchainPgEmbedding.document,
                    LangchainPgEmbedding.cmetadata,
                    distance_expr,
                )
                .where(LangchainPgEmbedding.cmetadata["user_id"].astext == str(user_id))
                .order_by(distance_expr)
                .limit(k)
            ).all()

        results = []
        for row in rows:
            metadata = row.cmetadata or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}
            results.append(
                (
                    Document(page_content=row.document or "", metadata=metadata),
                    float(row.distance or 0.0),
                )
            )
        return results

    @staticmethod
    def _stringify_profile_value(value) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    @staticmethod
    def _normalize_user_ids(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, (list, tuple, set)):
            items = []
            for item in value:
                if item is None:
                    continue
                text = str(item).strip()
                if text:
                    items.append(text)
            return items
        text = str(value).strip()
        return [text] if text else []
   
    # 避免脏数据和无效数据污染
    @staticmethod
    def _coerce_int(value: Any, default: int, min_value: int = 0) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError):
            result = int(default)
        return max(min_value, result)

    @staticmethod
    def _coerce_float(value: Any, default: float, min_value: float = 0.0) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            result = float(default)
        return max(min_value, result)

    @staticmethod
    def format_relative_time(ts: Any) -> str:
        if ts is None:
            return ""
        dt: datetime | None = None
        if isinstance(ts, datetime):
            dt = ts
        elif isinstance(ts, str):
            raw = ts.strip()
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                dt = None
        if dt is None:
            return ""

        if dt.tzinfo is None:
            now = datetime.now()
        else:
            now = datetime.now(dt.tzinfo)

        seconds = max(0, int((now - dt).total_seconds()))
        if seconds < 60:
            return "1分钟前"
        if seconds < 3600:
            return f"{seconds // 60}分钟前"
        if seconds < 86400:
            return f"{seconds // 3600}小时前"

        days = seconds // 86400
        if days < 7:
            return f"{days}天前"
        return f"{days // 7}周前"

    @staticmethod
    def filter_vector_hits(
        hits: list[RetrievedItem],
        max_items: int,
        min_items: int,
        max_distance: float,
    ) -> list[RetrievedItem]:
        if max_items <= 0 and min_items <= 0:
            return []

        sorted_hits = sorted(hits, key=lambda item: item.distance)
        within = [item for item in sorted_hits if item.distance <= max_distance]

        if len(within) >= min_items:
            if max_items <= 0:
                return within
            return within[:max_items]

        target = max(min_items, 0)
        if target == 0:
            return []

        result = list(within)
        existing_keys = {
            (
                item.document,
                item.distance,
                item.collection_name,
                json.dumps(item.metadata, sort_keys=True, ensure_ascii=False),
            )
            for item in result
        }
        for item in sorted_hits:
            key = (
                item.document,
                item.distance,
                item.collection_name,
                json.dumps(item.metadata, sort_keys=True, ensure_ascii=False),
            )
            if key in existing_keys:
                continue
            result.append(item)
            existing_keys.add(key)
            if len(result) >= target:
                break

        return result[:target]

    def _resolve_runtime_params(self, pp: PromptProfile | None, scene: str) -> dict[str, Any]:
        # 当前正式规则：最大召回条数统一使用 prompt_profile.chat_*；wm_* 暂不启用。
        profile_short = pp.chat_short_keep if pp else None
        profile_kb = pp.chat_kb_k if pp else None
        profile_long = pp.chat_long_k if pp else None

        return {
            "short_mem_max_items": self._coerce_int(
                profile_short,
                self.default_chat_short_keep,
                min_value=0,
            ),
            "kb_max_items": self._coerce_int(profile_kb, self.default_chat_kb_k, min_value=0),
            "kb_min_items": self.default_kb_min_items,
            "kb_max_distance": self.default_kb_max_distance,
            "long_mem_max_items": self._coerce_int(profile_long, self.default_chat_long_k, min_value=0),
            "long_mem_min_items": self.default_long_mem_min_items,
            "long_mem_max_distance": self.default_long_mem_max_distance,
            "kb_user_ids": self.default_kb_user_ids,
            "important_notice": self.default_important_notice,
        }

    def query_persona(self, user_id: str) -> PromptProfile:
        with Session(self.engine) as s:
            pp = s.scalar(select(PromptProfile).where(PromptProfile.user_id == str(user_id)))
        if not pp:
            raise ValueError(f"prompt_profile not found for user_id={user_id}")
        return pp

    def query_short_mem(self, user_id: str, short_keep: int) -> list[dict[str, Any]]:
        if short_keep <= 0:
            return []

        with Session(self.engine) as s:
            rows = s.execute(
                select(MemoryDoc.content, MemoryDoc.perception, MemoryDoc.created_at)
                .where(MemoryDoc.user_id == str(user_id), MemoryDoc.mem_type == "short")
                .order_by(MemoryDoc.created_at.desc(), MemoryDoc.id.desc())
                .limit(short_keep)
            ).all()

        ordered = list(reversed(rows))
        use_perception_for_older = short_keep > 3
        latest_raw_cutoff = max(len(rows) - 3, 0)

        return [
            {
                "content": (
                    (row.perception or row.content or "")
                    if use_perception_for_older and index < latest_raw_cutoff
                    else (row.content or "")
                ),
                "created_at": row.created_at,
            }
            for index, row in enumerate(ordered)
        ]

    def query_vector_entries(
        self,
        query_text: str,
        metadata_user_ids: list[str],
        collection_names: list[str] | None,
        max_items: int,
        min_items: int,
        max_distance: float,
    ) -> list[RetrievedItem]:
        user_ids = self._normalize_user_ids(metadata_user_ids)
        if not user_ids:
            return []

        max_items = self._coerce_int(max_items, 0, min_value=0)
        min_items = self._coerce_int(min_items, 0, min_value=0)
        max_distance = self._coerce_float(max_distance, 0.25, min_value=0.0)

        candidate_limit = max(max_items, min_items, 1) * 8
        embedding = self._retry_backoff(self._mem_embedder.embed_query, query_text)
        distance_expr = LangchainPgEmbedding.embedding.cosine_distance(embedding).label("distance")

        stmt = (
            select(
                LangchainPgEmbedding.document,
                LangchainPgEmbedding.cmetadata,
                LangchainPgCollection.name,
                distance_expr,
            )
            .select_from(LangchainPgEmbedding)
            .join(
                LangchainPgCollection,
                LangchainPgEmbedding.collection_id == LangchainPgCollection.uuid,
                isouter=True,
            )
            .where(LangchainPgEmbedding.cmetadata["user_id"].astext.in_(user_ids))
            .order_by(distance_expr.asc())
            .limit(candidate_limit)
        )

        if collection_names:
            stmt = stmt.where(LangchainPgCollection.name.in_(collection_names))

        with Session(self.engine) as s:
            rows = s.execute(stmt).all()

        hits: list[RetrievedItem] = []
        for row in rows:
            metadata = row.cmetadata or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}
            hits.append(
                RetrievedItem(
                    document=(row.document or "").strip(),
                    distance=float(row.distance or 0.0),
                    metadata=metadata if isinstance(metadata, dict) else {},
                    collection_name=str(row.name or ""),
                )
            )

        return self.filter_vector_hits(
            hits=hits,
            max_items=max_items,
            min_items=min_items,
            max_distance=max_distance,
        )
    #可以拓展的接口，现在默认是只是根据config来查config里面名字的知识库，后续可以选择多个知识库
    def query_kb(
        self,
        query_text: str,
        kb_user_ids: list[str],
        max_items: int,
        min_items: int,
        max_distance: float,
    ) -> list[RetrievedItem]:
        return self.query_vector_entries(
            query_text=query_text,
            metadata_user_ids=kb_user_ids,
            collection_names=None,
            max_items=max_items,
            min_items=min_items,
            max_distance=max_distance,
        )

    def query_long_mem(
        self,
        query_text: str,
        user_id: str,
        max_items: int,
        min_items: int,
        max_distance: float,
    ) -> list[RetrievedItem]:
        return self.query_vector_entries(
            query_text=query_text,
            metadata_user_ids=[str(user_id)],
            collection_names=None,
            max_items=max_items,
            min_items=min_items,
            max_distance=max_distance,
        )
    # 把查到的知识库和长期记忆转化为标准的提示词格式
    def render_kb_entries(self, items: list[RetrievedItem]) -> list[str]:
        if not items:
            return ["{assistant: psychology_kb 没有相关的知识库内容。}"]

        entries = []
        for item in items:
            content = item.document.strip()
            if not content:
                continue
            kb_name = str(item.metadata.get("user_id") or item.collection_name or "unknown")
            entries.append(f"{{assistant: psychology_kb[{kb_name}] {content}}}")
        return entries or ["{assistant: psychology_kb 没有相关的知识库内容。}"]

    def render_long_mem_entries(self, items: list[RetrievedItem]) -> list[str]:
        if not items:
            return ["{assistant: long_memory 没有相关的长期记忆。}"]

        entries = []
        for item in items:
            content = item.document.strip()
            if not content:
                continue
            rel_time = self.format_relative_time(item.metadata.get("created_at"))
            if rel_time:
                entries.append(f"{{assistant: long_memory[{rel_time}] {content}}}")
            else:
                entries.append(f"{{assistant: long_memory {content}}}")
        return entries or ["{assistant: long_memory 没有相关的长期记忆。}"]

    def render_persona_entries(self, pp: PromptProfile, scene: str) -> list[str]:
        entries = []
        prompt_text = pp.conv_prompt if scene == "chat" else (pp.wm_prompt or pp.conv_prompt)
        if prompt_text:
            entries.append(f"{{system: {prompt_text.strip()}}}")

        user_nick = self._stringify_profile_value(pp.user_nick_name).strip()
        pet_nick = self._stringify_profile_value(pp.pet_nick_name).strip()
        important_info = self._stringify_profile_value(pp.important_info).strip()
        if user_nick:
            entries.append(f"{{system: 用户昵称是 {user_nick}。}}")
        if pet_nick:
            entries.append(f"{{system: 助手昵称是 {pet_nick}。}}")
        if important_info:
            entries.append(f"{{system: 我记得： {important_info}。}}")
        return entries

    def render_short_mem_entries(self, short_mem_rows: list[dict[str, Any]]) -> list[str]:
        entries = []
        for row in short_mem_rows:
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            rel_time = self.format_relative_time(row.get("created_at"))
            if rel_time:
                entries.append(f"{{assistant: short_memory[{rel_time}] {content}}}")
            else:
                entries.append(f"{{assistant: short_memory {content}}}")
        return entries

    @staticmethod
    def render_user_query_entry(input_text: str, important_notice: str) -> str:
        query_text = str(input_text or "").strip()
        notice = str(important_notice or "").strip()
        if notice:
            return f"{{user: {query_text}（重要指令：{notice}）}}"
        return f"{{user: {query_text}}}"

    async def _build_prompt(self, query: str, scene: str = "chat") -> str:
        uid = str(self.role_id)
        pp = self.query_persona(uid)
        runtime_params = self._resolve_runtime_params(pp, scene)

        kb_items = self.query_kb(
            query_text=query,
            kb_user_ids=runtime_params["kb_user_ids"],
            max_items=runtime_params["kb_max_items"],
            min_items=runtime_params["kb_min_items"],
            max_distance=runtime_params["kb_max_distance"],
        )
        long_items = self.query_long_mem(
            query_text=query,
            user_id=uid,
            max_items=runtime_params["long_mem_max_items"],
            min_items=runtime_params["long_mem_min_items"],
            max_distance=runtime_params["long_mem_max_distance"],
        )
        short_rows = self.query_short_mem(
            user_id=uid,
            short_keep=runtime_params["short_mem_max_items"],
        )

        kb_entries = self.render_kb_entries(kb_items)
        long_entries = self.render_long_mem_entries(long_items)
        persona_entries = self.render_persona_entries(pp, scene)
        short_entries = self.render_short_mem_entries(short_rows)
        user_entry = self.render_user_query_entry(
            input_text=query,
            important_notice=runtime_params["important_notice"],
        )

        blocks = [
            "\n".join(kb_entries),
            "\n".join(long_entries),
            "\n".join(persona_entries),
            "\n".join(short_entries),
            user_entry,
        ]
        non_empty_blocks = [block for block in blocks if block.strip()]
        return "\n\n".join(non_empty_blocks)

    # ---------- query_memory ----------
    async def query_memory(self, query: str) -> str:
        """使用 build_prompt 构造提示词"""
        prompt = await self._build_prompt(query=query, scene="chat")
        logger.info(f"{TAG} query_memory: {query} -> {prompt}")
        return prompt

    def _load_compaction_policy(self) -> tuple[int, int]:
        """从数据库读取短期记忆保留与摘要批次策略。"""
        uid = str(self.role_id)
        with Session(self.engine) as s:
            pp = s.scalar(
                select(PromptProfile).where(PromptProfile.user_id == uid)
            )

        if not pp:
            return 20, self.default_compact_pop_batch

        short_keep = int(pp.chat_short_keep or 20)
        # wm_short_keep 为预留字段，当前不参与正式流程。
        pop_batch = self.default_compact_pop_batch
        return short_keep, pop_batch

    def _compact_short_to_long_sync(self, pop_batch: int | None = None):
        """将最早的短期记忆写入长期向量层，并删除超限的短期记忆。"""

        uid = str(self.role_id)
        short_keep, _ = self._load_compaction_policy()

        while True:
            with Session(self.engine) as s:
                short_cnt = s.scalar(
                    select(func.count()).where(
                        MemoryDoc.user_id == uid,
                        MemoryDoc.mem_type == "short",
                    )
                ) or 0
                overflow = max(0, int(short_cnt) - int(short_keep))
                if overflow <= 0:
                    return

                pop_rows = s.scalars(
                    select(MemoryDoc)
                    .where(MemoryDoc.user_id == uid, MemoryDoc.mem_type == "short")
                    .order_by(MemoryDoc.id.asc())
                    .limit(overflow)
                ).all()

            if not pop_rows:
                return

            pop_row_ids = [int(row.id) for row in pop_rows]
            long_memory_rows = []
            for row in pop_rows:
                long_text = self._clean_text(row.perception or row.content or "")
                if long_text:
                    long_memory_rows.append(
                        {
                            "embedding_text": long_text,
                            "original_text": row.content or "",
                            "created_at": row.created_at,
                        }
                    )

            if not pop_row_ids:
                return

            for item in long_memory_rows:
                self._add_text_to_long_memory(
                    item["embedding_text"],
                    original_text=item["original_text"],
                    created_at=item["created_at"],
                )

            with Session(self.engine) as s:
                delete_result = s.execute(
                    delete(MemoryDoc).where(
                        MemoryDoc.id.in_(pop_row_ids),
                        MemoryDoc.user_id == uid,
                        MemoryDoc.mem_type == "short",
                    )
                )

                s.commit()

                remaining_short_cnt = s.scalar(
                    select(func.count()).where(
                        MemoryDoc.user_id == uid,
                        MemoryDoc.mem_type == "short",
                    )
                ) or 0

            logger.info(
                f"{TAG} compacted short memory deleted={int(delete_result.rowcount or 0)} "
                f"remaining={int(remaining_short_cnt)} keep={int(short_keep)}"
            )

    async def _async_compact_short_to_long(self, pop_batch: int | None = None):
        await asyncio.to_thread(self._compact_short_to_long_sync, pop_batch)

    async def migrate_short_memory_to_long(self, pop_batch: int | None = None):
        """异步迁移入口：短期记忆超限后后台触发，不阻塞主链路。"""
        if self._short_migration_running:
            logger.info(f"{TAG} migrate_short_memory_to_long skipped: migration already running")
            return

        self._short_migration_running = True
        try:
            await self._async_compact_short_to_long(pop_batch)
            logger.info(f"{TAG} migrate_short_memory_to_long done")
        except Exception as exc:
            logger.exception(f"{TAG} migrate_short_memory_to_long failed: {exc}")
        finally:
            self._short_migration_running = False

    @staticmethod
    def _extract_latest_turn(msgs: Sequence) -> tuple[list[Any], str] | tuple[None, None]:
        last_user_idx = next(
            (i for i in range(len(msgs) - 1, -1, -1) if getattr(msgs[i], "role", None) == "user"), None
        )
        if last_user_idx is None:
            return None, None

        last_turn = list(msgs[last_user_idx:])
        user_input = str(getattr(msgs[last_user_idx], "content", "") or "").strip()
        return last_turn, user_input

    @staticmethod
    def _format_turn_content(last_turn: list[Any]) -> str:
        return "\n".join(
            f"{str(getattr(m, 'role', '')).capitalize()}: {str(getattr(m, 'content', '') or '')}"
            for m in last_turn
        )

    def save_memory_to_short(self, msgs: Sequence) -> dict[str, Any] | None:
        """同步短期记忆落库：保存本轮 user+assistant 原文到 memory_doc。"""
        last_turn, user_input = self._extract_latest_turn(msgs)
        if not last_turn:
            return None

        qa_block = self._format_turn_content(last_turn)
        uid = str(self.role_id)

        with Session(self.engine) as s:
            short_doc = MemoryDoc(
                user_id=uid,
                mem_type="short",
                content=qa_block,
                perception=None,
            )
            s.add(short_doc)
            s.commit()
            s.refresh(short_doc)

            short_cnt = s.scalar(
                select(func.count()).where(
                    MemoryDoc.user_id == uid,
                    MemoryDoc.mem_type == "short",
                )
            ) or 0

        return {
            "memory_doc_id": int(short_doc.id),
            "user_input": user_input,
            "qa_block": qa_block,
            "short_cnt": int(short_cnt),
        }

    @staticmethod
    def _safe_parse_json(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            parsed = json.loads(str(raw))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _serialize_debug_value(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, (dict, list, tuple, str, int, float, bool)):
            try:
                return json.dumps(value, ensure_ascii=False, default=str)
            except Exception:
                return str(value)

        for attr in ("model_dump_json", "model_dump", "dict", "to_dict"):
            method = getattr(value, attr, None)
            if not callable(method):
                continue
            try:
                dumped = method()
                if isinstance(dumped, str):
                    return dumped
                return json.dumps(dumped, ensure_ascii=False, default=str)
            except Exception:
                continue

        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return repr(value)

    def _load_function_call_tools(self) -> list[dict[str, Any]]:
        """从 function_call_list.json 读取工具定义并归一化为 OpenAPI tools 结构。"""
        try:
            raw = self.function_call_list_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            logger.warning(f"{TAG} function call list not found: {self.function_call_list_path}")
            return []
        except Exception as exc:
            logger.warning(f"{TAG} read function call list failed: {exc}")
            return []

        if not raw:
            logger.warning(f"{TAG} function call list is empty: {self.function_call_list_path}")
            return []

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(f"{TAG} function call list invalid json: {exc}")
            return []

        if isinstance(payload, dict):
            payload = payload.get("tools") or payload.get("functions") or []
        if not isinstance(payload, list):
            logger.warning(f"{TAG} function call list must be list")
            return []

        tools: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function" and isinstance(item.get("function"), dict):
                tools.append(item)
                continue

            name = str(item.get("name") or "").strip()
            if not name:
                continue
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": str(item.get("description") or ""),
                        "parameters": item.get("parameters") or {
                            "type": "object",
                            "properties": {},
                        },
                    },
                }
            )
        return tools

    def _save_profile_names(self, args: dict[str, Any]) -> bool:
        uid = str(self.role_id)
        user_nick_name = str(args.get("user_nick_name") or "").strip()
        pet_nick_name = str(args.get("pet_nick_name") or "").strip()

        if not user_nick_name and not pet_nick_name:
            logger.warning(
                f"{TAG} function call skipped name=save_profile_names "
                f"reason=no effective fields"
            )
            return False

        with Session(self.engine) as s:
            pp = s.scalar(select(PromptProfile).where(PromptProfile.user_id == uid))
            if not pp:
                logger.warning(f"{TAG} prompt_profile not found for user_id={uid}")
                return False

            if user_nick_name:
                pp.user_nick_name = user_nick_name
            if pet_nick_name:
                pp.pet_nick_name = pet_nick_name

            s.commit()
            logger.info(f"{TAG} function call committed name=save_profile_names user_id={uid}")
            return True

    def _save_important_info(self, args: dict[str, Any]) -> bool:
        uid = str(self.role_id)
        important_text = str(args.get("important_text") or "").strip()
        if not important_text:
            logger.warning(
                f"{TAG} function call skipped name=save_important_info "
                f"reason=no effective fields"
            )
            return False

        with Session(self.engine) as s:
            pp = s.scalar(select(PromptProfile).where(PromptProfile.user_id == uid))
            if not pp:
                logger.warning(f"{TAG} prompt_profile not found for user_id={uid}")
                return False

            pp.important_info = important_text
            s.commit()
            logger.info(f"{TAG} function call committed name=save_important_info user_id={uid}")
            return True

    def _apply_profile_function_call(self, function_name: str, args: dict[str, Any]) -> bool:
        logger.info(
            f"{TAG} applying function call name={function_name} "
            f"args={self._serialize_debug_value(args)} user_id={self.role_id}"
        )

        handler_name = self.PROFILE_FUNCTION_HANDLERS.get(function_name)
        if handler_name is None:
            logger.warning(f"{TAG} unsupported function call name={function_name}")
            return False

        handler = getattr(self, handler_name, None)
        if handler is None:
            logger.warning(
                f"{TAG} missing handler method function_name={function_name} handler={handler_name}"
            )
            return False

        return bool(handler(args))

    @staticmethod
    def _detect_forced_function_name(user_input: str) -> str | None:
        text = str(user_input or "").strip()
        if not text:
            return None

        name_intent_pattern = (
            r"(我的名字叫|我叫|叫我|请记住我的名字|记住我的名字|"
            r"我的昵称是|以后叫我|给我起名)"
        )
        important_intent_pattern = r"(记住这件事|这很重要|请记住|务必记住|重要信息|别忘了)"

        if re.search(name_intent_pattern, text):
            return "save_profile_names"
        if re.search(important_intent_pattern, text):
            return "save_important_info"
        return None

    @staticmethod
    def _extract_user_name_from_input(user_input: str) -> str:
        text = str(user_input or "").strip()
        patterns = [
            r"(?:我的名字叫|我叫|叫我)\s*([\u4e00-\u9fa5A-Za-z0-9_\-]{1,24})",
            r"(?:请记住我的名字(?:是|叫)?)\s*([\u4e00-\u9fa5A-Za-z0-9_\-]{1,24})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return str(match.group(1) or "").strip()
        return ""

    def _fallback_apply_from_user_input(self, user_input: str, forced_function_name: str | None) -> bool:
        if forced_function_name == "save_profile_names":
            user_name = self._extract_user_name_from_input(user_input)
            if not user_name:
                return False
            return self._apply_profile_function_call(
                "save_profile_names",
                {"user_nick_name": user_name},
            )

        if forced_function_name == "save_important_info":
            text_value = str(user_input or "").strip()
            if not text_value:
                return False
            return self._apply_profile_function_call(
                "save_important_info",
                {"important_text": text_value},
            )

        return False

    def _run_profile_function_calls_sync(self, user_input: str) -> None:
        if not user_input:
            logger.info(f"{TAG} function call skipped: empty user_input")
            return

        forced_function_name = self._detect_forced_function_name(user_input)
        if not forced_function_name:
            logger.info(f"{TAG} function call skipped: no explicit memory intent")
            return

        all_tools = self._load_function_call_tools()
        if not all_tools:
            logger.warning(f"{TAG} function call skipped: no tools loaded")
            return

        tools = [
            item
            for item in all_tools
            if str((item.get("function") or {}).get("name") or "").strip() == forced_function_name
        ]
        if not tools:
            logger.warning(
                f"{TAG} function call skipped: target tool not found name={forced_function_name}"
            )
            return

        request_payload = {
            "model": self.function_call_model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是记忆函数路由器。"
                        "当用户输入存在明确记忆意图时，必须输出 tool_calls。"
                        "不允许输出自然语言解释，不允许声称工具不可用。"
                        "昵称信息只调用 save_profile_names；"
                        "重要信息只调用 save_important_info。"
                    ),
                },
                {"role": "user", "content": user_input},
            ],
            "tools": tools,
            "tool_choice": {
                "type": "function",
                "function": {"name": forced_function_name},
            },
        }
        logger.info(
            f"{TAG} function call request={self._serialize_debug_value(request_payload)}"
        )

        try:
            resp = self.ark_client.chat.completions.create(
                model=request_payload["model"],
                messages=request_payload["messages"],
                tools=request_payload["tools"],
                tool_choice=request_payload["tool_choice"],
            )
        except Exception as exc:
            logger.warning(f"{TAG} function call request failed: {exc}")
            return

        logger.info(f"{TAG} function call response={self._serialize_debug_value(resp)}")

        message = None
        try:
            message = resp.choices[0].message
        except Exception:
            message = None
        if not message:
            logger.warning(f"{TAG} function call response has no message")
            return

        tool_calls = getattr(message, "tool_calls", None) or []
        logger.info(
            f"{TAG} function call parsed tool_calls="
            f"{self._serialize_debug_value(tool_calls)}"
        )
        if not tool_calls:
            logger.warning(
                f"{TAG} function call produced no tool_calls content="
                f"{self._serialize_debug_value(getattr(message, 'content', None))}"
            )
            fallback_applied = self._fallback_apply_from_user_input(user_input, forced_function_name)
            if fallback_applied:
                logger.warning(f"{TAG} function call fallback applied function={forced_function_name}")
            else:
                logger.warning(f"{TAG} function call fallback not applied function={forced_function_name}")
            return

        applied_any = False

        for tool_call in tool_calls:
            fn = getattr(tool_call, "function", None)
            if not fn:
                logger.warning(
                    f"{TAG} function call item missing function body="
                    f"{self._serialize_debug_value(tool_call)}"
                )
                continue
            function_name = str(getattr(fn, "name", "") or "").strip()
            if not function_name:
                logger.warning(
                    f"{TAG} function call item missing name body="
                    f"{self._serialize_debug_value(tool_call)}"
                )
                continue
            if function_name != forced_function_name:
                logger.warning(
                    f"{TAG} function name mismatch forced={forced_function_name} returned={function_name}"
                )
                function_name = forced_function_name

            args = self._safe_parse_json(getattr(fn, "arguments", ""))
            applied = self._apply_profile_function_call(function_name, args)
            if applied:
                applied_any = True

        if not applied_any:
            logger.warning(f"{TAG} function call parsed but no valid tool applied, fallback start")
            fallback_applied = self._fallback_apply_from_user_input(user_input, forced_function_name)
            if fallback_applied:
                logger.warning(f"{TAG} function call fallback applied function={forced_function_name}")
            else:
                logger.warning(f"{TAG} function call fallback not applied function={forced_function_name}")

    async def _async_run_profile_function_calls(self, user_input: str) -> None:
        try:
            logger.info(
                f"{TAG} async function-call flow start user_input="
                f"{self._serialize_debug_value(user_input)}"
            )
            await asyncio.to_thread(self._run_profile_function_calls_sync, user_input)
            logger.info(f"{TAG} async function-call flow done")
        except Exception as exc:
            logger.exception(f"{TAG} async function-call flow failed: {exc}")

    def _refine_memory_perception_sync(self, memory_doc_id: int, user_input: str, qa_block: str) -> None:
        if not qa_block:
            return

        try:
            resp = self.ark_client.chat.completions.create(
                model=self.perception_model_id,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是记忆提炼助手。请基于本轮对话输出用于 perception 的精炼结果："
                            "1) 用户话尽量保留原话；"
                            "2) 助手话压缩去废话和语气词；"
                            "3) 人名、地名、关键事实不得丢失；"
                            "4) 输出简洁文本，不要解释。"
                        ),
                    },
                    {"role": "user", "content": f"用户输入：{user_input}\n\n本轮对话：\n{qa_block}"},
                ],
            )
        except Exception as exc:
            logger.warning(f"{TAG} perception request failed: {exc}")
            return

        summary = ""
        try:
            summary = str(resp.choices[0].message.content or "").strip()
        except Exception:
            summary = ""
        if not summary:
            return

        with Session(self.engine) as s:
            row = s.scalar(
                select(MemoryDoc).where(
                    MemoryDoc.id == int(memory_doc_id),
                    MemoryDoc.user_id == str(self.role_id),
                    MemoryDoc.mem_type == "short",
                )
            )
            if not row:
                return
            row.perception = summary
            s.commit()

    async def _async_refine_memory_perception(self, memory_doc_id: int, user_input: str, qa_block: str) -> None:
        try:
            await asyncio.to_thread(
                self._refine_memory_perception_sync,
                memory_doc_id,
                user_input,
                qa_block,
            )
            logger.info(f"{TAG} async perception flow done memory_doc_id={memory_doc_id}")
        except Exception as exc:
            logger.exception(f"{TAG} async perception flow failed: {exc}")

    # ------------------------ 写入 ------------------------
    async def save_memory(self, msgs: Sequence):
        """
        只保存「最新一轮」：1 条 user + 若干 assistant 片段。
        假设 msgs 顺序 = [..., user, assistant-seg1, assistant-seg2, ...]
        """
        result = self.save_memory_to_short(msgs)
        if not result:
            return

        memory_doc_id = int(result["memory_doc_id"])
        user_input = str(result["user_input"])
        qa_block = str(result["qa_block"])
        short_cnt = int(result["short_cnt"])

        asyncio.create_task(
            self._async_refine_memory_perception(
                memory_doc_id=memory_doc_id,
                user_input=user_input,
                qa_block=qa_block,
            )
        )
        logger.info(
            f"{TAG} save_memory scheduling function-call flow "
            f"memory_doc_id={memory_doc_id} short_cnt={short_cnt}"
        )
        asyncio.create_task(self._async_run_profile_function_calls(user_input))

        short_keep, pop_batch = self._load_compaction_policy()
        overflow = max(0, int(short_cnt) - int(short_keep))
        if overflow > 0:
            # 保留 pop_batch 读取是为了兼容旧配置；迁移逻辑只按 overflow 执行。
            _ = pop_batch
            asyncio.create_task(self.migrate_short_memory_to_long())