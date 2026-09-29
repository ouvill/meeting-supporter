use crate::{error::SpeechError, protocol::Punctuation};
use ort::{session::Session, value::TensorRef};
use serde::Deserialize;
use std::{collections::HashMap, path::Path, time::Instant};

const CONTEXT: usize = 64;
const TARGET: usize = 128; // Up to 256 characters including context, plus CLS/SEP.

#[derive(Deserialize)]
#[serde(tag = "format", rename_all = "snake_case", deny_unknown_fields)]
enum ModelContract {
    BertJapanesePunctuationV1 {},
}

#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
enum Mark {
    #[default]
    None,
    Comma,
    Period,
}

impl Mark {
    fn from_logits(comma: f32, period: f32) -> Self {
        // Author's independent sigmoid thresholds: 0.1, period takes priority.
        let threshold = (0.1_f32 / 0.9).ln();
        if period > threshold {
            Self::Period
        } else if comma > threshold {
            Self::Comma
        } else {
            Self::None
        }
    }

    fn as_str(self) -> &'static str {
        match self {
            Self::None => "",
            Self::Comma => "、",
            Self::Period => "。",
        }
    }
}

pub(crate) struct Punctuator {
    session: Session,
    vocabulary: HashMap<String, i64>,
    cls: i64,
    sep: i64,
    unknown: i64,
}

impl Punctuator {
    // VAD initializes the shared ORT runtime before this model is loaded.
    pub(crate) fn load(directory: &Path) -> Result<Self, SpeechError> {
        let contract = std::fs::read(directory.join("punctuation.json"))
            .map_err(|_| SpeechError::PunctuationLoadFailed)?;
        let _: ModelContract =
            serde_json::from_slice(&contract).map_err(|_| SpeechError::PunctuationLoadFailed)?;
        let vocabulary: HashMap<_, _> = std::fs::read_to_string(directory.join("vocab.txt"))
            .map_err(|_| SpeechError::PunctuationLoadFailed)?
            .lines()
            .enumerate()
            .map(|(index, word)| (word.to_owned(), index as i64))
            .collect();
        let special = |token: &str| {
            vocabulary
                .get(token)
                .copied()
                .ok_or(SpeechError::PunctuationLoadFailed)
        };
        let (cls, sep, unknown) = (special("[CLS]")?, special("[SEP]")?, special("[UNK]")?);
        if vocabulary.len() != 7027 || (cls, sep, unknown) != (2, 3, 1) {
            return Err(SpeechError::PunctuationLoadFailed);
        }
        let session = Session::builder()
            .and_then(|b| Ok(b.with_intra_threads(1)?))
            .and_then(|b| Ok(b.with_inter_threads(1)?))
            .and_then(|mut b| b.commit_from_file(directory.join("punctuation.onnx")))
            .map_err(|_| SpeechError::PunctuationLoadFailed)?;
        let mut model = Self {
            session,
            vocabulary,
            cls,
            sep,
            unknown,
        };
        // Validate the actual tensor contract before accepting the selected model.
        model
            .restore("確認")
            .map_err(|_| SpeechError::PunctuationLoadFailed)?;
        Ok(model)
    }

    pub(crate) fn process(&mut self, text: &str) -> Punctuation {
        let start = Instant::now();
        match self.restore(text) {
            Ok(text) => Punctuation::Applied {
                text,
                processing_ms: start.elapsed().as_secs_f64() * 1000.0,
            },
            Err(code) => Punctuation::Failed { code },
        }
    }

    fn restore(&mut self, text: &str) -> Result<String, SpeechError> {
        let chars: Vec<char> = text.chars().collect();
        let mut result = String::with_capacity(text.len());
        // Keep original offsets. Spaces and existing punctuation are not model tokens.
        let tokens: Vec<(usize, char)> = chars
            .iter()
            .copied()
            .enumerate()
            .filter(|(_, c)| !c.is_whitespace() && !c.is_control() && !is_punctuation(*c))
            .collect();
        let mut marks = vec![Mark::None; chars.len()];
        // Context is local to this utterance; sources and generations cannot leak.
        for start in (0..tokens.len()).step_by(TARGET) {
            let end = (start + TARGET).min(tokens.len());
            let left = start.saturating_sub(CONTEXT);
            let right = (end + CONTEXT).min(tokens.len());
            let mut ids = Vec::with_capacity(right - left + 2);
            ids.push(self.cls);
            let mut buffer = [0; 4];
            ids.extend(tokens[left..right].iter().map(|(_, c)| {
                self.vocabulary
                    .get(c.to_ascii_lowercase().encode_utf8(&mut buffer))
                    .copied()
                    .unwrap_or(self.unknown)
            }));
            ids.push(self.sep);
            let mask = vec![1_i64; ids.len()];
            let input_ids = TensorRef::from_array_view(([1, ids.len()], ids.as_slice()))
                .map_err(|_| SpeechError::InvalidTensor)?;
            let attention = TensorRef::from_array_view(([1, ids.len()], mask.as_slice()))
                .map_err(|_| SpeechError::InvalidTensor)?;
            let outputs = self
                .session
                .run(ort::inputs!["input_ids" => input_ids, "attention_mask" => attention])
                .map_err(|_| SpeechError::InferenceFailed)?;
            let (shape, logits) = outputs
                .get("logits")
                .ok_or(SpeechError::InvalidModelOutput)?
                .try_extract_tensor::<f32>()
                .map_err(|_| SpeechError::InvalidModelOutput)?;
            if **shape != [1, ids.len() as i64, 2] || !logits.iter().all(|x| x.is_finite()) {
                return Err(SpeechError::InvalidModelOutput);
            }
            for (index, &(original, _)) in tokens.iter().enumerate().take(end).skip(start) {
                let offset = (index - left + 1) * 2;
                marks[original] = Mark::from_logits(logits[offset], logits[offset + 1]);
            }
        }
        for (index, &current) in chars.iter().enumerate() {
            append(
                &mut result,
                current,
                chars.get(index + 1).copied(),
                marks[index],
            );
        }
        Ok(result)
    }
}

