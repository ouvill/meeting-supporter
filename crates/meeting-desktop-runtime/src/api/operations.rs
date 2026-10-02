//! Operations behind the HTTP adapter. Accepted effects keep their lifecycle ownership.
use super::*;
type Reply = Result<Value, ApiError>;

#[derive(Deserialize)]
pub(super) struct Page {
    #[serde(default = "default_limit")]
    pub(super) limit: u32,
    #[serde(default)]
    pub(super) offset: u32,
}
fn default_limit() -> u32 {
    50
}
pub(super) async fn list(api: Api, page: Page) -> Reply {
    if !(1..=200).contains(&page.limit) {
        return Err(ApiError(StatusCode::BAD_REQUEST, "invalid limit".into()));
    }
    let mut items = api
        .shared
        .repository
        .execute(db::Command::ListMeetings {
            limit: page.limit,
            offset: page.offset,
        })
        .await?;
    if let Some(items) = items.as_array_mut() {
        for item in items {
            item["has_ai_note"] = json!(!item["ai_note"]
                .as_str()
                .unwrap_or_default()
                .trim()
                .is_empty());
        }
    }
    let total = api
        .shared
        .repository
        .execute(db::Command::CountMeetings {})
        .await?;
    Ok(json!({"items":items,"total":total,"limit":page.limit,"offset":page.offset}))
}
async fn get_meeting(api: &Api, id: &str) -> Result<Value, ApiError> {
    let meeting = api
        .shared
        .repository
        .execute(db::Command::GetMeeting {
            meeting_id: id.into(),
        })
        .await?;
    if meeting.is_null() {
        return Err(ApiError(StatusCode::NOT_FOUND, "Meeting not found".into()));
    }
    Ok(meeting)
}
pub(super) async fn detail(api: Api, id: String) -> Reply {
    let mut meeting = get_meeting(&api, &id).await?;
    meeting["turns"] = api
        .shared
        .repository
        .execute(db::Command::ListTurns {
            meeting_id: id.clone(),
        })
        .await?;
    meeting["reply_suggestions"] = api
        .shared
        .repository
        .execute(db::Command::ListReplySuggestions {
            meeting_id: id.clone(),
        })
        .await?;
    meeting["recording_assets"] = api
        .shared
        .repository
        .execute(db::Command::ListRecordingAssets { meeting_id: id })
        .await?;
    Ok(meeting)
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Title {
    pub(super) title: String,
}
pub(super) async fn title(api: Api, id: String, body: Title) -> Reply {
    get_meeting(&api, &id).await?;
    api.shared
        .repository
        .execute(db::Command::UpdateMeetingTitle {
            meeting_id: id,
            title: body.title,
        })
        .await?;
    Ok(json!({"ok":true}))
}
pub(super) async fn delete(api: Api, id: String) -> Reply {
    // Accepted deletion finishes even if the HTTP client disconnects.
    tokio::spawn(async move {
        let mut guard = api
            .runtime
            .try_lock()
            .map_err(|_| ApiError(StatusCode::CONFLICT, "会議を処理中です。".into()))?;
        get_meeting(&api, &id).await?;
        if !guard.can_delete(&id) {
            return Err(ApiError(
                StatusCode::CONFLICT,
                "処理中の会議は削除できません。終了してから削除してください。".into(),
            ));
        }
        let root = api.shared.config.data_dir.clone();
        let owned_id = id.clone();
        tokio::task::spawn_blocking(move || crate::cleanup::remove_files(&root, &owned_id))
            .await
            .map_err(|_| Error::CleanupFiles)??;
        api.shared
            .repository
            .execute(db::Command::DeleteMeeting { meeting_id: id })
            .await?;
        Ok(json!({"ok":true}))
    })
    .await
    .map_err(|_| Error::CleanupFiles)?
}
pub(super) async fn cleanup_preview(api: Api, body: crate::cleanup::Request) -> Reply {
    let _guard = api
        .runtime
        .try_lock()
        .map_err(|_| ApiError(StatusCode::CONFLICT, "会議を処理中です。".into()))?;
    let plan = crate::cleanup::plan(&api.shared.repository, &body).await?;
    let response = serde_json::to_value(&plan.preview).map_err(Error::from)?;
    *api.cleanup_preview.lock().await = Some(plan);
    Ok(response)
}
pub(super) async fn cleanup_execute(api: Api, body: crate::cleanup::Request) -> Reply {
    tokio::spawn(async move {
        let _guard = api
            .runtime
            .try_lock()
            .map_err(|_| ApiError(StatusCode::CONFLICT, "会議を処理中です。".into()))?;
        let previous = api
            .cleanup_preview
            .lock()
            .await
            .take()
            .ok_or(Error::CleanupChanged)?;
        let result = crate::cleanup::execute(&api.shared, &body, previous).await?;
        Ok(serde_json::to_value(result).map_err(Error::from)?)
    })
    .await
    .map_err(|_| Error::CleanupFiles)?
}

pub(super) async fn recordings(api: Api, id: String) -> Reply {
    get_meeting(&api, &id).await?;
    Ok(api
        .shared
        .repository
        .execute(db::Command::ListRecordingAssets { meeting_id: id })
        .await?)
}
async fn contained(
    base: &std::path::Path,
    path: &std::path::Path,
) -> Result<std::path::PathBuf, ApiError> {
    let base = tokio::fs::canonicalize(base).await.map_err(Error::from)?;
    let path = tokio::fs::canonicalize(path)
        .await
        .map_err(|_| ApiError(StatusCode::NOT_FOUND, "Recording not found".into()))?;
    if !path.starts_with(&base) || path == base {
        return Err(ApiError(StatusCode::BAD_REQUEST, "Invalid path".into()));
    }
    Ok(path)
}
pub(super) async fn recording(
    api: Api,
    id: String,
    role: db::Role,
    request: Request,
) -> Result<Response, ApiError> {
    let asset = api
        .shared
        .repository
        .execute(db::Command::GetRecordingAssetByRole {
            meeting_id: id,
            role,
        })
        .await?;
    if asset.is_null() {
        return Err(ApiError(
            StatusCode::NOT_FOUND,
            "Recording not found".into(),
        ));
    }
    let asset: db::Asset = serde_json::from_value(asset).map_err(Error::from)?;
    let path = contained(
        &api.shared.config.data_dir,
        &api.shared.config.data_dir.join(asset.relative_path),
    )
    .await?;
    match poem::endpoint::StaticFileEndpoint::new(&path)
        .call(request)
        .await
    {
        Ok(mut response) => {
            if response.status().is_success() {
                response
                    .headers_mut()
                    .insert(header::CONTENT_TYPE, "audio/wav".parse().unwrap());
            }
            Ok(response)
        }
        Err(error) if error.status() == StatusCode::RANGE_NOT_SATISFIABLE => {
            let size = tokio::fs::metadata(path).await.map_err(Error::from)?.len();
            Ok(Response::builder()
                .status(StatusCode::RANGE_NOT_SATISFIABLE)
                .header(header::CONTENT_RANGE, format!("bytes */{size}"))
                .finish())
        }
        Err(error) => Err(ApiError(
            error.status(),
            "録音を読み込めませんでした。".into(),
        )),
    }
}

async fn settings_value(api: &Api) -> Result<Value, ApiError> {
    let guard = api.shared.settings.lock().await;
    let store = guard.clone();
    let response = tokio::task::spawn_blocking(move || store.response())
        .await
        .map_err(|_| Error::Closed)?;
    drop(guard);
    let mut response = response?;
    let id = api
        .shared
        .live
        .lock()
        .await
        .session
        .as_ref()
        .map(|s| s.id.clone());
    if let Some(id) = id {
        let path = api.shared.config.data_dir.join("usage.jsonl");
        let summary = tokio::task::spawn_blocking(move || crate::usage::meeting(&path, &id))
            .await
            .map_err(|_| Error::Closed)??;
        response["usage"]["current_meeting"] =
            serde_json::to_value(summary).map_err(Error::from)?;
    }
    Ok(response)
}
pub(super) async fn settings_get(api: Api) -> Reply {
    settings_value(&api).await
}
fn settings_error(error: Error) -> ApiError {
    match error {
        Error::Busy | Error::Session(_) => ApiError(
            StatusCode::CONFLICT,
            dto::ErrorDetail::Settings(dto::SettingsConflictDetail {
                code: "AUDIO_SETTINGS_LOCKED".into(),
                message: "会議中・準備中は音声認識の設定を変更できません。".into(),
            }),
        ),
        Error::Settings | Error::Unsupported => {
            ApiError(StatusCode::UNPROCESSABLE_ENTITY, error.to_string().into())
        }
        _ => error.into(),
    }
}
pub(super) async fn settings_save(api: Api, patch: crate::settings::Patch) -> Reply {
    let mut runtime = api
        .runtime
        .try_lock()
        .map_err(|_| settings_error(Error::Busy))?;
    runtime.save_settings(patch).await.map_err(settings_error)?;
    Ok(json!({"ok":true,"settings":settings_value(&api).await?}))
}
pub(super) async fn speech_capabilities(api: Api) -> Value {
    let supported = meeting_media_runtime::whisper_gpu_supported(&api.shared.config.speech_worker)
        .await
        .ok();
    // null means unknown (missing, outdated or failing worker), never CPU-only.
    json!({"whisper_gpu": supported})
}

pub(super) async fn model_status(api: Api, query: crate::models::Request) -> Reply {
    Ok(
        serde_json::to_value(api.shared.models.status(&query).map_err(model_error)?)
            .map_err(Error::from)?,
    )
}
pub(super) async fn model_download(api: Api, query: crate::models::Request) -> Reply {
    Ok(
        serde_json::to_value(api.shared.models.start(&query).map_err(model_error)?)
            .map_err(Error::from)?,
    )
}
pub(super) async fn model_cancel(api: Api, query: crate::models::Request) -> Reply {
    Ok(
        serde_json::to_value(api.shared.models.cancel(&query).map_err(model_error)?)
            .map_err(Error::from)?,
    )
}
fn model_error(error: crate::models::ModelError) -> ApiError {
    ApiError(
        StatusCode::UNPROCESSABLE_ENTITY,
        dto::ErrorDetail::Nested(dto::NestedErrorDetail {
            detail: error.to_string(),
        }),
    )
}

pub(super) async fn ai_routes(api: Api) -> Reply {
    let store = api.shared.settings.lock().await.clone();
    // Warm the previously selected agent on startup without prompting the model.
    if let Some(id) = store
        .document
        .ai
        .assignments
        .clone()
        .reply
        .and_then(|id| id.strip_prefix("acp:").map(str::to_owned))
    {
        if !api.shared.live.lock().await.running && !api.shared.agents.pool.status(&id).await.ready
        {
            if let Ok(_guard) = api.shared.agents.maintenance.try_lock() {
                let _ = api.shared.agents.connect(&id, None).await;
            }
        }
    }
    Ok(crate::ai::routes::catalog(store, &api.shared.agents).await?)
}
pub(super) async fn ai_assignments(api: Api, body: crate::ai::routes::Assignments) -> Reply {
    let _runtime = api.runtime.try_lock().map_err(|_| {
        ApiError(
            StatusCode::CONFLICT,
            "会議の操作中です。完了を待ってください。".into(),
        )
    })?;
    let mut guard = api.shared.settings.lock().await;
    if let Some(id) = &body.reply {
        if let Some(agent) = id.strip_prefix("acp:") {
            api.shared.agents.launch(agent).await.map_err(Error::from)?;
            if !api.shared.agents.pool.status(agent).await.ready {
                return Err(Error::Agent(crate::agents::AgentError::Connect).into());
            }
        } else if !matches!(id.as_str(), "openai" | "gemini" | "anthropic" | "ollama") {
            return Err(ApiError(
                StatusCode::UNPROCESSABLE_ENTITY,
                "このAI経路はまだ選択できません。".into(),
            ));
        }
    }
    let mut candidate = guard.document.clone();
    candidate.ai.assignments = body;
    let mut store = guard.clone();
    let saved = tokio::task::spawn_blocking(move || {
        store.save(candidate, crate::settings::Patch::default())?;
        Ok::<_, Error>(store)
    })
    .await
    .map_err(|_| Error::Closed)??;
    *guard = saved.clone();
    drop(guard);
    api.shared
        .replies
        .lock()
        .await
        .cancel_all(&api.shared)
        .await;
    Ok(crate::ai::routes::catalog(saved, &api.shared.agents).await?)
}
#[derive(Deserialize)]
pub(super) struct OllamaQuery {
    pub(super) base_url: Option<String>,
}
pub(super) async fn ollama_models(api: Api, query: OllamaQuery) -> Reply {
    let base_url = match query.base_url {
        Some(url) => url,
        None => api
            .shared
            .settings
            .lock()
            .await
            .route_url(crate::ai::routes::Provider::Ollama)
            .to_owned(),
    };
    match crate::ai::routes::ollama_models(&base_url).await {
        Ok(models) => Ok(json!({"ok":true,"base_url":base_url,"models":models,"message":null})),
        Err(error) => {
            Ok(json!({"ok":false,"base_url":base_url,"models":[],"message":error.to_string()}))
        }
    }
}

pub(super) async fn connection_test(api: Api, body: crate::ai::connections::Request) -> Reply {
    let store = api.shared.settings.lock().await.clone();
    Ok(crate::ai::connections::check(store, body).await?)
}

pub(super) async fn ai_models(api: Api, provider: crate::ai::connections::Provider) -> Reply {
    let store = api.shared.settings.lock().await.clone();
    Ok(crate::ai::connections::models(store, provider).await?)
}
