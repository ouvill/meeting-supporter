use crate::{process, wire::*};
use std::{
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncReadExt, BufReader},
    process::{Child, ChildStdin},
    sync::mpsc,
    task::JoinHandle,
    time::timeout,
};

pub struct Frame {
    pub sequence: u64,
    pub pcm: [u8; 960],
}
pub struct ActiveInput {
    pub sender: mpsc::Sender<Result<Frame, Error>>,
    pub failed: Arc<AtomicBool>,
    pub lag_notified: bool,
}
pub type Route = Arc<Mutex<Option<ActiveInput>>>;

pub struct Capture {
    child: Arc<tokio::sync::Mutex<Child>>,
    input: ChildStdin,
    replies: mpsc::Receiver<Result<CaptureEvent, Error>>,
    reader: Option<JoinHandle<()>>,
    sequence: u64,
    pub recording_failed: Arc<AtomicBool>,
    pub route: Route,
    pub name: String,
}
impl Capture {
    pub async fn open(
        path: &Path,
        role: Role,
        device: Option<String>,
        events: mpsc::Sender<Event>,
    ) -> Result<Self, Error> {
        let mut args = vec!["--role".into(), role.as_str().into()];
        if let Some(device) = device {
            args.extend(["--device".into(), device]);
        }
        let mut child = process::spawn(path, &args, true)?;
        let input = child.stdin.take().ok_or(Error::Worker)?;
        let output = child.stdout.take().ok_or(Error::Worker)?;
        let child = Arc::new(tokio::sync::Mutex::new(child));
        let reader_child = child.clone();
        let recording_failed = Arc::new(AtomicBool::new(false));
        let reader_recording_failed = recording_failed.clone();
        let (reply_tx, mut replies) = mpsc::channel(16);
        let route: Route = Arc::new(Mutex::new(None));
        let reader_route = route.clone();
        let reader = tokio::spawn(async move {
            let result = read(
                BufReader::new(output),
                &reply_tx,
                &reader_route,
                &events,
                &reader_recording_failed,
            )
            .await;
            if result.is_err() {
                reader_recording_failed.store(true, Ordering::Release);
                let _ = process::reap(&mut *reader_child.lock().await).await;
                if let Some(active) = reader_route.lock().unwrap().take() {
                    active.failed.store(true, Ordering::Release);
                    let _ = active.sender.try_send(Err(Error::Capture));
                    // A dropped full queue is also detected by the failed capture flag below.
                }
                let _ = reply_tx.try_send(Err(Error::Capture));
                let _ = events.try_send(Event::CaptureError {});
            }
        });
        let ready = timeout(Duration::from_secs(8), replies.recv()).await;
        let name = match ready {
            Ok(Some(Ok(CaptureEvent::Ready {
                protocol: 1,
                rate: 16000,
                name,
            }))) => name,
            _ => {
                let _ = process::reap(&mut *child.lock().await).await;
                reader.abort();
                return Err(Error::Capture);
            }
        };
        Ok(Self {
            child,
            input,
            replies,
            reader: Some(reader),
            sequence: 0,
            recording_failed,
            route,
            name,
        })
    }
    pub async fn request(&mut self, command: serde_json::Value) -> Result<CaptureEvent, Error> {
        if !self.healthy() {
            return Err(Error::Capture);
        }
        self.sequence += 1;
        process::write(
            &mut self.input,
            &serde_json::json!({"id":self.sequence,"command":command}),
        )
        .await?;
        let reply = timeout(Duration::from_secs(8), self.replies.recv())
            .await
            .map_err(|_| Error::Timeout)?
            .ok_or(Error::Capture)??;
        let id = match &reply {
            CaptureEvent::RecordingStarted { id }
            | CaptureEvent::RecordingStopped { id, .. }
            | CaptureEvent::Stopped { id } => *id,
            CaptureEvent::Error { id: Some(id) } if *id == self.sequence => {
                return Err(Error::Recording)
            }
            _ => return Err(Error::Protocol),
        };
        if id != self.sequence {
            return Err(Error::Protocol);
        }
        Ok(reply)
    }
    pub fn healthy(&self) -> bool {
        self.reader
            .as_ref()
            .is_some_and(|reader| !reader.is_finished())
    }
    pub async fn close(&mut self) -> Result<(), Error> {
        self.route.lock().unwrap().take();
        // A normal shutdown finalizes any WAV still open.
        let _ = self.request(serde_json::json!({"op":"shutdown"})).await;
        let result = process::reap(&mut *self.child.lock().await).await;
        if let Some(reader) = self.reader.take() {
            reader.abort();
            let _ = reader.await;
        }
        result
    }
}
async fn read(
    mut stream: BufReader<tokio::process::ChildStdout>,
    replies: &mpsc::Sender<Result<CaptureEvent, Error>>,
    route: &Route,
    events: &mpsc::Sender<Event>,
    recording_failed: &AtomicBool,
) -> Result<(), Error> {
    let mut last_level = Instant::now();
    let mut peak: f64 = 0.0;
    loop {
        let packet = timeout(Duration::from_secs(6), async {
            let header = stream.read_u32_le().await? as usize;
            let size = stream.read_u32_le().await? as usize;
            if header == 0 || header > 16384 || (size != 0 && size != 960) {
                return Err(Error::Protocol);
            }
            let mut bytes = vec![0; header];
            stream.read_exact(&mut bytes).await?;
            let event: CaptureEvent = serde_json::from_slice(&bytes)?;
            if matches!(event, CaptureEvent::Audio { .. }) != (size == 960) {
                return Err(Error::Protocol);
            }
            let mut pcm = [0; 960];
            if size != 0 {
                stream.read_exact(&mut pcm).await?;
            }
            Ok::<_, Error>((event, pcm))
        })
        .await
        .map_err(|_| Error::Timeout)??;
        match packet {
            (
                CaptureEvent::Audio {
                    sequence,
                    peak: value,
                },
                pcm,
            ) => {
                if !value.is_finite() || !(0.0..=1.0).contains(&value) {
                    return Err(Error::Protocol);
                }
                let mut routing = route.lock().unwrap();
                if let Some(active) = routing.as_mut() {
                    let queued = active.sender.max_capacity() - active.sender.capacity();
                    if queued > 200 && !active.lag_notified {
                        active.lag_notified = events.try_send(Event::SpeechLag {}).is_ok();
                    } else if queued < 50 {
                        active.lag_notified = false;
                    }
                    if active.sender.try_send(Ok(Frame { sequence, pcm })).is_err() {
                        // Retire inference on overload; capture and WAV continue.
                        active.failed.store(true, Ordering::Release);
                        routing.take();
                        let _ = events.try_send(Event::SpeechError {
                            code: Error::Discontinuity,
                        });
                    }
                }
                drop(routing);
                peak = peak.max(value);
                if last_level.elapsed() >= Duration::from_millis(120) {
                    let _ = events.try_send(Event::Level { peak });
                    last_level = Instant::now();
                    peak = 0.0;
                }
            }
            (CaptureEvent::RecordingError {}, _) => {
                recording_failed.store(true, Ordering::Release);
                events
                    .try_send(Event::RecordingError {})
                    .map_err(|_| Error::Protocol)?;
            }
            (CaptureEvent::Error { id: None }, _) => return Err(Error::Capture),
            (event, _) => {
                let stopped = matches!(event, CaptureEvent::Stopped { .. });
                replies.try_send(Ok(event)).map_err(|_| Error::Protocol)?;
                if stopped {
                    return Ok(());
                }
            }
        }
    }
}
