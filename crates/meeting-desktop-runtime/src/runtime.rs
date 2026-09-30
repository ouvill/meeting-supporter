use crate::{
    wire::{self, Event},
    Config, Error,
};
use meeting_media_runtime::{wire as media, Options, Supervisor};
use meeting_session::{Command as SessionCommand, Coordinator, Effect, Outcome, Phase, Session};
use meeting_storage::{models as db, Repository};
use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio::{
    sync::{broadcast, mpsc, Mutex},
    task::JoinHandle,
};

pub(crate) struct Live {
    pub session: Option<Session>,
    pub running: bool,
    pub saved: bool,
    pub backend: String,
    pub initialized: bool,
    pub initializing: bool,
    pub failed: bool,
    pub turns: Vec<wire::TurnItem>,
    pub sequence: i64,
    pub context: wire::Context,
    pub references: Vec<crate::references::Document>,
    pub context_text: String,
    pub ai_accepting: bool,
    pub devices: Vec<wire::Device>,
    pub selected: [Option<String>; 2],
}
pub(crate) struct Shared {
    pub repository: Repository,
    pub models: Arc<crate::models::Manager>,
    pub replies: Mutex<crate::ai::Replies>,
    pub settings: Mutex<crate::settings::Store>,
    pub live: Mutex<Live>,
    pub events: broadcast::Sender<Event>,
    pub config: Config,
    pub cancel_prepare: tokio::sync::watch::Sender<u64>,
}
impl Shared {
    pub fn emit(&self, event: Event) {
        let _ = self.events.send(event);
    }
    pub fn error(&self, error: &Error) {
        let message = match error {
            Error::Media(media::Error::Timeout) => Some("音声処理が制限時間内に完了しませんでした。未確定の文字起こしが残る可能性があります。取得済みの履歴と録音は未確定の会議として保持します。"),
            Error::Media(media::Error::GpuUnavailable) => Some("GPUを初期化できません。GPU対応ワーカーを確認するか、実行デバイスを自動またはCPUに変更してください。"),
            Error::Media(media::Error::Discontinuity) => Some("音声処理の待ち行列が上限に達したか、入力が途切れたため認識を停止しました。録音が有効な場合は録音を確認してください。"),
            _ => None,
        };
        if let Some(text) = message {
            self.emit(Event::Error { text: text.into() });
            return;
        }

        self.emit(Event::Error {
            text: error.to_string(),
        });
    }
    pub async fn snapshot(&self) -> (Vec<Event>, broadcast::Receiver<Event>) {
        let settings = self.settings.lock().await;
        let agents = settings.agent_event().unwrap_or_else(|error| Event::Error {
            text: error.to_string(),
        });
        let live = self.live.lock().await;
        let mut events = vec![
            Event::Status {
                text: "Rust バックエンドに接続済み。".into(),
            },
            Event::MeetingState {
                running: live.running,
                saved: live.saved,
            },
            Event::SttState {
                backend: live.backend.clone(),
                initialized: live.initialized,
                initializing: live.initializing,
            },
            Event::DevicesList {
                devices: live.devices.clone(),
                current_self: live.selected[0].clone(),
                current_other: live.selected[1].clone(),
            },
            agents,
        ];
        if let Some(s) = &live.session {
            events.push(Event::SessionInfo {
                id: s.id.clone(),
                started_at: s.started_at.clone(),
                ended_at: s.ended_at.clone(),
                is_active: live.running,
            });
        }
        events.push(Event::HistoryReset {
            items: live.turns.clone(),
        });
        if live.failed {
            events.push(Event::Error {
                text: "音声または保存でエラーが発生しました。会議を未確定として保持します。".into(),
            });
        }
        (events, self.events.subscribe())
    }
    async fn transcript(self: &Arc<Self>, role: media::Role, text: String) -> Result<(), Error> {
        if text.trim().is_empty() {
            return Ok(());
        }
        let mut live = self.live.lock().await;
        let id = live.session.as_ref().ok_or(Error::NoMeeting)?.id.clone();
        live.sequence += 1;
        let turn_id = uuid::Uuid::new_v4().to_string();
        let result = self
            .repository
            .execute(db::Command::InsertTurn {
                record: db::Turn {
                    id: turn_id.clone(),
                    meeting_id: id,
                    sequence: live.sequence,
                    speaker: role.as_str().into(),
                    text: text.clone(),
                    speaker_id: None,
                    created_at: None,
                },
            })
            .await;
        if let Err(error) = result {
            live.failed = true;
            return Err(error.into());
        }
        // Reconnect state is bounded; complete transcripts remain in SQLite.
        if live.turns.len() == 2000 {
            live.turns.remove(0);
        }
        live.turns.push(wire::TurnItem {
            id: turn_id.clone(),
            speaker: role.as_str().into(),
            text: text.clone(),
            speaker_id: None,
        });
        self.emit(Event::SttFinal {
            role,
            text,
            speaker_id: None,
            utterance_id: turn_id.clone(),
        });
        let eligible = live.running && live.ai_accepting && role.as_str() == "other";
        let meeting_id = live.session.as_ref().map(|s| s.id.clone());
        drop(live);
        if eligible {
            let shared = self.clone();
            tokio::spawn(async move {
                let enabled = {
                    let settings = shared.settings.lock().await;
                    settings.document["reply"]["auto_generate"] == true
                        && settings.document["reply"]["enabled"] == true
                };
                if !enabled {
                    return;
                }
                if shared.live.lock().await.session.as_ref().map(|s| &s.id) != meeting_id.as_ref() {
                    return;
                }
                let result = shared
                    .replies
                    .lock()
                    .await
                    .start(
                        shared.clone(),
                        uuid::Uuid::new_v4().to_string(),
                        Some(turn_id),
                        wire::SuggestionMode::Normal,
                    )
                    .await;
                if let Err(error) = result {
                    shared.error(&error);
                }
            });
        }
        Ok(())
    }
    async fn media_event(self: &Arc<Self>, role: media::Role, event: media::Event) {
        match event {
            media::Event::Ready { name, rate, .. } => self.emit(Event::StreamInfo {
                role,
                device: name,
                rate,
            }),
            media::Event::ExecutionDevice { device } => self.emit(Event::Status {
                text: format!(
                    "Whisper.cpp: {}で音声認識を実行します。",
                    device.argument().to_uppercase()
                ),
            }),
            media::Event::SpeechLag {} => self.emit(Event::Status {
                text: "音声認識が会話に追いついていません。軽いモデルへの変更を検討してください。"
                    .into(),
            }),
            media::Event::Level { peak } => self.emit(Event::AudioLevel { role, level: peak }),
            media::Event::Transcript {
                text,
                punctuation_failed,
                ..
            } => {
                if punctuation_failed {
                    self.emit(Event::Status {
                        text: "句読点の追加に失敗したため認識結果をそのまま保存します。".into(),
                    });
                }
                if let Err(error) = self.transcript(role, text).await {
                    self.error(&error);
                }
            }
            media::Event::CaptureError {} | media::Event::SpeechError { .. } => {
                let mut live = self.live.lock().await;
                live.failed |= live.running;
                live.initialized = false;
                self.emit(Event::AudioLevel { role, level: 0.0 });
                self.emit(Event::SttState {
                    backend: live.backend.clone(),
                    initialized: false,
                    initializing: live.initializing,
                });
                let code = match event {
                    media::Event::SpeechError { code } => code,
                    _ => media::Error::Capture,
                };
                self.error(&Error::Media(code));
            }
            media::Event::RecordingError {} => self.error(&Error::Media(media::Error::Recording)),
            _ => {}
        }
    }
}
struct Source {
    supervisor: Supervisor,
    collector: JoinHandle<()>,
    role: media::Role,
    recording: bool,
    closing: Arc<AtomicBool>,
}
impl Source {
    async fn open(
        shared: Arc<Shared>,
        role: media::Role,
        device: Option<String>,
    ) -> Result<Self, Error> {
        let (tx, mut rx) = mpsc::channel(256);
        let sink = shared.clone();
        let closing = Arc::new(AtomicBool::new(false));
        let expected_close = closing.clone();
        let collector = tokio::spawn(async move {
            while let Some(event) = rx.recv().await {
                if let media::Event::Drained { done } = event {
                    let _ = done.send(());
                    continue;
                }
                if matches!(event, media::Event::CaptureError {})
                    && expected_close.load(Ordering::Acquire)
                {
                    continue;
                }
                sink.media_event(role, event).await;
            }
        });
        let supervisor = Supervisor::open(
            Options {
                audio_worker: shared.config.audio_worker.clone(),
                speech_worker: shared.config.speech_worker.clone(),
                role,
                device,
            },
            tx,
        )
        .await;
        match supervisor {
            Ok(supervisor) => Ok(Self {
                supervisor,
                collector,
                role,
                recording: false,
                closing,
            }),
            Err(error) => {
                let _ = collector.await;
                Err(error.into())
            }
        }
    }
    async fn close(mut self) -> Result<(), Error> {
        self.closing.store(true, Ordering::Release);
        self.supervisor.close().await;
        drop(self.supervisor);
        // Every producer is stopped before joining: final transcripts are now committed.
        self.collector.await.map_err(|_| Error::Closed)
    }
}
struct MeetingInput {
    context: wire::Context,
    references: Vec<crate::references::Document>,
    context_text: String,
}
pub(crate) struct Runtime {
    pub shared: Arc<Shared>,
    coordinator: Coordinator,
    sources: Vec<Source>,
    closed: bool,
    stopping: tokio::sync::watch::Receiver<bool>,
}
impl Runtime {
    pub async fn open(
        config: Config,
        stopping: tokio::sync::watch::Receiver<bool>,
        secrets: Arc<dyn crate::settings::Secrets>,
    ) -> Result<Self, Error> {
        tokio::fs::create_dir_all(&config.data_dir).await?;
        let settings = crate::settings::Store::open(&config, secrets)?;
        let backend = settings.backend();
        let repository = Repository::open(&config.data_dir.join("meeting_history.sqlite3")).await?;
        let devices = list_devices(&config).await.unwrap_or_default();
        let shared = Arc::new(Shared {
            models: crate::models::Manager::new(&config),
            repository,
            replies: Mutex::new(crate::ai::Replies::default()),
            settings: Mutex::new(settings),
            config,
            cancel_prepare: tokio::sync::watch::channel(0).0,
            events: broadcast::channel(256).0,
            live: Mutex::new(Live {
                session: None,
                running: false,
                saved: false,
                backend,
                initialized: false,
                initializing: false,
                failed: false,
                turns: vec![],
                sequence: 0,
                context: wire::Context::default(),
                references: vec![],
                context_text: String::new(),
                ai_accepting: false,
                devices,
                selected: [None, None],
            }),
        });
        Ok(Self {
            shared,
            coordinator: Coordinator::default(),
            sources: vec![],
            closed: false,
            stopping,
        })
    }
    fn idle(&mut self) -> Result<(), Error> {
        if self.coordinator.execute(SessionCommand::Snapshot {})?.phase != Phase::Idle {
            return Err(Error::Busy);
        }
        Ok(())
    }
    async fn stt_state(&self, initialized: bool, initializing: bool) {
        let mut live = self.shared.live.lock().await;
        live.initialized = initialized;
        live.initializing = initializing;
        self.shared.emit(Event::SttState {
            backend: live.backend.clone(),
            initialized,
            initializing,
        });
    }
    async fn close_sources(&mut self) -> Result<(), Error> {
        let mut result = Ok(());
        for source in self.sources.drain(..) {
            let role = source.role;
            if let Err(error) = source.close().await {
                result = Err(error);
            }
            self.shared.emit(Event::AudioLevel { role, level: 0.0 });
        }
        self.stt_state(false, false).await;
        result
    }
    pub async fn ensure_monitors(&mut self) {
        if self.closed || *self.stopping.borrow() {
            return;
        }
        let selected = self.shared.live.lock().await.selected.clone();
        for (role, device) in [media::Role::User, media::Role::Other]
            .into_iter()
            .zip(selected)
        {
            if self
                .sources
                .iter()
                .any(|source| source.role.as_str() == role.as_str())
            {
                continue;
            }
            match Source::open(self.shared.clone(), role, device).await {
                Ok(source) => self.sources.push(source),
                Err(error) => {
                    self.shared.emit(Event::AudioLevel { role, level: 0.0 });
                    self.shared.error(&error);
                }
            }
        }
    }
    pub async fn save_settings(&mut self, patch: crate::settings::Patch) -> Result<(), Error> {
        let shared = self.shared.clone();
        let mut current = shared.settings.lock().await;
        let store = current.clone();
        let candidate = store.candidate(&patch)?;
        let audio_changed = store.audio_changed(&candidate);
        if audio_changed {
            self.idle()?;
            let mut proposed = store.clone();
            proposed.document = candidate.clone();
            proposed.speech()?;
        }
        let saved = tokio::task::spawn_blocking(move || {
            let mut store = store;
            store.save(candidate, patch)?;
            Ok::<_, Error>(store)
        })
        .await
        .map_err(|_| Error::Closed)??;
        self.shared.live.lock().await.backend = saved.backend();
        if let Ok(event) = saved.agent_event() {
            self.shared.emit(event);
        }
        *current = saved;
        drop(current);
        self.shared
            .replies
            .lock()
            .await
            .cancel_all(&self.shared)
            .await;
        if audio_changed {
            self.close_sources().await?;
            self.ensure_monitors().await;
        }
        Ok(())
    }
    async fn prepare(&mut self, revision: u64) -> Result<(), Error> {
        let mut cancelled = self.shared.cancel_prepare.subscribe();
        if *cancelled.borrow() != revision {
            return Err(Error::Cancelled);
        }
        self.idle()?;
        self.shared.settings.lock().await.speech()?;
        let initialized = self.shared.live.lock().await.initialized;
        if initialized {
            self.stt_state(true, false).await;
            return Ok(());
        }
        self.close_sources().await?;
        self.stt_state(false, true).await;
        let mut stopping = self.stopping.clone();
        let result = tokio::select! {
            result = self.prepare_sources() => result,
            _ = stopping.wait_for(|stop| *stop) => Err(Error::Closed),
            _ = cancelled.changed() => Err(Error::Cancelled),
        };
        if result.is_err() {
            let _ = self.close_sources().await;
        }
        self.stt_state(result.is_ok(), false).await;
        result
    }
    async fn prepare_sources(&mut self) -> Result<(), Error> {
        let settings = self.shared.settings.lock().await;
        let speech = settings.speech()?;
        let recognizer = settings.recognizer()?;
        let model = match recognizer {
            media::Recognizer::Reazonspeech => self.shared.models.reazon_path()?,
            media::Recognizer::Whisper { .. } => {
                self.shared.models.whisper_path(settings.whisper_model()?)?
            }
        };
        drop(settings);
        let selected = self.shared.live.lock().await.selected.clone();
        for (role, device) in [media::Role::User, media::Role::Other]
            .into_iter()
            .zip(selected)
        {
            let source = Source::open(self.shared.clone(), role, device).await?;
            self.sources.push(source);
            let config = &self.shared.config;
            self.sources
                .last_mut()
                .unwrap()
                .supervisor
                .execute(media::Command::Prepare {
                    recognizer: recognizer.clone(),
                    model: model.clone(),
                    punctuation: match recognizer {
                        media::Recognizer::Reazonspeech => config.punctuation.clone(),
                        _ => None,
                    },
                    config: speech.clone(),
                })
                .await?;
        }
        Ok(())
    }
    async fn load_context(&self) -> Result<String, Error> {
        let settings = self.shared.settings.lock().await;
        let path = settings.document["context"]["dir_override"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| self.shared.config.data_dir.join("context"));
        drop(settings);
        tokio::task::spawn_blocking(move || crate::references::load_context(&path))
            .await
            .map_err(|_| Error::References)?
    }
    pub async fn dispatch(&mut self, command: wire::Command, revision: u64) -> Result<(), Error> {
        if self.closed || *self.stopping.borrow() {
            return Err(Error::Closed);
        }
        match command {
            wire::Command::ReloadContext {} => {
                let text = self.load_context().await?;
                self.shared.live.lock().await.context_text = text;
                self.shared.emit(Event::Status {
                    text: "参考情報を再読み込みしました。".into(),
                });
                Ok(())
            }
            wire::Command::GenerateReply {
                generation_id,
                target_utterance_id,
                mode,
            } => {
                self.shared
                    .replies
                    .lock()
                    .await
                    .start(
                        self.shared.clone(),
                        generation_id,
                        target_utterance_id,
                        mode,
                    )
                    .await
            }
            wire::Command::CancelReply {
                generation_id,
                target_utterance_id,
            } => {
                let ids = self
                    .shared
                    .replies
                    .lock()
                    .await
                    .cancel(&generation_id, &target_utterance_id)
                    .await;
                self.shared.emit(Event::ReplyCancelResult {
                    generation_id,
                    target_utterance_id,
                    status: if ids.is_empty() {
                        wire::CancelStatus::NotApplied
                    } else {
                        wire::CancelStatus::Applied
                    },
                    cancelled_suggestion_ids: ids,
                });
                Ok(())
            }
            wire::Command::InitStt {} => self.prepare(revision).await,
            wire::Command::ShutdownStt {} => {
                self.idle()?;
                self.close_sources().await?;
                self.ensure_monitors().await;
                Ok(())
            }
            wire::Command::SetDevice { role, device } => {
                self.idle()?;
                {
                    let live = self.shared.live.lock().await;
                    if device
                        .as_ref()
                        .is_some_and(|d| !live.devices.iter().any(|v| &v.index == d))
                    {
                        return Err(Error::Unsupported);
                    }
                }
                self.close_sources().await?;
                let mut live = self.shared.live.lock().await;
                live.selected[match role {
                    media::Role::User => 0,
                    media::Role::Other => 1,
                }] = device;
                self.shared.emit(Event::DevicesList {
                    devices: live.devices.clone(),
                    current_self: live.selected[0].clone(),
                    current_other: live.selected[1].clone(),
                });
                drop(live);
                self.ensure_monitors().await;
                Ok(())
            }
            wire::Command::StartMeeting {
                meeting_context,
                references,
            } => {
                self.idle()?;
                if !self.shared.live.lock().await.initialized {
                    return Err(Error::NotPrepared);
                }
                self.lifecycle(
                    SessionCommand::Start {},
                    Some(MeetingInput {
                        context: meeting_context.unwrap_or_default(),
                        references: crate::references::parse(
                            references,
                            &self.shared.config.python_worker,
                            self.stopping.clone(),
                        )
                        .await?,
                        context_text: self.load_context().await?,
                    }),
                )
                .await
            }
            wire::Command::StopMeeting {} => self.lifecycle(SessionCommand::Stop {}, None).await,
            wire::Command::ManualSpeech { text } => {
                if !self.shared.live.lock().await.running {
                    return Err(Error::NoMeeting);
                }
                self.shared.transcript(media::Role::Other, text).await
            }
            wire::Command::UserReply { text } => {
                if !self.shared.live.lock().await.running {
                    return Err(Error::NoMeeting);
                }
                self.shared.transcript(media::Role::User, text).await
            }
        }
    }
    async fn lifecycle(
        &mut self,
        command: SessionCommand,
        context: Option<MeetingInput>,
    ) -> Result<(), Error> {
        let mut state = self.coordinator.execute(command)?;
        while let Some(effect) = state.effect {
            let session = state.session.as_ref().ok_or(Error::NoMeeting)?;
            let result = self.effect(effect, session, context.as_ref()).await;
            let outcome = match result {
                Ok(outcome) => outcome,
                Err(error) => {
                    self.shared.error(&error);
                    Outcome::Failed
                }
            };
            state = self.coordinator.execute(SessionCommand::Acknowledge {
                generation: state.generation,
                step: state.step,
                outcome,
            })?;
        }
        let mut live = self.shared.live.lock().await;
        live.running = state.phase == Phase::Active;
        live.session = state.session;
        for notice in &state.notices {
            self.shared.emit(Event::Error {
                text: format!("会議処理を確認してください: {notice:?}"),
            });
        }
        if let Some(s) = &live.session {
            self.shared.emit(Event::SessionInfo {
                id: s.id.clone(),
                started_at: s.started_at.clone(),
                ended_at: s.ended_at.clone(),
                is_active: live.running,
            });
        }
        self.shared.emit(Event::MeetingState {
            running: live.running,
            saved: live.saved,
        });
        let idle = state.phase == Phase::Idle;
        drop(live);
        if idle {
            self.ensure_monitors().await;
        }
        Ok(())
    }
    async fn effect(
        &mut self,
        effect: Effect,
        session: &Session,
        context: Option<&MeetingInput>,
    ) -> Result<Outcome, Error> {
        let shared = &self.shared;
        let id = session.id.clone();
        match effect {
            Effect::Prepare => {
                if self.sources.len() != 2 {
                    return Err(Error::NotPrepared);
                }
            }
            Effect::CreateDraft => {
                shared
                    .repository
                    .execute(db::Command::CreateMeeting {
                        record: db::Meeting {
                            id: id.clone(),
                            started_at: timestamp(&session.started_at)?,
                            status: db::Status::Active,
                            ended_at: None,
                            duration_seconds: None,
                            title: None,
                            ai_note: String::new(),
                            minutes: String::new(),
                            created_at: None,
                            updated_at: None,
                        },
                    })
                    .await?;
                shared.replies.lock().await.reset();
                let mut live = shared.live.lock().await;
                let input = context.ok_or(Error::References)?;
                live.context = input.context.clone();
                live.references = input.references.clone();
                live.context_text = input.context_text.clone();
                live.ai_accepting = true;
                live.session = Some(session.clone());
                live.turns.clear();
                live.sequence = 0;
                live.saved = false;
                live.failed = false;
                shared.emit(Event::SessionInfo {
                    id: session.id.clone(),
                    started_at: session.started_at.clone(),
                    ended_at: None,
                    is_active: true,
                });
                shared.emit(Event::HistoryReset { items: vec![] });
                let directory = shared.config.data_dir.join("meetings").join(&id);
                tokio::fs::create_dir_all(&directory).await?;
                private_write(
                    &directory.join("context.json"),
                    &serde_json::to_vec(&input.context)?,
                )
                .await?;
                crate::references::persist(&directory, &input.references).await?;
                let failed = input
                    .references
                    .iter()
                    .filter(|r| r.status == crate::references::Status::Failed)
                    .count();
                if failed > 0 {
                    shared.emit(Event::Status{text:format!("資料{failed}件を読み込めませんでした。読み込めた資料で会議を続けます。")});
                }
            }
            Effect::StartRecording => {
                let dir = shared.config.data_dir.join("recordings").join(&id);
                tokio::fs::create_dir_all(&dir).await?;
                for source in &mut self.sources {
                    source
                        .supervisor
                        .execute(media::Command::StartRecording {
                            path: dir.join(format!("{}.wav", source.role.as_str())),
                        })
                        .await?;
                    source.recording = true;
                }
            }
            Effect::StartSpeech => {
                for source in &mut self.sources {
                    source
                        .supervisor
                        .execute(media::Command::StartSpeech {})
                        .await?;
                }
            }
            Effect::StopSpeech => {
                shared.emit(Event::Status {
                    text: "会議を終了しています。残りの音声認識を最大30秒待って保存します。".into(),
                });
                // Freeze both queues before awaiting either input. Otherwise the
                // second input keeps accumulating audio while the first drains.
                for source in &mut self.sources {
                    source.supervisor.detach_speech_input();
                }
                let results = futures_util::future::join_all(
                    self.sources
                        .iter_mut()
                        .map(|source| source.supervisor.execute(media::Command::StopSpeech {})),
                )
                .await;
                if let Some(error) = results.into_iter().find_map(Result::err) {
                    shared.live.lock().await.failed = true;
                    return Err(error.into());
                }
            }
            Effect::FinalizeRecording => {
                let mut assets = Vec::new();
                let mut failure = None;
                for source in &mut self.sources {
                    if !source.recording {
                        continue;
                    }
                    match source
                        .supervisor
                        .execute(media::Command::StopRecording {})
                        .await
                    {
                        Ok(Some(recording)) if recording.samples > 0 => assets.push(db::Asset {
                            id: uuid::Uuid::new_v4().to_string(),
                            meeting_id: id.clone(),
                            role: match source.role {
                                media::Role::User => db::Role::User,
                                media::Role::Other => db::Role::Other,
                            },
                            relative_path: format!("recordings/{id}/{}.wav", source.role.as_str()),
                            format: db::Format::Wav,
                            sample_rate: 16000,
                            channels: 1,
                            started_at: millis(recording.started_ms)?,
                            ended_at: Some(millis(recording.ended_ms)?),
                            size_bytes: Some(
                                i64::try_from(recording.size_bytes)
                                    .map_err(|_| Error::Unsupported)?,
                            ),
                        }),
                        Ok(_) => {}
                        Err(error) => failure = Some(error),
                    }
                    source.recording = false;
                }
                if let Some(error) = failure {
                    return Err(error.into());
                }
                if assets.is_empty() {
                    return Ok(Outcome::RecordingEmpty);
                }
                if let Err(error) = shared
                    .repository
                    .execute(db::Command::InsertRecordingAssets { records: assets })
                    .await
                {
                    // A failed commit can have an uncertain outcome. Keep WAVs and the draft.
                    shared.live.lock().await.failed = true;
                    shared.error(&Error::Storage(error));
                }
                return Ok(Outcome::RecordingSaved);
            }
            Effect::RemoveRecording => {
                // Reap capture before removing potentially open WAVs.
                self.close_sources().await?;
                let path = self.shared.config.data_dir.join("recordings").join(&id);
                match tokio::fs::remove_dir_all(path).await {
                    Ok(()) => {}
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                    Err(error) => return Err(error.into()),
                }
            }
            Effect::FlushHistory => {
                for source in &self.sources {
                    source.supervisor.drain_events().await?;
                }
                if self.shared.live.lock().await.failed {
                    return Err(Error::Media(media::Error::Speech));
                }
            }
            Effect::CompleteDraft => {
                let end = session.ended_at.as_deref().ok_or(Error::NoMeeting)?;
                let duration = chrono::DateTime::parse_from_rfc3339(end)
                    .ok()
                    .zip(chrono::DateTime::parse_from_rfc3339(&session.started_at).ok())
                    .map(|(e, s)| (e - s).num_seconds());
                shared
                    .repository
                    .execute(db::Command::CompleteMeeting {
                        meeting_id: id,
                        ended_at: timestamp(end)?,
                        duration_seconds: duration,
                        ai_note: String::new(),
                    })
                    .await?;
                shared.live.lock().await.saved = true;
                shared.emit(Event::Status {
                    text: "会議を保存しました。".into(),
                });
            }
            Effect::AbortDraft => {
                shared
                    .repository
                    .execute(db::Command::AbortMeeting {
                        meeting_id: id,
                        ended_at: timestamp(session.ended_at.as_deref().ok_or(Error::NoMeeting)?)?,
                    })
                    .await?;
            }
            Effect::ReloadAudio => {
                let live = self.shared.live.lock().await;
                let reusable = live.initialized
                    && !live.failed
                    && self.sources.len() == 2
                    && self
                        .sources
                        .iter()
                        .all(|source| source.supervisor.is_prepared());
                drop(live);
                if !reusable {
                    self.close_sources().await?;
                }
            }
            Effect::CancelReplies | Effect::CancelFinalReplies => {
                shared.live.lock().await.ai_accepting = false;
                shared.replies.lock().await.cancel_all(shared).await;
            }
        }
        Ok(Outcome::Ok)
    }
    pub async fn shutdown(&mut self) -> Result<(), Error> {
        if self.closed {
            return Ok(());
        }
        self.closed = true;
        self.shared.models.shutdown().await;
        self.shared.live.lock().await.ai_accepting = false;
        self.shared
            .replies
            .lock()
            .await
            .cancel_all(&self.shared)
            .await;
        let state = self.coordinator.execute(SessionCommand::Snapshot {})?;
        let result = if state.phase == Phase::Active {
            self.lifecycle(SessionCommand::Stop {}, None).await
        } else {
            Ok(())
        };
        self.close_sources().await?;
        result
    }
}
fn timestamp(value: &str) -> Result<db::Timestamp, Error> {
    Ok(serde_json::from_value(serde_json::Value::String(
        value.into(),
    ))?)
}
fn millis(value: u64) -> Result<db::Timestamp, Error> {
    let datetime = i64::try_from(value)
        .ok()
        .and_then(chrono::DateTime::from_timestamp_millis)
        .ok_or(Error::Unsupported)?;
    timestamp(&datetime.to_rfc3339())
}
pub(crate) async fn private_write(path: &std::path::Path, bytes: &[u8]) -> Result<(), Error> {
    use tokio::io::AsyncWriteExt;
    let mut options = tokio::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    options.mode(0o600);
    let mut file = options.open(path).await?;
    file.write_all(bytes).await?;
    file.sync_all().await?;
    Ok(())
}
async fn list_devices(config: &Config) -> Result<Vec<wire::Device>, Error> {
    use std::process::Stdio;
    use tokio::io::AsyncReadExt;
    let mut child = tokio::process::Command::new(&config.audio_worker)
        .arg("--list-devices")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()?;
    let result = tokio::time::timeout(Duration::from_secs(10), async {
        let mut bytes = Vec::new();
        child
            .stdout
            .take()
            .ok_or(Error::Closed)?
            .take(65537)
            .read_to_end(&mut bytes)
            .await?;
        if bytes.len() > 65536 {
            return Err(Error::Unsupported);
        }
        if !child.wait().await?.success() {
            return Err(Error::Media(media::Error::Capture));
        }
        if bytes.len() < 8 {
            return Err(Error::Unsupported);
        }
        let header_len =
            u32::from_le_bytes(bytes[..4].try_into().map_err(|_| Error::Unsupported)?) as usize;
        let pcm_len = u32::from_le_bytes(bytes[4..8].try_into().map_err(|_| Error::Unsupported)?);
        if pcm_len != 0 || header_len != bytes.len() - 8 {
            return Err(Error::Unsupported);
        }
        #[derive(serde::Deserialize)]
        #[serde(tag = "type", rename_all = "snake_case")]
        enum Devices {
            Devices { devices: Vec<wire::Device> },
        }
        let Devices::Devices { devices } = serde_json::from_slice(&bytes[8..])?;
        Ok(devices)
    })
    .await;
    match result {
        Ok(result) => result,
        Err(_) => {
            let _ = child.kill().await;
            let _ = child.wait().await;
            Err(Error::Media(media::Error::Timeout))
        }
    }
}
