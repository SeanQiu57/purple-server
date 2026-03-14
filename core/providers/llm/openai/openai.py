from __future__ import annotations

import asyncio
import json
import queue
import threading
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator, Optional

import httpx

from config.logger import setup_logging
from core.providers.llm.base import LLMProviderBase
from core.utils.util import check_model_key

TAG = __name__
logger = setup_logging()


class APIBackendError(RuntimeError):
    def __init__(self, api_name: str, message: str) -> None:
        super().__init__(message)
        self.api_name = api_name


class APITimeoutError(APIBackendError):
    pass


class APIConnectError(APIBackendError):
    pass


class APIStatusError(APIBackendError):
    def __init__(self, api_name: str, status_code: int, message: str) -> None:
        super().__init__(api_name, message)
        self.status_code = status_code


class AllBackendsFailedError(RuntimeError):
    def __init__(self, errors: dict[str, Exception]) -> None:
        self.errors = errors
        detail = "; ".join(f"{name}: {error}" for name, error in errors.items())
        super().__init__(f"all backends failed: {detail}")


@dataclass(frozen=True)
class APIConfig:
    name: str
    base_url: str
    api_key: str


@dataclass(frozen=True)
class RouterConfig:
    model_name: str
    apis: tuple[APIConfig, ...]
    timeout_seconds: float
    health_check_interval: float
    health_check_prompt: str
    max_tokens: int

    @classmethod
    def from_provider_config(cls, config: dict[str, Any]) -> "RouterConfig":
        model_name = config.get("model_name")
        if not model_name:
            raise ValueError("model_name is required")

        max_tokens = config.get("max_tokens")
        if max_tokens in (None, ""):
            max_tokens = 500
        try:
            parsed_max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            parsed_max_tokens = 500

        timeout_seconds = float(config.get("timeout_seconds", 10))
        health_check_interval = float(config.get("health_check_interval", 60))
        health_check_prompt = config.get("health_check_prompt", "ping，请只回答 pong")

        default_api_key = config.get("api_key")
        default_base_url = config.get("base_url") or config.get("url")
        apis_config = config.get("apis") or []
        if not apis_config and default_base_url:
            apis_config = [
                {
                    "name": "primary",
                    "base_url": default_base_url,
                    "api_key": default_api_key,
                }
            ]

        parsed_apis = []
        for index, api_config in enumerate(apis_config):
            name = api_config.get("name") or f"api-{index}"
            base_url = api_config.get("base_url") or api_config.get("url")
            api_key = api_config.get("api_key", default_api_key)
            if not base_url:
                raise ValueError(f"base_url is required for api '{name}'")
            if not api_key:
                raise ValueError(f"api_key is required for api '{name}'")
            parsed_apis.append(
                APIConfig(
                    name=name,
                    base_url=normalize_base_url(base_url),
                    api_key=api_key,
                )
            )

        if not parsed_apis:
            raise ValueError("at least one api must be configured")

        return cls(
            model_name=model_name,
            apis=tuple(parsed_apis),
            timeout_seconds=timeout_seconds,
            health_check_interval=health_check_interval,
            health_check_prompt=health_check_prompt,
            max_tokens=parsed_max_tokens,
        )

    @property
    def identity(self) -> str:
        return json.dumps(
            {
                "model_name": self.model_name,
                "apis": [api.__dict__ for api in self.apis],
                "timeout_seconds": self.timeout_seconds,
                "health_check_interval": self.health_check_interval,
                "health_check_prompt": self.health_check_prompt,
                "max_tokens": self.max_tokens,
            },
            sort_keys=True,
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class ToolFunctionDelta:
    name: Optional[str] = None
    arguments: Optional[str] = None


@dataclass(frozen=True)
class ToolCallDelta:
    id: Optional[str] = None
    function: ToolFunctionDelta = field(default_factory=ToolFunctionDelta)


@dataclass(frozen=True)
class StreamEvent:
    content: Optional[str] = None
    tool_calls: Optional[list[ToolCallDelta]] = None
    usage: Optional[dict[str, Any]] = None


def normalize_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if not normalized.endswith("/v1"):
        normalized = f"{normalized}/v1"
    return normalized


def parse_tool_calls(raw_tool_calls: Any) -> Optional[list[ToolCallDelta]]:
    if not raw_tool_calls:
        return None

    parsed_items = []
    for item in raw_tool_calls:
        function = item.get("function") or {}
        parsed_items.append(
            ToolCallDelta(
                id=item.get("id"),
                function=ToolFunctionDelta(
                    name=function.get("name"),
                    arguments=function.get("arguments"),
                ),
            )
        )
    return parsed_items or None


class APIClient:
    def __init__(self, config: APIConfig, timeout_seconds: float) -> None:
        self.config = config
        timeout = httpx.Timeout(timeout_seconds, connect=timeout_seconds)
        self.client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def stream_chat(
        self,
        *,
        model_name: str,
        dialogue: list[dict[str, Any]],
        max_tokens: int,
        conv_id: str,
        functions: Optional[list[dict[str, Any]]] = None,
    ) -> AsyncIterator[StreamEvent]:
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": dialogue,
            "stream": True,
            "max_tokens": max_tokens,
            "user": conv_id,
        }
        if functions is not None:
            payload["tools"] = functions

        try:
            async with self.client.stream("POST", "/chat/completions", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue

                    raw_data = line[5:].strip()
                    if raw_data == "[DONE]":
                        return

                    data = json.loads(raw_data)
                    usage = data.get("usage")
                    if usage:
                        yield StreamEvent(usage=usage)

                    choices = data.get("choices") or []
                    if not choices:
                        continue

                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    tool_calls = parse_tool_calls(delta.get("tool_calls"))
                    if content is not None or tool_calls is not None:
                        yield StreamEvent(content=content, tool_calls=tool_calls)
        except httpx.TimeoutException as exc:
            raise APITimeoutError(self.config.name, f"timeout from {self.config.name}") from exc
        except httpx.ConnectError as exc:
            raise APIConnectError(self.config.name, f"connect error from {self.config.name}") from exc
        except httpx.HTTPStatusError as exc:
            raise APIStatusError(
                self.config.name,
                exc.response.status_code,
                f"http status error {exc.response.status_code} from {self.config.name}",
            ) from exc
        except httpx.RequestError as exc:
            raise APIConnectError(self.config.name, f"request error from {self.config.name}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise APIBackendError(self.config.name, f"invalid json stream from {self.config.name}") from exc

    async def health_check(
        self,
        *,
        model_name: str,
        prompt: str,
        max_tokens: int,
    ) -> None:
        payload = {
            "model": model_name,
            "stream": False,
            "max_tokens": min(max_tokens, 8),
            "messages": [
                {"role": "system", "content": "你是健康检查助手，请只返回 pong。"},
                {"role": "user", "content": prompt},
            ],
        }

        try:
            response = await self.client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                raise APIBackendError(self.config.name, "health check returned no choices")
        except httpx.TimeoutException as exc:
            raise APITimeoutError(self.config.name, f"health timeout from {self.config.name}") from exc
        except httpx.ConnectError as exc:
            raise APIConnectError(self.config.name, f"health connect error from {self.config.name}") from exc
        except httpx.HTTPStatusError as exc:
            raise APIStatusError(
                self.config.name,
                exc.response.status_code,
                f"health http status error {exc.response.status_code} from {self.config.name}",
            ) from exc
        except httpx.RequestError as exc:
            raise APIConnectError(self.config.name, f"health request error from {self.config.name}: {exc}") from exc


class BackgroundLoopRunner:
    _loop: Optional[asyncio.AbstractEventLoop] = None
    _thread: Optional[threading.Thread] = None
    _lock = threading.Lock()

    @classmethod
    def ensure_loop(cls) -> asyncio.AbstractEventLoop:
        with cls._lock:
            if cls._loop is not None and cls._loop.is_running():
                return cls._loop

            loop_ready = threading.Event()

            def run_loop() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                cls._loop = loop
                loop_ready.set()
                loop.run_forever()

            cls._thread = threading.Thread(
                target=run_loop,
                name="openai-router-loop",
                daemon=True,
            )
            cls._thread.start()
            loop_ready.wait()
            if cls._loop is None:
                raise RuntimeError("background event loop was not created")
            return cls._loop

    @classmethod
    def submit(cls, coroutine: Any) -> Future:
        loop = cls.ensure_loop()
        return asyncio.run_coroutine_threadsafe(coroutine, loop)


class LLMRouter:
    _routers: dict[str, "LLMRouter"] = {}
    _routers_lock = threading.Lock()

    @classmethod
    def get_router(cls, config: RouterConfig) -> "LLMRouter":
        with cls._routers_lock:
            router = cls._routers.get(config.identity)
            if router is None:
                router = cls(config)
                cls._routers[config.identity] = router
            return router

    def __init__(self, config: RouterConfig) -> None:
        self.config = config
        self.api_list = list(config.apis)
        self.original_primary_api = config.apis[0].name
        self.current_api = config.apis[0].name
        self.lock = asyncio.Lock()
        self.fallback_lock = asyncio.Lock()
        self.health_check_tasks: dict[str, asyncio.Task[None]] = {}
        self.clients = {
            api.name: APIClient(api, config.timeout_seconds) for api in config.apis
        }

    async def request_stream(
        self,
        dialogue: list[dict[str, Any]],
        functions: Optional[list[dict[str, Any]]] = None,
    ) -> AsyncIterator[StreamEvent]:
        current_api = await self.get_current_api_name()
        first_token_emitted = False

        try:
            async for event in self._call_api_stream(current_api, dialogue, functions):
                if event.content or event.tool_calls:
                    first_token_emitted = True
                yield event
            return
        except APIBackendError as exc:
            logger.bind(tag=TAG).warning(
                f"api request failed before completion: api={exc.api_name}, first_token_emitted={first_token_emitted}, error={exc}"
            )
            if first_token_emitted:
                raise

        winner_api, events = await self.fallback(dialogue, bad_api=current_api, functions=functions)
        logger.bind(tag=TAG).info(f"fallback switched current_api to {winner_api}")
        for event in events:
            yield event

    async def fallback(
        self,
        dialogue: list[dict[str, Any]],
        bad_api: str,
        functions: Optional[list[dict[str, Any]]] = None,
    ) -> tuple[str, list[StreamEvent]]:
        async with self.fallback_lock:
            current_api = await self.get_current_api_name()
            if current_api != bad_api:
                logger.bind(tag=TAG).info(
                    f"reusing already switched api: bad_api={bad_api}, current_api={current_api}"
                )
                events = await self._collect_response(current_api, dialogue, functions)
                await self.start_health_check_if_needed(self.original_primary_api)
                return current_api, events

            winner_api, events = await self.race_apis(
                dialogue=dialogue,
                exclude_api=bad_api,
                functions=functions,
            )
            await self.set_current_api_name(winner_api)
            await self.start_health_check_if_needed(self.original_primary_api)
            return winner_api, events

    async def race_apis(
        self,
        dialogue: list[dict[str, Any]],
        exclude_api: str,
        functions: Optional[list[dict[str, Any]]] = None,
    ) -> tuple[str, list[StreamEvent]]:
        candidates = [api.name for api in self.api_list if api.name != exclude_api]
        if not candidates:
            raise AllBackendsFailedError(
                {exclude_api: APIBackendError(exclude_api, "no fallback candidates available")}
            )

        errors: dict[str, Exception] = {}
        result_queue: asyncio.Queue[
            tuple[str, str, Optional[list[StreamEvent]], Optional[Exception]]
        ] = asyncio.Queue()
        tasks_by_api: dict[str, asyncio.Task[None]] = {}

        async def run_candidate(api_name: str) -> None:
            try:
                events = await self._collect_response(api_name, dialogue, functions)
                await result_queue.put(("success", api_name, events, None))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await result_queue.put(("error", api_name, None, exc))

        async with asyncio.TaskGroup() as task_group:
            for api_name in candidates:
                task = task_group.create_task(
                    run_candidate(api_name),
                    name=f"race-{api_name}",
                )
                tasks_by_api[api_name] = task

            for _ in range(len(candidates)):
                status, api_name, events, exc = await result_queue.get()
                if status == "success" and events is not None:
                    for other_api, task in tasks_by_api.items():
                        if other_api != api_name and not task.done():
                            task.cancel()
                    return api_name, events

                if exc is not None:
                    errors[api_name] = exc
                    logger.bind(tag=TAG).warning(
                        f"fallback candidate failed: api={api_name}, error={exc}"
                    )

        raise AllBackendsFailedError(errors)

    async def health_checker(self, target_api: str) -> None:
        try:
            while True:
                await asyncio.sleep(self.config.health_check_interval)
                try:
                    await self.clients[target_api].health_check(
                        model_name=self.config.model_name,
                        prompt=self.config.health_check_prompt,
                        max_tokens=self.config.max_tokens,
                    )
                except APIBackendError as exc:
                    logger.bind(tag=TAG).debug(
                        f"health check still failing: api={target_api}, error={exc}"
                    )
                    continue

                await self.set_current_api_name(target_api)
                logger.bind(tag=TAG).info(f"primary api recovered, switched back to {target_api}")
                return
        finally:
            async with self.lock:
                task = self.health_check_tasks.get(target_api)
                if task is asyncio.current_task():
                    self.health_check_tasks.pop(target_api, None)

    async def start_health_check_if_needed(self, target_api: str) -> None:
        current_api = await self.get_current_api_name()
        if target_api == current_api:
            return

        async with self.lock:
            task = self.health_check_tasks.get(target_api)
            if task is not None and not task.done():
                return
            self.health_check_tasks[target_api] = asyncio.create_task(
                self.health_checker(target_api),
                name=f"health-check-{target_api}",
            )

    async def get_current_api_name(self) -> str:
        async with self.lock:
            return self.current_api

    async def set_current_api_name(self, api_name: str) -> None:
        async with self.lock:
            self.current_api = api_name

    async def _call_api_stream(
        self,
        api_name: str,
        dialogue: list[dict[str, Any]],
        functions: Optional[list[dict[str, Any]]] = None,
    ) -> AsyncIterator[StreamEvent]:
        client = self.clients[api_name]
        conv_id = str(uuid.uuid4())
        async for event in client.stream_chat(
            model_name=self.config.model_name,
            dialogue=dialogue,
            max_tokens=self.config.max_tokens,
            conv_id=conv_id,
            functions=functions,
        ):
            yield event

    async def _collect_response(
        self,
        api_name: str,
        dialogue: list[dict[str, Any]],
        functions: Optional[list[dict[str, Any]]] = None,
    ) -> list[StreamEvent]:
        events = []
        async for event in self._call_api_stream(api_name, dialogue, functions):
            events.append(event)
        return events


class LLMProvider(LLMProviderBase):
    def __init__(self, config):
        router_config = RouterConfig.from_provider_config(config)
        self.model_name = router_config.model_name
        self.max_tokens = router_config.max_tokens
        self.api_key = router_config.apis[0].api_key
        self.base_url = router_config.apis[0].base_url

        for api_config in router_config.apis:
            check_model_key("LLM", api_config.api_key)

        self.router = LLMRouter.get_router(router_config)

    def response(self, session_id, dialogue):
        is_active = True
        for event in self._sync_stream(dialogue, functions=None):
            if event.usage is not None:
                self._log_usage(event.usage)
                continue

            content = event.content or ""
            if not content:
                continue

            if "<think>" in content:
                is_active = False
                content = content.split("<think>")[0]
            if "</think>" in content:
                is_active = True
                content = content.split("</think>")[-1]
            if is_active and content:
                yield content

    def response_with_functions(self, session_id, dialogue, functions=None):
        try:
            for event in self._sync_stream(dialogue, functions=functions):
                if event.usage is not None:
                    self._log_usage(event.usage)
                    continue
                yield event.content, event.tool_calls
        except Exception as e:
            logger.bind(tag=TAG).error(f"Error in function call streaming: {e}")
            yield f"【OpenAI服务响应异常: {e}】", None

    def _sync_stream(
        self,
        dialogue: list[dict[str, Any]],
        functions: Optional[list[dict[str, Any]]] = None,
    ) -> Iterator[StreamEvent]:
        message_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        async def runner() -> None:
            try:
                async for event in self.router.request_stream(dialogue, functions=functions):
                    message_queue.put(("data", event))
            except Exception as exc:
                message_queue.put(("error", exc))
            finally:
                message_queue.put(("done", None))

        future = BackgroundLoopRunner.submit(runner())

        while True:
            message_type, payload = message_queue.get()
            if message_type == "data":
                yield payload
                continue
            if message_type == "error":
                logger.bind(tag=TAG).error(f"Error in response generation: {payload}")
                raise payload
            future.result()
            break

    def _log_usage(self, usage_info: dict[str, Any]) -> None:
        logger.bind(tag=TAG).info(
            f"Token 消耗：输入 {usage_info.get('prompt_tokens', '未知')}，"
            f"输出 {usage_info.get('completion_tokens', '未知')}，"
            f"共计 {usage_info.get('total_tokens', '未知')}"
        )
