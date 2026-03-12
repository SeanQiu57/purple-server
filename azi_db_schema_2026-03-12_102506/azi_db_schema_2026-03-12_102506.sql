--
-- PostgreSQL database dump
--

-- Dumped from database version 16.9 (Ubuntu 16.9-1.pgdg24.04+1)
-- Dumped by pg_dump version 16.9 (Ubuntu 16.9-1.pgdg24.04+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

-- *not* creating schema, since initdb creates it


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS '';


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: bind_student_device(uuid, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.bind_student_device(p_student_id uuid, p_mac_raw text) RETURNS void
    LANGUAGE plpgsql
    AS $$
declare
  v_mac text;
begin
  -- 统一 MAC 形态
  v_mac := public.normalize_mac(p_mac_raw);

  -- 1) 先把这台设备上原来的学生解绑
  update public.students s
     set bound_mac_normalized = null
   where s.bound_mac_normalized = v_mac;

  -- 2) 再把这名学生原来绑定的设备解绑
  update public.devices d
     set current_student_id = null
   where d.current_student_id = p_student_id;

  -- 3) 正向绑定：学生 ⇄ 设备
  update public.students
     set bound_mac_normalized = v_mac
   where student_id = p_student_id;

  update public.devices
     set current_student_id = p_student_id
   where mac_normalized = v_mac;
end;
$$;


