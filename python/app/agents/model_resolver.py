"""Resolve Pydantic AI models from schema-v2 runtime routes."""

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.core.config import ProviderDefinition, RouteDefinition
from app.core.protocols import SecretStore


def provider_for_route(
    route: RouteDefinition,
    providers: list[ProviderDefinition],
) -> ProviderDefinition:
    """Resolve the model provider named by a Pydantic AI route."""

    if route.runtime != "pydantic-ai" or not route.provider_id:
        raise ValueError(f"route '{route.id}' はmodel provider routeではありません")
    provider = next((candidate for candidate in providers if candidate.id == route.provider_id), None)
    if provider is None:
        raise ValueError(f"route '{route.id}' のprovider '{route.provider_id}' が未定義です")
    return provider


def resolve_route_model(
    route: RouteDefinition,
    providers: list[ProviderDefinition],
    secret_store: SecretStore,
) -> OpenAIChatModel | str:
    """Resolve a Pydantic AI route without accepting legacy model strings."""

    provider = provider_for_route(route, providers)
    model_name = route.model
    if not model_name:
        raise ValueError(f"route '{route.id}' にはmodelが必要です")

    if provider.key_ref and not secret_store.get(provider.key_ref):
        raise ValueError(f"route '{route.id}' の認証情報が未設定です")

    if provider.kind in (
        "google-gla",
        "google-vertex",
        "openai",
        "openai-chat",
        "openai-responses",
        "anthropic",
        "ollama",
    ):
        return f"{provider.kind}:{model_name}"

    if provider.kind == "openai-compatible":
        if not provider.base_url:
            raise ValueError(f"route '{route.id}' のproviderにはbase_urlが必要です")
        api_key = secret_store.get(provider.key_ref) if provider.key_ref else "local-provider"
        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(base_url=provider.base_url, api_key=api_key or "local-provider"),
        )

    raise ValueError(f"route '{route.id}' のprovider kindは未対応です")


__all__ = ["provider_for_route", "resolve_route_model"]
