//! Poem routes and their concrete DTO contracts share one definition.
use super::*;
use dto::*;
use poem_openapi::{
    auth::Bearer,
    param::{Header, Path, Query},
    payload::Binary,
    OpenApi, SecurityScheme,
};

#[derive(SecurityScheme)]
#[oai(ty = "bearer")]
struct DesktopAuth(#[allow(dead_code)] Bearer);

pub(super) struct HttpApi;
type Reply<T> = Result<Json<T>, ApiError>;

#[OpenApi]
impl HttpApi {
    #[oai(path = "/health", method = "get", operation_id = "health_health_get")]
    async fn health(&self, _auth: DesktopAuth) -> Reply<HealthResponse> {
        Ok(Json(HealthResponse {
            status: "ok".into(),
            runtime: "rust".into(),
        }))
    }
    #[oai(
        path = "/api/settings",
        method = "get",
        operation_id = "get_settings_api_settings_get"
    )]
    async fn settings(&self, Data(api): Data<&Api>, _auth: DesktopAuth) -> Reply<SettingsResponse> {
        response(operations::settings_get(api.clone()).await?)
    }
    #[oai(
        path = "/api/settings",
        method = "post",
        operation_id = "save_settings_api_settings_post"
    )]
    async fn save_settings(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        body: Json<SettingsSaveRequest>,
    ) -> Reply<SaveSettingsResponse> {
        response(operations::settings_save(api.clone(), request(body.0)?).await?)
    }
    #[oai(
        path = "/api/stt/capabilities",
        method = "get",
        operation_id = "get_speech_capabilities"
    )]
    async fn capabilities(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
    ) -> Reply<SpeechCapabilities> {
        response(operations::speech_capabilities(api.clone()).await)
    }
    #[oai(
        path = "/api/stt/model",
        method = "get",
        operation_id = "get_speech_model_status_api_stt_model_get"
    )]
    async fn model_status(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        backend: Query<SpeechBackend>,
        language: Query<ModelLanguage>,
        model: Query<Option<WhisperModel>>,
    ) -> Reply<SpeechModelStatusResponse> {
        response(
            operations::model_status(
                api.clone(),
                request(SpeechModelDownloadRequest {
                    backend: backend.0,
                    language: language.0,
                    model: model.0,
                })?,
            )
            .await?,
        )
    }
    #[oai(
        path = "/api/stt/model/download",
        method = "post",
        operation_id = "start_speech_model_download_api_stt_model_download_post"
    )]
    async fn model_download(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        body: Json<SpeechModelDownloadRequest>,
    ) -> Reply<SpeechModelStatusResponse> {
        response(operations::model_download(api.clone(), request(body.0)?).await?)
    }
    #[oai(
        path = "/api/stt/model/cancel",
        method = "post",
        operation_id = "cancel_speech_model_download_api_stt_model_cancel_post"
    )]
    async fn model_cancel(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        backend: Query<SpeechBackend>,
        language: Query<ModelLanguage>,
        model: Query<Option<WhisperModel>>,
    ) -> Reply<SpeechModelStatusResponse> {
        response(
            operations::model_cancel(
                api.clone(),
                request(SpeechModelDownloadRequest {
                    backend: backend.0,
                    language: language.0,
                    model: model.0,
                })?,
            )
            .await?,
        )
    }
    #[oai(
        path = "/meetings",
        method = "get",
        operation_id = "list_meetings_meetings_get"
    )]
    async fn meetings(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        #[oai(default = "default_limit")] limit: Query<u32>,
        #[oai(default)] offset: Query<u32>,
    ) -> Reply<MeetingListPage> {
        response(
            operations::list(
                api.clone(),
                operations::Page {
                    limit: limit.0,
                    offset: offset.0,
                },
            )
            .await?,
        )
    }
    #[oai(
        path = "/meetings/:meeting_id",
        method = "get",
        operation_id = "get_meeting_meetings__meeting_id__get"
    )]
    async fn detail(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        meeting_id: Path<String>,
    ) -> Reply<MeetingDetail> {
        response(operations::detail(api.clone(), meeting_id.0).await?)
    }
    #[oai(
        path = "/meetings/:meeting_id",
        method = "patch",
        operation_id = "update_meeting_title_meetings__meeting_id__patch"
    )]
    async fn title(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        meeting_id: Path<String>,
        body: Json<TitleUpdateRequest>,
    ) -> Reply<OkResponse> {
        response(
            operations::title(
                api.clone(),
                meeting_id.0,
                operations::Title {
                    title: body.0.title,
                },
            )
            .await?,
        )
    }
    #[oai(
        path = "/meetings/:meeting_id",
        method = "delete",
        operation_id = "delete_meeting_meetings__meeting_id__delete"
    )]
    async fn delete(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        meeting_id: Path<String>,
    ) -> Reply<OkResponse> {
        response(operations::delete(api.clone(), meeting_id.0).await?)
    }
    #[oai(
        path = "/meetings/:meeting_id/recordings",
        method = "get",
        operation_id = "list_recordings_meetings__meeting_id__recordings_get"
    )]
    async fn recordings(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        meeting_id: Path<String>,
    ) -> Reply<Vec<RecordingAssetItem>> {
        response(operations::recordings(api.clone(), meeting_id.0).await?)
    }
    #[oai(
        path = "/meetings/recordings/cleanup/preview",
        method = "post",
        operation_id = "preview_recording_cleanup_meetings_recordings_cleanup_preview_post"
    )]
    async fn cleanup_preview(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        body: Json<RecordingCleanupRequest>,
    ) -> Reply<RecordingCleanupPreviewResponse> {
        response(operations::cleanup_preview(api.clone(), request(body.0)?).await?)
    }
    #[oai(
        path = "/meetings/recordings/cleanup",
        method = "post",
        operation_id = "execute_recording_cleanup_meetings_recordings_cleanup_post"
    )]
    async fn cleanup_execute(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        body: Json<RecordingCleanupRequest>,
    ) -> Reply<RecordingCleanupExecuteResponse> {
        response(operations::cleanup_execute(api.clone(), request(body.0)?).await?)
    }
    #[oai(
        path = "/api/ai/routes",
        method = "get",
        operation_id = "get_ai_routes_api_ai_routes_get"
    )]
    async fn routes(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
    ) -> Reply<RouteCatalogResponse> {
        response(operations::ai_routes(api.clone()).await?)
    }
    #[oai(
        path = "/api/ai/routes/assignments",
        method = "put",
        operation_id = "replace_ai_route_assignments_api_ai_routes_assignments_put"
    )]
    async fn assignments(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        body: Json<RouteAssignmentsUpdate>,
    ) -> Reply<RouteCatalogResponse> {
        response(operations::ai_assignments(api.clone(), request(body.0)?).await?)
    }
    #[oai(
        path = "/api/settings/ollama/models",
        method = "get",
        operation_id = "get_ollama_models_api_settings_ollama_models_get"
    )]
    async fn ollama_models(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        base_url: Query<Option<String>>,
    ) -> Reply<OllamaModelsResponse> {
        response(
            operations::ollama_models(
                api.clone(),
                operations::OllamaQuery {
                    base_url: base_url.0,
                },
            )
            .await?,
        )
    }
    #[oai(
        path = "/api/settings/connections/test",
        method = "post",
        operation_id = "test_connection_api_settings_connections_test_post"
    )]
    async fn connection_test(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        body: Json<ConnectionTestRequest>,
    ) -> Reply<ConnectionTestResponse> {
        response(operations::connection_test(api.clone(), request(body.0)?).await?)
    }
    #[oai(
        path = "/api/settings/ai/models",
        method = "get",
        operation_id = "get_ai_models"
    )]
    async fn ai_models(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        provider: Query<ConnectionProvider>,
    ) -> Reply<AiModelsResponse> {
        response(operations::ai_models(api.clone(), request(provider.0)?).await?)
    }
    #[oai(
        path = "/api/ai/agents",
        method = "get",
        operation_id = "get_agent_catalog"
    )]
    async fn agents(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        #[oai(default)] refresh: Query<bool>,
    ) -> Reply<AgentCatalog> {
        response(agents::catalog(api.clone(), agents::CatalogQuery { refresh: refresh.0 }).await?)
    }
    #[oai(
        path = "/api/ai/agents/:id/install",
        method = "post",
        operation_id = "install_agent"
    )]
    async fn install_agent(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        id: Path<String>,
    ) -> Reply<AgentInstallResponse> {
        response(agents::install(api.clone(), id.0).await?)
    }
    #[oai(
        path = "/api/ai/agents/:id/connect",
        method = "post",
        operation_id = "connect_agent"
    )]
    async fn connect_agent(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        id: Path<String>,
        body: Json<AgentConnectRequest>,
    ) -> Reply<AgentStatus> {
        response(
            agents::connect(
                api.clone(),
                id.0,
                agents::Connect {
                    method: body.0.method,
                },
            )
            .await?,
        )
    }
    #[oai(
        path = "/api/ai/agents/:id/model",
        method = "put",
        operation_id = "select_agent_model"
    )]
    async fn select_agent_model(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        id: Path<String>,
        body: Json<AgentModelRequest>,
    ) -> Reply<AgentStatus> {
        response(agents::select_model(api.clone(), id.0, body.0.model).await?)
    }
    #[oai(
        path = "/api/ai/agents/:id/thought-level",
        method = "put",
        operation_id = "select_agent_thought_level"
    )]
    async fn select_agent_thought_level(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        id: Path<String>,
        body: Json<AgentThoughtLevelRequest>,
    ) -> Reply<AgentStatus> {
        response(agents::select_thought_level(api.clone(), id.0, body.0.thought_level).await?)
    }
    #[oai(
        path = "/api/ai/agents/:id",
        method = "delete",
        operation_id = "remove_agent"
    )]
    async fn remove_agent(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        id: Path<String>,
    ) -> Reply<OkResponse> {
        response(agents::remove(api.clone(), id.0).await?)
    }
    #[oai(
        path = "/api/ai/agents/update-all",
        method = "post",
        operation_id = "update_all_agents"
    )]
    async fn update_agents(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
    ) -> Reply<AgentUpdatesResponse> {
        response(agents::update_all(api.clone()).await?)
    }

    #[oai(
        path = "/meetings/:meeting_id/recordings/:role",
        method = "get",
        operation_id = "serve_recording_meetings__meeting_id__recordings__role__get"
    )]
    async fn recording(
        &self,
        Data(api): Data<&Api>,
        _auth: DesktopAuth,
        meeting_id: Path<String>,
        role: Path<RecordingRole>,
        #[oai(name = "Range")] _range: Header<Option<String>>,
        incoming: &Request,
    ) -> Result<RecordingResponse, ApiError> {
        let mut req = Request::builder().finish();
        *req.headers_mut() = incoming.headers().clone();
        operations::recording(api.clone(), meeting_id.0, request(role.0)?, req)
            .await
            .map(RecordingResponse)
    }
}
fn default_limit() -> u32 {
    50
}

struct RecordingResponse(Response);
impl IntoResponse for RecordingResponse {
    fn into_response(self) -> Response {
        self.0
    }
}
impl poem_openapi::ApiResponse for RecordingResponse {
    fn meta() -> poem_openapi::registry::MetaResponses {
        poem_openapi::registry::MetaResponses {
            responses: [200, 206, 304, 416]
                .into_iter()
                .map(|status| {
                    let mut response = Binary::<Vec<u8>>::meta().responses.remove(0);
                    response.status = Some(status);
                    if status == 200 || status == 206 {
                        response.content[0].content_type = "audio/wav";
                    } else {
                        response.content.clear();
                    }
                    for name in ["Accept-Ranges", "Content-Range", "ETag", "Last-Modified"] {
                        response.headers.push(poem_openapi::registry::MetaHeader {
                            name: name.into(),
                            description: None,
                            required: false,
                            deprecated: false,
                            schema: <String as poem_openapi::types::Type>::schema_ref(),
                        });
                    }
                    response
                })
                .collect(),
        }
    }
    fn register(registry: &mut poem_openapi::registry::Registry) {
        Binary::<Vec<u8>>::register(registry);
    }
}