--
-- Name: check_hash(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.check_hash(raw text, hashed text) RETURNS boolean
    LANGUAGE sql
    AS $$
  select crypt(raw, hashed) = hashed;
$$;


--
-- Name: id_decrypt(bytea); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.id_decrypt(enc bytea) RETURNS text
    LANGUAGE sql STRICT
    AS $$
  select convert_from(
           decrypt(
             enc,
             digest(current_setting('app.idkey'), 'sha256'),
             'aes'
           ),
           'UTF8'
         );
$$;


--
-- Name: id_decrypt_with_key(bytea, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.id_decrypt_with_key(enc bytea, key text) RETURNS text
    LANGUAGE sql STRICT
    AS $$
  select convert_from(
           decrypt(
             enc,
             digest(key, 'sha256'),
             'aes'
           ),
           'UTF8'
         );
$$;


--
-- Name: id_encrypt(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.id_encrypt(raw text) RETURNS bytea
    LANGUAGE sql STRICT
    AS $$
  select encrypt(
           convert_to(raw, 'UTF8'),
           digest(current_setting('app.idkey'), 'sha256'),
           'aes'
         );
$$;


--
-- Name: id_encrypt_with_key(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.id_encrypt_with_key(raw text, key text) RETURNS bytea
    LANGUAGE sql STRICT
    AS $$
  select encrypt(
           convert_to(raw, 'UTF8'),
           digest(key, 'sha256'),
           'aes'
         );
$$;


--
-- Name: make_hash(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.make_hash(raw text) RETURNS text
    LANGUAGE sql
    AS $$
  select crypt(raw, gen_salt('bf'));
$$;


--
-- Name: normalize_mac(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.normalize_mac(mac text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $$
  select upper(regexp_replace(mac, '[^0-9A-Fa-f]', '', 'g'));
$$;


--
-- Name: sha256_bytes(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sha256_bytes(in_text text) RETURNS bytea
    LANGUAGE sql IMMUTABLE STRICT
    AS $$
  select digest(in_text, 'sha256');
$$;


--
-- Name: tgt_update_timestamp(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.tgt_update_timestamp() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END; $$;


--
-- Name: unbind_by_device(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.unbind_by_device(p_mac_raw text) RETURNS void
    LANGUAGE plpgsql
    AS $$
declare
  v_mac text;
  v_sid uuid;
begin
  v_mac := public.normalize_mac(p_mac_raw);

  select current_student_id into v_sid
  from public.devices where mac_normalized = v_mac;

  update public.devices set current_student_id = null
  where mac_normalized = v_mac;

  update public.students set bound_mac_normalized = null
  where student_id = v_sid;
end;
$$;


--
-- Name: unbind_by_student(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.unbind_by_student(p_student_id uuid) RETURNS void
    LANGUAGE plpgsql
    AS $$
declare
  v_mac text;
begin
  select bound_mac_normalized into v_mac
  from public.students where student_id = p_student_id;

  update public.students set bound_mac_normalized = null
  where student_id = p_student_id;

  update public.devices set current_student_id = null
  where mac_normalized = v_mac;
end;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: devices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.devices (
    mac_normalized text NOT NULL,
    pair_code_hash text NOT NULL,
    label text,
    created_at timestamp with time zone DEFAULT now(),
    pair_code_sha256 bytea,
    current_student_id uuid
);


--
-- Name: jiuchongmemory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.jiuchongmemory (
    id bigint NOT NULL,
    collection_id bigint,
    embedding public.vector(1536),
    document text,
    cmetadata jsonb,
    custom_id character varying,
    uuid uuid DEFAULT gen_random_uuid()
);


--
-- Name: jiuchongmemory_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.jiuchongmemory_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: jiuchongmemory_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.jiuchongmemory_id_seq OWNED BY public.jiuchongmemory.id;


--
-- Name: langchain_pg_collection; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.langchain_pg_collection (
    name character varying,
    cmetadata json,
    uuid uuid NOT NULL
);


--
-- Name: langchain_pg_embedding; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.langchain_pg_embedding (
    collection_id uuid,
    embedding public.vector,
    document character varying,
    cmetadata json,
    custom_id character varying,
    uuid uuid NOT NULL
);


--
-- Name: langchain_pg_embedding_backup_20251203_group1; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.langchain_pg_embedding_backup_20251203_group1 (
    collection_id uuid,
    embedding public.vector,
    document character varying,
    cmetadata json,
    custom_id character varying,
    uuid uuid NOT NULL
);


--
-- Name: langchain_pg_embedding_backup_group2; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.langchain_pg_embedding_backup_group2 (
    collection_id uuid,
    embedding public.vector,
    document character varying,
    cmetadata json,
    custom_id character varying,
    uuid uuid NOT NULL
);


--
-- Name: memory_doc_backup_20231203_group1; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memory_doc_backup_20231203_group1 (
    id integer NOT NULL,
    user_id text,
    mem_type character varying,
    content text,
    created_at timestamp with time zone DEFAULT now(),
    perception text
);


--
-- Name: memory_doc_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.memory_doc_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: memory_doc_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.memory_doc_id_seq OWNED BY public.memory_doc_backup_20231203_group1.id;


--
-- Name: memory_doc; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memory_doc (
    id integer DEFAULT nextval('public.memory_doc_id_seq'::regclass) NOT NULL,
    user_id text,
    mem_type character varying,
    content text,
    created_at timestamp with time zone DEFAULT now(),
    perception text
);


--
-- Name: memory_doc_backup_group2; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memory_doc_backup_group2 (
    id integer DEFAULT nextval('public.memory_doc_id_seq'::regclass) NOT NULL,
    user_id text,
    mem_type character varying,
    content text,
    created_at timestamp with time zone DEFAULT now(),
    perception text
);


--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    mac_id text NOT NULL,
    device_id text NOT NULL,
    message text NOT NULL,
    received_at timestamp without time zone
);


--
-- Name: prompt_profile; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.prompt_profile (
    id bigint NOT NULL,
    username text NOT NULL,
    password_hash text NOT NULL,
    user_id text NOT NULL,
    conv_prompt text,
    wm_prompt text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    chat_short_keep integer DEFAULT 5,
    chat_kb_k integer DEFAULT 3,
    chat_long_k integer DEFAULT 5,
    wm_short_keep integer DEFAULT 5,
    wm_kb_k integer DEFAULT 3,
    wm_long_k integer DEFAULT 5,
    "group" text DEFAULT 'default'::text COLLATE pg_catalog."C.utf8",
    pet_nick_name text DEFAULT '卡波'::text,
    user_nick_name text,
    notifications jsonb,
    important_info text
);


--
-- Name: COLUMN prompt_profile."group"; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.prompt_profile."group" IS 'the group of the users, in order to change muiltiple user''s prompt file';


--
-- Name: prompt_profile_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.prompt_profile_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: prompt_profile_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.prompt_profile_id_seq OWNED BY public.prompt_profile.id;


--
-- Name: students; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.students (
    student_id uuid DEFAULT gen_random_uuid() NOT NULL,
    id_full_enc bytea NOT NULL,
    id_full_hash text NOT NULL,
    id_last6_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    id_full_sha256 bytea,
    bound_mac_normalized text
);


--
-- Name: system_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_logs (
    id bigint NOT NULL,
    student_id uuid,
    mac_normalized text,
    ip inet,
    ok boolean NOT NULL,
    reason text,
    user_agent text,
    note text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: system_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.system_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: system_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.system_logs_id_seq OWNED BY public.system_logs.id;


--
-- Name: jiuchongmemory id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jiuchongmemory ALTER COLUMN id SET DEFAULT nextval('public.jiuchongmemory_id_seq'::regclass);


--
-- Name: memory_doc_backup_20231203_group1 id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_doc_backup_20231203_group1 ALTER COLUMN id SET DEFAULT nextval('public.memory_doc_id_seq'::regclass);


--
-- Name: prompt_profile id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompt_profile ALTER COLUMN id SET DEFAULT nextval('public.prompt_profile_id_seq'::regclass);


--
-- Name: system_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_logs ALTER COLUMN id SET DEFAULT nextval('public.system_logs_id_seq'::regclass);


--
-- Name: devices devices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devices
    ADD CONSTRAINT devices_pkey PRIMARY KEY (mac_normalized);


--
-- Name: jiuchongmemory jiuchongmemory_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jiuchongmemory
    ADD CONSTRAINT jiuchongmemory_pkey PRIMARY KEY (id);


--
-- Name: langchain_pg_collection langchain_pg_collection_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.langchain_pg_collection
    ADD CONSTRAINT langchain_pg_collection_pkey PRIMARY KEY (uuid);


--
-- Name: langchain_pg_embedding_backup_20251203_group1 langchain_pg_embedding_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.langchain_pg_embedding_backup_20251203_group1
    ADD CONSTRAINT langchain_pg_embedding_pkey PRIMARY KEY (uuid);


--
-- Name: langchain_pg_embedding_backup_group2 langchain_pg_embedding_pkey1; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.langchain_pg_embedding_backup_group2
    ADD CONSTRAINT langchain_pg_embedding_pkey1 PRIMARY KEY (uuid);


--
-- Name: langchain_pg_embedding langchain_pg_embedding_pkey2; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.langchain_pg_embedding
    ADD CONSTRAINT langchain_pg_embedding_pkey2 PRIMARY KEY (uuid);


--
-- Name: memory_doc_backup_20231203_group1 memory_doc_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_doc_backup_20231203_group1
    ADD CONSTRAINT memory_doc_pkey PRIMARY KEY (id);


--
-- Name: memory_doc_backup_group2 memory_doc_pkey1; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_doc_backup_group2
    ADD CONSTRAINT memory_doc_pkey1 PRIMARY KEY (id);


--
-- Name: memory_doc memory_doc_pkey2; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_doc
    ADD CONSTRAINT memory_doc_pkey2 PRIMARY KEY (id);


--
-- Name: prompt_profile prompt_profile_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompt_profile
    ADD CONSTRAINT prompt_profile_pkey PRIMARY KEY (id);


--
-- Name: prompt_profile prompt_profile_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompt_profile
    ADD CONSTRAINT prompt_profile_user_id_key UNIQUE (user_id);


--
-- Name: prompt_profile prompt_profile_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompt_profile
    ADD CONSTRAINT prompt_profile_username_key UNIQUE (username);


--
-- Name: students students_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.students
    ADD CONSTRAINT students_pkey PRIMARY KEY (student_id);


--
-- Name: system_logs system_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_logs
    ADD CONSTRAINT system_logs_pkey PRIMARY KEY (id);


--
-- Name: idx_devices_current_student; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_devices_current_student ON public.devices USING btree (current_student_id);


--
-- Name: idx_devices_label; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_devices_label ON public.devices USING btree (label);


--
-- Name: idx_devices_pair_sha; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_devices_pair_sha ON public.devices USING btree (pair_code_sha256);


--
-- Name: idx_prompt_profile_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_prompt_profile_user ON public.prompt_profile USING btree (user_id);


--
-- Name: idx_students_bound_mac; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_students_bound_mac ON public.students USING btree (bound_mac_normalized);


--
-- Name: idx_students_id_sha; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_students_id_sha ON public.students USING btree (id_full_sha256);


--
-- Name: idx_students_idhash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_students_idhash ON public.students USING btree (id_full_hash);


--
-- Name: idx_students_last6; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_students_last6 ON public.students USING btree (id_last6_hash);


--
-- Name: idx_syslog_ip_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_syslog_ip_time ON public.system_logs USING btree (ip, created_at DESC);


--
-- Name: idx_syslog_mac_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_syslog_mac_time ON public.system_logs USING btree (mac_normalized, created_at DESC);


--
-- Name: idx_syslog_student; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_syslog_student ON public.system_logs USING btree (student_id, created_at DESC);


--
-- Name: ux_devices_current_student; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_devices_current_student ON public.devices USING btree (current_student_id) WHERE (current_student_id IS NOT NULL);


--
-- Name: ux_students_bound_mac; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_students_bound_mac ON public.students USING btree (bound_mac_normalized) WHERE (bound_mac_normalized IS NOT NULL);


--
-- Name: prompt_profile trg_prompt_profile_ts; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_prompt_profile_ts BEFORE UPDATE ON public.prompt_profile FOR EACH ROW EXECUTE FUNCTION public.tgt_update_timestamp();


--
-- Name: devices devices_current_student_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devices
    ADD CONSTRAINT devices_current_student_id_fkey FOREIGN KEY (current_student_id) REFERENCES public.students(student_id) ON DELETE SET NULL;


--
-- Name: langchain_pg_embedding_backup_20251203_group1 langchain_pg_embedding_collection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.langchain_pg_embedding_backup_20251203_group1
    ADD CONSTRAINT langchain_pg_embedding_collection_id_fkey FOREIGN KEY (collection_id) REFERENCES public.langchain_pg_collection(uuid) ON DELETE CASCADE;


--
-- Name: students students_bound_mac_normalized_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.students
    ADD CONSTRAINT students_bound_mac_normalized_fkey FOREIGN KEY (bound_mac_normalized) REFERENCES public.devices(mac_normalized) ON DELETE SET NULL;


--
-- Name: system_logs system_logs_mac_normalized_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_logs
    ADD CONSTRAINT system_logs_mac_normalized_fkey FOREIGN KEY (mac_normalized) REFERENCES public.devices(mac_normalized) ON DELETE SET NULL;


--
-- Name: system_logs system_logs_student_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_logs
    ADD CONSTRAINT system_logs_student_id_fkey FOREIGN KEY (student_id) REFERENCES public.students(student_id) ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--

