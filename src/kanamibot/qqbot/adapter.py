from __future__ import annotations

from nonebot.adapters.qq import Adapter
from nonebot.adapters.qq.event import Event
from nonebot.adapters.qq.models import Dispatch


class QQAdapter(Adapter):
    @staticmethod
    def payload_to_event(payload: Dispatch) -> Event:
        if (
            payload.type in {"GROUP_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"}
            and isinstance(payload.data, dict)
            and "group_id" not in payload.data
            and payload.data.get("group_openid")
        ):
            payload = payload.model_copy(update={
                "data": {**payload.data, "group_id": payload.data["group_openid"]},
            })
        return Adapter.payload_to_event(payload)
