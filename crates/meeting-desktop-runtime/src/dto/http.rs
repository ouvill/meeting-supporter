//! HTTP contracts, organized by API surface. Domain and persistence types are not exported.
mod common;
pub use common::*;
mod settings;
pub use settings::*;
mod history;
pub use history::*;
mod ai;
pub use ai::*;
mod speech;
pub use speech::*;
mod agents;
pub use agents::*;
