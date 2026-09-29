use crate::{
    capture::{ActiveInput, Frame, Route},
    process,
    wire::*,
};
use serde_json::json;
use std::{
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio::{
    io::BufReader,
    process::{Child, ChildStdin, ChildStdout},
    sync::{mpsc, oneshot},
    task::JoinHandle,
    time::timeout,
};

pub struct Prepared {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
    sequence: u64,
    generation: u64,
}
pub struct Running {
    cancel: Option<oneshot::Sender<()>>,
    task: JoinHandle<Result<Prepared, Error>>,
}
impl Prepared {
    pub fn spawn(path: &Path, model: &Path, punctuation: Option<&Path>) -> Result<Self, Error> {
        if !model.is_absolute() || punctuation.is_some_and(|p| !p.is_absolute()) {
            return Err(Error::Protocol);
        }
        let mut args = vec![
            "--reazon-model".into(),
            model.to_string_lossy().into_owned(),
        ];
        if let Some(path) = punctuation {
            args.extend([
                "--punctuation-model".into(),
                path.to_string_lossy().into_owned(),
            ]);
        }
        let mut child = process::spawn(path, &args, false)?;
        let input = child.stdin.take().ok_or(Error::Worker)?;
        let output = BufReader::new(child.stdout.take().ok_or(Error::Worker)?);
        Ok(Self {
            child,
            input,
            output,
            sequence: 0,
            generation: 0,
        })
    }
    pub async fn prepare(&mut self, config: SpeechConfig) -> Result<(), Error> {
        let result = async {
            let ready: SpeechReply = timeout(
                Duration::from_secs(10),
                process::line(&mut self.output, 65536),
            )
            .await
            .map_err(|_| Error::Timeout)??;
            if !matches!(
                ready,
                SpeechReply {
                    id: None,
                    body: SpeechBody::Ready {
                        protocol: 2,
                        transcription_available: true
                    }
                }
            ) {
                return Err(Error::Protocol);
            }
            if !matches!(
                self.request(json!({"op":"prepare"}), 120).await?,
                SpeechBody::Prepared {}
            ) {
                return Err(Error::Protocol);
            }
            let mut configure = serde_json::to_value(config)?;
            configure["op"] = json!("configure");
            if !matches!(
                self.request(configure, 10).await?,
                SpeechBody::Configured {}
            ) {
                return Err(Error::Protocol);
            }
            Ok(())
        }
        .await;
        if let Err(error) = result {
            self.close().await;
            return Err(error);
        }
        Ok(())
    }
    async fn request(
        &mut self,
        command: serde_json::Value,
        seconds: u64,
    ) -> Result<SpeechBody, Error> {
        self.sequence += 1;
        process::write(
            &mut self.input,
            &json!({"id":self.sequence,"command":command}),
        )
        .await?;
        let response: SpeechReply = timeout(
            Duration::from_secs(seconds),
            process::line(&mut self.output, 65536),
        )
        .await
        .map_err(|_| Error::Timeout)??;
        if response.id != Some(self.sequence) || matches!(response.body, SpeechBody::Error {}) {
            return Err(Error::Speech);
        }
        Ok(response.body)
    }
    pub async fn start(
        mut self,
        role: Role,
        route: &Route,
        events: mpsc::Sender<Event>,
    ) -> Result<Running, Error> {
        let reset = self.request(json!({"op":"reset","role":role}), 10).await;
        if !matches!(reset, Ok(SpeechBody::Reset {})) {
            self.close().await;
            return Err(Error::Speech);
        }
        self.generation += 1;
        let (sender, input) = mpsc::channel(200);
        let failed = Arc::new(AtomicBool::new(false));
        *route.lock().unwrap() = Some(ActiveInput {
            sender,
            failed: failed.clone(),
        });
        let (cancel, cancelled) = oneshot::channel();
        let task = tokio::spawn(async move {
            let result = tokio::select! {
                _ = cancelled => Err(Error::Speech),
                result = self.transcribe(role, input, &failed, &events) => result,
            };
            if let Err(error) = result {
                self.close().await;
                let _ = timeout(
                    Duration::from_secs(2),
                    events.send(Event::SpeechError { code: error }),
                )
                .await;
                return Err(error);
            }
            Ok(self)
        });
        Ok(Running {
            cancel: Some(cancel),
            task,
        })
    }
    async fn transcribe(
        &mut self,
        role: Role,
        mut input: mpsc::Receiver<Result<Frame, Error>>,
        failed: &AtomicBool,
        events: &mpsc::Sender<Event>,
    ) -> Result<(), Error> {
        let mut last_sequence: Option<u64> = None;
        let mut last_end = 0;
        while let Some(frame) = input.recv().await {
            if failed.load(Ordering::Acquire) {
                return Err(Error::Discontinuity);
            }
            let frame = frame?;
            if last_sequence.is_some_and(|previous| Some(frame.sequence) != previous.checked_add(1))
            {
                return Err(Error::Discontinuity);
            }
            last_sequence = Some(frame.sequence);
            let pcm: Vec<_> = frame
                .pcm
                .as_chunks::<2>()
                .0
                .iter()
                .map(|p| i16::from_le_bytes([p[0], p[1]]))
                .collect();
            let response = self
                .request(json!({"op":"audio","role":role,"pcm":pcm}), 30)
                .await?;
            let SpeechBody::Audio { segment } = response else {
                return Err(Error::Protocol);
            };
            self.segment(segment, &mut last_end, events).await?;
        }
        if failed.load(Ordering::Acquire) {
            return Err(Error::Discontinuity);
        }
        let response = self.request(json!({"op":"finish","role":role}), 30).await?;
        let SpeechBody::Finished { segment } = response else {
            return Err(Error::Protocol);
        };
        self.segment(segment, &mut last_end, events).await
    }
    async fn segment(
        &self,
        segment: Option<Segment>,
        last_end: &mut u64,
        events: &mpsc::Sender<Event>,
    ) -> Result<(), Error> {
        let Some(segment) = segment else {
            return Ok(());
        };
        if segment.generation != self.generation
            || segment.end_sample < segment.start_sample
            || segment.end_sample <= *last_end
        {
            return Err(Error::Protocol);
        }
        *last_end = segment.end_sample;
        if let Recognition::Recognized {
            text: raw_text,
            punctuation,
        } = segment.recognition
        {
            let (text, punctuation_failed) = match punctuation {
                Some(Punctuation::Applied { text }) => (text, false),
                Some(Punctuation::Failed {}) => (raw_text.clone(), true),
                None => (raw_text.clone(), false),
            };
            if !text.trim().is_empty() {
                timeout(
                    Duration::from_secs(5),
                    events.send(Event::Transcript {
                        generation: self.generation,
                        text,
                        raw_text,
                        start_sample: segment.start_sample,
                        end_sample: segment.end_sample,
                        punctuation_failed,
                    }),
                )
                .await
                .map_err(|_| Error::Timeout)?
                .map_err(|_| Error::Protocol)?;
            }
        }
        Ok(())
    }
    pub fn generation(&self) -> u64 {
        self.generation
    }
    pub async fn close(&mut self) {
        process::reap(&mut self.child).await;
    }
}
impl Running {
    pub async fn finish(mut self) -> Result<Prepared, Error> {
        // The caller detached capture before entering here. Drain the finite queue.
        match timeout(Duration::from_secs(40), &mut self.task).await {
            Ok(result) => result.map_err(|_| Error::Speech)?,
            Err(_) => {
                if let Some(cancel) = self.cancel.take() {
                    let _ = cancel.send(());
                }
                let _ = self.task.await;
                Err(Error::Timeout)
            }
        }
    }
    pub async fn cancel(mut self) {
        if let Some(cancel) = self.cancel.take() {
            let _ = cancel.send(());
        }
        if let Ok(Ok(mut prepared)) = self.task.await {
            prepared.close().await;
        }
    }
}
// Dropping an in-flight operation closes the cancellation sender, so its child
// is explicitly killed and reaped inside the task rather than detached forever.
