"""AI provider adapters.

* ``home_assistant`` – (default) Home Assistant's AI Task integration
                   (``ai_task.generate_data``).  Uses whatever model the user
                   already configured in Home Assistant (e.g. Google Gemini,
                   including its free tier), so no AI key is stored in the App.
* ``anthropic``  – Claude via the official Anthropic SDK, structured JSON output.
* ``openai``     – any OpenAI-compatible Chat Completions endpoint (OpenAI,
                   Azure-compatible gateways, OpenRouter, LM Studio, vLLM).
* ``ollama``     – local Ollama server (OpenAI-compatible endpoint); no key.
* ``gemini``     – Google Gemini REST API.

Each adapter implements ``generate_json(prompt, schema, image, timeout)``.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Protocol

import httpx

from ..config import Settings


class AIProviderError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class Provider(Protocol):
    name: str
    model: str

    def generate_json(
        self, prompt: str, schema: dict[str, Any], image: tuple[bytes, str] | None, timeout: float
    ) -> dict[str, Any] | str: ...


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[:-3]
    start, end = t.find("{"), t.rfind("}")
    return t[start : end + 1] if start != -1 and end != -1 else t


class AnthropicProvider:
    name = "anthropic"
    DEFAULT_MODEL = "claude-opus-5-5"

    def __init__(self, api_key: str, model: str | None, base_url: str | None) -> None:
        import anthropic

        if not api_key:
            raise AIProviderError("An Anthropic API key is required")
        self._anthropic = anthropic
        kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": 0}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = anthropic.Anthropic(**kwargs)
        self.model = model or self.DEFAULT_MODEL

    def generate_json(self, prompt, schema, image, timeout):
        a = self._anthropic
        content: list[dict[str, Any]] = []
        if image is not None:
            data, mime = image
            content.append(
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.standard_b64encode(data).decode()}}
            )
        content.append({"type": "text", "text": prompt})
        try:
            response = self.client.with_options(timeout=timeout).messages.create(
                model=self.model,
                max_tokens=16000,
                messages=[{"role": "user", "content": content}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except a.RateLimitError as err:
            raise AIProviderError("rate limited by Anthropic", retryable=True) from err
        except a.APITimeoutError as err:
            raise AIProviderError("Anthropic request timed out", retryable=True) from err
        except a.APIConnectionError as err:
            raise AIProviderError("could not reach Anthropic", retryable=True) from err
        except a.AuthenticationError as err:
            raise AIProviderError("Anthropic rejected the API key") from err
        except a.BadRequestError as err:
            raise AIProviderError(f"Anthropic rejected the request: {err.message}") from err
        except a.APIStatusError as err:
            raise AIProviderError(f"Anthropic error {err.status_code}", retryable=err.status_code >= 500) from err
        if response.stop_reason == "refusal":
            raise AIProviderError("The model declined to analyse this image")
        if response.stop_reason == "max_tokens":
            raise AIProviderError("The response was cut off", retryable=True)
        text = next((b.text for b in response.content if b.type == "text"), "")
        return json.loads(text)


class OpenAICompatibleProvider:
    def __init__(self, name: str, api_key: str, model: str, base_url: str, json_schema: bool = True) -> None:
        self.name = name
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.json_schema = json_schema

    def generate_json(self, prompt, schema, image, timeout):
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if image is not None:
            data, mime = image
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}
            )
        body: dict[str, Any] = {"model": self.model, "messages": [{"role": "user", "content": content}]}
        if self.json_schema:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}}
        else:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=timeout)
        except httpx.TimeoutException as err:
            raise AIProviderError(f"{self.name} request timed out", retryable=True) from err
        except httpx.HTTPError as err:
            raise AIProviderError(f"could not reach {self.name}: {err}", retryable=True) from err
        if r.status_code in (429,) or r.status_code >= 500:
            raise AIProviderError(f"{self.name} error {r.status_code}", retryable=True)
        if r.status_code >= 400:
            raise AIProviderError(f"{self.name} rejected the request ({r.status_code}): {r.text[:200]}")
        try:
            msg = r.json()["choices"][0]["message"]
        except (KeyError, IndexError, ValueError) as err:
            raise AIProviderError(f"unexpected {self.name} response", retryable=True) from err
        if msg.get("refusal"):
            raise AIProviderError("The model declined the request")
        return _strip_fences(msg.get("content") or "")


class GeminiProvider:
    name = "gemini"
    DEFAULT_MODEL = "gemini-2.5-flash"

    def __init__(self, api_key: str, model: str | None, base_url: str | None) -> None:
        if not api_key:
            raise AIProviderError("A Gemini API key is required")
        self.api_key = api_key
        self.model = model or self.DEFAULT_MODEL
        self.base_url = (base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

    def generate_json(self, prompt, schema, image, timeout):
        parts: list[dict[str, Any]] = []
        if image is not None:
            data, mime = image
            parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
        parts.append({"text": prompt})
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema},
        }
        try:
            r = httpx.post(
                f"{self.base_url}/models/{self.model}:generateContent",
                json=body,
                headers={"x-goog-api-key": self.api_key},
                timeout=timeout,
            )
        except httpx.TimeoutException as err:
            raise AIProviderError("Gemini request timed out", retryable=True) from err
        except httpx.HTTPError as err:
            raise AIProviderError(f"could not reach Gemini: {err}", retryable=True) from err
        if r.status_code == 429 or r.status_code >= 500:
            raise AIProviderError(f"Gemini error {r.status_code}", retryable=True)
        if r.status_code >= 400:
            raise AIProviderError(f"Gemini rejected the request ({r.status_code}): {r.text[:200]}")
        try:
            cand = r.json()["candidates"][0]
            text = "".join(p.get("text", "") for p in cand["content"]["parts"])
        except (KeyError, IndexError, ValueError) as err:
            raise AIProviderError("Gemini returned no answer (possibly blocked)") from err
        return _strip_fences(text)


class HomeAssistantAITaskProvider:
    """Runs prompts through Home Assistant's ``ai_task.generate_data`` action.

    Images are passed as media-source attachments: the App writes a
    temporary JPEG into the shared ``/media`` folder (mapped read/write) and
    references it as ``media-source://media_source/local/...``; the file is
    deleted as soon as the call returns.
    """

    name = "home_assistant"
    MEDIA_SUBDIR = "home_inventory/.ai-tmp"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.supervisor_token:
            self.base = settings.supervisor_url.rstrip("/") + "/core/api"
            self.token = settings.supervisor_token
        elif settings.ha_url and settings.ha_token:
            self.base = settings.ha_url.rstrip("/") + "/api"
            self.token = settings.ha_token
        else:
            raise AIProviderError("Home Assistant API access is not available to the App")
        self.media_root = settings.ha_media_dir
        self.entity_id = settings.ai_task_entity or None
        self.model = self.entity_id or "auto"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _resolve_entity(self, timeout: float) -> str:
        if self.entity_id:
            return self.entity_id
        try:
            r = httpx.get(f"{self.base}/states", headers=self._headers(), timeout=timeout)
            r.raise_for_status()
        except httpx.HTTPError as err:
            raise AIProviderError(f"could not list Home Assistant AI Task entities: {err}", retryable=True) from err
        # Supported feature 2 = attachments; prefer entities that accept images.
        candidates = [s for s in r.json() if s["entity_id"].startswith("ai_task.")]
        candidates.sort(key=lambda s: -(int(s["attributes"].get("supported_features", 0)) & 2))
        if not candidates:
            raise AIProviderError(
                "No AI Task entity found in Home Assistant. Add an AI integration such as Google Gemini "
                "(Settings > Devices & services) or pick another AI provider."
            )
        self.entity_id = candidates[0]["entity_id"]
        self.model = self.entity_id
        return self.entity_id

    def generate_json(self, prompt, schema, image, timeout):
        import secrets as _secrets

        entity = self._resolve_entity(timeout)
        instructions = (
            f"{prompt}\n\nRespond with a single JSON object only (no prose, no code fences) that conforms to this "
            f"JSON Schema:\n{json.dumps(schema)}"
        )
        body: dict[str, Any] = {"task_name": "home_inventory", "entity_id": entity, "instructions": instructions}
        tmp = None
        if image is not None:
            data, _mime = image
            folder = self.media_root / self.MEDIA_SUBDIR
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / f"{_secrets.token_hex(12)}.jpg"
            tmp.write_bytes(data)
            body["attachments"] = [
                {
                    "media_content_id": f"media-source://media_source/local/{self.MEDIA_SUBDIR}/{tmp.name}",
                    "media_content_type": "image/jpeg",
                }
            ]
        try:
            r = httpx.post(
                f"{self.base}/services/ai_task/generate_data?return_response",
                json=body,
                headers=self._headers(),
                timeout=timeout,
            )
        except httpx.TimeoutException as err:
            raise AIProviderError("Home Assistant AI Task timed out", retryable=True) from err
        except httpx.HTTPError as err:
            raise AIProviderError(f"could not reach Home Assistant: {err}", retryable=True) from err
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        if r.status_code >= 500:
            raise AIProviderError(f"Home Assistant AI Task failed ({r.status_code}): {r.text[:200]}", retryable=True)
        if r.status_code >= 400:
            raise AIProviderError(f"Home Assistant rejected the AI Task ({r.status_code}): {r.text[:200]}")
        try:
            data = r.json()["service_response"]["data"]
        except (KeyError, ValueError) as err:
            raise AIProviderError("unexpected AI Task response") from err
        if isinstance(data, dict):
            return data
        return _strip_fences(str(data))


def build_provider(settings: Settings) -> Provider:
    p = settings.ai_provider
    if p in ("home_assistant", "ha", "ai_task"):
        return HomeAssistantAITaskProvider(settings)
    if p == "anthropic":
        return AnthropicProvider(settings.ai_api_key, settings.ai_model or None, settings.ai_base_url or None)
    if p == "openai":
        if not settings.ai_api_key and not settings.ai_base_url:
            raise AIProviderError("An OpenAI API key is required")
        return OpenAICompatibleProvider(
            "openai", settings.ai_api_key, settings.ai_model or "gpt-4.1-mini", settings.ai_base_url or "https://api.openai.com/v1"
        )
    if p == "ollama":
        return OpenAICompatibleProvider(
            "ollama",
            settings.ai_api_key,
            settings.ai_model or "qwen2.5vl:7b",
            settings.ai_base_url or "http://homeassistant.local:11434/v1",
            json_schema=False,
        )
    if p == "gemini":
        return GeminiProvider(settings.ai_api_key, settings.ai_model or None, settings.ai_base_url or None)
    raise AIProviderError(f"Unknown AI provider '{p}'")
