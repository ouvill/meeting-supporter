//! Protocol 3: frame acknowledgements never wait for recognition. Completed
//! utterances carry their role/generation in independent events.
use super::{Output, Status, respond};
use meeting_native_backend::{
    error::SpeechError,
    protocol::{Command, ModelStatus, Reply, Request, Role},
    session::{Frontend, Inference, PendingSegment, SessionConfig},
};
use std::{
    io,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
        mpsc::{self, Receiver, SyncSender},
    },
    thread::{self, JoinHandle},
    time::Instant,
};

// Bound both allocation count and queued audio, including in-flight inference.
const MAX_SAMPLES: usize = 60 * 16_000;
const LAG_SAMPLES: usize = 6 * 16_000;
const MAX_JOBS: usize = 16;
#[derive(Default)]
struct Budget {
    samples: AtomicUsize,
    pending: [AtomicUsize; 2],
    failed: AtomicBool,
}
struct Reservation {
    budget: Arc<Budget>,
    role: Role,
    samples: usize,
}
impl Reservation {
    fn new(budget: &Arc<Budget>, role: Role, samples: usize) -> Result<Self, SpeechError> {
        budget
            .samples
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |n| {
                n.checked_add(samples).filter(|&n| n <= MAX_SAMPLES)
            })
            .map_err(|_| SpeechError::Busy)?;
        budget.pending[role.index()].fetch_add(1, Ordering::AcqRel);
        Ok(Self {
            budget: budget.clone(),
            role,
            samples,
        })
    }
}
impl Drop for Reservation {
    fn drop(&mut self) {
        self.budget
            .samples
            .fetch_sub(self.samples, Ordering::AcqRel);
        self.budget.pending[self.role.index()].fetch_sub(1, Ordering::AcqRel);
    }
}
enum Job {
    Segment(PendingSegment, Reservation),
    Finish(u64, Reservation),
}
struct Shared {
    frontend: Frontend,
    sender: Option<SyncSender<Job>>,
    task: Option<JoinHandle<io::Result<()>>>,
    budget: Arc<Budget>,
    device: Option<&'static str>,
    lagging: bool,
}
impl Shared {
    fn prepare(config: SessionConfig, output: Output, status: Status) -> Result<Self, SpeechError> {
        let frontend = Frontend::load(&config.runtime_library)?;
        let (sender, jobs) = mpsc::sync_channel(MAX_JOBS);
        let (ready, loaded) = mpsc::sync_channel(1);
        let budget = Arc::new(Budget::default());
        let worker_budget = budget.clone();
        let task = thread::Builder::new()
            .name("speech-inference".into())
            .spawn(move || {
                let mut engine = match Inference::load(config.plan) {
                    Ok(engine) => engine,
                    Err(error) => {
                        let _ = ready.send(Err(error));
                        return Ok(());
                    }
                };
                if ready.send(Ok(engine.execution_device())).is_err() {
                    return Ok(());
                }
                let result = infer(
                    jobs,
                    &worker_budget,
                    |pending| engine.recognize(pending),
                    |id, reply| respond(&output, id, reply),
                );
                if result.is_err() {
                    worker_budget.failed.store(true, Ordering::Release);
                    *status.lock().unwrap() = ModelStatus::Failed;
                    let _ = respond(
                        &output,
                        None,
                        Reply::Error {
                            code: SpeechError::InferenceFailed,
                        },
                    );
                }
                result
            })
            .map_err(|_| SpeechError::RuntimeUnavailable)?;
        match loaded.recv() {
            Ok(Ok(device)) => Ok(Self {
                frontend,
                sender: Some(sender),
                task: Some(task),
                budget,
                device,
                lagging: false,
            }),
            result => {
                drop(sender);
                let _ = task.join();
                Err(match result {
                    Ok(Err(error)) => error,
                    _ => SpeechError::InferenceFailed,
                })
            }
        }
    }
    fn enqueue(&self, job: Job) -> Result<(), SpeechError> {
        self.sender
            .as_ref()
            .ok_or(SpeechError::InferenceFailed)?
            .try_send(job)
            .map_err(|_| SpeechError::Busy)
    }
    fn execute(&mut self, request: Request, output: &Output) -> Result<(), SpeechError> {
        if self.budget.failed.load(Ordering::Acquire) {
            return Err(SpeechError::InferenceFailed);
        }
        if let Command::Reset { role } = &request.command
            && self.budget.pending[role.index()].load(Ordering::Acquire) != 0
        {
            return Err(SpeechError::InvalidAudioCommand);
        }
        if matches!(request.command, Command::Configure { .. })
            && self
                .budget
                .pending
                .iter()
                .any(|n| n.load(Ordering::Acquire) != 0)
        {
            return Err(SpeechError::InvalidAudioCommand);
        }
        let finish = match &request.command {
            Command::Finish { role } => Some(*role),
            _ => None,
        };
        let (reply, pending) = self.frontend.execute(request.command)?;
        if let Some(pending) = pending {
            let reservation = Reservation::new(&self.budget, pending.role, pending.audio.len())?;
            let role = pending.role;
            self.enqueue(Job::Segment(pending, reservation))?;
            if self.budget.samples.load(Ordering::Acquire) > LAG_SAMPLES && !self.lagging {
                respond(output, None, Reply::Lag { role })
                    .map_err(|_| SpeechError::InferenceFailed)?;
                self.lagging = true;
            }
        }
        if self.budget.samples.load(Ordering::Acquire) < LAG_SAMPLES / 2 {
            self.lagging = false;
        }
        if let Some(role) = finish {
            self.enqueue(Job::Finish(
                request.id,
                Reservation::new(&self.budget, role, 0)?,
            ))
        } else {
            respond(output, Some(request.id), reply).map_err(|_| SpeechError::InferenceFailed)
        }
    }
    fn close(&mut self) -> io::Result<()> {
        self.sender.take();
        if let Some(task) = self.task.take() {
            task.join()
                .map_err(|_| io::Error::other("inference thread failed"))??;
        }
        Ok(())
    }
}
impl Drop for Shared {
    fn drop(&mut self) {
        let _ = self.close();
    }
}

