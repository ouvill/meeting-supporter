//! One native model owner, with independent PCM producers and role-tagged results.
use crate::{
    capture::{ActiveInput, Frame, Route},
    process,
    wire::*,
};
use serde_json::json;
use std::{
    collections::HashMap,
    path::Path,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::Duration,
};
use tokio::{
    io::BufReader,
    process::{Child, ChildStdin, ChildStdout},
    sync::{mpsc, oneshot, Mutex as AsyncMutex},
    task::JoinHandle,
    time::timeout,
};

type ReplySender = oneshot::Sender<Result<SpeechBody, Error>>;
struct Subscriber {
    events: mpsc::Sender<Event>,
    generation: u64,
    last_end: u64,
    active: bool,
}
struct Inner {
    child: AsyncMutex<Child>,
    input: AsyncMutex<ChildStdin>,
    pending: Mutex<HashMap<u64, ReplySender>>,
    subscribers: Mutex<[Option<Subscriber>; 2]>,
    sequence: AtomicU64,
    closed: AtomicBool,
    release_failed: AtomicBool,
}
#[derive(Clone)]
pub(crate) struct Client(Arc<Inner>);
pub struct SharedSpeech {
    client: Client,
    reader: Option<JoinHandle<()>>,
    ready: Option<oneshot::Receiver<Result<(), Error>>>,
    pub execution_device: Option<ExecutionDevice>,
}
pub(crate) struct Running {
    client: Client,
    cancel: Option<oneshot::Sender<()>>,
    task: JoinHandle<Result<(), Error>>,
}
impl SharedSpeech {
    pub fn spawn(
        path: &Path,
        model: &Path,
        recognizer: &Recognizer,
        punctuation: Option<&Path>,
    ) -> Result<Self, Error> {
        if !model.is_absolute() || punctuation.is_some_and(|p| !p.is_absolute()) {
            return Err(Error::Protocol);
        }
        let mut args = vec!["--shared".into()];
        match recognizer {
            Recognizer::Reazonspeech => args.extend([
                "--reazon-model".into(),
                model.to_string_lossy().into_owned(),
            ]),
            Recognizer::Whisper { device, language } => {
                if punctuation.is_some() {
                    return Err(Error::Protocol);
                }
                args.extend([
                    "--whisper-model".into(),
                    model.to_string_lossy().into_owned(),
                    "--inference-device".into(),
                    device.argument().into(),
                    "--language".into(),
                    language.argument().into(),
                ]);
            }
        }
        if let Some(path) = punctuation {
            args.extend([
                "--punctuation-model".into(),
                path.to_string_lossy().into_owned(),
            ]);
        }
        let mut child = process::spawn(path, &args, false)?;
        let input = child.stdin.take().ok_or(Error::Worker)?;
        let output = BufReader::new(child.stdout.take().ok_or(Error::Worker)?);
        let client = Client(Arc::new(Inner {
            child: AsyncMutex::new(child),
            input: AsyncMutex::new(input),
            pending: Mutex::new(HashMap::new()),
            subscribers: Mutex::new([None, None]),
            sequence: AtomicU64::new(0),
            closed: AtomicBool::new(false),
            release_failed: AtomicBool::new(false),
        }));
        let (ready, received) = oneshot::channel();
        let reader_client = client.clone();
        let reader = tokio::spawn(async move {
            if let Err(error) = reader_client.read(output, ready).await {
                reader_client.fail(error).await;
            }
        });
        Ok(Self {
            client,
            reader: Some(reader),
            ready: Some(received),
            execution_device: None,
        })
    }
    pub async fn prepare(&mut self, config: SpeechConfig) -> Result<(), Error> {
        if let Some(ready) = self.ready.take() {
            ready.await.map_err(|_| Error::Worker)??;
        }
        let SpeechBody::Prepared { execution_device } =
            self.client.request(json!({"op":"prepare"}), 120).await?
        else {
            return Err(Error::Protocol);
        };
        self.execution_device = execution_device;
        let mut command = serde_json::to_value(config)?;
        command["op"] = json!("configure");
        if !matches!(
            self.client.request(command, 10).await?,
            SpeechBody::Configured {}
        ) {
            return Err(Error::Protocol);
        }
        Ok(())
    }
    pub(crate) fn bind(&self, role: Role, events: mpsc::Sender<Event>) -> Result<Client, Error> {
        if !self.client.healthy() {
            return Err(Error::Speech);
        }
        let mut subscribers = self.client.0.subscribers.lock().unwrap();
        let slot = &mut subscribers[role.index()];
        if slot.is_some() {
            return Err(Error::Busy);
        }
        *slot = Some(Subscriber {
            events,
            generation: 0,
            last_end: 0,
            active: false,
        });
        Ok(self.client.clone())
    }
    pub async fn close(&mut self) -> Result<(), Error> {
        let result = self.client.close().await;
        if let Some(reader) = self.reader.take() {
            reader.abort();
            let _ = reader.await;
        }
        result
    }
}
impl Drop for SharedSpeech {
    fn drop(&mut self) {
        if let Some(reader) = self.reader.take() {
            reader.abort();
        }
    }
}
impl Client {
    pub fn healthy(&self) -> bool {
        !self.0.closed.load(Ordering::Acquire)
    }
    async fn request(&self, command: serde_json::Value, seconds: u64) -> Result<SpeechBody, Error> {
        let id = self.0.sequence.fetch_add(1, Ordering::Relaxed) + 1;
        let (reply, received) = oneshot::channel();
        {
            let mut pending = self.0.pending.lock().unwrap();
            if !self.healthy() {
                return Err(Error::Speech);
            }
            if pending.len() >= 8 {
                return Err(Error::Busy);
            }
            pending.insert(id, reply);
        }
        let result = async {
            // Do not hold the writer while waiting for a reply or inference.
            process::write(
                &mut *self.0.input.lock().await,
                &json!({"id":id,"command":command}),
            )
            .await?;
            let body = timeout(Duration::from_secs(seconds), received)
                .await
                .map_err(|_| Error::Timeout)?
                .map_err(|_| Error::Speech)??;
            match body {
                SpeechBody::Error {
                    code: SpeechFailure::GpuUnavailable,
                } => Err(Error::GpuUnavailable),
                SpeechBody::Error {
                    code: SpeechFailure::Busy,
                } => Err(Error::Discontinuity),
                SpeechBody::Error { .. } => Err(Error::Speech),
                _ => Ok(body),
            }
        }
        .await;
        if let Err(error) = result {
            self.fail(error).await;
        }
        result
    }
    async fn read(
        &self,
        mut output: BufReader<ChildStdout>,
        ready: oneshot::Sender<Result<(), Error>>,
    ) -> Result<(), Error> {
        let first = timeout(
            Duration::from_secs(10),
            process::line::<SpeechReply>(&mut output, 65536),
        )
        .await
        .map_err(|_| Error::Timeout)?;
        match first {
            Ok(SpeechReply {
                id: None,
                body:
                    SpeechBody::Ready {
                        protocol: 3,
                        transcription_available: true,
                    },
            }) => {
                let _ = ready.send(Ok(()));
            }
            _ => {
                let _ = ready.send(Err(Error::Protocol));
                return Err(Error::Protocol);
            }
        }
        loop {
            let reply: SpeechReply = process::line(&mut output, 65536).await?;
            if let Some(id) = reply.id {
                let pending = self
                    .0
                    .pending
                    .lock()
                    .unwrap()
                    .remove(&id)
                    .ok_or(Error::Protocol)?;
                let _ = pending.send(Ok(reply.body));
            } else {
                let event = match reply.body {
                    SpeechBody::Segment { role, segment } => self.segment(role, segment)?,
                    SpeechBody::Lag { role } => self.0.subscribers.lock().unwrap()[role.index()]
                        .as_ref()
                        .map(|s| (s.events.clone(), Event::SpeechLag {})),
                    _ => return Err(Error::Speech),
                };
                if let Some((events, event)) = event {
                    timeout(Duration::from_secs(5), events.send(event))
                        .await
                        .map_err(|_| Error::Timeout)?
                        .map_err(|_| Error::Protocol)?;
                }
            }
        }
    }
    fn segment(
        &self,
        role: Role,
        segment: Segment,
    ) -> Result<Option<(mpsc::Sender<Event>, Event)>, Error> {
        let mut subscribers = self.0.subscribers.lock().unwrap();
        let source = subscribers[role.index()].as_mut().ok_or(Error::Protocol)?;
        if !source.active
            || segment.generation != source.generation
            || segment.end_sample <= segment.start_sample
            || segment.end_sample <= source.last_end
        {
            return Err(Error::Protocol);
        }
        source.last_end = segment.end_sample;
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
                return Ok(Some((
                    source.events.clone(),
                    Event::Transcript {
                        generation: source.generation,
                        text,
                        raw_text,
                        start_sample: segment.start_sample,
                        end_sample: segment.end_sample,
                        punctuation_failed,
                    },
                )));
            }
        }
        Ok(None)
    }
    async fn fail(&self, error: Error) {
        let events = self.retire();
        let code = self.reap().await.err().unwrap_or(error);
        for events in events {
            let _ = timeout(
                Duration::from_secs(2),
                events.send(Event::SpeechError { code }),
            )
            .await;
        }
    }
    fn retire(&self) -> Vec<mpsc::Sender<Event>> {
        if self.0.closed.swap(true, Ordering::AcqRel) {
            return vec![];
        }
        for (_, reply) in self.0.pending.lock().unwrap().drain() {
            let _ = reply.send(Err(Error::Speech));
        }
        self.0
            .subscribers
            .lock()
            .unwrap()
            .iter_mut()
            .filter_map(|s| s.take().map(|s| s.events))
            .collect()
    }
    async fn reap(&self) -> Result<(), Error> {
        let result = process::reap(&mut *self.0.child.lock().await).await;
        if result.is_err() {
            self.0.release_failed.store(true, Ordering::Release);
        }
        if self.0.release_failed.load(Ordering::Acquire) {
            Err(Error::Shutdown)
        } else {
            result
        }
    }
    pub async fn close(&self) -> Result<(), Error> {
        self.retire();
        self.reap().await
    }
    pub async fn start(&self, role: Role, route: &Route) -> Result<(u64, Running), Error> {
        if !matches!(
            self.request(json!({"op":"reset","role":role}), 10).await?,
            SpeechBody::Reset {}
        ) {
            return Err(Error::Protocol);
        }
        let generation = {
            let mut subscribers = self.0.subscribers.lock().unwrap();
            let source = subscribers[role.index()].as_mut().ok_or(Error::Speech)?;
            source.generation += 1;
            source.last_end = 0;
            source.active = true;
            source.generation
        };
        let (sender, input) = mpsc::channel(2000);
        let failed = Arc::new(AtomicBool::new(false));
        *route.lock().unwrap() = Some(ActiveInput {
            sender,
            failed: failed.clone(),
            lag_notified: false,
        });
        let client = self.clone();
        let (cancel, cancelled) = oneshot::channel();
        let task = tokio::spawn(async move {
            let result = tokio::select! {
                _ = cancelled => Err(Error::Speech),
                result = client.transcribe(role, input, &failed) => result,
            };
            if let Err(error) = result {
                client.fail(error).await;
            }
            result
        });
        Ok((
            generation,
            Running {
                client: self.clone(),
                cancel: Some(cancel),
                task,
            },
        ))
    }
    async fn transcribe(
        &self,
        role: Role,
        mut input: mpsc::Receiver<Result<Frame, Error>>,
        failed: &AtomicBool,
    ) -> Result<(), Error> {
        let mut previous: Option<u64> = None;
        while let Some(frame) = input.recv().await {
            if failed.load(Ordering::Acquire) {
                return Err(Error::Discontinuity);
            }
            let frame = frame?;
            if previous.is_some_and(|p| Some(frame.sequence) != p.checked_add(1)) {
                return Err(Error::Discontinuity);
            }
            previous = Some(frame.sequence);
            let pcm: Vec<_> = frame
                .pcm
                .as_chunks::<2>()
                .0
                .iter()
                .map(|p| i16::from_le_bytes(*p))
                .collect();
            if !matches!(
                self.request(json!({"op":"audio","role":role,"pcm":pcm}), 10)
                    .await?,
                SpeechBody::Audio { segment: None }
            ) {
                return Err(Error::Protocol);
            }
        }
        if failed.load(Ordering::Acquire) {
            return Err(Error::Discontinuity);
        }
        if !matches!(
            self.request(json!({"op":"finish","role":role}), 120)
                .await?,
            SpeechBody::Finished { segment: None }
        ) {
            return Err(Error::Protocol);
        }
        if let Some(source) = self.0.subscribers.lock().unwrap()[role.index()].as_mut() {
            source.active = false;
        }
        Ok(())
    }
}
impl Running {
    pub async fn finish(mut self) -> Result<Client, Error> {
        match timeout(Duration::from_secs(30), &mut self.task).await {
            Ok(result) => {
                result.map_err(|_| Error::Shutdown)??;
                Ok(self.client)
            }
            Err(_) => {
                self.cancel().await?;
                Err(Error::Timeout)
            }
        }
    }
    pub async fn cancel(mut self) -> Result<(), Error> {
        let result = self.client.close().await;
        if let Some(cancel) = self.cancel.take() {
            let _ = cancel.send(());
        }
        self.task.await.map_err(|_| Error::Shutdown)?.ok();
        result
    }
}
