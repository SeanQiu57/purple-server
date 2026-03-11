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
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi import Depends,Body
from .prompt_store import PromptStore, PromptProfile   # 刚才写的封装

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

# ───── PromptStore & Auth ─────
PROMPT_DB = PromptStore(DB_URL)          # 复用同一个 PG
PWD_CTX   = CryptContext(schemes=["bcrypt"], deprecated="auto")
OAUTH2    = OAuth2PasswordBearer(tokenUrl="/prompt/login")
JWT_SECRET = os.getenv("PROMPT_JWT_SECRET", "CHANGEME")  # 建议写到环境变量

# ───────────────────────── MemoryDoc 定义 ─────────────────────────
class DebugBase(DeclarativeBase):
    pass

class MemoryDoc(DebugBase):
    __tablename__ = "memory_doc"
    id:         Mapped[int]      = mapped_column(primary_key=True)
    user_id:    Mapped[str]      = mapped_column(Text, nullable=False)
    mem_type:   Mapped[str]      = mapped_column(String(12), nullable=False)
    content:    Mapped[str]      = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
class RegisterInput(BaseModel):
    username: str
    password: str
    user_id: str
    
class PromptUpdate(BaseModel):
    conv_prompt: str | None = None
    wm_prompt:   str | None = None
    chat_short_keep: int | None = None
    chat_kb_k: int | None = None
    chat_long_k: int | None = None
    wm_short_keep: int | None = None
    wm_kb_k: int | None = None
    wm_long_k: int | None = None
    
class PromptTestInput(BaseModel):
    query: str
    scene: str = "chat"     # "chat" or "workingmem"
    short_keep: int = 5
    kb_k: int = 3
    long_k: int = 5

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
def _gen_token(username: str, user_id: str) -> str:
    return jwt.encode({"sub": username, "dev": user_id}, JWT_SECRET, algorithm="HS256")

def _get_user_id(token: str = Depends(OAUTH2)) -> str:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload["dev"]
    except JWTError:
        raise HTTPException(401, "无效令牌")


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
        if not mem or mem.user_id != upd.user_id or mem.mem_type != "short":
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
# ───────────────── Prompt / 用户接口 ─────────────────
@app.post("/prompt/register")
def prompt_register(data: RegisterInput):
    if not PROMPT_DB.create_user(data.username, data.password, data.user_id):
        raise HTTPException(400, "用户名或设备已存在")
    return {"msg": "ok"}

@app.post("/prompt/login")
def prompt_login(form: OAuth2PasswordRequestForm = Depends()):
    dev = PROMPT_DB.verify_user(form.username, form.password)
    if not dev:
        raise HTTPException(401, "认证失败")
    return {"access_token": _gen_token(form.username, dev), "token_type": "bearer", "user_id": dev}

@app.get("/prompt/me", response_model=PromptProfile)
def prompt_me(dev_id: str = Depends(_get_user_id)):
    profile = PROMPT_DB.get_profile(dev_id)
    if not profile:
        raise HTTPException(404, "未找到配置")
    return profile

@app.put("/prompt")
def prompt_update(data: PromptUpdate, dev_id: str = Depends(_get_user_id)):
    PROMPT_DB.update_prompts(
        dev_id,
        data.conv_prompt, data.wm_prompt,
        data.chat_short_keep, data.chat_kb_k, data.chat_long_k,
        data.wm_short_keep, data.wm_kb_k, data.wm_long_k
    )
    return {"msg": "updated"}

@app.post("/prompt/kb")
def prompt_kb_add(item: str, dev_id: str = Depends(_get_user_id)):
    PROMPT_DB.add_kb_item(dev_id, item)
    return {"msg": "added"}

@app.delete("/prompt/kb")
def prompt_kb_del(item: str, dev_id: str = Depends(_get_user_id)):
    PROMPT_DB.remove_kb_item(dev_id, item)
    return {"msg": "removed"}

@app.post("/prompt/test")
async def prompt_test(
    data: PromptTestInput,
    user_id: str = Depends(_get_user_id)
):
    store = get_store(user_id)
    prompt_str = await store.build_prompt(
        query=data.query,
        memory_doc_model=MemoryDoc,
        scene=data.scene
    )
    return {"prompt": prompt_str}

# ─────────────────── 应用入口 ───────────────────
if __name__ == "__main__":
    LOGGER.info(f"{TAG} 调试 API 启动 → http://0.0.0.0:8081/docs")
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="info")
