//! Owns capture/inference child lifetimes and PCM routing independently of Python.
mod capabilities;
mod capture;
mod process;
mod speech;
pub mod wire;

pub use capabilities::whisper_gpu_supported;

use capture::Capture;
use speech::{Prepared, Running};
use std::path::PathBuf;
use tokio::sync::mpsc;
use wire::*;

pub struct Options {
    pub audio_worker: PathBuf,
    pub speech_worker: PathBuf,
    pub role: Role,
    pub device: Option<String>,
}
enum Speech {
    Unprepared,
    Preparing(Box<Prepared>),
    Prepared(Box<Prepared>),
    Running(Running),
    Failed,
}
pub struct Supervisor {
    capture: Capture,
    speech: Speech,
    options: Options,
    events: mpsc::Sender<Event>,
    generation: u64,
}
impl Supervisor {
    pub async fn open(options: Options, events: mpsc::Sender<Event>) -> Result<Self, Error> {
        let capture = Capture::open(
            &options.audio_worker,
            options.role,
            options.device.clone(),
            events.clone(),
        )
        .await?;
        events
            .send(Event::Ready {
                protocol: 1,
                name: capture.name.clone(),
                rate: 16000,
            })
            .await
            .map_err(|_| Error::Protocol)?;
        Ok(Self {
            capture,
            speech: Speech::Unprepared,
            options,
            events,
            generation: 0,
        })
    }
    pub fn detach_speech_input(&mut self) {
        self.capture.route.lock().unwrap().take();
    }
    pub fn is_prepared(&self) -> bool {
        matches!(self.speech, Speech::Prepared(_)) && self.capture.healthy()
    }
    /// Wait for the owner to commit all events produced before this barrier.
    pub async fn drain_events(&self) -> Result<(), Error> {
        let (done, received) = tokio::sync::oneshot::channel();
        self.events
            .send(Event::Drained { done })
            .await
            .map_err(|_| Error::Protocol)?;
        tokio::time::timeout(std::time::Duration::from_secs(10), received)
            .await
            .map_err(|_| Error::Timeout)?
            .map_err(|_| Error::Protocol)
    }
    pub async fn execute(&mut self, command: Command) -> Result<Option<Recording>, Error> {
        match command {
            Command::Prepare {
                recognizer,
                model,
                punctuation,
                config,
            } => {
                if matches!(self.speech, Speech::Running(_)) {
                    return Err(Error::Busy);
                }
                self.shutdown_speech().await;
                // Store the child before the first await so cancellation can reap it.
                self.speech = Speech::Preparing(Box::new(Prepared::spawn(
                    &self.options.speech_worker,
                    &model,
                    &recognizer,
                    punctuation.as_deref(),
                )?));
                if let Speech::Preparing(worker) = &mut self.speech {
                    worker.prepare(config).await?;
                    if let Some(device) = worker.execution_device {
                        self.events
                            .send(Event::ExecutionDevice { device })
                            .await
                            .map_err(|_| Error::Protocol)?;
                    }
                }
                let Speech::Preparing(worker) = std::mem::replace(&mut self.speech, Speech::Failed)
                else {
                    return Err(Error::Protocol);
                };
                self.speech = Speech::Prepared(worker);
                self.generation = 0;
            }
            Command::StartSpeech {} => {
                if !self.capture.healthy() {
                    return Err(Error::Capture);
                }
                let speech = std::mem::replace(&mut self.speech, Speech::Failed);
                let Speech::Prepared(prepared) = speech else {
                    self.speech = speech;
                    return Err(Error::Busy);
                };
                self.generation = prepared.generation() + 1;
                let running = (*prepared)
                    .start(self.options.role, &self.capture.route, self.events.clone())
                    .await?;
                self.speech = Speech::Running(running);
            }
            Command::StopSpeech {} => {
                self.capture.route.lock().unwrap().take();
                let speech = std::mem::replace(&mut self.speech, Speech::Failed);
                if let Speech::Running(running) = speech {
                    self.speech = Speech::Prepared(Box::new(running.finish().await?));
                } else {
                    let failed = matches!(speech, Speech::Failed);
                    self.speech = speech;
                    if failed {
                        return Err(Error::Speech);
                    }
                }
            }
            Command::ShutdownSpeech {} => self.shutdown_speech().await,
            Command::StartRecording { path } => {
                self.capture
                    .recording_failed
                    .store(false, std::sync::atomic::Ordering::Release);
                let reply = self
                    .capture
                    .request(serde_json::json!({"op":"start_recording","path":path}))
                    .await?;
                if !matches!(reply, CaptureEvent::RecordingStarted { .. }) {
                    return Err(Error::Protocol);
                }
            }
            Command::StopRecording {} => {
                let reply = self
                    .capture
                    .request(serde_json::json!({"op":"stop_recording"}))
                    .await?;
                let CaptureEvent::RecordingStopped { recording, .. } = reply else {
                    return Err(Error::Protocol);
                };
                if self
                    .capture
                    .recording_failed
                    .load(std::sync::atomic::Ordering::Acquire)
                {
                    return Err(Error::Recording);
                }
                if recording
                    .as_ref()
                    .is_some_and(|r| r.size_bytes < 44 || r.ended_ms < r.started_ms)
                {
                    return Err(Error::Recording);
                }
                return Ok(recording);
            }
            Command::Shutdown {} => self.close().await,
        }
        Ok(None)
    }
    pub fn generation(&self) -> u64 {
        self.generation
    }
    async fn shutdown_speech(&mut self) {
        self.capture.route.lock().unwrap().take();
        match std::mem::replace(&mut self.speech, Speech::Unprepared) {
            Speech::Preparing(mut prepared) | Speech::Prepared(mut prepared) => {
                prepared.close().await
            }
            Speech::Running(running) => running.cancel().await,
            _ => {}
        }
    }
    pub async fn close(&mut self) {
        self.shutdown_speech().await;
        self.capture.close().await;
    }
}
