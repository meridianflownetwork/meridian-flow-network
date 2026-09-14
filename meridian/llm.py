"""Unified OpenAI / Anthropic client with JSON-first responses."""

from __future__ import annotations

import json
import re
from typing import Any

from meridian.brand import fail, info, warn
from meridian.config import Settings

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-0",
    "mock": "local-mock",
}


class LLMClient:
    def __init__(self, settings: Settings, *, mock: bool = False) -> None:
        requested = "mock" if mock else settings.llm_provider
        self.provider = requested if requested in DEFAULT_MODELS else "openai"
        self.model = settings.llm_model or DEFAULT_MODELS[self.provider]
        self._openai = None
        self._anthropic = None
        self._mock = self.provider == "mock"

        if self._mock:
            info("llm", "mock · local templates (no API credits)")
            return

        if self.provider == "anthropic":
            if not settings.anthropic_api_key:
                fail("llm", "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty")
            import anthropic

            self._anthropic = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        else:
            if not settings.openai_api_key:
                fail("llm", "OPENAI_API_KEY is empty — set it or switch LLM_PROVIDER=mock")
            from openai import OpenAI

            self._openai = OpenAI(api_key=settings.openai_api_key)

        info("llm", f"{self.provider} · {self.model}")

    def complete(self, *, system: str, user: str, temperature: float = 0.2) -> str:
        if self._mock:
            return json.dumps({"error": "use complete_json in mock mode"})
        if self._openai is not None:
            response = self._openai.chat.completions.create(
                model=self.model,
                temperature=temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return response.choices[0].message.content or "{}"

        response = self._anthropic.messages.create(
            model=self.model,
            max_tokens=2200,
            temperature=temperature,
            system=system + "\nReturn a single valid JSON object and nothing else.",
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in response.content if getattr(block, "type", "") == "text")

    def complete_json(self, *, system: str, user: str, temperature: float = 0.2) -> dict[str, Any]:
        raw = self.complete(system=system, user=user, temperature=temperature)
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    pass
            warn("llm", "Model returned non-JSON — using empty object")
        return {}
