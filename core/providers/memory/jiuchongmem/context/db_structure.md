# 数据库架构摘要

> 这是给 AI 和人类快速理解数据库结构的摘要。权威结构文件仍然是同目录下的 schema SQL。

## 快照信息

- 生成时间: `2026-03-12_102506`
- 连接串: `postgresql://postgres@127.0.0.1:5432/azi_db`
- 数据库: `azi_db`
- 用户: `postgres`
- PostgreSQL 版本: `16.9 (Ubuntu 16.9-1.pgdg24.04+1)`
- 业务 schema: `public`
- 表数量: **13**
- 视图数量: **0**

## 外键关系摘要

- `public.devices(current_student_id)` -> `public.students(student_id)`
- `public.langchain_pg_embedding_backup_20251203_group1(collection_id)` -> `public.langchain_pg_collection(uuid)`
- `public.students(bound_mac_normalized)` -> `public.devices(mac_normalized)`
- `public.system_logs(mac_normalized)` -> `public.devices(mac_normalized)`
- `public.system_logs(student_id)` -> `public.students(student_id)`

## 表清单

- `public.devices`
- `public.jiuchongmemory`
- `public.langchain_pg_collection`
- `public.langchain_pg_embedding`
- `public.langchain_pg_embedding_backup_20251203_group1`
- `public.langchain_pg_embedding_backup_group2`
- `public.memory_doc`
- `public.memory_doc_backup_20231203_group1`
- `public.memory_doc_backup_group2`
- `public.messages`
- `public.prompt_profile`
- `public.students`
- `public.system_logs`

## public.devices

- 主键: `mac_normalized`
- 列数: **6**
- 外键数: **1**
- 索引数: **5**

### 列

| 列名 | 类型 | 可空 | 默认值 |
|---|---|---|---|
| `mac_normalized` | `text` | `NO` | `` |
| `pair_code_hash` | `text` | `NO` | `` |
| `label` | `text` | `YES` | `` |
| `created_at` | `timestamp with time zone` | `YES` | `now()` |
| `pair_code_sha256` | `bytea` | `YES` | `` |
| `current_student_id` | `uuid` | `YES` | `` |

### 外键

- `devices_current_student_id_fkey`: `current_student_id` -> `public.students(student_id)`

### 索引

- `devices_pkey`
  - 定义: `CREATE UNIQUE INDEX devices_pkey ON public.devices USING btree (mac_normalized)`
- `idx_devices_current_student`
  - 定义: `CREATE INDEX idx_devices_current_student ON public.devices USING btree (current_student_id)`
- `idx_devices_label`
  - 定义: `CREATE INDEX idx_devices_label ON public.devices USING btree (label)`
- `idx_devices_pair_sha`
  - 定义: `CREATE INDEX idx_devices_pair_sha ON public.devices USING btree (pair_code_sha256)`
- `ux_devices_current_student`
  - 定义: `CREATE UNIQUE INDEX ux_devices_current_student ON public.devices USING btree (current_student_id) WHERE (current_student_id IS NOT NULL)`

## public.jiuchongmemory

- 主键: `id`
- 列数: **7**
- 外键数: **0**
- 索引数: **1**

### 列

| 列名 | 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `bigint` | `NO` | `nextval('jiuchongmemory_id_seq'::regclass)` |
| `collection_id` | `bigint` | `YES` | `` |
| `embedding` | `vector` | `YES` | `` |
| `document` | `text` | `YES` | `` |
| `cmetadata` | `jsonb` | `YES` | `` |
| `custom_id` | `character varying` | `YES` | `` |
| `uuid` | `uuid` | `YES` | `gen_random_uuid()` |

### 索引

- `jiuchongmemory_pkey`
  - 定义: `CREATE UNIQUE INDEX jiuchongmemory_pkey ON public.jiuchongmemory USING btree (id)`

## public.langchain_pg_collection

- 主键: `uuid`
- 列数: **3**
- 外键数: **0**
- 索引数: **1**

### 列

| 列名 | 类型 | 可空 | 默认值 |
|---|---|---|---|
| `name` | `character varying` | `YES` | `` |
| `cmetadata` | `json` | `YES` | `` |
| `uuid` | `uuid` | `NO` | `` |

### 索引

- `langchain_pg_collection_pkey`
  - 定义: `CREATE UNIQUE INDEX langchain_pg_collection_pkey ON public.langchain_pg_collection USING btree (uuid)`

## public.langchain_pg_embedding

- 主键: `uuid`
- 列数: **6**
- 外键数: **0**
- 索引数: **1**

### 列

| 列名 | 类型 | 可空 | 默认值 |
|---|---|---|---|
| `collection_id` | `uuid` | `YES` | `` |
| `embedding` | `vector` | `YES` | `` |
| `document` | `character varying` | `YES` | `` |
| `cmetadata` | `json` | `YES` | `` |
| `custom_id` | `character varying` | `YES` | `` |
| `uuid` | `uuid` | `NO` | `` |

### 索引

