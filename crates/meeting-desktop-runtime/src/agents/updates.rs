//! 更新の確認結果だけを保存する。エージェントの導入・起動は明示操作で行う。
use super::{npm, registry, AgentError, Manager};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};

const DAY: u64 = 24 * 60 * 60;

#[derive(Default, Deserialize, Serialize)]
pub(super) struct Cache {
    pub checked_at: Option<u64>,
    #[serde(default)]
    pub entries: Vec<registry::Entry>,
    #[serde(default)]
    pub offers: BTreeMap<String, Offer>,
    pub message: Option<String>,
}
#[derive(Clone, Deserialize, Serialize)]
pub(super) struct Offer {
    pub installed_version: String,
    pub version: String,
}
pub(super) fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
pub(super) fn newer(candidate: &str, current: &str) -> bool {
    match (
        semver::Version::parse(candidate),
        semver::Version::parse(current),
    ) {
        (Ok(candidate), Ok(current)) => candidate.cmp_precedence(&current).is_gt(),
        _ => false,
    }
}
impl Cache {
    pub fn due(&self, now: u64) -> bool {
        self.checked_at
            .is_none_or(|last| now < last || now - last >= DAY)
    }
    pub fn load(root: &Path) -> Self {
        let path = root.join("updates.json");
        if std::fs::metadata(&path).is_ok_and(|m| m.len() <= 4 * 1024 * 1024) {
            if let Ok(bytes) = std::fs::read(path) {
                if let Ok(cache) = serde_json::from_slice::<Self>(&bytes) {
                    if cache.entries.len() <= 1000
                        && cache.entries.iter().all(|e| registry::identifier(&e.id))
                    {
                        return cache;
                    }
                }
            }
        }
        Self::default()
    }
    pub(super) fn save(&self, root: &Path) -> Result<(), AgentError> {
        use std::io::Write;
        std::fs::create_dir_all(root).map_err(|_| AgentError::Storage)?;
        let mut file = tempfile::NamedTempFile::new_in(root).map_err(|_| AgentError::Storage)?;
        file.write_all(&serde_json::to_vec(self).map_err(|_| AgentError::Storage)?)
            .map_err(|_| AgentError::Storage)?;
        file.as_file().sync_all().map_err(|_| AgentError::Storage)?;
        file.persist(root.join("updates.json"))
            .map_err(|_| AgentError::Storage)?;
        Ok(())
    }
}

impl Manager {
    pub async fn checks_due(&self) -> bool {
        !self.installed.lock().await.is_empty() && self.updates.lock().await.due(now())
    }
    pub async fn cancel_checks(&self) {
        self.cancel_check
            .send_modify(|revision| *revision = revision.wrapping_add(1));
        let _guard = self.checking.lock().await;
    }
    pub async fn refresh(&self) -> Result<(), AgentError> {
        let entries = match registry::fetch().await {
            Ok(entries) => entries,
            Err(error) => {
                let mut cache = self.updates.lock().await;
                cache.checked_at = Some(now());
                cache.offers.clear();
                cache.message = Some(error.to_string());
                cache.save(&self.root)?;
                return Err(error);
            }
        };
        self.check_entries(entries).await
    }
    async fn check_entries(&self, entries: Vec<registry::Entry>) -> Result<(), AgentError> {
        self.check_entries_with(entries, |entry| async move {
            if entry.distribution_kind() == "npm" {
                let probe = tempfile::Builder::new()
                    .prefix(".check-")
                    .tempdir_in(&self.root)
                    .map_err(|_| AgentError::Storage)?;
                npm::available(
                    probe.path(),
                    &entry
                        .distribution
                        .npx
                        .as_ref()
                        .ok_or(AgentError::Registry)?
                        .package,
                )
                .await
            } else {
                Ok(entry.version)
            }
        })
        .await
    }
    async fn check_entries_with<F>(
        &self,
        entries: Vec<registry::Entry>,
        available: impl Fn(registry::Entry) -> F,
    ) -> Result<(), AgentError>
    where
        F: std::future::Future<Output = Result<String, AgentError>>,
    {
        let installed = self.installed.lock().await.clone();
        let mut offers = BTreeMap::new();
        let mut message = None;
        for entry in &entries {
            let Some(old) = installed.get(&entry.id) else {
                continue;
            };
            if !entry.supported() || !newer(&entry.version, old.version()) {
                continue;
            }
            match available(entry.clone()).await {
                Ok(version) if newer(&version, old.version()) => {
                    offers.insert(entry.id.clone(), Offer { installed_version: old.version().into(), version });
                }
                Ok(_) | Err(AgentError::NpmReleaseAge { .. } | AgentError::NpmBefore | AgentError::NpmVersion) => {},
                Err(_) => message = Some("一部のエージェントの更新を確認できませんでした。時間をおいて再試行してください。".into()),
            }
        }
        let cache = Cache {
            checked_at: Some(now()),
            entries: entries.clone(),
            offers,
            message,
        };
        cache.save(&self.root)?;
        *self.entries.lock().await = entries;
        *self.updates.lock().await = cache;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn check_interval_survives_restart_and_versions_do_not_downgrade() {
        let temp = tempfile::tempdir().unwrap();
        let cache = Cache {
            checked_at: Some(100),
            ..Cache::default()
        };
        cache.save(temp.path()).unwrap();
        let loaded = Cache::load(temp.path());
        assert!(!loaded.due(100 + DAY - 1));
        assert!(loaded.due(100 + DAY));
        assert!(loaded.due(99));
        assert!(newer("1.10.0", "1.9.0"));
        assert!(!newer("1.0.0+build", "1.0.0"));
        assert!(!newer("1.0.0", "2.0.0"));
    }

    #[tokio::test]
    async fn only_installable_newer_versions_are_offered_and_cached() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("agents");
        let old = super::super::tests::installed_fixture(&root, "synthetic", "1.0.0");
        registry::save(&root, &BTreeMap::from([("synthetic".into(), old)])).unwrap();
        let manager = Manager::open(temp.path()).unwrap();
        let mut entry = super::super::tests::installed_fixture(&root, "synthetic", "1.1.0").entry;
        for (available, count) in [
            (Ok("1.0.0".into()), 0),
            (Err(AgentError::NpmReleaseAge { days: 1.0 }), 0),
            (Err(AgentError::NpmNetwork), 0),
            (Ok("1.0.1".into()), 1),
        ] {
            manager
                .check_entries_with(vec![entry.clone()], |_| {
                    std::future::ready(available.clone())
                })
                .await
                .unwrap();
            let catalog = manager.catalog(false).await.unwrap();
            assert_eq!(catalog["update_count"], count);
        }
        let restarted = Manager::open(temp.path()).unwrap();
        assert!(!restarted.checks_due().await);
        let catalog = restarted.catalog(false).await.unwrap();
        assert_eq!(catalog["agents"][0]["update_version"], "1.0.1");
        assert_eq!(catalog["agents"][0]["installed_version"], "1.0.0");
        entry.version = "0.9.0".into();
        entry.distribution.npx.as_mut().unwrap().package = "synthetic@0.9.0".into();
        restarted
            .check_entries_with(vec![entry], |_| async {
                panic!("older versions must not be probed")
            })
            .await
            .unwrap();
        assert_eq!(restarted.catalog(false).await.unwrap()["update_count"], 0);
    }
}