fn is_punctuation(c: char) -> bool {
    matches!(
        c,
        '、' | '。' | '？' | '！' | ',' | '.' | '?' | '!' | '，' | '．'
    )
}

fn append(output: &mut String, current: char, next: Option<char>, mark: Mark) {
    output.push(current);
    if !current.is_whitespace() && !is_punctuation(current) && !next.is_some_and(is_punctuation) {
        output.push_str(mark.as_str());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn punctuation_and_unknown_characters_are_preserved() {
        let mut output = String::new();
        append(&mut output, '🦀', Some('。'), Mark::Period);
        append(&mut output, '。', None, Mark::Period);
        assert_eq!(output, "🦀。");
        let mut output = String::new();
        append(&mut output, ' ', None, Mark::Period);
        assert_eq!(output, " ");
    }

    #[test]
    fn independent_labels_use_period_priority() {
        assert_eq!(Mark::from_logits(-3.0, -3.0), Mark::None);
        assert_eq!(Mark::from_logits(0.0, -3.0), Mark::Comma);
        assert_eq!(Mark::from_logits(0.0, 0.0), Mark::Period);
    }

    #[test]
    fn incompatible_model_contract_is_rejected() {
        assert!(serde_json::from_str::<ModelContract>(r#"{"format":"unknown"}"#).is_err());
        assert!(
            serde_json::from_str::<ModelContract>(
                r#"{"format":"bert_japanese_punctuation_v1","extra":1}"#
            )
            .is_err()
        );
    }

    #[test]
    #[ignore = "requires pinned punctuation model and ORT runtime"]
    fn synthetic_real_model() {
        let runtime = std::env::var("MEETING_TEST_ORT_LIBRARY").unwrap();
        ort::init_from(runtime).unwrap().commit();
        let start = Instant::now();
        let mut model = Punctuator::load(Path::new(
            &std::env::var("MEETING_TEST_PUNCTUATION_MODEL").unwrap(),
        ))
        .unwrap();
        eprintln!(
            "punctuation load: {:.2} ms",
            start.elapsed().as_secs_f64() * 1000.0
        );
        let example = "明日の会議は何時からですか資料は私が用意しておくので先に始めていてください";
        for text in [
            example.to_owned(),
            String::new(),
            "🦀𠮷野家です。確認します？".into(),
            example.repeat(20),
        ] {
            let start = Instant::now();
            let output = model.restore(&text).unwrap();
            // Every original scalar remains, in order; only comma and period can be inserted.
            let mut actual = output.chars().peekable();
            for expected in text.chars() {
                while actual
                    .peek()
                    .is_some_and(|&c| c != expected && matches!(c, '、' | '。'))
                {
                    actual.next();
                }
                assert_eq!(actual.next(), Some(expected));
            }
            assert!(actual.all(|c| matches!(c, '、' | '。')));
            eprintln!(
                "{} chars: {:.2} ms",
                text.chars().count(),
                start.elapsed().as_secs_f64() * 1000.0
            );
            if text == example {
                eprintln!("{output}");
                assert_eq!(
                    output,
                    "明日の会議は何時からですか。資料は私が用意しておくので先に始めていてください。"
                );
            }
        }
        assert_eq!(
            model
                .restore("これは音声認識の動作確認です明日の会議は午前十時に始まります")
                .unwrap(),
            "これは音声認識の動作確認です。明日の会議は午前十時に始まります。"
        );
        assert_eq!(
            model.restore("今日は晴れです明日は雨です").unwrap(),
            "今日は晴れです。明日は雨です。"
        );
        assert_eq!(
            model.restore("今日は晴れです。明日は雨です。").unwrap(),
            "今日は晴れです。明日は雨です。"
        );
        assert_eq!(
            model
                .restore("私は資料を作りますので田中さんは会場を予約してください")
                .unwrap(),
            "私は資料を作りますので、田中さんは会場を予約してください。"
        );
        assert!(matches!(model.process(""), Punctuation::Applied { text, .. } if text.is_empty()));
    }
}
