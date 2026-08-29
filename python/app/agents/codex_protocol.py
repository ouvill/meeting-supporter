"""Typed stable subset of the Codex app-server protocol.

The protocol is JSONL over stdio. Models ignore additional fields so newer
releases may extend payloads, while required fields and literals still fail
closed when the consumed contract changes.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class CodexProtocolModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", populate_by_name=True)


class EmptyParams(CodexProtocolModel):
    pass


class EmptyResult(CodexProtocolModel):
    pass


class ClientInfo(CodexProtocolModel):
    name: str
    version: str
    title: str | None = None


class InitializeCapabilities(CodexProtocolModel):
    experimental_api: bool = Field(default=False, alias="experimentalApi")
    request_attestation: bool = Field(default=False, alias="requestAttestation")
    mcp_server_openai_form_elicitation: bool = Field(default=False, alias="mcpServerOpenaiFormElicitation")
    opt_out_notification_methods: list[str] | None = Field(default=None, alias="optOutNotificationMethods")


class InitializeParams(CodexProtocolModel):
    client_info: ClientInfo = Field(alias="clientInfo")
    capabilities: InitializeCapabilities | None = None


class InitializeResult(CodexProtocolModel):
    user_agent: str = Field(alias="userAgent")
    platform_family: str = Field(alias="platformFamily")
    platform_os: str = Field(alias="platformOs")
    codex_home: str = Field(alias="codexHome")


class AccountReadParams(CodexProtocolModel):
    refresh_token: bool = Field(default=False, alias="refreshToken")


class Account(CodexProtocolModel):
    type: Literal["apiKey", "chatgpt", "amazonBedrock"]
    plan_type: str | None = Field(default=None, alias="planType")


class AccountReadResult(CodexProtocolModel):
    account: Account | None = None
    requires_openai_auth: bool = Field(alias="requiresOpenaiAuth")


class ChatGptLoginStartParams(CodexProtocolModel):
    type: Literal["chatgpt"] = "chatgpt"
    app_brand: Literal["codex", "chatgpt"] | None = Field(default="codex", alias="appBrand")
    codex_streamlined_login: bool = Field(default=False, alias="codexStreamlinedLogin")
    use_hosted_login_success_page: bool = Field(default=False, alias="useHostedLoginSuccessPage")


class DeviceCodeLoginStartParams(CodexProtocolModel):
    type: Literal["chatgptDeviceCode"] = "chatgptDeviceCode"


class ChatGptLoginStartResult(CodexProtocolModel):
    type: Literal["chatgpt"]
    login_id: str = Field(alias="loginId")
    auth_url: str = Field(alias="authUrl")


class DeviceCodeLoginStartResult(CodexProtocolModel):
    type: Literal["chatgptDeviceCode"]
    login_id: str = Field(alias="loginId")
    user_code: SecretStr = Field(alias="userCode")
    verification_url: str = Field(alias="verificationUrl")


class LoginCancelParams(CodexProtocolModel):
    login_id: str = Field(alias="loginId")


class LoginCancelResult(CodexProtocolModel):
    status: Literal["canceled", "notFound"]


class AccountLoginCompletedNotification(CodexProtocolModel):
    success: bool
    login_id: str | None = Field(default=None, alias="loginId")
    error: SecretStr | None = None


class AccountUpdatedNotification(CodexProtocolModel):
    auth_mode: str | None = Field(default=None, alias="authMode")
    plan_type: str | None = Field(default=None, alias="planType")


class RateLimitWindow(CodexProtocolModel):
    used_percent: int = Field(alias="usedPercent")
    resets_at: int | None = Field(default=None, alias="resetsAt")
    window_duration_mins: int | None = Field(default=None, alias="windowDurationMins")


class CreditsSnapshot(CodexProtocolModel):
    has_credits: bool = Field(alias="hasCredits")
    unlimited: bool
    balance: SecretStr | None = None


class RateLimitSnapshot(CodexProtocolModel):
    limit_id: str | None = Field(default=None, alias="limitId")
    limit_name: str | None = Field(default=None, alias="limitName")
    plan_type: str | None = Field(default=None, alias="planType")
    primary: RateLimitWindow | None = None
    secondary: RateLimitWindow | None = None
    credits: CreditsSnapshot | None = None
    rate_limit_reached_type: str | None = Field(default=None, alias="rateLimitReachedType")


class RateLimitResetCreditsSummary(CodexProtocolModel):
    available_count: int = Field(alias="availableCount")


class RateLimitsReadResult(CodexProtocolModel):
    rate_limits: RateLimitSnapshot = Field(alias="rateLimits")
    rate_limits_by_limit_id: dict[str, RateLimitSnapshot] | None = Field(default=None, alias="rateLimitsByLimitId")
    rate_limit_reset_credits: RateLimitResetCreditsSummary | None = Field(default=None, alias="rateLimitResetCredits")


class AccountRateLimitsUpdatedNotification(CodexProtocolModel):
    rate_limits: RateLimitSnapshot = Field(alias="rateLimits")


class ModelListParams(CodexProtocolModel):
    cursor: str | None = None
    limit: int | None = None
    include_hidden: bool | None = Field(default=None, alias="includeHidden")


class ModelReasoningEffort(CodexProtocolModel):
    reasoning_effort: str = Field(alias="reasoningEffort")


class ModelServiceTier(CodexProtocolModel):
    id: str


class ModelListItem(CodexProtocolModel):
    model: str
    hidden: bool
    supported_reasoning_efforts: list[ModelReasoningEffort] = Field(alias="supportedReasoningEfforts")
    service_tiers: list[ModelServiceTier] = Field(default_factory=list, alias="serviceTiers")


class ModelListResult(CodexProtocolModel):
    data: list[ModelListItem]
    next_cursor: str | None = Field(default=None, alias="nextCursor")


class ThreadStartParams(CodexProtocolModel):
    model: str | None = None
    service_tier: str | None = Field(default=None, alias="serviceTier")
    cwd: str
    approval_policy: Literal["never"] = Field(default="never", alias="approvalPolicy")
    sandbox: Literal["read-only"] = "read-only"
    ephemeral: Literal[True] = True
    config: dict[str, object]
    base_instructions: str = Field(alias="baseInstructions")
    developer_instructions: str = Field(alias="developerInstructions")


class Thread(CodexProtocolModel):
    id: str


class ReadOnlySandboxPolicy(CodexProtocolModel):
    type: Literal["readOnly"]
    network_access: Literal[False] = Field(default=False, alias="networkAccess")


class ThreadStartResult(CodexProtocolModel):
    thread: Thread
    approval_policy: Literal["never"] = Field(alias="approvalPolicy")
    cwd: str
    model: str
    model_provider: str = Field(alias="modelProvider")
    sandbox: ReadOnlySandboxPolicy
    service_tier: str | None = Field(default=None, alias="serviceTier")


class ThreadUnsubscribeParams(CodexProtocolModel):
    thread_id: str = Field(alias="threadId")


class ThreadUnsubscribeResult(CodexProtocolModel):
    status: Literal["unsubscribed", "notSubscribed"]


class TextUserInput(CodexProtocolModel):
    type: Literal["text"] = "text"
    text: str


class TurnStartParams(CodexProtocolModel):
    thread_id: str = Field(alias="threadId")
    input: list[TextUserInput]
    approval_policy: Literal["never"] = Field(default="never", alias="approvalPolicy")
    cwd: str
    model: str | None = None
    effort: Literal["low"] | None = None
    service_tier: str | None = Field(default=None, alias="serviceTier")


class TurnError(CodexProtocolModel):
    message: SecretStr
    codex_error_info: str | dict[str, object] | None = Field(default=None, alias="codexErrorInfo")


class ErrorNotification(CodexProtocolModel):
    thread_id: str = Field(alias="threadId")
    turn_id: str = Field(alias="turnId")
    error: TurnError
    will_retry: bool = Field(alias="willRetry")


class Turn(CodexProtocolModel):
    id: str
    status: Literal["completed", "interrupted", "failed", "inProgress"]
    error: TurnError | None = None
    service_tier: str | None = Field(default=None, alias="serviceTier")


class TurnStartResult(CodexProtocolModel):
    turn: Turn


class AgentMessageDeltaNotification(CodexProtocolModel):
    thread_id: str = Field(alias="threadId")
    turn_id: str = Field(alias="turnId")
    item_id: str = Field(alias="itemId")
    delta: str


class TurnCompletedNotification(CodexProtocolModel):
    thread_id: str = Field(alias="threadId")
    turn: Turn


class ModelReroutedNotification(CodexProtocolModel):
    thread_id: str = Field(alias="threadId")
    turn_id: str = Field(alias="turnId")
    from_model: str = Field(alias="fromModel")
    to_model: str = Field(alias="toModel")
    reason: Literal["highRiskCyberActivity"]


class TurnInterruptParams(CodexProtocolModel):
    thread_id: str = Field(alias="threadId")
    turn_id: str = Field(alias="turnId")


class TurnInterruptResult(EmptyResult):
    pass


__all__ = [
    "AccountLoginCompletedNotification",
    "AccountRateLimitsUpdatedNotification",
    "AccountReadParams",
    "AccountReadResult",
    "AccountUpdatedNotification",
    "AgentMessageDeltaNotification",
    "ChatGptLoginStartParams",
    "ChatGptLoginStartResult",
    "ClientInfo",
    "CodexProtocolModel",
    "DeviceCodeLoginStartParams",
    "DeviceCodeLoginStartResult",
    "ErrorNotification",
    "EmptyParams",
    "EmptyResult",
    "InitializeCapabilities",
    "InitializeParams",
    "InitializeResult",
    "LoginCancelParams",
    "LoginCancelResult",
    "ModelListItem",
    "ModelListParams",
    "ModelListResult",
    "ModelReasoningEffort",
    "ModelServiceTier",
    "ModelReroutedNotification",
    "RateLimitSnapshot",
    "RateLimitsReadResult",
    "TextUserInput",
    "ThreadStartParams",
    "ThreadStartResult",
    "ReadOnlySandboxPolicy",
    "ThreadUnsubscribeParams",
    "ThreadUnsubscribeResult",
    "TurnCompletedNotification",
    "TurnInterruptParams",
    "TurnStartParams",
    "TurnStartResult",
    "TurnInterruptResult",
]
