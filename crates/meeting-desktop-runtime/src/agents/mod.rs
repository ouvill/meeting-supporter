pub(crate) mod connection;
mod npm;
pub(crate) mod registry;
mod updates;
use registry::{Entry, Installed, Launch};
use serde_json::{json, Value};
use std::{collections::BTreeMap, path::PathBuf, sync::Arc};
use tokio::sync::Mutex;

#[derive(Debug, Clone, thiserror::Error)]
pub enum AgentError {
    #[error("エージェント一覧を取得できませんでした。再度お試しください。")]
    Registry,
    #[error("このエージェントの配布形式またはOSにはまだ対応していません。")]
    Platform,
    #[error("エージェントをダウンロードできませんでした。")]
    Download,
    #[error("配布物の検証に失敗しました。再度導入してください。")]
    Integrity,
    #[error("エージェントを導入できませんでした。")]
    Install,
    #[error("npmによる導入がタイムアウトしました。通信状況を確認して再試行してください。")]
    InstallTimeout,
    #[error("条件に合うバージョンを取得できませんでした。npmに公開から{days}日待つ設定があります。時間をおいて再試行してください。")]
    NpmReleaseAge { days: f64 },
    #[error("条件に合うバージョンを取得できませんでした。npmのbefore設定で公開日が制限されています。設定を確認してください。")]
    NpmBefore,
    #[error("npmから条件に合うバージョンを取得できませんでした。エージェント一覧を更新して再試行してください。")]
    NpmVersion,
    #[error("npmの保存先に書き込み権限がありません。アプリの保存先とnpmキャッシュの権限を確認してください。")]
    NpmPermission,
    #[error("npmの配布元との通信に失敗しました。ネットワーク・プロキシ・証明書の設定を確認してください。")]
    NpmNetwork,
    #[error(
        "エージェントが要求するNode.jsのバージョンを満たしていません。Node.jsを更新してください。"
    )]
    NpmEngine,
    #[error("エージェントを導入する空き容量が不足しています。")]
    NpmSpace,
    #[error(
        "npmの配布元への認証またはアクセスが拒否されました。npmの接続設定を確認してください。"
    )]
    NpmRegistryAuth,
    #[error("Node.jsとnpmをインストールしてから再度お試しください。")]
    Node,
    #[error("エージェントの導入状態を保存できませんでした。")]
    Storage,
    #[error("先にエージェントを追加してください。")]
    NotInstalled,
    #[error("エージェントに接続できませんでした。導入状態と認証を確認してください。")]
    Connect,
    #[error("このエージェントのACPバージョンには対応していません。")]
    Protocol,
    #[error("このエージェントはモデル変更に対応していません。")]
    ModelUnsupported,
    #[error("選択したモデルをこのエージェントで利用できません。接続を確認してください。")]
    ModelUnavailable,
    #[error("このエージェント・モデルは推論量の変更に対応していません。")]
    ThoughtLevelUnsupported,
    #[error("選択した推論量を利用できません。モデルと選択肢を確認してください。")]
    ThoughtLevelUnavailable,
    #[error("エージェントへのログインが必要です。")]
    Auth,
    #[error("エージェントの処理中です。完了を待ってください。")]
    Busy,
    #[error("エージェントの応答待ちがタイムアウトしました。")]
    Timeout,
    #[error("生成を中断しました。")]
    Cancelled,
    #[error("エージェントが返答を完了しませんでした。")]
    Incomplete,
    #[error("この返答には外部操作や確認が必要なため、生成を停止しました。")]
    Tools,
}

