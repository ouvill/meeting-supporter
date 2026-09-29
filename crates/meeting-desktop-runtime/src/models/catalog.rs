use super::ModelError;
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Backend {
    Reazonspeech,
    Whisper,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Language {
    Ja,
    En,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "kebab-case")]
pub(crate) enum Whisper {
    Tiny,
    Base,
    Small,
    Medium,
    LargeV2,
    LargeV3Turbo,
}
impl Whisper {
    pub fn id(self) -> &'static str {
        match self {
            Self::Tiny => "tiny",
            Self::Base => "base",
            Self::Small => "small",
            Self::Medium => "medium",
            Self::LargeV2 => "large-v2",
            Self::LargeV3Turbo => "large-v3-turbo",
        }
    }
    pub fn repo(self) -> &'static str {
        match self {
            Self::Tiny => "Systran/faster-whisper-tiny",
            Self::Base => "Systran/faster-whisper-base",
            Self::Small => "Systran/faster-whisper-small",
            Self::Medium => "Systran/faster-whisper-medium",
            Self::LargeV2 => "Systran/faster-whisper-large-v2",
            Self::LargeV3Turbo => "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        }
    }
}
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Request {
    pub backend: Backend,
    pub language: Language,
    pub model: Option<Whisper>,
}
impl Request {
    pub fn key(&self) -> Result<Key, ModelError> {
        match self.backend {
            Backend::Reazonspeech if self.language == Language::Ja => Ok(Key::Reazon),
            Backend::Reazonspeech => Err(ModelError::Selection),
            Backend::Whisper => Ok(Key::Whisper(self.model.unwrap_or(Whisper::LargeV3Turbo))),
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) enum Key {
    Reazon,
    Whisper(Whisper),
}
impl Key {
    pub fn backend(self) -> Backend {
        match self {
            Self::Reazon => Backend::Reazonspeech,
            Self::Whisper(_) => Backend::Whisper,
        }
    }
    pub fn id(self) -> &'static str {
        match self {
            Self::Reazon => "reazonspeech-k2-v2-int8",
            Self::Whisper(m) => m.id(),
        }
    }
    pub fn repo(self) -> Option<&'static str> {
        match self {
            Self::Reazon => Some("reazon-research/reazonspeech-k2-v2"),
            Self::Whisper(m) => Some(m.repo()),
        }
    }
    pub fn revision(self) -> &'static str {
        if self == Self::Reazon {
            REAZON_REVISION
        } else {
            "main"
        }
    }
    pub fn accepts(self, name: &str) -> bool {
        match self {
            Self::Reazon => REAZON_FILES.iter().any(|(n, _)| *n == name),
            Self::Whisper(_) => matches!(
                name,
                "config.json"
                    | "preprocessor_config.json"
                    | "model.bin"
                    | "tokenizer.json"
                    | "vocabulary.txt"
                    | "vocabulary.json"
            ),
        }
    }
    pub fn required(self) -> &'static [&'static str] {
        match self {
            Self::Reazon => &[
                "tokens.txt",
                "encoder-epoch-99-avg-1.int8.onnx",
                "decoder-epoch-99-avg-1.int8.onnx",
                "joiner-epoch-99-avg-1.int8.onnx",
            ],
            Self::Whisper(_) => &["config.json", "model.bin", "tokenizer.json"],
        }
    }
}
pub(crate) const REAZON_REVISION: &str = "291488c8151be24d7da4bf7af26e533fad96e407";
pub(crate) const REAZON_FILES: &[(&str, &str)] = &[
    (
        "tokens.txt",
        "2c3ac659818a48a0c04010e0593bbc4d7c8a24a054340b01131499c05fd52def",
    ),
    (
        "encoder-epoch-99-avg-1.int8.onnx",
        "2c7bd08a8a99f9ddd0d9e458456577b1f6279214e51426f114f9eced44c54e1d",
    ),
    (
        "decoder-epoch-99-avg-1.int8.onnx",
        "8f0bff94d38797b03b762634ed03211a8e303d06cc4603cdd0cf4199d6eb1485",
    ),
    (
        "joiner-epoch-99-avg-1.int8.onnx",
        "49cc7ea1d3d35a40a27442db5e89996da64bf0e683a903dce76e99e57a12e4de",
    ),
];