- `langchain_pg_embedding_pkey2`
  - 定义: `CREATE UNIQUE INDEX langchain_pg_embedding_pkey2 ON public.langchain_pg_embedding USING btree (uuid)`

## public.langchain_pg_embedding_backup_20251203_group1

- 主键: `uuid`
- 列数: **6**
- 外键数: **1**
- 索引数: **1**

### 列

| 列名 | 类型 | 可空 | 默认值 |
|---|---|---|---|
| `collection_id` | `uuid` | `YES` | `` |
| `embedding` | `vector` | `YES` | `` |
| `document` | `character varying` | `YES` | `` |
| `cmetadata` | `json` | `YES` | `` |
| `custom_id` | `character varying` | `YES` | `` |
| `uuid` | `uuid` | `NO` | `` |

### 外键

- `langchain_pg_embedding_collection_id_fkey`: `collection_id` -> `public.langchain_pg_collection(uuid)`

### 索引

- `langchain_pg_embedding_pkey`
  - 定义: `CREATE UNIQUE INDEX langchain_pg_embedding_pkey ON public.langchain_pg_embedding_backup_20251203_group1 USING btree (uuid)`

## public.langchain_pg_embedding_backup_group2

- 主键: `uuid`
- 列数: **6**
- 外键数: **0**
- 索引数: **1**

### 列

| 列名 | 类型 | 可空 | 默认值 |
|---|---|---|---|
| `collection_id` | `uuid` | `YES` | `` |
| `embedding` | `vector` | `YES` | `` |
| `document` | `character varying` | `YES` | `` |
| `cmetadata` | `json` | `YES` | `` |
| `custom_id` | `character varying` | `YES` | `` |
| `uuid` | `uuid` | `NO` | `` |

### 索引

- `langchain_pg_embedding_pkey1`
  - 定义: `CREATE UNIQUE INDEX langchain_pg_embedding_pkey1 ON public.langchain_pg_embedding_backup_group2 USING btree (uuid)`

## public.memory_doc

- 主键: `id`
- 列数: **6**
- 外键数: **0**
- 索引数: **1**

### 列

| 列名 | 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `integer` | `NO` | `nextval('memory_doc_id_seq'::regclass)` |
| `user_id` | `text` | `YES` | `` |
| `mem_type` | `character varying` | `YES` | `` |
| `content` | `text` | `YES` | `` |
| `created_at` | `timestamp with time zone` | `YES` | `now()` |
| `perception` | `text` | `YES` | `` |

### 索引

- `memory_doc_pkey2`
  - 定义: `CREATE UNIQUE INDEX memory_doc_pkey2 ON public.memory_doc USING btree (id)`

## public.memory_doc_backup_20231203_group1

- 主键: `id`
- 列数: **6**
- 外键数: **0**
- 索引数: **1**

### 列

| 列名 | 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `integer` | `NO` | `nextval('memory_doc_id_seq'::regclass)` |
| `user_id` | `text` | `YES` | `` |
| `mem_type` | `character varying` | `YES` | `` |
| `content` | `text` | `YES` | `` |
| `created_at` | `timestamp with time zone` | `YES` | `now()` |
| `perception` | `text` | `YES` | `` |

### 索引

- `memory_doc_pkey`
  - 定义: `CREATE UNIQUE INDEX memory_doc_pkey ON public.memory_doc_backup_20231203_group1 USING btree (id)`

## public.memory_doc_backup_group2

- 主键: `id`
- 列数: **6**
- 外键数: **0**
- 索引数: **1**

### 列

| 列名 | 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `integer` | `NO` | `nextval('memory_doc_id_seq'::regclass)` |
| `user_id` | `text` | `YES` | `` |
| `mem_type` | `character varying` | `YES` | `` |
| `content` | `text` | `YES` | `` |
| `created_at` | `timestamp with time zone` | `YES` | `now()` |
| `perception` | `text` | `YES` | `` |

### 索引

- `memory_doc_pkey1`
  - 定义: `CREATE UNIQUE INDEX memory_doc_pkey1 ON public.memory_doc_backup_group2 USING btree (id)`

## public.messages

- 主键: 无
- 列数: **4**
- 外键数: **0**
- 索引数: **0**

### 列

| 列名 | 类型 | 可空 | 默认值 |
|---|---|---|---|
| `mac_id` | `text` | `NO` | `` |
| `device_id` | `text` | `NO` | `` |
| `message` | `text` | `NO` | `` |
| `received_at` | `timestamp without time zone` | `YES` | `` |

## public.prompt_profile

- 主键: `id`
- 列数: **19**
- 外键数: **0**
- 索引数: **4**

### 列