pub(crate) struct Manager {
    root: PathBuf,
    entries: Mutex<Vec<Entry>>,
    installed: Mutex<BTreeMap<String, Installed>>,
    pub maintenance: Mutex<()>,
    pub checking: Mutex<()>,
    pub cancel_check: tokio::sync::watch::Sender<u64>,
    updates: Mutex<updates::Cache>,
    pub pool: Arc<connection::Pool>,
}
impl Manager {
    pub fn open(data_dir: &std::path::Path) -> Result<Arc<Self>, AgentError> {
        let root = data_dir.join("agents");
        let updates = updates::Cache::load(&root);
        Ok(Arc::new(Self {
            installed: Mutex::new(registry::load(&root)?),
            root,
            entries: Mutex::new(updates.entries.clone()),
            updates: Mutex::new(updates),
            maintenance: Mutex::new(()),
            checking: Mutex::new(()),
            cancel_check: tokio::sync::watch::channel(0).0,
            pool: Arc::default(),
        }))
    }
    pub async fn catalog(&self, refresh: bool) -> Result<Value, AgentError> {
        if refresh {
            self.refresh().await?;
        }
        let installed = self.installed.lock().await.clone();
        let mut entries: BTreeMap<String, Entry> = self
            .entries
            .lock()
            .await
            .iter()
            .map(|e| (e.id.clone(), e.clone()))
            .collect();
        for (id, record) in &installed {
            entries
                .entry(id.clone())
                .or_insert_with(|| record.entry.clone());
        }
        let cache = self.updates.lock().await;
        let mut result = vec![];
        for (id, entry) in entries {
            let record = installed.get(&id);
            let status = self.pool.status(&id).await;
            let update = cache.offers.get(&id).filter(|offer| {
                record.is_some_and(|old| {
                    old.version() == offer.installed_version
                        && updates::newer(&offer.version, old.version())
                })
            });
            result.push(json!({"id":id,"name":entry.name,"description":entry.description,"authors":entry.authors,"version":entry.version,"installed_version":record.map(Installed::version),"update_version":update.map(|offer| &offer.version),"supported":entry.supported(),"distribution":entry.distribution_kind(),"status":status}));
        }
        result.sort_by_key(|e| {
            (
                !matches!(
                    e["id"].as_str(),
                    Some("codex-acp" | "claude-acp" | "antigravity-acp")
                ),
                e["name"].as_str().unwrap_or("").to_lowercase(),
            )
        });
        let count = result
            .iter()
            .filter(|agent| !agent["update_version"].is_null())
            .count();
        Ok(
            json!({"supported":true,"agents":result,"update_count":count,"checked_at":cache.checked_at,"update_message":cache.message}),
        )
    }
    pub async fn install(&self, id: &str) -> Result<bool, AgentError> {
        let entry = self
            .entries
            .lock()
            .await
            .iter()
            .find(|e| e.id == id)
            .cloned();
        let entry = match entry {
            Some(entry) => entry,
            // 再起動後も、保存した Registry 情報で導入版の更新を確認できる。
            None => self
                .installed
                .lock()
                .await
                .get(id)
                .map(|record| record.entry.clone())
                .ok_or(AgentError::Registry)?,
        };
        let installed = registry::install(&self.root, entry).await?;
        self.accept_installation(installed).await
    }
    async fn accept_installation(&self, installed: Installed) -> Result<bool, AgentError> {
        self.accept_installation_with(installed, |launch, cwd| async move {
            let probe = connection::Pool::default();
            let status = probe.connect(launch, cwd, None).await?;
            if !status.ready {
                return Err(AgentError::Auth);
            }
            Ok(probe)
        })
        .await
    }
    async fn accept_installation_with<F>(
        &self,
        mut installed: Installed,
        verify: impl FnOnce(Launch, PathBuf) -> F,
    ) -> Result<bool, AgentError>
    where
        F: std::future::Future<Output = Result<connection::Pool, AgentError>>,
    {
        let id = installed.entry.id.clone();
        let mut candidate = CandidateDirectory {
            path: self.root.join(&installed.directory),
            keep: false,
        };
        let old = self.installed.lock().await.get(&id).cloned();
        installed.model = old.as_ref().and_then(|record| record.model.clone());
        installed.thought_level = old.as_ref().and_then(|record| record.thought_level.clone());
        if old
            .as_ref()
            .is_some_and(|old| !updates::newer(installed.version(), old.version()))
        {
            self.clear_update_offer(&id).await;
            return Ok(false);
        }
        // 旧版の接続を維持したまま、新版だけを別の接続で検証する。
        let probe = if old.is_some() {
            Some(verify(installed.launch(&self.root)?, self.cwd().await?).await?)
        } else {
            None
        };
        let mut records = self.installed.lock().await;
        let mut proposed = records.clone();
        proposed.insert(id.clone(), installed);
        registry::save(&self.root, &proposed)?;
        candidate.keep = true;
        *records = proposed;
        drop(records);
        if let Some(probe) = probe {
            self.pool.adopt(&id, probe).await;
        }
        self.clear_update_offer(&id).await;
        if let Some(old) = old {
            let _ = tokio::fs::remove_dir_all(self.root.join(old.directory)).await;
        }
        Ok(true)
    }
    async fn clear_update_offer(&self, id: &str) {
        let mut cache = self.updates.lock().await;
        cache.offers.remove(id);
        // 導入情報は確定済み。通知キャッシュの保存失敗で導入結果を取り消さない。
        let _ = cache.save(&self.root);
    }
    pub async fn update_all(&self) -> Result<Value, AgentError> {
        self.update_all_with(|id| async move { self.install(&id).await })
            .await
    }
    async fn update_all_with<F>(&self, install: impl Fn(String) -> F) -> Result<Value, AgentError>
    where
        F: std::future::Future<Output = Result<bool, AgentError>>,
    {
        let catalog = self.catalog(false).await?;
        let mut results = vec![];
        if let Some(agents) = catalog["agents"].as_array() {
            for agent in agents
                .iter()
                .filter(|agent| !agent["update_version"].is_null())
            {
                let Some(id) = agent["id"].as_str() else {
                    continue;
                };
                let result = install(id.to_owned()).await;
                results.push(json!({"id":id,"name":agent["name"],"updated":matches!(result, Ok(true)),"error":result.err().map(|error| error.to_string())}));
            }
        }
        Ok(json!({"results":results}))
    }
    pub async fn remove(&self, id: &str) -> Result<(), AgentError> {
        self.pool.disconnect(id).await?;
        let mut records = self.installed.lock().await;
        let mut proposed = records.clone();
        let removed = proposed.remove(id).ok_or(AgentError::NotInstalled)?;
        registry::save(&self.root, &proposed)?;
        *records = proposed;
        let _ = tokio::fs::remove_dir_all(self.root.join(removed.directory)).await;
        Ok(())
    }
    pub async fn launch(&self, id: &str) -> Result<Launch, AgentError> {
        self.installed
            .lock()
            .await
            .get(id)
            .ok_or(AgentError::NotInstalled)?
            .launch(&self.root)
    }
    pub async fn connect(
        &self,
        id: &str,
        method: Option<String>,
    ) -> Result<connection::Status, AgentError> {
        let launch = self.launch(id).await?;
        self.pool.connect(launch, self.cwd().await?, method).await
    }
    pub async fn select_model(
        &self,
        id: &str,
        model: String,
    ) -> Result<connection::Status, AgentError> {
        if model.is_empty() || model.len() > 256 {
            return Err(AgentError::ModelUnavailable);
        }
        let launch = self.launch(id).await?;
        let status = self
            .pool
            .select_model(launch, self.cwd().await?, &model)
            .await?;
        let mut records = self.installed.lock().await;
        let mut proposed = records.clone();
        let record = proposed.get_mut(id).ok_or(AgentError::NotInstalled)?;
        record.model = Some(model);
        // A different model can remove a previously selected reasoning level.
        if record.thought_level.as_ref().is_some_and(|selected| {
            status
                .thought_level
                .as_ref()
                .is_none_or(|selector| &selector.current != selected)
        }) {
            record.thought_level = None;
        }
        registry::save(&self.root, &proposed)?;
        *records = proposed;
        Ok(status)
    }
    pub async fn select_thought_level(
        &self,
        id: &str,
        thought_level: String,
    ) -> Result<connection::Status, AgentError> {
        if thought_level.is_empty() || thought_level.len() > 256 {
            return Err(AgentError::ThoughtLevelUnavailable);
        }
        let launch = self.launch(id).await?;
        let status = self
            .pool
            .select_thought_level(launch, self.cwd().await?, &thought_level)
            .await?;
        let mut records = self.installed.lock().await;
        let mut proposed = records.clone();
        proposed
            .get_mut(id)
            .ok_or(AgentError::NotInstalled)?
            .thought_level = Some(thought_level);
        registry::save(&self.root, &proposed)?;
        *records = proposed;
        Ok(status)
    }
    pub async fn cwd(&self) -> Result<PathBuf, AgentError> {
        let path = self.root.join("workspace");
        tokio::fs::create_dir_all(&path)
            .await
            .map_err(|_| AgentError::Storage)?;
        Ok(path)
    }
    pub async fn routes(&self, assigned: Option<&str>) -> Vec<Value> {
        let mut rows = vec![];
        for (id, record) in self.installed.lock().await.iter() {
            let status = self.pool.status(id).await;
            let route_id = format!("acp:{id}");
            rows.push(json!({"id":route_id,"kind":"subscription_app","label":record.entry.name,"description":"追加したエージェントで返答案を生成します。","availability":"experimental","readiness":if status.ready{"ready"}else{"setup_required"},"selectable":status.ready,"selected":assigned==Some(route_id.as_str()),"data_location":"unknown","billing_owner":"user","capabilities":["reply","stream","cancel"],"reason_code":if status.ready{None}else{Some("ACP_CONNECT_REQUIRED")},"message":if status.message.is_empty(){"設定のエージェント一覧から接続してください。"}else{&status.message},"action":"none"}));
        }
        rows
    }
}

