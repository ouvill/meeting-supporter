use super::catalog::{Whisper, REAZON_REVISION};
use super::*;
use axum::{
    body::Body,
    extract::State as HttpState,
    http::{StatusCode, Uri},
    response::Response,
    Router,
};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::sync::atomic::AtomicUsize;
use std::time::Duration;

fn manager(cache: PathBuf) -> Arc<Manager> {
    Arc::new(Manager {
        cache,
        local_reazon: None,
        legacy_reazon: None,
        offline: false,
        source: transfer::Source::default(),
        slots: Mutex::new(HashMap::new()),
        closed: AtomicBool::new(false),
    })
}
fn request() -> Request {
    Request {
        backend: Backend::Whisper,
        language: Language::Ja,
        model: Some(Whisper::Tiny),
    }
}
#[test]
fn cache_environment_precedence_and_home_expansion() {
    let keys = [
        "HF_HUB_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "HF_HOME",
        "XDG_CACHE_HOME",
    ];
    let expected = [
        "/0",
        "/1",
        "/2/hub",
        "/3/huggingface/hub",
        "/synthetic-user/.cache/huggingface/hub",
    ];
    for (skip, expected) in expected.iter().enumerate() {
        let resolved = cache_path(
            |key| {
                keys.iter()
                    .enumerate()
                    .skip(skip)
                    .find(|(_, k)| **k == key)
                    .map(|(i, _)| PathBuf::from(format!("/{i}")))
            },
            Some("/synthetic-user".into()),
        );
        assert_eq!(resolved, Some(PathBuf::from(expected)));
    }
    assert_eq!(
        cache_path(
            |k| (k == "HF_HOME").then(|| "~/hf".into()),
            Some("/synthetic-user".into())
        ),
        Some("/synthetic-user/hf/hub".into())
    );
    assert!(cache_path(|_| None, None).is_none());
}
#[test]
fn pinned_python_snapshot_is_reused_without_revision_ref() {
    let temp = tempfile::tempdir().unwrap();
    let manager = manager(temp.path().into());
    let snapshot = temp
        .path()
        .join("models--reazon-research--reazonspeech-k2-v2/snapshots")
        .join(REAZON_REVISION);
    std::fs::create_dir_all(&snapshot).unwrap();
    for name in Key::Reazon.required() {
        std::fs::write(snapshot.join(name), b"synthetic").unwrap();
    }
    assert_eq!(manager.reazon_path().unwrap(), snapshot);
    std::fs::remove_file(snapshot.join("tokens.txt")).unwrap();
    assert!(matches!(manager.reazon_path(), Err(ModelError::NotReady)));
}
#[test]
fn rejects_removed_backend_and_invalid_language() {
    assert!(serde_json::from_value::<Request>(json!({"backend":"vosk","language":"ja"})).is_err());
    let req = Request {
        backend: Backend::Reazonspeech,
        language: Language::En,
        model: None,
    };
    assert!(matches!(req.key(), Err(ModelError::Selection)));
}

