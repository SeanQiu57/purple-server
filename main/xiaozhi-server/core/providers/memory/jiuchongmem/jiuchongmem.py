"""
jiuchongmem_provider.py
真正挂给小智框架的 MemoryProvider，只负责：
    ▸ 短 / 工作记忆　→ 本地表 memory_doc
    ▸ 长期记忆　　　 → 调用 lc_mem_store.* 写到 pgvector
"""
import os, re, asyncio, traceback
from datetime import datetime
from typing import List, Sequence
from urllib.parse import quote_plus
from sqlalchemy import (
    create_engine, select, func, text, String, exc as sa_exc
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector
from volcenginesdkarkruntime import Ark
from ..base import MemoryProviderBase, logger          # ↖ 你的框架基类
from .lc_mem_store import (                            # ☆ 仅这一行 import
    MemoryStore,
)
import threading, uvicorn   

TAG = "[JiuchongMem]"

# ────────────────────────  SQLAlchemy Base ────────────────────────
class Base(DeclarativeBase): ...
class MemoryDoc(Base):
    __tablename__ = "memory_doc"
    id:        Mapped[int]      = mapped_column(primary_key=True)
    user_id:   Mapped[str]      = mapped_column(String)
    mem_type:  Mapped[str]      = mapped_column(String)     # short / working
    content:   Mapped[str]      = mapped_column(String)
    created_at:Mapped[datetime] = mapped_column(server_default=func.now())

#（可选）MemoryVec 仍保留；如果已全部用 pgvector 可直接删掉
class MemoryVec(Base):
    __tablename__ = "memory_vec"
    id:        Mapped[int]         = mapped_column(primary_key=True)
    user_id:   Mapped[str]         = mapped_column(String)
    embedding: Mapped[List[float]] = mapped_column(Vector(2560))
    content:   Mapped[str]
    meta:      Mapped[dict]        = mapped_column(JSONB)
    created_at:Mapped[datetime]    = mapped_column(server_default=func.now())

# ────────────────────────  常量配置 ────────────────────────
DB_URL = (
    "postgresql+psycopg2://postgres:"
    f"{quote_plus('sean')}@127.0.0.1:5432/azi_db"
)

# ────────────────────────  Provider 核心 ────────────────────────
class MemoryProvider(MemoryProviderBase):
    provider_name = "jiuchongmem"
    _debug_api_started = False
    @classmethod
    def _ensure_debug_api(cls):
        if cls._debug_api_started:
            return
        from .jiuchongmem_debug_api import app as _debug_app

        def _run():
            uvicorn.run(
                _debug_app,
                host="0.0.0.0",
                port=8081,
                log_level="debug",
                access_log=False
            )

        t = threading.Thread(
            target=_run,
            daemon=True,
            name="JiuchongMemDebugAPI"
        )
        t.start()
        cls._debug_api_started = True

    def __init__(
        self,
        config: dict,
        summary_memory: str | None = None
    ):
        super().__init__(config)
        self.SHORT_KEEP   = int(config.get("short_keep", 20))
        self.LONG_BATCH   = int(config.get("long_batch", 10))
        self.EmbeddingID  = config.get("embedding_model_id", 10)
        self.SECTION_SIZE = 1
        self._summary     = summary_memory or ""

        # 本地 DB：短/工作记忆
        self.engine = create_engine(DB_URL, pool_pre_ping=True)
        Base.metadata.create_all(self.engine)

        # Ark 客户端
        ark_key  = config.get("ARK_API_KEY")
        model_id = config.get("MODEL_ENDPOINT")
        if not ark_key or not model_id:
            raise RuntimeError("ARK_API_KEY / MODEL_ENDPOINT 缺失")
        self.ark_client  = Ark(api_key=ark_key)
        self.ARK_MODEL_ID = model_id
        # 自动启动 Debug API 服务
        self._ensure_debug_api()

    def init_memory(self, role_id, llm, **kwargs):
        super().init_memory(role_id, llm, **kwargs)
        self.store = MemoryStore(
            pg_url       = DB_URL,
            ark_client   = self.ark_client,
            ark_model_id = self.EmbeddingID,
            chunk_size   = 300,
            role_id      = self.role_id,
        )
        logger.info(f"{TAG} init_store ✅ role_id={self.role_id}")

    # ---------- query_memory ----------
    async def query_memory(self, query: str) -> str:
        """使用 build_prompt 构造提示词"""
        prompt = await self.store.build_prompt(
            query=query,
            memory_doc_model=MemoryDoc,
            scene="chat"
        )
        logger.info(f"{TAG} query_memory: {query} -> {prompt}")
        return prompt

    # ------------------------ 写入 ------------------------
    async def save_memory(self, msgs: Sequence):
        """
        只保存「最新一轮」：1 条 user + 若干 assistant 片段。
        假设 msgs 顺序 = [..., user, assistant-seg1, assistant-seg2, ...]
        """
        # 找到最后一条 user
        last_user_idx = next(
            (i for i in range(len(msgs) - 1, -1, -1) if msgs[i].role == "user"), None
        )
        if last_user_idx is None:
            return  # 没有 user，不保存

        # 收集从该 user 到结尾的所有 assistant
        last_turn = msgs[last_user_idx:]
        qa_block = "\n".join(
            f"{m.role.capitalize()}: {m.content}" for m in last_turn
        )

        with Session(self.engine) as s:
            s.add(
                MemoryDoc(
                    user_id=str(self.role_id),
                    mem_type="short",
                    content=qa_block,
                )
            )
            s.commit()

            # 仅统计短期条数，超阈值再弹出
            short_cnt = s.scalar(
                select(func.count()).where(
                    MemoryDoc.user_id == str(self.role_id), MemoryDoc.mem_type == "short"
                )
            )
        if short_cnt > self.SHORT_KEEP:
            asyncio.create_task(self._async_consolidate())
            
    # ------------------------ 后台摘要 + 弹出 ------------------------
    def _consolidate_sync(self):
        """
        将部分短期记忆写入长期存储并生成新的工作记忆。
        触发条件：短期记忆条数 > self.SHORT_KEEP
        """
        try:
            with Session(self.engine) as s:
                # 获取需要合并的短期记忆
                pop_rows = s.scalars(
                    select(MemoryDoc)
                    .where(MemoryDoc.user_id == str(self.role_id), MemoryDoc.mem_type == "short")
                    .order_by(MemoryDoc.id.asc())
                    .limit(self.LONG_BATCH)
                ).all()
                
                if not pop_rows:
                    return
                
                # 构建用于反思的内容
                memories_to_reflect = "\n".join([row.content for row in pop_rows])
                
                # 直接调用 build_prompt 生成工作记忆的提示词
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    user_query = loop.run_until_complete(
                        self.store.build_prompt(
                            query=f"基于以下记忆进行反思和总结：\n{memories_to_reflect}",
                            memory_doc_model=MemoryDoc,
                            scene="workingmem"
                        )
                    )
                finally:
                    loop.close()
                
                resp = self.ark_client.chat.completions.create(
                    model=self.ARK_MODEL_ID,
                    messages=[
                        {"role": "system", "content": "你正在根据记忆进行反思"},
                        {"role": "user", "content": user_query},
                    ],
                )
                self._summary = resp.choices[0].message.content.strip()
                logger.info(
                    f"////////////////new summary for {self.role_id}:\n{self._summary};//////////////query::{user_query}",
                )
                
                # 将短期记忆转为长期记忆
                for row in pop_rows:
                    self.store.add_text(row.content)
                
                # 删除已处理的短期记忆
                for row in pop_rows:
                    s.delete(row)
                
                # 更新/插入新的工作记忆
                s.execute(
                    text(
                        "DELETE FROM memory_doc "
                        "WHERE user_id = :uid AND mem_type = 'working'"
                    ),
                    {"uid": str(self.role_id)},
                )
                s.add(
                    MemoryDoc(
                        user_id=str(self.role_id),
                        mem_type="working",
                        content=self._summary,
                    )
                )
                s.commit()

            logger.info(
                "[Memory] consolidate done for %s | processed=%d records",
                self.role_id, len(pop_rows)
            )

        except Exception as e:
            logger.exception(
                "[Memory] consolidate crash for %s: %s",
                self.role_id, e
            )

    async def _async_consolidate(self):
        await asyncio.to_thread(self._consolidate_sync)