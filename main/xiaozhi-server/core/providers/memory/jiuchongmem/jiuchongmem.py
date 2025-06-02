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
        """拼 prompt：短记忆 + 工作记忆 + 长记忆(similarity_search)"""
        with Session(self.engine) as s:
            shorts = s.scalars(
                select(MemoryDoc.content)
                .where(MemoryDoc.user_id == str(self.role_id),
                       MemoryDoc.mem_type == 'short')
                .order_by(MemoryDoc.id.desc())
                .limit(self.SHORT_KEEP)
            ).all()
            working = s.scalars(
                select(MemoryDoc.content)
                .where(MemoryDoc.user_id == str(self.role_id),
                       MemoryDoc.mem_type == 'working')
                .order_by(MemoryDoc.id.desc())
            ).first()
        # ☆ 长期记忆检索
        hidden_memory_docs = self.store.similarity_search_by_name(query,"sdt-practice", k=2)
        hidden_memory = "\n".join(f"- {doc.page_content}" for doc, _ in hidden_memory_docs)
        long_docs = self.store.similarity_search(query, k=3)
        long_memory = "\n".join(f"- {doc.page_content}" for doc, _ in long_docs)    
        short_memory = "\n".join(shorts[::-1])
        prompt = (
            f"【短期记忆】\n{short_memory}\n\n"
            f"【SDT理论和实践的一些想法】\n{working}\n\n"
            f"【长期记忆-突然想起你和主人之前的对话】\n{long_memory}\n\n"
            f"【当前主人问你的话】\n{query}\n\n"
            f"【潜意识】：一些SDT理论在无意识中指导你的对话\n{hidden_memory}\n"
        )
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
                # ① 取出该用户所有短期记忆（按 id 升序 = 时间顺序）
                shorts_all: list[MemoryDoc] = s.scalars(
                    select(MemoryDoc)
                    .where(
                        MemoryDoc.user_id == str(self.role_id),
                        MemoryDoc.mem_type == "short",
                    )
                    .order_by(MemoryDoc.id.asc())
                ).all()

                # ② 计算需要“弹出”写入长期记忆的条目
                pop_rows = shorts_all[: self.LONG_BATCH] if len(shorts_all) > self.SHORT_KEEP else []
                remaining_short = shorts_all[len(pop_rows) :]      # 保留下来的短期记忆

                # ③ 先把 short_block 设为空，保证后续引用安全
                short_block = ""
                if remaining_short:
                    short_block = "\n".join(
                        r.content for r in remaining_short[-self.SHORT_KEEP :]
                    )

                # ④ 写 pop_rows 到向量库（每 SECTION_SIZE 条打包）
                for i in range(0, len(pop_rows), self.SECTION_SIZE):
                    batch = pop_rows[i : i + self.SECTION_SIZE]
                    text_block = "\n".join(r.content for r in batch)
                    if text_block.strip():
                        self.store.add_text(text_block)

                # ⑤ 删除已写入的 pop_rows，只 commit 一次
                if pop_rows:
                    for r in pop_rows:
                        s.delete(r)
                    s.commit()

                # ⑥ 准备 query、检索长期记忆
                query = short_block + self._summary

                #   - SDT 理论/实践 文献召回
                sdt_theory_docs = self.store.similarity_search_by_name(
                    query, "sdt-theory", k=5
                )
                sdt_theory_memory = "\n".join(f"- {doc.page_content}" for doc, _ in sdt_theory_docs)

                sdt_practice_docs = self.store.similarity_search_by_name(
                    query, "sdt-practice", k=5
                )
                sdt_practice_memory = "\n".join(f"- {doc.page_content}" for doc, _ in sdt_practice_docs)

                #   - 当前用户自己的长期记忆
                long_docs = self.store.similarity_search(query, k=50)
                long_memory = "\n".join(f"- {doc.page_content}" for doc, _ in long_docs)

                # ⑦ 构造 prompt 让 LLM 生成新工作记忆
                user_query = (
                    f"""请严格按照以下结构与要求，生成高质量、有信息密度的输出内容：

                ---

                ### 部分一：迭代更新工作记忆（限1500字）

                请迭代更新上一次生成的工作记忆，之前的内容为：
                “{self._summary}”

                基于以下给出的信息进行更新：

                * **用户与阿紫最近的对话摘要（短期记忆）**：
                “{short_block}”

                * **检索到的SDT（自我决定理论）相关理论章节**：
                “{sdt_theory_memory}”

                #### 生成要求：

                * 仅基于SDT理论进行分析，禁止引入其他理论或个人主观发挥。
                * 仅当用户在对话中明确展现出具体问题或心理机制时，才生成对应的解释分析；如无具体问题暴露，可不生成。
                * 表述必须精炼，用最少的字表达最多的信息。
                * 严格使用如下结构化编号格式：

                理论解释一：……
                理论解释二：……
                理论依据：……

                ---

                ### 部分二：SDT理论的实践应用建议

                请根据SDT理论中的“实践应用”章节内容：
                “{sdt_practice_memory}”

                结合用户与阿紫之间的实际对话内容，提供切实可行的实践建议。

                #### 生成要求：

                * 仅限SDT理论推导，不允许凭空发挥或引用其他理论。
                * 如果SDT理论本身不足以提供明确支持，则可跳过该部分，不必强行生成。
                * 必须严格遵循如下格式进行输出：

                建议一（简要陈述）：……
                实践解释：说明该建议如何具体落地，如何在实际对话中体现。

                建议二（简要陈述）：……
                实践解释：说明该建议如何具体落地，如何在实际对话中体现。

                （根据实际情况依次类推）

                ---

                ### 部分三：根据记忆总结你的性格特质（600字左右）

                请综合参考以下信息：

                * 【当前短期记忆】
                * 【相关长期记忆】
                “{long_memory}”

                客观且深入地总结你的性格和特质。

                示例格式：
                我自己的性格：内向、细心、善于倾听，喜欢独立思考和深度分析问题……我曾经……

                ---

                ### 整体输出示例

                基于当前对话，可以使用的SDT理论：
                理论解释一：
                理论解释二：
                理论依据：

                基于对话，可以根据SDT提出的实践建议：
                建议一（简要陈述）：……
                实践解释：……

                建议二（简要陈述）：……
                实践解释：……

                我自己的性格：……
                """
                )

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
                # ⑧ 更新/插入新的工作记忆
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
                "[Memory] consolidate done for %s | pop=%d, remain=%d",
                self.role_id, len(pop_rows), len(remaining_short)
            )

        except Exception as e:
            logger.exception(
                "[Memory] consolidate crash for %s: %s",
                self.role_id, e, exc_info=True
            )


    async def _async_consolidate(self):
        await asyncio.to_thread(self._consolidate_sync)