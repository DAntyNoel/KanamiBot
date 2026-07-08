from __future__ import annotations

import base64
import imghdr
import json
from dataclasses import dataclass
from typing import Any

import httpx

from .config import CodexGPTConfig


class CodexGPTError(Exception):
    pass


class CodexGPTImageTextResponse(Exception):
    def __init__(self, text: str):
        self.text = text
        super().__init__(text)


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    mime_type: str = "image/png"
    revised_prompt: str | None = None
    text: str | None = None


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


def _extract_text_response(data: Any) -> str:
    candidates: list[str] = []

    if not isinstance(data, dict):
        return ""

    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        candidates.append(output_text.strip())

    for item in _as_dicts(data.get("output")):
        item_type = item.get("type")
        if item_type in {"output_text", "text"}:
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                candidates.append(text.strip())
            continue
        if item_type != "message":
            continue
        text = _final_content_text(item.get("content"))
        if text:
            candidates.append(text)

    for item in _as_dicts(data.get("data")):
        item_type = item.get("type")
        if item_type in {"output_text", "text"}:
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                candidates.append(text.strip())
            continue
        if item_type in {"reasoning", "input_text"}:
            continue
        output_text = item.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            candidates.append(output_text.strip())
        text = _final_content_text(item.get("content"))
        if text:
            candidates.append(text)
        message = item.get("message")
        if isinstance(message, dict):
            text = _final_content_text(message.get("content"))
            if text:
                candidates.append(text)

    for choice in _as_dicts(data.get("choices")):
        message = choice.get("message")
        if isinstance(message, dict):
            text = _final_content_text(message.get("content"))
            if text:
                candidates.append(text)

    return candidates[-1] if candidates else ""


def _as_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _first_image_item(items: list[Any]) -> dict[str, Any] | None:
    for item in items:
        if not isinstance(item, dict):
            continue
        b64_json = item.get("b64_json")
        url = item.get("url")
        if (isinstance(b64_json, str) and b64_json) or (isinstance(url, str) and url):
            return item
    return None


def _final_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            text = item.strip()
            if text:
                parts.append(text)
            continue
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in {"reasoning", "input_text"}:
            continue
        if item_type not in {None, "output_text", "text"}:
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())

    return "\n".join(parts).strip()


