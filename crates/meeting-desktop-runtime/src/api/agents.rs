use super::*;
type Reply = Result<Value, ApiError>;

#[derive(Default, Deserialize)]
pub(super) struct CatalogQuery {
    #[serde(default)]
    pub(super) refresh: bool,
}
pub(super) async fn catalog(api: Api, query: CatalogQuery) -> Reply {
    if query.refresh {
        check(&api, true).await?;
    }
    Ok(api
        .shared
        .agents
        .catalog(false)
        .await
        .map_err(Error::from)?)
}
pub(super) async fn install(api: Api, id: String) -> Reply {
    let _guard = maintenance(&api).await?;
    let mut stopping = api.stopping.clone();
    let changed = tokio::select! {
        result = api.shared.agents.install(&id) => result.map_err(Error::from)?,
        _ = stopping.changed() => return Err(Error::Closed.into()),
    };
    Ok(json!({"ok":true,"changed":changed}))
}
pub(super) async fn update_all(api: Api) -> Reply {
    let _guard = maintenance(&api).await?;
    let mut stopping = api.stopping.clone();
    let result = tokio::select! {
        result = api.shared.agents.update_all() => result.map_err(Error::from)?,
        _ = stopping.changed() => return Err(Error::Closed.into()),
    };
    Ok(result)
}

async fn maintenance(api: &Api) -> Result<tokio::sync::MutexGuard<'_, ()>, ApiError> {
    let runtime = api
        .runtime
        .try_lock()
        .map_err(|_| ApiError::from(Error::Busy))?;
    if api.shared.live.lock().await.running {
        return Err(Error::Busy.into());
    }
    api.shared.agents.cancel_checks().await;
    let _guard = api
        .shared
        .agents
        .maintenance
        .try_lock()
        .map_err(|_| ApiError::from(Error::Busy))?;
    drop(runtime);
    Ok(_guard)
}
#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Connect {
    pub(super) method: Option<String>,
}
pub(super) async fn connect(api: Api, id: String, body: Connect) -> Reply {
    let _guard = maintenance(&api).await?;
    let mut stopping = api.stopping.clone();
    let status = tokio::select! {
        result = api.shared.agents.connect(&id, body.method) => result.map_err(Error::from)?,
        _ = stopping.changed() => return Err(Error::Closed.into()),
    };
    Ok(serde_json::to_value(status).map_err(Error::from)?)
}
pub(super) async fn remove(api: Api, id: String) -> Reply {
    let _guard = maintenance(&api).await?;
    let store = api.shared.settings.lock().await;
    let assigned = &store.document.ai.assignments;
    if assigned.reply.as_deref() == Some(format!("acp:{id}").as_str()) {
        return Err(ApiError(
            StatusCode::CONFLICT,
            dto::ErrorDetail::Nested(dto::NestedErrorDetail {
                detail: "利用するAIの選択を解除して保存してから削除してください。".into(),
            }),
        ));
    }
    drop(store);
    api.shared.agents.remove(&id).await.map_err(Error::from)?;
    Ok(json!({"ok":true}))
}

async fn check(api: &Api, force: bool) -> Result<(), ApiError> {
    check_with(api, force, api.shared.agents.refresh()).await
}
async fn check_with(
    api: &Api,
    force: bool,
    refresh: impl std::future::Future<Output = Result<(), crate::agents::AgentError>>,
) -> Result<(), ApiError> {
    let runtime = api
        .runtime
        .try_lock()
        .map_err(|_| ApiError::from(Error::Busy))?;
    if api.shared.live.lock().await.running {
        return Err(Error::Busy.into());
    }
    let _checking = api
        .shared
        .agents
        .checking
        .try_lock()
        .map_err(|_| ApiError::from(Error::Busy))?;
    let _maintenance = api
        .shared
        .agents
        .maintenance
        .try_lock()
        .map_err(|_| ApiError::from(Error::Busy))?;
    if !force && !api.shared.agents.checks_due().await {
        return Ok(());
    }
    let mut cancel = api.shared.agents.cancel_check.subscribe();
    let mut stopping = api.stopping.clone();
    drop(runtime);
    tokio::select! {
        result = refresh => result.map_err(|error| Error::from(error).into()),
        _ = cancel.changed() => Err(Error::Busy.into()),
        _ = stopping.changed() => Err(Error::Closed.into()),
    }
}

pub(super) async fn background(api: Api) {
    use std::time::Duration;
    let mut stopping = api.stopping.clone();
    let mut events = api.shared.events.subscribe();
    let mut ticks = tokio::time::interval_at(
        tokio::time::Instant::now() + Duration::from_secs(30),
        Duration::from_secs(60),
    );
    loop {
        tokio::select! {
            _ = stopping.changed() => break,
            _ = ticks.tick() => {},
            event = events.recv() => if !matches!(event, Ok(wire::Event::MeetingState { running: false, .. })) { continue; },
        }
        if *stopping.borrow() {
            break;
        }
        // 更新確認の失敗は設定画面だけに表示し、会話画面へ通知しない。
        let _ = check(&api, false).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    async fn fixture(temp: &tempfile::TempDir) -> (Api, watch::Sender<bool>) {
        let (stop, stopping) = watch::channel(false);
        let config = Config {
            agent_updates: false,
            data_dir: temp.path().into(),
            audio_worker: temp.path().join("missing-worker"),
            speech_worker: temp.path().join("missing-worker"),
            python_worker: temp.path().join("missing-worker"),
            model: None,
            legacy_model: None,
            hub_cache: temp.path().join("hub"),
            hub_offline: true,
            punctuation: None,
        };
        let runtime = Runtime::open(
            config,
            stopping.clone(),
            Arc::new(crate::settings::UnavailableSecrets),
        )
        .await
        .unwrap();
        (
            Api {
                shared: runtime.shared.clone(),
                runtime: Arc::new(Mutex::new(runtime)),
                token: "synthetic".into(),
                cleanup_preview: Arc::default(),
                stopping,
            },
            stop,
        )
    }

    #[tokio::test]
    async fn update_checks_defer_during_meetings_and_yield_when_a_meeting_starts() {
        let temp = tempfile::tempdir().unwrap();
        let (api, _stop) = fixture(&temp).await;
        api.shared.live.lock().await.running = true;
        assert!(
            check_with(&api, true, async { panic!("must defer during a meeting") })
                .await
                .is_err()
        );
        api.shared.live.lock().await.running = false;
        assert!(check_with(&api, true, async { Ok(()) }).await.is_ok());
        let started = Arc::new(tokio::sync::Notify::new());
        let notify = started.clone();
        let worker_api = api.clone();
        let worker = tokio::spawn(async move {
            check_with(&worker_api, true, async {
                notify.notify_one();
                std::future::pending::<Result<(), crate::agents::AgentError>>().await
            })
            .await
            .is_err()
        });
        started.notified().await;
        let runtime = api.runtime.lock().await;
        tokio::time::timeout(
            std::time::Duration::from_secs(1),
            api.shared.agents.cancel_checks(),
        )
        .await
        .unwrap();
        assert!(api.shared.agents.maintenance.try_lock().is_ok());
        drop(runtime);
        assert!(worker.await.unwrap());
        assert!(api.shared.agents.catalog(false).await.unwrap()["checked_at"].is_null());
    }
}
