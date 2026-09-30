//! Native audio backend. Model preparation is separate from control readiness.
pub mod error;
pub mod microphone;
pub mod protocol;
mod reazon;
pub mod session;
mod vad;
pub mod wav;

mod punctuation;

pub mod whisper;