| 列名 | 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `bigint` | `NO` | `nextval('prompt_profile_id_seq'::regclass)` |
| `username` | `text` | `NO` | `` |
| `password_hash` | `text` | `NO` | `` |
| `user_id` | `text` | `NO` | `` |
| `conv_prompt` | `text` | `YES` | `` |
| `wm_prompt` | `text` | `YES` | `` |
| `created_at` | `timestamp with time zone` | `YES` | `now()` |
| `updated_at` | `timestamp with time zone` | `YES` | `now()` |
| `chat_short_keep` | `integer` | `YES` | `5` |
| `chat_kb_k` | `integer` | `YES` | `3` |
| `chat_long_k` | `integer` | `YES` | `5` |
| `wm_short_keep` | `integer` | `YES` | `5` |
| `wm_kb_k` | `integer` | `YES` | `3` |
| `wm_long_k` | `integer` | `YES` | `5` |
| `group` | `text` | `YES` | `'default'::text` |
| `pet_nick_name` | `text` | `YES` | `'卡波'::text` |
| `user_nick_name` | `text` | `YES` | `` |
| `notifications` | `jsonb` | `YES` | `` |
| `important_info` | `text` | `YES` | `` |

### 索引

- `idx_prompt_profile_user`
  - 定义: `CREATE INDEX idx_prompt_profile_user ON public.prompt_profile USING btree (user_id)`
- `prompt_profile_pkey`
  - 定义: `CREATE UNIQUE INDEX prompt_profile_pkey ON public.prompt_profile USING btree (id)`
- `prompt_profile_user_id_key`
  - 定义: `CREATE UNIQUE INDEX prompt_profile_user_id_key ON public.prompt_profile USING btree (user_id)`
- `prompt_profile_username_key`
  - 定义: `CREATE UNIQUE INDEX prompt_profile_username_key ON public.prompt_profile USING btree (username)`

## public.students

- 主键: `student_id`
- 列数: **7**
- 外键数: **1**
- 索引数: **6**

### 列

| 列名 | 类型 | 可空 | 默认值 |
|---|---|---|---|
| `student_id` | `uuid` | `NO` | `gen_random_uuid()` |
| `id_full_enc` | `bytea` | `NO` | `` |
| `id_full_hash` | `text` | `NO` | `` |
| `id_last6_hash` | `text` | `NO` | `` |
| `created_at` | `timestamp with time zone` | `YES` | `now()` |
| `id_full_sha256` | `bytea` | `YES` | `` |
| `bound_mac_normalized` | `text` | `YES` | `` |

### 外键

- `students_bound_mac_normalized_fkey`: `bound_mac_normalized` -> `public.devices(mac_normalized)`

### 索引

- `idx_students_bound_mac`
  - 定义: `CREATE INDEX idx_students_bound_mac ON public.students USING btree (bound_mac_normalized)`
- `idx_students_id_sha`
  - 定义: `CREATE INDEX idx_students_id_sha ON public.students USING btree (id_full_sha256)`
- `idx_students_idhash`
  - 定义: `CREATE INDEX idx_students_idhash ON public.students USING btree (id_full_hash)`
- `idx_students_last6`
  - 定义: `CREATE INDEX idx_students_last6 ON public.students USING btree (id_last6_hash)`
- `students_pkey`
  - 定义: `CREATE UNIQUE INDEX students_pkey ON public.students USING btree (student_id)`
- `ux_students_bound_mac`
  - 定义: `CREATE UNIQUE INDEX ux_students_bound_mac ON public.students USING btree (bound_mac_normalized) WHERE (bound_mac_normalized IS NOT NULL)`

## public.system_logs

- 主键: `id`
- 列数: **9**
- 外键数: **2**
- 索引数: **4**

### 列

| 列名 | 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `bigint` | `NO` | `nextval('system_logs_id_seq'::regclass)` |
| `student_id` | `uuid` | `YES` | `` |
| `mac_normalized` | `text` | `YES` | `` |
| `ip` | `inet` | `YES` | `` |
| `ok` | `boolean` | `NO` | `` |
| `reason` | `text` | `YES` | `` |
| `user_agent` | `text` | `YES` | `` |
| `note` | `text` | `YES` | `` |
| `created_at` | `timestamp with time zone` | `YES` | `now()` |

### 外键

- `system_logs_mac_normalized_fkey`: `mac_normalized` -> `public.devices(mac_normalized)`
- `system_logs_student_id_fkey`: `student_id` -> `public.students(student_id)`

### 索引

- `idx_syslog_ip_time`
  - 定义: `CREATE INDEX idx_syslog_ip_time ON public.system_logs USING btree (ip, created_at DESC)`
- `idx_syslog_mac_time`
  - 定义: `CREATE INDEX idx_syslog_mac_time ON public.system_logs USING btree (mac_normalized, created_at DESC)`
- `idx_syslog_student`
  - 定义: `CREATE INDEX idx_syslog_student ON public.system_logs USING btree (student_id, created_at DESC)`
- `system_logs_pkey`
  - 定义: `CREATE UNIQUE INDEX system_logs_pkey ON public.system_logs USING btree (id)`

## 给 AI 的使用建议

- 改表结构前，先看外键关系摘要，避免把引用链改断。
- 新增字段时，优先复用现有命名风格，不要发明一套新方言。
- 这份 Markdown 用于理解结构；真正要执行迁移时，以 schema SQL 和 migration 文件为准。
