//! Meeting lifecycle policy independent of Python, Tauri, devices, and database I/O.
//!
//! An executor performs one effect at a time and acknowledges its generation/step.
//! A failed or unknown write is never automatically retried.
use chrono::{SecondsFormat, Utc};
use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Idle,
    Starting,
    Active,
    Stopping,
    Faulted,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Effect {
    Prepare,
    CreateDraft,
    StartRecording,
    StartSpeech,
    CancelReplies,
    StopSpeech,
    CancelFinalReplies,
    FinalizeRecording,
    RemoveRecording,
    FlushHistory,
    CompleteDraft,
    AbortDraft,
    ReloadAudio,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    Ok,
    Failed,
    /// Remaining records are usable, but some input or writes were lost.
    Interrupted,
    /// The executor could not confirm that all producers have stopped.
    ResourcesActive,
    RecordingSaved,
    RecordingEmpty,
    RecordingDisabled,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Notice {
    PrepareFailed,
    DraftFailed,
    RecordingStartFailed,
    SpeechFailed,
    StopFailed,
    RecordingRemoved,
    RecordingIntegrityFailed,
    HistoryFlushFailed,
    SaveFailed,
    ReloadFailed,
}
#[derive(Debug, Clone, Serialize)]
pub struct Session {
    pub id: String,
    pub started_at: String,
    pub ended_at: Option<String>,
}
#[derive(Debug, Clone, Serialize)]
pub struct Snapshot {
    pub generation: u64,
    pub step: u64,
    pub phase: Phase,
    pub session: Option<Session>,
    pub effect: Option<Effect>,
    pub notices: Vec<Notice>,
}
#[derive(Debug, Error, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Error {
    #[error("meeting_busy")]
    Busy,
    #[error("stale_acknowledgement")]
    Stale,
    #[error("invalid_effect_outcome")]
    InvalidOutcome,
}
#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum Command {
    Start {},
    Stop {},
    Snapshot {},
    Acknowledge {
        generation: u64,
        step: u64,
        outcome: Outcome,
    },
}
#[derive(Clone, Copy)]
enum Finish {
    Complete,
    Abort,
    RetainDraft,
    Halt,
}
#[derive(Clone, Copy)]
enum Cleanup {
    Empty,
    Failed,
}

