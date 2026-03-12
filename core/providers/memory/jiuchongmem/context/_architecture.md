# 架构摘要

- 源文件: `/root/purple-server-ubantuversion/main/xiaozhi-server/core/providers/memory/jiuchongmem/jiuchongmem.py`
- 函数总数: **51**
- 顶层入口候选: `_direct_vector_search_by_user_id`, `init_memory`, `query_memory`, `save_memory`

## 功能分层速览

### 初始化 / 运行时

- `_init_vector_runtime`
- `init_memory`

### 检索 / 查询

- `query_kb`
- `query_long_mem`
- `query_memory`
- `query_persona`
- `query_short_mem`
- `query_vector_entries`

### 渲染 / 拼装

- `render_kb_entries`
- `render_long_mem_entries`
- `render_persona_entries`
- `render_short_mem_entries`
- `render_user_query_entry`

### 保存 / 压缩 / 记忆写入

- `_add_text_to_long_memory`
- `_async_compact_short_to_long`
- `_async_refine_memory_perception`
- `_compact_short_to_long_sync`
- `_load_compaction_policy`
- `_refine_memory_perception_sync`
- `init_memory`
- `migrate_short_memory_to_long`
- `query_memory`
- `save_memory`
- `save_memory_to_short`

### 内部 helper

- `__init__`
- `_apply_profile_function_call`
- `_async_run_profile_function_calls`
- `_build_db_url`
- `_build_prompt`
- `_clean_text`
- `_coerce_float`
- `_coerce_int`
- `_direct_vector_search_by_user_id`
- `_extract_latest_turn`
- `_format_turn_content`
- `_load_function_call_tools`
- `_normalize_user_ids`
- `_resolve_runtime_params`
- `_retry_backoff`
- `_run_profile_function_calls_sync`
- `_safe_parse_json`
- `_stringify_profile_value`

## 直接调用关系

- `LangchainPgCollection` -> `Base`
- `LangchainPgEmbedding` -> `Base`
- `MemoryDoc` -> `Base`
- `PromptProfile` -> `Base`
- `__init__` -> `Base`, `_build_db_url`, `_normalize_user_ids`
- `_add_text_to_long_memory` -> `_clean_text`, `_retry_backoff`
- `_apply_profile_function_call` -> `PromptProfile`
- `_async_compact_short_to_long` -> `_compact_short_to_long_sync`
- `_async_refine_memory_perception` -> `_refine_memory_perception_sync`
- `_async_run_profile_function_calls` -> `_run_profile_function_calls_sync`
- `_build_prompt` -> `_resolve_runtime_params`, `query_kb`, `query_long_mem`, `query_persona`, `query_short_mem`, `render_kb_entries`, `render_long_mem_entries`, `render_persona_entries`, `render_short_mem_entries`, `render_user_query_entry`
- `_compact_short_to_long_sync` -> `MemoryDoc`, `_add_text_to_long_memory`, `_build_prompt`
- `_direct_vector_search_by_user_id` -> `_retry_backoff`, `embed_query`
- `_init_vector_runtime` -> `ArkEmbedding`, `__init__`, `_build_db_url`
- `_load_compaction_policy` -> `PromptProfile`
- `_refine_memory_perception_sync` -> `MemoryDoc`
- `_resolve_runtime_params` -> `PromptProfile`, `_coerce_int`, `_normalize_user_ids`
- `_run_profile_function_calls_sync` -> `_apply_profile_function_call`, `_load_function_call_tools`, `_safe_parse_json`
- `embed_query` -> `embed_documents`
- `filter_vector_hits` -> `RetrievedItem`
- `init_memory` -> `_init_vector_runtime`
- `migrate_short_memory_to_long` -> `_async_compact_short_to_long`
- `query_kb` -> `RetrievedItem`, `query_vector_entries`
- `query_long_mem` -> `RetrievedItem`, `query_vector_entries`
- `query_memory` -> `_build_prompt`
- `query_persona` -> `PromptProfile`
- `query_vector_entries` -> `LangchainPgCollection`, `LangchainPgEmbedding`, `RetrievedItem`, `_coerce_float`, `_coerce_int`, `_normalize_user_ids`, `_retry_backoff`, `embed_query`, `filter_vector_hits`
- `render_kb_entries` -> `RetrievedItem`
- `render_long_mem_entries` -> `RetrievedItem`, `format_relative_time`
- `render_persona_entries` -> `PromptProfile`, `_stringify_profile_value`
- `render_short_mem_entries` -> `format_relative_time`
- `save_memory` -> `_async_refine_memory_perception`, `_async_run_profile_function_calls`, `_load_compaction_policy`, `migrate_short_memory_to_long`, `save_memory_to_short`
- `save_memory_to_short` -> `MemoryDoc`, `_extract_latest_turn`, `_format_turn_content`