class CodexGPTClient:
    def __init__(self, config: CodexGPTConfig, debug: Any | None = None):
        self.config = config
        self.debug = debug

    def _ensure_configured(self) -> None:
        if not self.config.is_configured:
            raise CodexGPTError("未配置 Codex GPT API Key，请在 .env 中设置 CODEX_GPT_API_KEY。")

    def _headers(self) -> dict[str, str]:
        self._ensure_configured()
        return {
            "Authorization": self.config.auth_header,
            "Content-Type": "application/json",
        }

    def _auth_headers(self) -> dict[str, str]:
        self._ensure_configured()
        return {"Authorization": self.config.auth_header}

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        stream: bool | None = None,
    ) -> str:
        use_stream = self.config.stream if stream is None else stream
        model_name = _normalize_model(model or self.config.default_model)
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
        }
        if temperature is not None:
            payload["temperature"] = temperature

        self._debug(
            "chat.request",
            model=model_name,
            stream=use_stream,
            temperature=temperature,
            messages=_summarize_messages(messages),
        )

        if use_stream:
            payload["stream"] = True
            return await self._chat_stream(payload)

        payload["stream"] = False
        return await self._chat_once(payload)

    async def _chat_once(self, payload: dict[str, Any]) -> str:
        self._debug(
            "http.post",
            url=self.config.chat_completions_url,
            model=payload.get("model"),
            stream=False,
        )
        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            response = await client.post(
                self.config.chat_completions_url,
                headers=self._headers(),
                json=payload,
            )
        self._debug(
            "http.response",
            status_code=response.status_code,
            model=payload.get("model"),
            stream=False,
        )

        if response.status_code >= 400:
            self._debug(
                "http.error", status_code=response.status_code, body=_shorten(response.text, 1000)
            )
            raise CodexGPTError(_format_http_error(response))

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            self._debug("http.invalid_json", body=_shorten(response.text, 1000))
            raise CodexGPTError("接口返回了无法解析的 JSON。") from exc

        choices = data.get("choices") or []
        if not choices:
            self._debug("chat.empty_choices", body=_shorten(response.text, 1000))
            raise CodexGPTError("接口没有返回候选回复。")

        message = choices[0].get("message") or {}
        content = _content_to_text(message.get("content")).strip()
        if not content:
            self._debug("chat.empty_content", body=_shorten(response.text, 1000))
            raise CodexGPTError("接口返回了空回复。")
        self._debug("chat.response", chars=len(content), stream=False)
        return content

    async def _chat_stream(self, payload: dict[str, Any]) -> str:
        chunks: list[str] = []
        self._debug(
            "http.post",
            url=self.config.chat_completions_url,
            model=payload.get("model"),
            stream=True,
        )
        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            async with client.stream(
                "POST",
                self.config.chat_completions_url,
                headers=self._headers(),
                json=payload,
            ) as response:
                self._debug(
                    "http.response",
                    status_code=response.status_code,
                    model=payload.get("model"),
                    stream=True,
                )
                if response.status_code >= 400:
                    body = await response.aread()
                    text = body.decode("utf-8", errors="ignore")
                    self._debug(
                        "http.error", status_code=response.status_code, body=_shorten(text, 1000)
                    )
                    raise CodexGPTError(_format_error_text(response.status_code, text))

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    if not line.startswith("{"):
                        continue

                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if data.get("error"):
                        self._debug("stream.api_error", error=data["error"])
                        raise CodexGPTError(_format_api_error(data["error"]))

                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = _content_to_text(delta.get("content"))
                    if text:
                        chunks.append(text)

        content = "".join(chunks).strip()
        if not content:
            self._debug("chat.empty_content", chunk_count=len(chunks), stream=True)
            raise CodexGPTError("接口返回了空回复。")
        self._debug("chat.response", chars=len(content), chunk_count=len(chunks), stream=True)
        return content

    async def list_models(self) -> list[str]:
        self._debug("models.request", url=self.config.models_url)
        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            response = await client.get(self.config.models_url, headers=self._headers())
        self._debug("models.response", status_code=response.status_code)

        if response.status_code >= 400:
            self._debug(
                "models.error", status_code=response.status_code, body=_shorten(response.text, 1000)
            )
            raise CodexGPTError(_format_http_error(response))

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            self._debug("models.invalid_json", body=_shorten(response.text, 1000))
            raise CodexGPTError("模型列表接口返回了无法解析的 JSON。") from exc

        models = []
        for item in data.get("data", []):
            model_id = item.get("id")
            if isinstance(model_id, str):
                models.append(model_id)
        self._debug("models.loaded", count=len(models))
        return models

    async def create_image(
        self,
        prompt: str,
        model: str | None = None,
        images: list[bytes] | None = None,
    ) -> GeneratedImage:
        model_name = _normalize_model(model or self.config.default_model)
        image_inputs = images or []
        if image_inputs:
            return await self._edit_image(prompt, model_name, image_inputs)
        return await self._generate_image(prompt, model_name)

    async def _generate_image(self, prompt: str, model: str) -> GeneratedImage:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
        }
        if self.config.image_size:
            payload["size"] = self.config.image_size

        self._debug(
            "image.request",
            endpoint="generations",
            model=model,
            prompt_chars=len(prompt),
            size=self.config.image_size,
            timeout=self.config.image_timeout_seconds,
        )
        async with httpx.AsyncClient(timeout=self.config.image_timeout_seconds) as client:
            response = await client.post(
                self.config.images_generations_url,
                headers=self._headers(),
                json=payload,
            )
        self._debug(
            "image.response", endpoint="generations", status_code=response.status_code, model=model
        )

        if response.status_code >= 400:
            self._debug(
                "image.error",
                endpoint="generations",
                status_code=response.status_code,
                body=_shorten(response.text, 1000),
            )
            raise CodexGPTError(_format_http_error(response))
        return await self._parse_image_response(response)

    async def _edit_image(self, prompt: str, model: str, images: list[bytes]) -> GeneratedImage:
        data: dict[str, str] = {
            "model": model,
            "prompt": prompt,
        }
        if self.config.image_size:
            data["size"] = self.config.image_size

        files = []
        for index, image in enumerate(images):
            mime_type = _guess_image_mime(image)
            ext = mime_type.rsplit("/", 1)[-1].replace("jpeg", "jpg")
            files.append(("image", (f"image_{index}.{ext}", image, mime_type)))

        self._debug(
            "image.request",
            endpoint="edits",
            model=model,
            prompt_chars=len(prompt),
            image_count=len(images),
            size=self.config.image_size,
            timeout=self.config.image_timeout_seconds,
        )
        async with httpx.AsyncClient(timeout=self.config.image_timeout_seconds) as client:
            response = await client.post(
                self.config.images_edits_url,
                headers=self._auth_headers(),
                data=data,
                files=files,
            )
        self._debug(
            "image.response", endpoint="edits", status_code=response.status_code, model=model
        )

        if response.status_code >= 400:
            self._debug(
                "image.error",
                endpoint="edits",
                status_code=response.status_code,
                body=_shorten(response.text, 1000),
            )
            raise CodexGPTError(_format_http_error(response))
        return await self._parse_image_response(response)

    async def _parse_image_response(self, response: httpx.Response) -> GeneratedImage:
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            self._debug("image.invalid_json", body=_shorten(response.text, 1000))
            raise CodexGPTError("图片接口返回了无法解析的 JSON。") from exc

        items = data.get("data") or []
        if not items or not isinstance(items[0], dict):
            fallback_text = _extract_text_response(data)
            if fallback_text:
                self._debug("image.text_response", source="empty_data", chars=len(fallback_text))
                raise CodexGPTImageTextResponse(fallback_text)
            self._debug("image.empty_data", body=_shorten(response.text, 1000))
            raise CodexGPTError("图片接口没有返回图片数据。")

        item = _first_image_item(items) or items[0]
        text_response = _extract_text_response(data) or None
        revised_prompt = (
            item.get("revised_prompt") if isinstance(item.get("revised_prompt"), str) else None
        )
        b64_json = item.get("b64_json")
        if isinstance(b64_json, str) and b64_json:
            try:
                image_bytes = base64.b64decode(b64_json)
            except ValueError as exc:
                self._debug("image.invalid_base64")
                raise CodexGPTError("图片接口返回了无法解码的 base64 图片。") from exc
            mime_type = _guess_image_mime(image_bytes)
            self._debug(
                "image.parsed", source="b64_json", bytes=len(image_bytes), mime_type=mime_type
            )
            return GeneratedImage(
                image_bytes,
                mime_type=mime_type,
                revised_prompt=revised_prompt,
                text=text_response,
            )

        url = item.get("url")
        if isinstance(url, str) and url:
            async with httpx.AsyncClient(timeout=self.config.image_timeout_seconds) as client:
                image_response = await client.get(url)
            if image_response.status_code >= 400:
                self._debug(
                    "image.download_error",
                    status_code=image_response.status_code,
                    url=_shorten(url, 200),
                )
                raise CodexGPTError(_format_http_error(image_response))
            image_bytes = image_response.content
            mime_type = image_response.headers.get("content-type") or _guess_image_mime(image_bytes)
            self._debug("image.parsed", source="url", bytes=len(image_bytes), mime_type=mime_type)
            return GeneratedImage(
                image_bytes,
                mime_type=mime_type,
                revised_prompt=revised_prompt,
                text=text_response,
            )

        fallback_text = _extract_text_response(data)
        if fallback_text:
            self._debug("image.text_response", source="missing_payload", chars=len(fallback_text))
            raise CodexGPTImageTextResponse(fallback_text)

        self._debug("image.missing_payload", body=_shorten(response.text, 1000))
        raise CodexGPTError("图片接口未返回 b64_json 或 url。")

    def _debug(self, event: str, **fields: Any) -> None:
        if self.debug is not None:
            self.debug.log(event, **fields)


def _normalize_model(model: str) -> str:
    model = model.strip()
    if model == "gpt-5":
        return f"openai/{model}"
    return model


def _summarize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for message in messages:
        content = message.get("content", "")
        text = _content_to_text(content)
        summary.append(
            {
                "role": message.get("role"),
                "chars": len(text),
                "tail": _shorten(text[-80:], 80),
            }
        )
    return summary


def _guess_image_mime(data: bytes) -> str:
    kind = imghdr.what(None, data)
    if kind == "jpg":
        kind = "jpeg"
    if kind in {"png", "jpeg", "gif", "webp", "bmp"}:
        return f"image/{kind}"
    return "image/png"


def _format_api_error(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message") or error.get("code") or str(error)
        return f"接口错误：{message}"
    return f"接口错误：{error}"


def _format_http_error(response: httpx.Response) -> str:
    return _format_error_text(response.status_code, response.text)


def _format_error_text(status_code: int, text: str) -> str:
    text = (text or "").strip()
    if len(text) > 500:
        text = text[:500] + "..."
    return f"接口请求失败：HTTP {status_code}" + (f"\n{text}" if text else "")


def _shorten(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
