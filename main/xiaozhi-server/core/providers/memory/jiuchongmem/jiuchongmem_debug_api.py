# jiuchongmem_debug_api.py
import os, uvicorn, logging, threading
from typing import List, Optional, Dict
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from sqlalchemy import (
    create_engine, select,
    BigInteger, String, Text, DateTime, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from volcenginesdkarkruntime import Ark

# ★ 新：引入 MemoryStore，而不是全局函数
from .lc_mem_store import MemoryStore

LOGGER = logging.getLogger("jiuchongmem_api")
LOGGER.setLevel(logging.INFO)
if not LOGGER.handlers:
    LOGGER.addHandler(logging.StreamHandler())

TAG = "[JiuchongMem-API]"

# ───────────────── Configuration ─────────────────
DB_URL        = "postgresql+psycopg2://postgres:sean@127.0.0.1:5432/azi_db"

# —— 火山 Vulcengine 配置（硬编码） —— 
ARK_API_KEY   = "8b4e1f4a-c8eb-46dd-9d69-47e248988770"
ARK_MODEL_ID  = "ep-20250308190107-tghm7"

# 默认调试用户
DEFAULT_ROLE  = "debug"

# ───────────────────────── MemoryDoc 定义 ─────────────────────────
class DebugBase(DeclarativeBase):
    pass

class MemoryDoc(DebugBase):
    __tablename__ = "memory_doc"
    id:         Mapped[int]      = mapped_column(primary_key=True)
    user_id:    Mapped[int]      = mapped_column(BigInteger, nullable=False)
    mem_type:   Mapped[str]      = mapped_column(String(12), nullable=False)
    content:    Mapped[str]      = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

_engine = create_engine(DB_URL)

# ─────────────────── FastAPI + Lifespan ───────────────────
app = FastAPI(title="九重Memory 调试 API", version="2.0.0")

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/debug-ui", StaticFiles(directory=static_dir, html=True), name="debug-ui")

# Ark 单例
_ark_client = Ark(api_key=ARK_API_KEY)

# ★ 新：按 user_id 缓存 MemoryStore
_STORES: Dict[str, MemoryStore] = {}
_STORE_LOCK = threading.Lock()

def get_store(user_id: str) -> MemoryStore:
    """
    线程安全地获取 / 创建 MemoryStore 实例
    """
    with _STORE_LOCK:
        if user_id not in _STORES:
            _STORES[user_id] = MemoryStore(
                pg_url       = DB_URL,
                ark_client   = _ark_client,
                ark_model_id = ARK_MODEL_ID,
                chunk_size   = 500,
                role_id      = user_id,
            )
        return _STORES[user_id]

@app.on_event("startup")
def startup_event():
    # 预先为默认用户建一下 store，非必须
    get_store(DEFAULT_ROLE)
    LOGGER.info(f"{TAG} MemoryStore ready for default role={DEFAULT_ROLE}")

# ───────────────── Pydantic 请求结构 ─────────────────
class BaseRequest(BaseModel):
    user_id: str

class QueryRequest(BaseRequest):
    q: str

class ImportRequest(BaseRequest):
    text:  Optional[str] = None
    texts: Optional[List[str]] = None

class ShortUpdate(BaseModel):
    user_id: str
    id:      int
    content: str

class WorkingUpdate(BaseModel):
    user_id: str
    content: str

# ─────────────────── CRUD 接口 ───────────────────
@app.get("/memory/short")
async def get_short(user_id: str):
    with Session(_engine) as s:
        rows = s.scalars(
            select(MemoryDoc)
            .where(MemoryDoc.user_id == user_id, MemoryDoc.mem_type == "short")
            .order_by(MemoryDoc.id.desc())
        ).all()
    return {"short": [{"id": r.id, "content": r.content} for r in rows]}

@app.put("/memory/short")
async def update_short(upd: ShortUpdate):
    with Session(_engine) as s:
        mem = s.get(MemoryDoc, upd.id)
        if not mem or mem.user_id != int(upd.user_id) or mem.mem_type != "short":
            raise HTTPException(404, "短期记忆未找到")
        mem.content = upd.content
        s.commit()
    return {"success": True}

@app.get("/memory/working")
async def get_working(user_id: str):
    with Session(_engine) as s:
        mem = s.scalars(
            select(MemoryDoc)
            .where(MemoryDoc.user_id == user_id, MemoryDoc.mem_type == "working")
            .order_by(MemoryDoc.id.desc())
        ).first()
    return {"working": None if not mem else {"id": mem.id, "content": mem.content}}

@app.put("/memory/working")
async def update_working(upd: WorkingUpdate):
    with Session(_engine) as s:
        existing = s.scalars(
            select(MemoryDoc)
            .where(MemoryDoc.user_id == upd.user_id, MemoryDoc.mem_type == "working")
            .order_by(MemoryDoc.id.desc())
        ).first()
        if existing:
            existing.content = upd.content
        else:
            s.add(MemoryDoc(user_id=upd.user_id, mem_type="working", content=upd.content))
        s.commit()
    return {"success": True}

# ─────────────────── Memory 操作接口 ───────────────────
@app.post("/memory/query")
async def memory_query(req: QueryRequest):
    store = get_store(req.user_id)
    hits = [
        f"{score:.4f} {doc.page_content}"
        for doc, score in store.similarity_search(req.q, k=5)
    ]
    return {"user_id": req.user_id, "query": req.q, "hits": hits}

@app.post("/memory/import")
async def memory_import(req: ImportRequest):
    if not (req.text or req.texts):
        raise HTTPException(422, "请提供 text 或 texts")
    store = get_store(req.user_id)
    full_text = req.text or "\n".join(req.texts)
    total = store.add_text(full_text)
    return {"user_id": req.user_id, "total_segments": total}

@app.delete("/memory/clear")
async def memory_clear(user_id: str):
    # 1. 清 postgres 向量
    deleted = MemoryStore.clear_all(DB_URL, user_id)
    # 2. 清缓存实例
    with _STORE_LOCK:
        _STORES.pop(user_id, None)
    return {"user_id": user_id, "deleted": deleted}

# ─────────────────── 应用入口 ───────────────────
if __name__ == "__main__":
    LOGGER.info(f"{TAG} 调试 API 启动 → http://0.0.0.0:8081/docs")
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="info")
