"""Registry for model API providers only."""

from collections.abc import Iterable
from dataclasses import dataclass

from app.core.config import ProviderDefinition

BUILT_IN_PROVIDERS: tuple[ProviderDefinition, ...] = (
    ProviderDefinition(
        id="gemini",
        label="Google Gemini",
        kind="google-gla",
        base_url=None,
        key_ref="GEMINI_API_KEY",
        models=["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3-flash-preview"],
        data_location="cloud",
        experimental=False,
    ),
    ProviderDefinition(
        id="openai",
        label="OpenAI",
        kind="openai",
        base_url=None,
        key_ref="OPENAI_API_KEY",
        models=["gpt-5.4-mini", "gpt-5.4-nano"],
        data_location="cloud",
        experimental=False,
    ),
    ProviderDefinition(
        id="anthropic",
        label="Anthropic",
        kind="anthropic",
        base_url=None,
        key_ref="ANTHROPIC_API_KEY",
        models=["claude-haiku-4-5-20251001"],
        data_location="cloud",
        experimental=False,
    ),
    ProviderDefinition(
        id="ollama",
        label="Ollama (local)",
        kind="ollama",
        base_url="http://localhost:11434/v1",
        key_ref=None,
        models=None,
        data_location="local",
        experimental=False,
    ),
)


@dataclass(frozen=True)
class ProviderRegistry:
    """Merged view of built-in providers and user-defined provider overrides."""

    providers: tuple[ProviderDefinition, ...]

    @classmethod
    def from_user_providers(cls, user_providers: Iterable[ProviderDefinition]) -> "ProviderRegistry":
        user_by_id = {p.id: p for p in user_providers}
        merged: list[ProviderDefinition] = []
        built_in_ids: set[str] = set()
        for built_in in BUILT_IN_PROVIDERS:
            built_in_ids.add(built_in.id)
            merged.append(user_by_id.get(built_in.id, built_in))
        for provider in user_providers:
            if provider.id not in built_in_ids:
                merged.append(provider)
        return cls(tuple(merged))

    def with_ollama_base_url(self, base_url: str) -> "ProviderRegistry":
        return ProviderRegistry(
            tuple(
                ProviderDefinition(
                    id=p.id,
                    label=p.label,
                    kind=p.kind,
                    base_url=base_url,
                    key_ref=p.key_ref,
                    models=p.models,
                    data_location=p.data_location,
                    experimental=p.experimental,
                )
                if p.id == "ollama" and p.base_url != base_url
                else p
                for p in self.providers
            )
        )

    def as_list(self) -> list[ProviderDefinition]:
        return list(self.providers)


__all__ = ["BUILT_IN_PROVIDERS", "ProviderRegistry"]
