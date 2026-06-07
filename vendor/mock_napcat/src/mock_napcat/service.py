from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from . import __version__
from .config import MockConfig
from .protocol import (
    group_message_event,
    heartbeat_event,
    lifecycle_event,
    message_plain_text,
    normalize_message,
    now_ts,
    onebot_response,
    private_message_event,
)

LOGGER = logging.getLogger("mock_napcat")

NULL_API_ACTIONS = {
    "delete_msg",
    "set_group_ban",
    "set_group_admin",
    "set_group_special_title",
    "set_group_kick",
    "set_group_card",
    "set_group_name",
    "set_group_whole_ban",
    "set_essence_msg",
    "delete_essence_msg",
    "set_msg_emoji_like",
    "upload_group_file",
    "group_poke",
    "send_poke",
    "_send_group_notice",
    "set_group_leave",
}


@dataclass
class MockState:
    config: MockConfig
    next_message_id: int = 1
    sequence: int = 0
    connected: bool = False
    message_records: dict[int, dict[str, Any]] = field(default_factory=dict)
    sent_messages: list[dict[str, Any]] = field(default_factory=list)
    api_calls: list[dict[str, Any]] = field(default_factory=list)
    members: dict[tuple[int, int], dict[str, Any]] = field(default_factory=dict)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)

    def allocate_message_id(self) -> int:
        message_id = self.next_message_id
        self.next_message_id += 1
        return message_id

    def reset_history(self) -> None:
        self.sequence = 0
        self.next_message_id = 1
        self.message_records.clear()
        self.sent_messages.clear()
        self.api_calls.clear()

    def remember_member(self, group_id: int, user_id: int, nickname: str, role: str) -> None:
        self.members[(group_id, user_id)] = self.group_member_info(
            group_id=group_id,
            user_id=user_id,
            nickname=nickname,
            role=role,
        )

    def group_member_info(
        self,
        *,
        group_id: int,
        user_id: int,
        nickname: str | None = None,
        role: str | None = None,
    ) -> dict[str, Any]:
        if user_id == self.config.self_id:
            nickname = nickname or self.config.nickname
            role = role or self.config.bot_role
        else:
            known = self.members.get((group_id, user_id))
            if known:
                return known
            nickname = nickname or f"User{user_id}"
            role = role or "member"

        return {
            "group_id": group_id,
            "user_id": user_id,
            "nickname": nickname,
            "card": nickname,
            "sex": "unknown",
            "age": 0,
            "area": "",
            "join_time": now_ts(),
            "last_sent_time": now_ts(),
            "level": "",
            "role": role,
            "unfriendly": False,
            "title": "",
            "title_expire_time": 0,
            "card_changeable": True,
        }

    def store_incoming(self, event: dict[str, Any]) -> None:
        message_id = int(event["message_id"])
        self.message_records[message_id] = {
            "time": event["time"],
            "message_type": event["message_type"],
            "message_id": message_id,
            "real_id": message_id,
            "sender": event.get("sender", {}),
            "message": event.get("message", []),
            "raw_message": event.get("raw_message", ""),
            "group_id": event.get("group_id"),
            "user_id": event.get("user_id"),
        }

    async def record_api_call(
        self,
        action: str,
        params: dict[str, Any],
        echo: Any,
    ) -> dict[str, Any]:
        async with self.condition:
            self.sequence += 1
            record = {
                "sequence": self.sequence,
                "time": now_ts(),
                "action": action,
                "params": params,
                "echo": echo,
            }
            self.api_calls.append(record)
            self.api_calls = self.api_calls[-500:]
            self.condition.notify_all()
            return record

    async def record_sent_message(
        self,
        *,
        action: str,
        message_type: str,
        group_id: int | None,
        user_id: int | None,
        message: Any,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        message_id = self.allocate_message_id()
        segments = normalize_message(message)
        raw_message = message_plain_text(segments)
        sender = self.group_member_info(
            group_id=group_id or self.config.default_group_id,
            user_id=self.config.self_id,
            nickname=self.config.nickname,
            role=self.config.bot_role,
        )
        record = {
            "time": now_ts(),
            "message_type": message_type,
            "message_id": message_id,
            "real_id": message_id,
            "sender": sender,
            "message": segments,
            "raw_message": raw_message,
            "group_id": group_id,
            "user_id": user_id,
        }

        async with self.condition:
            self.sequence += 1
            sent = {
                "sequence": self.sequence,
                "time": now_ts(),
                "message_id": message_id,
                "action": action,
                "message_type": message_type,
                "group_id": group_id,
                "user_id": user_id,
                "message": segments,
                "raw_message": raw_message,
                "params": params,
            }
            self.sent_messages.append(sent)
            self.sent_messages = self.sent_messages[-500:]
            self.message_records[message_id] = record
            self.condition.notify_all()

        return sent

    async def wait_for_messages(
        self,
        *,
        since_sequence: int,
        message_type: str,
        group_id: int | None,
        user_id: int | None,
        timeout: float,
    ) -> list[dict[str, Any]]:
        def matches(item: dict[str, Any]) -> bool:
            if item["sequence"] <= since_sequence:
                return False
            if item["message_type"] != message_type:
                return False
            if message_type == "group":
                return group_id is None or item.get("group_id") == group_id
            return user_id is None or item.get("user_id") == user_id

        async with self.condition:
            await asyncio.wait_for(
                self.condition.wait_for(lambda: any(matches(item) for item in self.sent_messages)),
                timeout=timeout,
            )
            return [item for item in self.sent_messages if matches(item)]

    def api_calls_since(self, since_sequence: int) -> list[dict[str, Any]]:
        return [item for item in self.api_calls if item["sequence"] > since_sequence]


class MockNapCatService:
    def __init__(self, config: MockConfig) -> None:
        self.config = config
        self.state = MockState(config)
        self.websocket: Any = None
        self.websocket_lock = asyncio.Lock()
        self.connected_event = asyncio.Event()

    async def run(self) -> None:
        control_server = await asyncio.start_server(
            self.handle_control_client,
            host=self.config.control_host,
            port=self.config.control_port,
        )
        LOGGER.info(
            "control socket listening on %s:%s",
            self.config.control_host,
            self.config.control_port,
        )
        ws_task = asyncio.create_task(self.websocket_forever())
        try:
            async with control_server:
                await control_server.serve_forever()
        finally:
            ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ws_task

    async def websocket_forever(self) -> None:
        while True:
            try:
                await self.websocket_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state.connected = False
                self.connected_event.clear()
                self.websocket = None
                LOGGER.warning(
                    "OneBot connection failed: %s; retrying in %.1fs",
                    exc,
                    self.config.reconnect_interval,
                )
                await asyncio.sleep(self.config.reconnect_interval)

    async def websocket_once(self) -> None:
        ws = await open_websocket(self.config.onebot_url, self.websocket_headers())
        try:
            self.websocket = ws
            self.state.connected = True
            self.connected_event.set()
            LOGGER.info(
                "connected to %s as self_id=%s",
                self.config.onebot_url,
                self.config.self_id,
            )
            await self.send_ws_payload(lifecycle_event(self.config.self_id))
            heartbeat_task = asyncio.create_task(self.heartbeat_loop())
            try:
                async for raw_message in ws:
                    await self.handle_onebot_api_message(raw_message)
            finally:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
                self.state.connected = False
                self.connected_event.clear()
                self.websocket = None
        finally:
            close = getattr(ws, "close", None)
            if close:
                result = close()
                if asyncio.iscoroutine(result):
                    await result

    def websocket_headers(self) -> dict[str, str]:
        headers = {
            "X-Self-ID": str(self.config.self_id),
            "X-Client-Role": "Universal",
        }
        if self.config.access_token:
            headers["Authorization"] = f"Bearer {self.config.access_token}"
        return headers

    async def heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.heartbeat_interval)
            await self.send_ws_payload(heartbeat_event(self.config.self_id))

    async def send_ws_payload(self, payload: dict[str, Any]) -> None:
        if not self.websocket:
            raise RuntimeError("OneBot WebSocket is not connected")
        async with self.websocket_lock:
            await self.websocket.send(json.dumps(payload, ensure_ascii=False))

    async def handle_onebot_api_message(self, raw_message: str | bytes) -> None:
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8")
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            LOGGER.warning("received invalid JSON from bot backend: %r", raw_message)
            return

        action = str(payload.get("action", ""))
        params = payload.get("params")
        echo = payload.get("echo")
        if not isinstance(params, dict):
            params = {}

        await self.state.record_api_call(action, params, echo)
        response = await self.handle_api(action, params, echo)
        if self.websocket:
            await self.send_ws_payload(response)

    async def handle_api(self, action: str, params: dict[str, Any], echo: Any) -> dict[str, Any]:
        if action in {
            "send_group_msg",
            "send_private_msg",
            "send_msg",
            "send_group_forward_msg",
            "send_private_forward_msg",
        }:
            data = await self.handle_send_api(action, params)
            return onebot_response(echo, {"message_id": data["message_id"]})

        if action == "get_group_member_info":
            group_id = int(params.get("group_id") or self.config.default_group_id)
            user_id = int(params.get("user_id") or self.config.default_user_id)
            return onebot_response(
                echo,
                self.state.group_member_info(group_id=group_id, user_id=user_id),
            )

        if action == "get_msg":
            message_id = int(params.get("message_id") or 0)
            return onebot_response(echo, self.state.message_records.get(message_id, {}))

        if action == "get_forward_msg":
            return onebot_response(echo, {"messages": []})

        if action == "get_login_info":
            return onebot_response(
                echo,
                {"user_id": self.config.self_id, "nickname": self.config.nickname},
            )

        if action == "get_status":
            return onebot_response(echo, {"online": True, "good": True})

        if action == "get_version_info":
            return onebot_response(
                echo,
                {
                    "app_name": "MockNapCat",
                    "app_version": __version__,
                    "protocol_version": "v11",
                },
            )

        if action in {"can_send_image", "can_send_record"}:
            return onebot_response(echo, {"yes": True})

        if action == "get_group_info":
            group_id = int(params.get("group_id") or self.config.default_group_id)
            return onebot_response(
                echo,
                {
                    "group_id": group_id,
                    "group_name": f"Mock Group {group_id}",
                    "member_count": 3,
                    "max_member_count": 500,
                },
            )

        if action == "get_group_list":
            group_id = self.config.default_group_id
            return onebot_response(
                echo,
                [
                    {
                        "group_id": group_id,
                        "group_name": f"Mock Group {group_id}",
                        "member_count": 3,
                        "max_member_count": 500,
                    }
                ],
            )

        if action == "get_friend_list":
            return onebot_response(
                echo,
                [
                    {
                        "user_id": self.config.default_user_id,
                        "nickname": f"User{self.config.default_user_id}",
                        "remark": "",
                    }
                ],
            )

        if action == "get_stranger_info":
            user_id = int(params.get("user_id") or self.config.default_user_id)
            return onebot_response(
                echo,
                {"user_id": user_id, "nickname": f"User{user_id}", "sex": "unknown", "age": 0},
            )

        if action in NULL_API_ACTIONS:
            return onebot_response(echo, None)

        if self.config.strict_api:
            LOGGER.warning("unhandled API action in strict mode: %s params=%s", action, params)
            return onebot_response(echo, None, ok=False, message=f"Unhandled mock API: {action}")

        LOGGER.warning("unhandled API action accepted: %s params=%s", action, params)
        return onebot_response(echo, None)

    async def handle_send_api(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if action in {"send_group_msg", "send_group_forward_msg"}:
            message_type = "group"
            group_id = int(params.get("group_id") or self.config.default_group_id)
            user_id = None
        elif action in {"send_private_msg", "send_private_forward_msg"}:
            message_type = "private"
            group_id = None
            user_id = int(params.get("user_id") or self.config.default_user_id)
        else:
            group_id_value = params.get("group_id")
            user_id_value = params.get("user_id")
            if group_id_value is not None:
                message_type = "group"
                group_id = int(group_id_value)
                user_id = None
            else:
                message_type = "private"
                group_id = None
                user_id = int(user_id_value or self.config.default_user_id)

        record = await self.state.record_sent_message(
            action=action,
            message_type=message_type,
            group_id=group_id,
            user_id=user_id,
            message=params.get("message") or params.get("messages") or "[forward]",
            params=params,
        )
        LOGGER.info("bot -> %s %s", message_type, record["raw_message"])
        return record

    async def handle_control_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            request = json.loads(line.decode("utf-8"))
            response = await self.handle_control_request(request)
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}

        writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def handle_control_request(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        if action == "status":
            return {
                "ok": True,
                "connected": self.state.connected,
                "onebot_url": self.config.onebot_url,
                "self_id": self.config.self_id,
                "control_host": self.config.control_host,
                "control_port": self.config.control_port,
            }
        if action == "reset":
            self.state.reset_history()
            return {"ok": True}
        if action == "history":
            limit = int(request.get("limit") or 20)
            return {"ok": True, "messages": self.state.sent_messages[-limit:]}
        if action == "api_calls":
            limit = int(request.get("limit") or 20)
            return {"ok": True, "api_calls": self.state.api_calls[-limit:]}
        if action == "send_message":
            return await self.control_send_message(request)
        return {"ok": False, "error": f"unknown control action: {action}"}

    async def control_send_message(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self.state.connected:
            return {"ok": False, "error": "OneBot WebSocket is not connected"}

        message_type = str(request.get("message_type") or "group")
        group_id = int(request.get("group_id") or self.config.default_group_id)
        user_id = int(request.get("user_id") or self.config.default_user_id)
        role = str(request.get("role") or "member")
        nickname = str(request.get("nickname") or f"User{user_id}")
        text = str(request.get("text") or "")
        wait_timeout = float(request.get("wait_timeout") or 0)
        include_api_calls = bool(request.get("include_api_calls"))

        segments = normalize_message(text)
        if reply_id := request.get("reply_id"):
            segments.insert(0, {"type": "reply", "data": {"id": str(reply_id)}})
        for at_user in reversed(request.get("at") or []):
            segments.insert(0, {"type": "at", "data": {"qq": str(at_user)}})
        for image_url in request.get("image_urls") or []:
            segments.append(
                {
                    "type": "image",
                    "data": {"url": str(image_url), "file": str(image_url)},
                }
            )

        message_id = self.state.allocate_message_id()
        if message_type == "private":
            event = private_message_event(
                self_id=self.config.self_id,
                message_id=message_id,
                user_id=user_id,
                nickname=nickname,
                message=segments,
                raw_message=text,
            )
            target_group_id = None
        else:
            self.state.remember_member(group_id, user_id, nickname, role)
            event = group_message_event(
                self_id=self.config.self_id,
                message_id=message_id,
                group_id=group_id,
                user_id=user_id,
                nickname=nickname,
                role=role,
                message=segments,
                raw_message=text,
            )
            target_group_id = group_id

        since_sequence = self.state.sequence
        self.state.store_incoming(event)
        LOGGER.info("%s -> bot %s", message_type, text)
        await self.send_ws_payload(event)

        replies: list[dict[str, Any]] = []
        if wait_timeout > 0:
            with contextlib.suppress(asyncio.TimeoutError):
                replies = await self.state.wait_for_messages(
                    since_sequence=since_sequence,
                    message_type=message_type,
                    group_id=target_group_id,
                    user_id=user_id,
                    timeout=wait_timeout,
                )

        response: dict[str, Any] = {
            "ok": True,
            "event_message_id": message_id,
            "replies": replies,
        }
        if include_api_calls:
            response["api_calls"] = self.state.api_calls_since(since_sequence)
        return response


async def open_websocket(url: str, headers: dict[str, str]) -> Any:
    try:
        from websockets.asyncio.client import connect
    except ImportError:
        from websockets import connect  # type: ignore[no-redef]

    try:
        return await connect(url, additional_headers=headers)
    except TypeError:
        return await connect(url, extra_headers=headers)


async def run_service(config: MockConfig) -> None:
    service = MockNapCatService(config)
    await service.run()