## 顶层调用树

```text
_direct_vector_search_by_user_id
├─ _retry_backoff
└─ embed_query
   └─ embed_documents

init_memory
└─ _init_vector_runtime
   ├─ ArkEmbedding
   ├─ __init__
   │  ├─ Base
   │  ├─ _build_db_url
   │  └─ _normalize_user_ids
   └─ _build_db_url

query_memory
└─ _build_prompt
   ├─ _resolve_runtime_params
   │  ├─ PromptProfile
   │  │  └─ Base
   │  ├─ _coerce_int
   │  └─ _normalize_user_ids
   ├─ query_kb
   │  ├─ RetrievedItem
   │  └─ query_vector_entries
   │     ├─ LangchainPgCollection
   │     ├─ LangchainPgEmbedding
   │     ├─ RetrievedItem
   │     ├─ _coerce_float
   │     ├─ _coerce_int
   │     ├─ _normalize_user_ids
   │     ├─ _retry_backoff
   │     └─ embed_query
   ├─ query_long_mem
   │  ├─ RetrievedItem
   │  └─ query_vector_entries
   │     ├─ LangchainPgCollection
   │     ├─ LangchainPgEmbedding
   │     ├─ RetrievedItem
   │     ├─ _coerce_float
   │     ├─ _coerce_int
   │     ├─ _normalize_user_ids
   │     ├─ _retry_backoff
   │     └─ embed_query
   ├─ query_persona
   │  └─ PromptProfile
   │     └─ Base
   ├─ query_short_mem
   ├─ render_kb_entries
   │  └─ RetrievedItem
   ├─ render_long_mem_entries
   │  ├─ RetrievedItem
   │  └─ format_relative_time
   └─ render_persona_entries
      ├─ PromptProfile
      │  └─ Base
      └─ _stringify_profile_value

save_memory
├─ _async_refine_memory_perception
│  └─ _refine_memory_perception_sync
│     └─ MemoryDoc
│        └─ Base
├─ _async_run_profile_function_calls
│  └─ _run_profile_function_calls_sync
│     ├─ _apply_profile_function_call
│     │  └─ PromptProfile
│     ├─ _load_function_call_tools
│     └─ _safe_parse_json
├─ _load_compaction_policy
│  └─ PromptProfile
│     └─ Base
├─ migrate_short_memory_to_long
│  └─ _async_compact_short_to_long
│     └─ _compact_short_to_long_sync
│        ├─ MemoryDoc
│        ├─ _add_text_to_long_memory
│        └─ _build_prompt
└─ save_memory_to_short
   ├─ MemoryDoc
   │  └─ Base
   ├─ _extract_latest_turn
   └─ _format_turn_content

```

## 给 AI 的使用建议

- 修改代码前，先看 `顶层入口候选` 和 `顶层调用树`，不要打乱主链。
- 新增函数时，优先挂到已有层次里：初始化 / 查询 / 渲染 / 保存。
- 如果某个函数被多个上游调用，改签名时要同步检查所有调用点。
- 若 AI 新增了跨层调用，先怀疑它是不是在偷懒。