fn infer(
    jobs: Receiver<Job>,
    budget: &Budget,
    mut recognize: impl FnMut(
        PendingSegment,
    ) -> Result<meeting_native_backend::protocol::SegmentInfo, SpeechError>,
    mut emit: impl FnMut(Option<u64>, Reply) -> io::Result<()>,
) -> io::Result<()> {
    for job in jobs {
        if budget.failed.load(Ordering::Acquire) {
            return Err(io::Error::other("inference cancelled"));
        }
        match job {
            Job::Segment(pending, reservation) => {
                let role = pending.role;
                let segment = recognize(pending).map_err(io::Error::other)?;
                if budget.failed.load(Ordering::Acquire) {
                    return Err(io::Error::other("inference cancelled"));
                }
                emit(None, Reply::Segment { role, segment })?;
                drop(reservation);
            }
            Job::Finish(id, reservation) => {
                // All earlier segment events are flushed before this barrier.
                drop(reservation);
                emit(Some(id), Reply::Finished { segment: None })?;
            }
        }
    }
    Ok(())
}

pub(super) fn worker(
    jobs: Receiver<Request>,
    config: SessionConfig,
    output: Output,
    status: Status,
) -> io::Result<()> {
    let mut core: Option<Shared> = None;
    for request in jobs {
        if matches!(request.command, Command::Prepare {}) {
            let start = Instant::now();
            let already_loaded = core.is_some();
            if core.is_none() {
                *status.lock().unwrap() = ModelStatus::Loading;
                match Shared::prepare(config.clone(), output.clone(), status.clone()) {
                    Ok(loaded) => core = Some(loaded),
                    Err(code) => {
                        *status.lock().unwrap() = ModelStatus::Failed;
                        respond(&output, Some(request.id), Reply::Error { code })?;
                        continue;
                    }
                }
            }
            *status.lock().unwrap() = ModelStatus::Ready;
            respond(
                &output,
                Some(request.id),
                Reply::Prepared {
                    load_ms: if already_loaded {
                        0.0
                    } else {
                        start.elapsed().as_secs_f64() * 1000.0
                    },
                    execution_device: core.as_ref().and_then(|c| c.device),
                },
            )?;
        } else {
            let id = request.id;
            let result = core
                .as_mut()
                .ok_or(SpeechError::ModelNotPrepared)
                .and_then(|core| core.execute(request, &output));
            if let Err(code) = result {
                let retire = code.retires_session();
                respond(&output, Some(id), Reply::Error { code })?;
                if retire {
                    if let Some(core) = &core {
                        core.budget.failed.store(true, Ordering::Release);
                    }
                    *status.lock().unwrap() = ModelStatus::Failed;
                    // Parent retires the entire worker; never emit stale queued results.
                    return Err(io::Error::other("shared speech session failed"));
                }
            }
        }
    }
    if let Some(core) = &mut core {
        core.close()?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn segment(budget: &Arc<Budget>, role: Role, generation: u64) -> Job {
        let segment = meeting_audio_core::Segment {
            audio: vec![0.125; 480],
            voiced_frames: 1,
            segment_frames: 1,
            rms_dbfs: -18.0,
            accepted: true,
        };
        Job::Segment(
            PendingSegment::new(role, segment, 480, generation).unwrap(),
            Reservation::new(budget, role, 480).unwrap(),
        )
    }

    #[test]
    fn blocked_inference_accepts_both_roles_and_finishes_after_their_results() {
        let budget = Arc::new(Budget::default());
        let (sender, jobs) = mpsc::sync_channel(MAX_JOBS);
        let (entered, started) = mpsc::channel();
        let (release, resume) = mpsc::channel();
        let (emitted, replies) = mpsc::channel();
        let worker_budget = budget.clone();
        let task = thread::spawn(move || {
            let mut first = true;
            infer(
                jobs,
                &worker_budget,
                |pending| {
                    if first {
                        entered.send(()).unwrap();
                        resume.recv_timeout(Duration::from_secs(5)).unwrap();
                        first = false;
                    }
                    Ok(pending.info)
                },
                |id, reply| {
                    emitted.send((id, reply)).unwrap();
                    Ok(())
                },
            )
        });
        sender
            .try_send(segment(&budget, Role::User, 7))
            .ok()
            .unwrap();
        started.recv_timeout(Duration::from_secs(5)).unwrap();
        // The other input can enqueue a completed utterance without waiting for ASR.
        sender
            .try_send(segment(&budget, Role::Other, 9))
            .ok()
            .unwrap();
        for (id, role) in [(10, Role::User), (11, Role::Other)] {
            sender
                .try_send(Job::Finish(id, Reservation::new(&budget, role, 0).unwrap()))
                .ok()
                .unwrap();
        }
        assert!(replies.try_recv().is_err());
        release.send(()).unwrap();
        drop(sender);
        task.join().unwrap().unwrap();
        let replies: Vec<_> = replies.try_iter().collect();
        assert_eq!(replies.len(), 4);
        assert!(
            matches!(&replies[0], (None, Reply::Segment { role: Role::User, segment }) if segment.generation == 7)
        );
        assert!(
            matches!(&replies[1], (None, Reply::Segment { role: Role::Other, segment }) if segment.generation == 9)
        );
        assert!(matches!(
            &replies[2],
            (Some(10), Reply::Finished { segment: None })
        ));
        assert!(matches!(
            &replies[3],
            (Some(11), Reply::Finished { segment: None })
        ));
        assert_eq!(budget.samples.load(Ordering::Acquire), 0);
        assert!(
            budget
                .pending
                .iter()
                .all(|p| p.load(Ordering::Acquire) == 0)
        );
    }

    #[test]
    fn failed_inference_discards_queued_results_and_releases_the_budget() {
        let budget = Arc::new(Budget::default());
        let (sender, jobs) = mpsc::sync_channel(MAX_JOBS);
        sender
            .try_send(segment(&budget, Role::User, 1))
            .ok()
            .unwrap();
        sender
            .try_send(segment(&budget, Role::Other, 1))
            .ok()
            .unwrap();
        drop(sender);
        let result = infer(
            jobs,
            &budget,
            |_| Err(SpeechError::InferenceFailed),
            |_, _| panic!("failed results must not be emitted"),
        );
        assert!(result.is_err());
        assert_eq!(budget.samples.load(Ordering::Acquire), 0);
        assert!(
            budget
                .pending
                .iter()
                .all(|p| p.load(Ordering::Acquire) == 0)
        );
    }
    #[test]
    fn shared_budget_bounds_audio_and_releases_each_role() {
        let budget = Arc::new(Budget::default());
        let user = Reservation::new(&budget, Role::User, MAX_SAMPLES / 2).unwrap();
        let other = Reservation::new(&budget, Role::Other, MAX_SAMPLES / 2).unwrap();
        assert!(Reservation::new(&budget, Role::User, 1).is_err());
        assert_eq!(budget.pending[1].load(Ordering::Acquire), 1);
        drop(user);
        assert_eq!(budget.pending[0].load(Ordering::Acquire), 0);
        assert!(Reservation::new(&budget, Role::User, MAX_SAMPLES / 2).is_ok());
        drop(other);
        assert_eq!(budget.samples.load(Ordering::Acquire), 0);
    }
}
