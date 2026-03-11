# prompt_store.py
import logging, json
from typing import Optional, List
from pydantic import BaseModel
from sqlalchemy import create_engine, text, select
from passlib.context import CryptContext
from pydantic import Field
from collections import UserDict

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
_log = logging.getLogger("prompt_store")

class PromptProfile(BaseModel):
    username: str
    user_id: str
    conv_prompt: str | None = None
    wm_prompt: str | None = None
    knowledge_base: list[str] = []
    chat_short_keep: int = 5
    chat_kb_k: int = 3
    chat_long_k: int = 5
    wm_short_keep: int = 5
    wm_kb_k: int = 3
    wm_long_k: int = 5
class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


class PromptStore:
    def __init__(self, pg_url: str):
        self.engine = create_engine(pg_url)

    # ───────── 用户注册 ─────────
    def create_user(self, username: str, password: str, user_id: str) -> bool:
        pw_hash = pwd_ctx.hash(password)
        sql = text("""
            INSERT INTO prompt_profile(username, password_hash, user_id)
            VALUES (:u,:p,:d)
        """)
        try:
            with self.engine.begin() as conn:
                conn.execute(sql, {"u": username, "p": pw_hash, "d": user_id})
            return True
        except Exception as e:
            _log.warning("create_user failed: %s", e)
            return False

    # ───────── 登录校验 ─────────
    def verify_user(self, username: str, password: str) -> Optional[str]:
        sql = text("SELECT password_hash, user_id FROM prompt_profile WHERE username=:u")
        with self.engine.begin() as conn:
            row = conn.execute(sql, {"u": username}).fetchone()
        if row and pwd_ctx.verify(password, row.password_hash):
            return row.user_id               # 返回用户ID，用于后续查询
        return None

    # ───────── CRUD 提示词 ─────────
    def get_profile(self, user_id: str) -> Optional[PromptProfile]:
        sql = text("SELECT * FROM prompt_profile WHERE user_id=:d")
        with self.engine.begin() as conn:
            row = conn.execute(sql, {"d": user_id}).mappings().fetchone()
        return PromptProfile(**row) if row else None

    def update_prompts(
        self, user_id, conv_prompt, wm_prompt,
        chat_short_keep=None, chat_kb_k=None, chat_long_k=None,
        wm_short_keep=None, wm_kb_k=None, wm_long_k=None,
    ):
        sql = text("""
            UPDATE prompt_profile SET
                conv_prompt=:c,
                wm_prompt=:w,
                chat_short_keep=:cs,
                chat_kb_k=:ck,
                chat_long_k=:cl,
                wm_short_keep=:ws,
                wm_kb_k=:wk,
                wm_long_k=:wl
            WHERE user_id=:d
        """)
        with self.engine.begin() as conn:
            conn.execute(sql, {
                "c": conv_prompt, "w": wm_prompt, "cs": chat_short_keep, "ck": chat_kb_k, "cl": chat_long_k,
                "ws": wm_short_keep, "wk": wm_kb_k, "wl": wm_long_k, "d": user_id
            })
        return True


    def add_kb_item(self, user_id: str, item: str) -> None:
        sql = text("""
            UPDATE prompt_profile
            SET knowledge_base = COALESCE(knowledge_base, '[]'::jsonb) || :arr
            WHERE user_id = :d
        """)
        arr = json.dumps([item])  # 变成 '["xxx"]'
        with self.engine.begin() as conn:
            conn.execute(sql, {"arr": arr, "d": user_id})


    def remove_kb_item(self, user_id: str, item: str) -> None:
        sql = text("""
            UPDATE prompt_profile
            SET knowledge_base = (
                SELECT jsonb_agg(elem) FROM jsonb_array_elements_text(COALESCE(knowledge_base, '[]'::jsonb)) AS elem
                WHERE elem <> :item
            )
            WHERE user_id = :d
        """)
        with self.engine.begin() as conn:
            conn.execute(sql, {"item": item, "d": user_id})
            
   