struct Fixture {
    corrupt: bool,
    stall: bool,
    requests: AtomicUsize,
}
const COMMIT: &str = super::catalog::WHISPER_REVISION;
const DATA: &[u8] = b"synthetic model";
fn metadata() -> serde_json::Value {
    json!({"sha": COMMIT, "siblings": [
        {"rfilename":"ggml-tiny-q8_0.bin", "size":DATA.len(), "lfs":{"size":DATA.len(), "sha256":format!("{:x}",Sha256::digest(DATA))}}
    ]})
}
async fn respond(HttpState(fixture): HttpState<Arc<Fixture>>, uri: Uri) -> Response {
    fixture.requests.fetch_add(1, Ordering::SeqCst);
    if uri.path().starts_with("/api/") {
        return Response::new(Body::from(metadata().to_string()));
    }
    if fixture.stall {
        let stream = futures_util::stream::once(async {
            tokio::time::sleep(Duration::from_secs(60)).await;
            Ok::<_, std::io::Error>(DATA)
        });
        return Response::new(Body::from_stream(stream));
    }
    if !uri.path().contains(COMMIT) {
        return Response::builder()
            .status(StatusCode::NOT_FOUND)
            .body(Body::empty())
            .unwrap();
    }
    Response::new(Body::from(if fixture.corrupt {
        b"corrupt content".as_slice()
    } else {
        DATA
    }))
}
async fn server(corrupt: bool, stall: bool) -> (String, Arc<Fixture>, tokio::task::JoinHandle<()>) {
    let fixture = Arc::new(Fixture {
        corrupt,
        stall,
        requests: AtomicUsize::new(0),
    });
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = format!("http://{}", listener.local_addr().unwrap());
    let router = Router::new().fallback(respond).with_state(fixture.clone());
    let task = tokio::spawn(async move {
        axum::serve(listener, router).await.unwrap();
    });
    (address, fixture, task)
}
async fn finished(manager: &Manager, req: &Request) -> Status {
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            let status = manager.status(req).unwrap();
            if status.state != State::Downloading {
                return status;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap()
}
#[tokio::test]
async fn download_verifies_shared_blobs_and_reuses_cache_offline() {
    let temp = tempfile::tempdir().unwrap();
    let (endpoint, fixture, server) = server(false, false).await;
    let mut manager = manager(temp.path().into());
    Arc::get_mut(&mut manager).unwrap().source.endpoint = endpoint;
    let req = request();
    assert!(manager.status(&req).unwrap().state == State::Missing);
    assert_eq!(fixture.requests.load(Ordering::SeqCst), 0);
    manager.start(&req).unwrap();
    let mut english = req.clone();
    english.language = Language::En;
    manager.start(&english).unwrap();
    let status = finished(&manager, &req).await;
    assert!(status.state == State::Ready, "{}", status.message);
    let snapshot = status.model_path.unwrap();
    for name in req.key().unwrap().required() {
        assert_eq!(std::fs::read(snapshot.join(name)).unwrap(), DATA);
    }
    assert_eq!(fixture.requests.load(Ordering::SeqCst), 2); // metadata + selected Q8 model
    assert!(manager.status(&english).unwrap().language == Language::En);
    manager.shutdown().await;
    server.abort();
    let cached = super::tests::manager(temp.path().into());
    assert!(cached.status(&req).unwrap().state == State::Ready);
    std::fs::remove_file(snapshot.join("ggml-tiny-q8_0.bin")).unwrap();
    assert!(cached.status(&req).unwrap().state == State::Missing);
}
#[tokio::test]
async fn corrupt_download_is_not_published_and_retry_succeeds() {
    let temp = tempfile::tempdir().unwrap();
    let (endpoint, _, server) = server(true, false).await;
    let mut manager = manager(temp.path().into());
    Arc::get_mut(&mut manager).unwrap().source.endpoint = endpoint;
    manager.start(&request()).unwrap();
    let status = finished(&manager, &request()).await;
    assert!(status.state == State::Failed);
    assert_eq!(status.error_code, Some(ErrorCode::Checksum));
    assert!(hub::cached(temp.path(), request().key().unwrap()).is_none());
    manager.shutdown().await;
    server.abort();
    let (endpoint, _, server) = self::server(false, false).await;
    let mut retry = self::manager(temp.path().into());
    Arc::get_mut(&mut retry).unwrap().source.endpoint = endpoint;
    retry.start(&request()).unwrap();
    assert!(finished(&retry, &request()).await.state == State::Ready);
    retry.shutdown().await;
    server.abort();
}
#[tokio::test]
async fn shutdown_cancels_stream_and_removes_owned_temporary_files() {
    let temp = tempfile::tempdir().unwrap();
    let (endpoint, fixture, server) = server(false, true).await;
    let mut manager = manager(temp.path().into());
    Arc::get_mut(&mut manager).unwrap().source.endpoint = endpoint;
    manager.start(&request()).unwrap();
    tokio::time::timeout(Duration::from_secs(5), async {
        while fixture.requests.load(Ordering::SeqCst) < 2 {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    manager.cancel(&request()).unwrap();
    tokio::time::timeout(Duration::from_secs(2), manager.shutdown())
        .await
        .unwrap();
    assert!(manager.status(&request()).unwrap().state == State::Cancelled);
    let blobs = temp.path().join("models--ggerganov--whisper.cpp/blobs");
    assert_eq!(std::fs::read_dir(blobs).unwrap().count(), 0);
    assert!(matches!(
        manager.start(&request()),
        Err(ModelError::Cancelled)
    ));
    server.abort();
}
#[tokio::test]
async fn shared_lock_wait_is_cancellable_and_never_unlinks_lock() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("blob.lock");
    let (tx, rx) = watch::channel(false);
    let first = transfer::lock(&path, rx.clone()).await.unwrap();
    tx.send(true).unwrap();
    assert!(matches!(
        transfer::lock(&path, rx).await,
        Err(ModelError::Cancelled)
    ));
    drop(first);
    assert!(path.is_file());
    let (_tx, rx) = watch::channel(false);
    transfer::lock(&path, rx).await.unwrap();
}

#[tokio::test]
async fn offline_failure_and_invalid_override_remain_visible_on_polling() {
    let temp = tempfile::tempdir().unwrap();
    let mut manager = manager(temp.path().join("hub"));
    Arc::get_mut(&mut manager).unwrap().offline = true;
    assert!(manager.start(&request()).unwrap().state == State::Failed);
    assert!(manager.status(&request()).unwrap().state == State::Failed);
    let inner = Arc::get_mut(&mut manager).unwrap();
    inner.local_reazon = Some(temp.path().join("explicit-missing"));
    inner.legacy_reazon = Some(temp.path().join("legacy"));
    std::fs::create_dir_all(inner.legacy_reazon.as_ref().unwrap()).unwrap();
    for file in Key::Reazon.required() {
        std::fs::write(
            inner.legacy_reazon.as_ref().unwrap().join(file),
            "synthetic",
        )
        .unwrap();
    }
    let req = Request {
        backend: Backend::Reazonspeech,
        language: Language::Ja,
        model: None,
    };
    assert!(manager.start(&req).unwrap().state == State::Failed);
    assert!(!manager.status(&req).unwrap().retryable);
    assert!(manager.reazon_path().is_err());
    assert!(!temp.path().join("explicit-missing").exists());
    manager.shutdown().await;
}