struct CandidateDirectory {
    path: PathBuf,
    keep: bool,
}
impl Drop for CandidateDirectory {
    fn drop(&mut self) {
        if !self.keep {
            let _ = std::fs::remove_dir_all(&self.path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    pub(super) fn installed_fixture(root: &std::path::Path, id: &str, version: &str) -> Installed {
        let directory = format!("{id}-{version}");
        std::fs::create_dir_all(root.join(&directory)).unwrap();
        std::fs::write(root.join(&directory).join("agent"), "synthetic").unwrap();
        serde_json::from_value(json!({
            "entry":{"id":id,"name":id,"version":version,"distribution":{"npx":{"package":format!("{id}@{version}")}}},
            "installed_version":version,"directory":directory,"executable":"agent","args":[],"env":{},"node":false,
        })).unwrap()
    }

    #[tokio::test]
    async fn updates_preserve_old_files_and_manifest_until_connection_verifies() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("agents");
        let old = installed_fixture(&root, "synthetic", "1.0.0");
        registry::save(&root, &BTreeMap::from([("synthetic".into(), old.clone())])).unwrap();
        let manager = Manager::open(temp.path()).unwrap();
        for error in [AgentError::Auth, AgentError::Connect] {
            let candidate = installed_fixture(&root, "synthetic", "1.0.1");
            assert!(manager
                .accept_installation_with(candidate.clone(), |_, _| async { Err(error) })
                .await
                .is_err());
            assert!(!root.join(candidate.directory).exists());
            assert!(root.join(&old.directory).is_dir());
            assert_eq!(
                registry::load(&root).unwrap()["synthetic"].version(),
                "1.0.0"
            );
        }
        let candidate = installed_fixture(&root, "synthetic", "1.0.1");
        assert!(manager
            .accept_installation_with(candidate.clone(), |_, _| async {
                assert!(root.join(&old.directory).is_dir());
                assert_eq!(
                    registry::load(&root).unwrap()["synthetic"].version(),
                    "1.0.0"
                );
                Ok(connection::Pool::default())
            })
            .await
            .unwrap());
        assert!(root.join(candidate.directory).is_dir());
        assert!(!root.join(&old.directory).exists());
        assert_eq!(
            registry::load(&root).unwrap()["synthetic"].version(),
            "1.0.1"
        );
    }

    #[tokio::test]
    async fn batch_updates_continue_after_failure_and_skip_unavailable_versions() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("agents");
        let records = ["first", "second", "waiting"]
            .into_iter()
            .map(|id| (id.into(), installed_fixture(&root, id, "1.0.0")))
            .collect();
        registry::save(&root, &records).unwrap();
        let manager = Manager::open(temp.path()).unwrap();
        for id in ["first", "second"] {
            manager.updates.lock().await.offers.insert(
                id.into(),
                updates::Offer {
                    installed_version: "1.0.0".into(),
                    version: "1.0.1".into(),
                },
            );
        }
        let results = manager
            .update_all_with(|id| async move {
                match id.as_str() {
                    "first" => Err(AgentError::Connect),
                    "second" => Ok(true),
                    _ => panic!("unavailable update"),
                }
            })
            .await
            .unwrap();
        assert_eq!(results["results"].as_array().unwrap().len(), 2);
        assert_eq!(results["results"][0]["updated"], false);
        assert!(results["results"][0]["error"].is_string());
        assert_eq!(results["results"][1]["updated"], true);
    }

    #[tokio::test]
    async fn catalog_preserves_resolved_version_after_restart_and_reads_legacy_installations() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("agents");
        std::fs::create_dir(&root).unwrap();
        let mut record = json!({
            "entry":{
                "id":"synthetic", "name":"Synthetic Agent", "version":"1.0.1",
                "distribution":{"npx":{"package":"synthetic-agent@1.0.1"}}
            },
            "installed_version":"1.0.0", "directory":"synthetic-1", "executable":"index.js",
            "args":[], "env":{}, "node":true
        });
        for expected in ["1.0.0", "1.0.1"] {
            std::fs::write(
                root.join("installed.json"),
                json!({"synthetic":record}).to_string(),
            )
            .unwrap();
            let manager = Manager::open(temp.path()).unwrap();
            let catalog = manager.catalog(false).await.unwrap();
            assert_eq!(catalog["agents"][0]["version"], "1.0.1");
            assert_eq!(catalog["agents"][0]["installed_version"], expected);
            record.as_object_mut().unwrap().remove("installed_version");
        }
    }
}