/// Constructed idle; only this owner can advance lifecycle state.
pub struct Coordinator {
    snapshot: Snapshot,
    finish: Finish,
    cleanup: Cleanup,
}
impl Default for Coordinator {
    fn default() -> Self {
        Self {
            snapshot: Snapshot {
                generation: 0,
                step: 0,
                phase: Phase::Idle,
                session: None,
                effect: None,
                notices: Vec::new(),
            },
            finish: Finish::Complete,
            cleanup: Cleanup::Empty,
        }
    }
}
fn now() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Micros, true)
}
impl Coordinator {
    pub fn execute(&mut self, command: Command) -> Result<Snapshot, Error> {
        match command {
            Command::Snapshot {} => {}
            Command::Start {} => {
                if self.snapshot.phase != Phase::Idle {
                    return Err(Error::Busy);
                }
                self.snapshot.generation += 1;
                self.snapshot.notices.clear();
                self.snapshot.session = Some(Session {
                    id: uuid::Uuid::new_v4().to_string(),
                    started_at: now(),
                    ended_at: None,
                });
                self.finish = Finish::Complete;
                self.snapshot.phase = Phase::Starting;
                self.next(Effect::Prepare);
            }
            Command::Stop {} => match self.snapshot.phase {
                Phase::Starting | Phase::Stopping | Phase::Faulted => return Err(Error::Busy),
                Phase::Idle => {}
                Phase::Active => {
                    self.finish = Finish::Complete;
                    self.snapshot.notices.clear();
                    self.snapshot.phase = Phase::Stopping;
                    self.next(Effect::CancelReplies);
                }
            },
            Command::Acknowledge {
                generation,
                step,
                outcome,
            } => {
                if generation != self.snapshot.generation || step != self.snapshot.step {
                    return Err(Error::Stale);
                }
                let effect = self.snapshot.effect.ok_or(Error::Stale)?;
                let valid = match effect {
                    Effect::FinalizeRecording => matches!(
                        outcome,
                        Outcome::Failed
                            | Outcome::Interrupted
                            | Outcome::RecordingSaved
                            | Outcome::RecordingEmpty
                            | Outcome::RecordingDisabled
                    ),
                    Effect::FlushHistory => matches!(
                        outcome,
                        Outcome::Ok
                            | Outcome::Failed
                            | Outcome::Interrupted
                            | Outcome::ResourcesActive
                    ),
                    _ => matches!(outcome, Outcome::Ok | Outcome::Failed),
                };
                if !valid {
                    return Err(Error::InvalidOutcome);
                }
                self.advance(effect, outcome);
            }
        }
        Ok(self.snapshot.clone())
    }
    fn next(&mut self, effect: Effect) {
        self.snapshot.step += 1;
        self.snapshot.effect = Some(effect);
    }
    fn notice(&mut self, notice: Notice) {
        self.snapshot.notices.push(notice);
    }
    fn idle(&mut self) {
        self.snapshot.phase = Phase::Idle;
        self.snapshot.effect = None;
    }
    fn advance(&mut self, effect: Effect, outcome: Outcome) {
        let ok = outcome == Outcome::Ok;
        match effect {
            Effect::Prepare => {
                if ok {
                    self.next(Effect::CreateDraft);
                } else {
                    self.notice(Notice::PrepareFailed);
                    self.idle();
                }
            }
            Effect::CreateDraft => {
                if ok {
                    self.next(Effect::StartRecording);
                } else {
                    self.notice(Notice::DraftFailed);
                    self.finish = Finish::Abort;
                    self.end_session();
                    self.next(Effect::AbortDraft);
                }
            }
            Effect::StartRecording => {
                if !ok {
                    self.notice(Notice::RecordingStartFailed);
                }
                self.next(Effect::StartSpeech);
            }
            Effect::StartSpeech => {
                if ok {
                    self.snapshot.phase = Phase::Active;
                    self.snapshot.effect = None;
                } else {
                    self.notice(Notice::SpeechFailed);
                    self.finish = Finish::Abort;
                    self.snapshot.phase = Phase::Stopping;
                    self.next(Effect::CancelReplies);
                }
            }
            Effect::CancelReplies | Effect::CancelFinalReplies => {
                if !ok {
                    self.notice(Notice::StopFailed);
                    self.finish = Finish::Halt;
                }
                self.next(if effect == Effect::CancelReplies {
                    Effect::StopSpeech
                } else {
                    Effect::FinalizeRecording
                });
            }
            Effect::StopSpeech => {
                if !ok {
                    self.notice(Notice::StopFailed);
                    self.finish = Finish::Halt;
                }
                self.next(Effect::CancelFinalReplies);
            }
            Effect::FinalizeRecording => match outcome {
                Outcome::Interrupted => {
                    if !matches!(self.finish, Finish::Halt) {
                        self.finish = Finish::Abort;
                    }
                    self.notice(Notice::RecordingIntegrityFailed);
                    self.next(Effect::FlushHistory);
                }
                Outcome::RecordingSaved | Outcome::RecordingDisabled => {
                    self.next(Effect::FlushHistory)
                }
                Outcome::RecordingEmpty => {
                    self.cleanup = Cleanup::Empty;
                    self.next(Effect::RemoveRecording);
                }
                _ => {
                    self.cleanup = Cleanup::Failed;
                    self.next(Effect::RemoveRecording);
                }
            },
            Effect::RemoveRecording => {
                if !ok {
                    if !matches!(self.finish, Finish::Halt) {
                        self.finish = Finish::RetainDraft;
                    }
                    self.notice(Notice::RecordingIntegrityFailed);
                } else if matches!(self.cleanup, Cleanup::Failed) {
                    self.notice(Notice::RecordingRemoved);
                }
                self.next(Effect::FlushHistory);
            }
            Effect::FlushHistory => {
                self.end_session();
                if outcome == Outcome::ResourcesActive {
                    self.finish = Finish::Halt;
                    self.notice(Notice::StopFailed);
                } else if outcome == Outcome::Interrupted {
                    if !matches!(self.finish, Finish::Halt) {
                        self.finish = Finish::Abort;
                    }
                } else if !ok {
                    if !matches!(self.finish, Finish::Halt) {
                        self.finish = Finish::RetainDraft;
                    }
                    self.notice(Notice::HistoryFlushFailed);
                }
                self.next(match self.finish {
                    Finish::Complete => Effect::CompleteDraft,
                    Finish::Abort => Effect::AbortDraft,
                    Finish::RetainDraft | Finish::Halt => Effect::ReloadAudio,
                });
            }
            Effect::CompleteDraft | Effect::AbortDraft => {
                if !ok {
                    self.notice(Notice::SaveFailed);
                }
                self.next(Effect::ReloadAudio);
            }
            Effect::ReloadAudio => {
                if !ok {
                    self.notice(Notice::ReloadFailed);
                }
                self.idle();
                if matches!(self.finish, Finish::Halt) {
                    self.snapshot.phase = Phase::Faulted;
                }
            }
        }
    }
    fn end_session(&mut self) {
        if let Some(session) = &mut self.snapshot.session {
            session.ended_at = Some(now());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn snapshot(owner: &mut Coordinator) -> Snapshot {
        owner.execute(Command::Snapshot {}).unwrap()
    }
    fn ack(owner: &mut Coordinator, outcome: Outcome) -> Snapshot {
        let state = snapshot(owner);
        owner
            .execute(Command::Acknowledge {
                generation: state.generation,
                step: state.step,
                outcome,
            })
            .unwrap()
    }
    fn start(owner: &mut Coordinator) -> Snapshot {
        owner.execute(Command::Start {}).unwrap();
        for effect in [
            Effect::Prepare,
            Effect::CreateDraft,
            Effect::StartRecording,
            Effect::StartSpeech,
        ] {
            assert_eq!(snapshot(owner).effect, Some(effect));
            ack(owner, Outcome::Ok);
        }
        snapshot(owner)
    }
    fn stop_to_recording(owner: &mut Coordinator) {
        owner.execute(Command::Stop {}).unwrap();
        for effect in [
            Effect::CancelReplies,
            Effect::StopSpeech,
            Effect::CancelFinalReplies,
        ] {
            assert_eq!(snapshot(owner).effect, Some(effect));
            ack(owner, Outcome::Ok);
        }
        assert_eq!(snapshot(owner).effect, Some(Effect::FinalizeRecording));
    }
    #[test]
    fn stop_drains_then_records_then_flushes_before_completing() {
        let mut owner = Coordinator::default();
        let active = start(&mut owner);
        assert_eq!(active.phase, Phase::Active);
        assert_eq!(owner.execute(Command::Start {}).unwrap_err(), Error::Busy);
        stop_to_recording(&mut owner);
        assert_eq!(owner.execute(Command::Start {}).unwrap_err(), Error::Busy);
        assert_eq!(
            ack(&mut owner, Outcome::RecordingSaved).effect,
            Some(Effect::FlushHistory)
        );
        let completing = ack(&mut owner, Outcome::Ok);
        assert_eq!(completing.effect, Some(Effect::CompleteDraft));
        assert!(completing.session.unwrap().ended_at.is_some());
        assert_eq!(
            ack(&mut owner, Outcome::Ok).effect,
            Some(Effect::ReloadAudio)
        );
        assert_eq!(ack(&mut owner, Outcome::Ok).phase, Phase::Idle);
        assert_eq!(owner.execute(Command::Stop {}).unwrap().effect, None);
        let next = start(&mut owner);
        assert!(next.generation > active.generation);
        assert_ne!(next.session.unwrap().id, active.session.unwrap().id);
    }
    #[test]
    fn stale_duplicate_and_invalid_outcomes_do_not_advance() {
        let mut owner = Coordinator::default();
        let pending = owner.execute(Command::Start {}).unwrap();
        assert_eq!(
            owner
                .execute(Command::Acknowledge {
                    generation: pending.generation,
                    step: pending.step,
                    outcome: Outcome::RecordingSaved,
                })
                .unwrap_err(),
            Error::InvalidOutcome
        );
        assert_eq!(snapshot(&mut owner).effect, Some(Effect::Prepare));
        ack(&mut owner, Outcome::Ok);
        for (generation, step) in [
            (pending.generation, pending.step),
            (pending.generation + 1, pending.step + 1),
        ] {
            assert_eq!(
                owner
                    .execute(Command::Acknowledge {
                        generation,
                        step,
                        outcome: Outcome::Ok,
                    })
                    .unwrap_err(),
                Error::Stale
            );
        }
        assert_eq!(snapshot(&mut owner).effect, Some(Effect::CreateDraft));
    }
    #[test]
    fn failed_start_drains_partial_speech_and_aborts_draft() {
        let mut owner = Coordinator::default();
        owner.execute(Command::Start {}).unwrap();
        ack(&mut owner, Outcome::Ok);
        ack(&mut owner, Outcome::Ok);
        ack(&mut owner, Outcome::Failed); // Recording is nonfatal.
        assert_eq!(snapshot(&mut owner).effect, Some(Effect::StartSpeech));
        assert_eq!(
            ack(&mut owner, Outcome::Failed).effect,
            Some(Effect::CancelReplies)
        );
        ack(&mut owner, Outcome::Ok);
        assert_eq!(snapshot(&mut owner).effect, Some(Effect::StopSpeech));
        ack(&mut owner, Outcome::Ok);
        ack(&mut owner, Outcome::Ok);
        ack(&mut owner, Outcome::RecordingDisabled);
        assert_eq!(
            ack(&mut owner, Outcome::Ok).effect,
            Some(Effect::AbortDraft)
        );
    }
    #[test]
    fn unresolved_recording_never_publishes_a_completed_meeting() {
        let mut owner = Coordinator::default();
        start(&mut owner);
        stop_to_recording(&mut owner);
        assert_eq!(
            ack(&mut owner, Outcome::Failed).effect,
            Some(Effect::RemoveRecording)
        );
        ack(&mut owner, Outcome::Failed);
        assert_eq!(
            ack(&mut owner, Outcome::Ok).effect,
            Some(Effect::ReloadAudio)
        );
        let done = ack(&mut owner, Outcome::Ok);
        assert!(done.notices.contains(&Notice::RecordingIntegrityFailed));
    }
    #[test]
    fn compensated_recording_can_complete_but_failed_history_cannot() {
        for flush in [Outcome::Ok, Outcome::Failed] {
            let mut owner = Coordinator::default();
            start(&mut owner);
            stop_to_recording(&mut owner);
            ack(&mut owner, Outcome::Failed);
            ack(&mut owner, Outcome::Ok);
            let state = ack(&mut owner, flush);
            assert!(state.notices.contains(&Notice::RecordingRemoved));
            assert_eq!(
                state.effect,
                Some(if flush == Outcome::Ok {
                    Effect::CompleteDraft
                } else {
                    Effect::ReloadAudio
                })
            );
        }
    }
    #[test]
    fn failed_save_is_not_retried() {
        let mut owner = Coordinator::default();
        start(&mut owner);
        stop_to_recording(&mut owner);
        ack(&mut owner, Outcome::RecordingDisabled);
        ack(&mut owner, Outcome::Ok);
        let state = ack(&mut owner, Outcome::Failed);
        assert_eq!(state.effect, Some(Effect::ReloadAudio));
        assert!(state.notices.contains(&Notice::SaveFailed));
    }
    #[test]
    fn failed_context_write_attempts_to_abort_a_persisted_draft() {
        let mut owner = Coordinator::default();
        owner.execute(Command::Start {}).unwrap();
        ack(&mut owner, Outcome::Ok);
        assert_eq!(
            ack(&mut owner, Outcome::Failed).effect,
            Some(Effect::AbortDraft)
        );
    }
    #[test]
    fn interrupted_content_with_released_resources_can_start_another_meeting() {
        let mut owner = Coordinator::default();
        start(&mut owner);
        stop_to_recording(&mut owner);
        ack(&mut owner, Outcome::RecordingSaved);
        assert_eq!(
            ack(&mut owner, Outcome::Interrupted).effect,
            Some(Effect::AbortDraft)
        );
        ack(&mut owner, Outcome::Ok);
        assert_eq!(ack(&mut owner, Outcome::Ok).phase, Phase::Idle);
        assert_eq!(start(&mut owner).phase, Phase::Active);
    }
    #[test]
    fn unconfirmed_resource_release_blocks_completion_and_new_meetings() {
        let mut owner = Coordinator::default();
        start(&mut owner);
        stop_to_recording(&mut owner);
        ack(&mut owner, Outcome::RecordingSaved);
        assert_eq!(
            ack(&mut owner, Outcome::ResourcesActive).effect,
            Some(Effect::ReloadAudio)
        );
        assert_eq!(ack(&mut owner, Outcome::Ok).phase, Phase::Faulted);
        assert_eq!(owner.execute(Command::Start {}).unwrap_err(), Error::Busy);
    }
    #[test]
    fn uncertain_stop_requires_restart_even_after_cleanup() {
        let mut owner = Coordinator::default();
        start(&mut owner);
        owner.execute(Command::Stop {}).unwrap();
        ack(&mut owner, Outcome::Ok);
        ack(&mut owner, Outcome::Failed); // Input may still be running.
        ack(&mut owner, Outcome::Ok);
        ack(&mut owner, Outcome::RecordingDisabled);
        assert_eq!(
            ack(&mut owner, Outcome::Failed).effect,
            Some(Effect::ReloadAudio)
        );
        assert_eq!(ack(&mut owner, Outcome::Ok).phase, Phase::Faulted);
        assert_eq!(owner.execute(Command::Start {}).unwrap_err(), Error::Busy);
    }
}
