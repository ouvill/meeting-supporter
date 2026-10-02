//! Owns capture/inference child lifetimes and PCM routing independently of Python.
mod capabilities;
mod capture;
mod process;
mod shared_speech;
mod speech;
pub use shared_speech::SharedSpeech;
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
    SharedPrepared(shared_speech::Client),
    SharedRunning(shared_speech::Running),
    Failed,
}
pub struct Supervisor {
    capture: Capture,
    speech: Speech,
    options: Options,
    events: mpsc::Sender<Event>,
    generation: u64,
    release_failed: bool,
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
            release_failed: false,
        })
    }
    pub fn detach_speech_input(&mut self) {
        self.capture.route.lock().unwrap().take();
    }
    pub fn is_prepared(&self) -> bool {
        match &self.speech {
            Speech::Prepared(_) => self.capture.healthy(),
            Speech::SharedPrepared(client) => self.capture.healthy() && client.healthy(),
            _ => false,
        }
    }
    pub async fn attach_shared(&mut self, shared: &SharedSpeech) -> Result<(), Error> {
        self.shutdown_speech().await?;
        self.speech = Speech::SharedPrepared(shared.bind(self.options.role, self.events.clone())?);
        self.generation = 0;
        if let Some(device) = shared.execution_device {
            self.events
                .send(Event::ExecutionDevice { device })
                .await
                .map_err(|_| Error::Protocol)?;
        }
        Ok(())
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
        let result = self.execute_inner(command).await;
        self.release_failed |= matches!(result, Err(Error::Shutdown));
        result
    }
    async fn execute_inner(&mut self, command: Command) -> Result<Option<Recording>, Error> {
        match command {
            Command::Prepare {
                recognizer,
                model,
                punctuation,
                config,
            } => {
                if matches!(self.speech, Speech::Running(_) | Speech::SharedRunning(_)) {
                    return Err(Error::Busy);
                }
                self.shutdown_speech().await?;
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
                if let Speech::SharedPrepared(client) = speech {
                    let (generation, running) =
                        client.start(self.options.role, &self.capture.route).await?;
                    self.generation = generation;
                    self.speech = Speech::SharedRunning(running);
                    return Ok(None);
                }
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
                if let Speech::SharedRunning(running) = speech {
                    self.speech = Speech::SharedPrepared(running.finish().await?);
                } else if let Speech::Running(running) = speech {
                    self.speech = Speech::Prepared(Box::new(running.finish().await?));
                } else {
                    let failed = matches!(speech, Speech::Failed);
                    self.speech = speech;
                    if failed {
                        return Err(Error::Speech);
                    }
                }
            }
            Command::ShutdownSpeech {} => self.shutdown_speech().await?,
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
            Command::Shutdown {} => self.close().await?,
        }
        Ok(None)
    }
    pub fn generation(&self) -> u64 {
        self.generation
    }
    async fn shutdown_speech(&mut self) -> Result<(), Error> {
        self.capture.route.lock().unwrap().take();
        match std::mem::replace(&mut self.speech, Speech::Unprepared) {
            Speech::Preparing(mut prepared) | Speech::Prepared(mut prepared) => {
                prepared.close().await
            }
            Speech::Running(running) => running.cancel().await,
            Speech::SharedRunning(running) => running.cancel().await,
            Speech::SharedPrepared(client) => client.close().await,
            _ => Ok(()),
        }
    }
    pub async fn close(&mut self) -> Result<(), Error> {
        let speech = self.shutdown_speech().await;
        let capture = self.capture.close().await;
        if self.release_failed {
            return Err(Error::Shutdown);
        }
        speech.and(capture)
    }
}
