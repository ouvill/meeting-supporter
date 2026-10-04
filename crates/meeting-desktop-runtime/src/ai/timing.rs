//! Duration-only diagnostics. Never retain or emit prompts, model names or reply text.
use crate::{
    runtime::Shared,
    wire::{Event, ReplyMeta, ReplyOutcome},
};
use std::{sync::Arc, time::Instant};

pub(super) struct Timing {
    shared: Arc<Shared>,
    generation_id: String,
    suggestion_id: String,
    started: Instant,
    preparation_ms: Option<u64>,
    first_text_ms: Option<u64>,
    first_sentence_ms: Option<u64>,
    outcome: ReplyOutcome,
}

impl Timing {
    pub fn new(shared: Arc<Shared>, meta: &ReplyMeta, started: Instant) -> Self {
        Self {
            shared,
            generation_id: meta.generation_id.clone(),
            suggestion_id: meta.suggestion_id.clone(),
            started,
            preparation_ms: None,
            first_text_ms: None,
            first_sentence_ms: None,
            outcome: ReplyOutcome::Cancelled,
        }
    }
    fn elapsed(&self) -> u64 {
        self.started.elapsed().as_millis().min(u64::MAX as u128) as u64
    }
    pub fn dispatched(&mut self) {
        self.preparation_ms = Some(self.elapsed());
    }
    pub fn text(&mut self, text: &str) {
        if text.trim().is_empty() {
            return;
        }
        let elapsed = self.elapsed();
        self.first_text_ms.get_or_insert(elapsed);
        // Japanese sentence endings are a measurable proxy, not a quality judgment.
        if text.contains(['。', '！', '？', '!', '?']) {
            self.first_sentence_ms.get_or_insert(elapsed);
        }
    }
    pub fn generated(&mut self) {
        if self.first_text_ms.is_some() && self.first_sentence_ms.is_none() {
            self.first_sentence_ms = Some(self.elapsed());
        }
    }
    pub fn finish(&mut self, outcome: ReplyOutcome) {
        self.outcome = outcome;
    }
}

impl Drop for Timing {
    fn drop(&mut self) {
        self.shared.emit(Event::ReplyTiming {
            generation_id: self.generation_id.clone(),
            suggestion_id: self.suggestion_id.clone(),
            preparation_ms: self.preparation_ms,
            first_text_ms: self.first_text_ms,
            first_sentence_ms: self.first_sentence_ms,
            total_ms: self.elapsed(),
            outcome: self.outcome,
        });
    }
}
