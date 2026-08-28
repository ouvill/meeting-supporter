#[cfg(unix)]
use std::cmp::Ordering;
#[cfg(unix)]
use std::ffi::OsStr;
use std::ffi::OsString;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;
use wait_timeout::ChildExt;

/// NVM バージョンディレクトリから解決対象にする最大数。名前順を固定して新しい版を優先する。
#[cfg(unix)]
const MAX_NVM_VERSION_DIRECTORIES: usize = 64;
/// GUI の継承 PATH は無制限に信頼せず、探索するディレクトリ数を制限する。
const MAX_PATH_DIRECTORIES: usize = 64;
/// canonicalize 後に `--version` を実行する候補数の上限。起動遅延を 4 秒以内に保つ。
const MAX_CODEX_CANDIDATES: usize = 8;
/// 不正または壊れた候補が起動を遅延させないための `codex --version` 制限時間（ミリ秒）。
const CODEX_VERSION_TIMEOUT_MILLIS: u64 = 500;
/// `wait-timeout` coordinates child exits through process-global SIGCHLD state. Unit tests launch
/// many fake CLIs in parallel, unlike the single startup resolver, so serialize their probes.
#[cfg(test)]
static CODEX_SEARCH_TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn codex_binary_names() -> &'static [&'static str] {
    #[cfg(windows)]
    {
        &["codex.exe"]
    }
    #[cfg(not(windows))]
    {
        &["codex"]
    }
}

fn is_executable_file(path: &Path) -> bool {
    if !path.is_file() {
        return false;
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        return path
            .metadata()
            .map(|metadata| metadata.permissions().mode() & 0o111 != 0)
            .unwrap_or(false);
    }

    #[cfg(not(unix))]
    true
}

#[derive(Debug, Clone)]
struct CodexSearchEnvironment {
    path: Option<OsString>,
    pnpm_home: Option<PathBuf>,
    volta_home: Option<PathBuf>,
    #[cfg(unix)]
    home: Option<PathBuf>,
    #[cfg(unix)]
    nvm_dir: Option<PathBuf>,
    #[cfg(windows)]
    local_app_data: Option<PathBuf>,
    #[cfg(windows)]
    app_data: Option<PathBuf>,
}

impl CodexSearchEnvironment {
    fn collect() -> Self {
        Self {
            path: std::env::var_os("PATH"),
            pnpm_home: std::env::var_os("PNPM_HOME").map(PathBuf::from),
            volta_home: std::env::var_os("VOLTA_HOME").map(PathBuf::from),
            #[cfg(unix)]
            home: std::env::var_os("HOME").map(PathBuf::from),
            #[cfg(unix)]
            nvm_dir: std::env::var_os("NVM_DIR").map(PathBuf::from),
            #[cfg(windows)]
            local_app_data: std::env::var_os("LOCALAPPDATA").map(PathBuf::from),
            #[cfg(windows)]
            app_data: std::env::var_os("APPDATA").map(PathBuf::from),
        }
    }
}

/// PATH と公式インストーラーの既知の配置を、重複のない絶対ディレクトリ列へ変換する純粋関数。
///
/// ファイルシステム探索は呼び出し側で済ませるため、環境入力だけで単体テストできる。
fn codex_candidate_directories(
    environment: &CodexSearchEnvironment,
    nvm_bin_directories: impl IntoIterator<Item = PathBuf>,
) -> Vec<PathBuf> {
    let mut directories = Vec::new();

    if let Some(path) = &environment.path {
        for directory in std::env::split_paths(path).take(MAX_PATH_DIRECTORIES) {
            push_unique_absolute_directory(&mut directories, directory);
        }
    }

    #[cfg(unix)]
    if let Some(home) = &environment.home {
        for directory in [
            home.join(".local/bin"),
            home.join(".volta/bin"),
            home.join(".npm-global/bin"),
            home.join(".local/share/pnpm"),
            home.join(".bun/bin"),
        ] {
            push_unique_absolute_directory(&mut directories, directory);
        }
    }

    #[cfg(unix)]
    for directory in [
        PathBuf::from("/opt/homebrew/bin"),
        PathBuf::from("/usr/local/bin"),
    ] {
        push_unique_absolute_directory(&mut directories, directory);
    }

    #[cfg(windows)]
    {
        // Codex の公式 Windows ドキュメントは Windows 11 + WSL2 を前提にしている。
        // GUI からの既存導入も検出できるよう、公式 installer と npm の標準配置だけを調べる。
        if let Some(local_app_data) = &environment.local_app_data {
            push_unique_absolute_directory(
                &mut directories,
                local_app_data.join("Programs/OpenAI/Codex/bin"),
            );
            push_unique_absolute_directory(&mut directories, local_app_data.join("Volta/bin"));
        }
        if let Some(app_data) = &environment.app_data {
            push_unique_absolute_directory(&mut directories, app_data.join("npm"));
        }
    }

    if let Some(volta_home) = &environment.volta_home {
        push_unique_absolute_directory(&mut directories, volta_home.join("bin"));
    }
    if let Some(pnpm_home) = &environment.pnpm_home {
        push_unique_absolute_directory(&mut directories, pnpm_home.clone());
    }
    for directory in nvm_bin_directories {
        push_unique_absolute_directory(&mut directories, directory);
    }

    directories
}

fn push_unique_absolute_directory(directories: &mut Vec<PathBuf>, directory: PathBuf) {
    if directory.is_absolute() && !directories.iter().any(|existing| existing == &directory) {
        directories.push(directory);
    }
}

#[cfg(unix)]
fn nvm_bin_directories(environment: &CodexSearchEnvironment) -> Vec<PathBuf> {
    let mut version_roots = Vec::new();
    if let Some(nvm_dir) = &environment.nvm_dir {
        push_unique_absolute_directory(&mut version_roots, nvm_dir.join("versions/node"));
    }
    if let Some(home) = &environment.home {
        push_unique_absolute_directory(&mut version_roots, home.join(".nvm/versions/node"));
    }

    let mut directories = Vec::new();
    for versions_root in version_roots {
        if directories.len() == MAX_NVM_VERSION_DIRECTORIES {
            break;
        }
        let Ok(entries) = std::fs::read_dir(versions_root) else {
            continue;
        };
        let mut entries: Vec<_> = entries.flatten().collect();
        entries.sort_by(|left, right| {
            let left_name = left.file_name();
            let right_name = right.file_name();
            compare_nvm_version_names(&right_name, &left_name)
                .then_with(|| right.path().cmp(&left.path()))
        });
        directories.extend(
            entries
                .into_iter()
                .take(MAX_NVM_VERSION_DIRECTORIES - directories.len())
                .map(|entry| entry.path().join("bin")),
        );
    }

    directories
}

#[cfg(unix)]
fn compare_nvm_version_names(left: &OsStr, right: &OsStr) -> Ordering {
    let left = left.to_string_lossy();
    let right = right.to_string_lossy();
    let mut left_components = left.trim_start_matches('v').split('.');
    let mut right_components = right.trim_start_matches('v').split('.');

    loop {
        match (left_components.next(), right_components.next()) {
            (Some(left), Some(right)) => {
                let ordering = compare_nvm_version_component(left, right);
                if ordering != Ordering::Equal {
                    return ordering;
                }
            }
            (Some(_), None) => return Ordering::Greater,
            (None, Some(_)) => return Ordering::Less,
            (None, None) => return Ordering::Equal,
        }
    }
}

#[cfg(unix)]
fn compare_nvm_version_component(left: &str, right: &str) -> Ordering {
    let left_digits = left.trim_end_matches(|character: char| !character.is_ascii_digit());
    let right_digits = right.trim_end_matches(|character: char| !character.is_ascii_digit());
    let left_suffix = &left[left_digits.len()..];
    let right_suffix = &right[right_digits.len()..];

    left_digits
        .len()
        .cmp(&right_digits.len())
        .then_with(|| left_digits.cmp(right_digits))
        .then_with(|| match (left_suffix.is_empty(), right_suffix.is_empty()) {
            (true, false) => Ordering::Greater,
            (false, true) => Ordering::Less,
            _ => left_suffix.cmp(right_suffix),
        })
}

#[cfg(not(unix))]
fn nvm_bin_directories(_: &CodexSearchEnvironment) -> Vec<PathBuf> {
    Vec::new()
}

fn canonical_codex_candidates(directories: impl IntoIterator<Item = PathBuf>) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    for directory in directories {
        if !directory.is_absolute() {
            continue;
        }
        for name in codex_binary_names() {
            let candidate = directory.join(name);
            if !is_executable_file(&candidate) {
                continue;
            }
            let Ok(candidate) = std::fs::canonicalize(candidate) else {
                continue;
            };
            if candidate.is_absolute()
                && is_executable_file(&candidate)
                && !candidates.iter().any(|existing| existing == &candidate)
            {
                candidates.push(candidate);
                if candidates.len() == MAX_CODEX_CANDIDATES {
                    return candidates;
                }
            }
        }
    }

    candidates
}

fn parse_codex_release(value: &str) -> Option<(u64, u64, u64)> {
    let mut components = value.trim_start_matches('v').split('.');
    let major = components.next()?.parse().ok()?;
    let minor = components.next()?.parse().ok()?;
    let patch = components.next()?.parse().ok()?;
    if components.next().is_some() {
        return None;
    }
    Some((major, minor, patch))
}

fn codex_version_priority(candidate: &Path) -> Option<u8> {
    let mut child = std::process::Command::new(candidate)
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let status = match child.wait_timeout(Duration::from_millis(CODEX_VERSION_TIMEOUT_MILLIS)) {
        Ok(Some(status)) => status,
        Ok(None) | Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            return None;
        }
    };
    if !status.success() {
        return None;
    }

    let mut output = Vec::new();
    child.stdout.take()?.read_to_end(&mut output).ok()?;
    let output = String::from_utf8_lossy(&output);
    let mut tokens = output.split_whitespace();
    while let Some(token) = tokens.next() {
        if token != "codex-cli" {
            continue;
        }
        let version = parse_codex_release(tokens.next()?)?;
        return match version {
            (0, 144, 1) => Some(3),
            (0, 144, 0) => Some(2),
            version if version >= (0, 144, 0) => Some(1),
            _ => None,
        };
    }
    None
}

fn find_codex_in_directories(directories: impl IntoIterator<Item = PathBuf>) -> Option<PathBuf> {
    let candidates = canonical_codex_candidates(directories);
    let fallback = candidates.first().cloned()?;
    let mut verified_fallback = None;
    let mut compatible_unverified = None;

    for candidate in candidates {
        match codex_version_priority(&candidate) {
            Some(3) => return Some(candidate),
            Some(2) if verified_fallback.is_none() => verified_fallback = Some(candidate),
            Some(1) if compatible_unverified.is_none() => compatible_unverified = Some(candidate),
            _ => {}
        }
    }

    verified_fallback
        .or(compatible_unverified)
        .or(Some(fallback))
}

fn find_codex_from_environment(environment: &CodexSearchEnvironment) -> Option<PathBuf> {
    #[cfg(test)]
    let _guard = CODEX_SEARCH_TEST_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let nvm_directories = nvm_bin_directories(environment);
    find_codex_in_directories(codex_candidate_directories(environment, nvm_directories))
}

/// 親環境を収集し、既知の公式インストール先と PATH から Codex CLI を解決する。
///
/// CODEX_BINARY など親環境の任意 override は信頼せず、CLI 未導入はエラーにしない。
/// backend 自体を起動して route から not_installed を返せるようにするためである。
pub fn find_codex() -> Option<PathBuf> {
    find_codex_from_environment(&CodexSearchEnvironment::collect())
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::fs;
    #[cfg(unix)]
    use std::os::unix::fs::{symlink, PermissionsExt};
    use std::sync::atomic::{AtomicUsize, Ordering as AtomicOrdering};

    static TEST_DIRECTORY_SEQUENCE: AtomicUsize = AtomicUsize::new(0);

    struct TestDirectory(PathBuf);

    impl TestDirectory {
        fn new(label: &str) -> Self {
            let sequence = TEST_DIRECTORY_SEQUENCE.fetch_add(1, AtomicOrdering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "meeting-supporter-paths-{label}-{}-{sequence}",
                std::process::id()
            ));
            fs::create_dir_all(&path).expect("create isolated test directory");
            Self(path)
        }

        fn child(&self, name: &str) -> PathBuf {
            self.0.join(name)
        }
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[cfg(unix)]
    fn fake_codex(directory: &Path, version: &str) -> PathBuf {
        fs::create_dir_all(directory).expect("create fake Codex directory");
        let executable = directory.join("codex");
        fs::write(
            &executable,
            format!("#!/bin/sh\nprintf '%s\\n' 'codex-cli v{version}'\n"),
        )
        .expect("write fake Codex executable");
        let mut permissions = fs::metadata(&executable)
            .expect("read fake Codex permissions")
            .permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&executable, permissions).expect("make fake Codex executable");
        executable
    }

    #[cfg(unix)]
    fn hanging_codex(directory: &Path) -> (PathBuf, PathBuf) {
        fs::create_dir_all(directory).expect("create hanging Codex directory");
        let executable = directory.join("codex");
        let pid_file = executable.with_extension("pid");
        fs::write(
            &executable,
            "#!/bin/sh\nprintf '%s' \"$$\" > \"$0.pid\"\nexec sleep 60\n",
        )
        .expect("write hanging Codex executable");
        let mut permissions = fs::metadata(&executable)
            .expect("read hanging Codex permissions")
            .permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&executable, permissions).expect("make hanging Codex executable");
        (executable, pid_file)
    }

    #[cfg(unix)]
    fn search_environment(
        path_directories: &[PathBuf],
        home: &Path,
        nvm_dir: Option<PathBuf>,
    ) -> CodexSearchEnvironment {
        CodexSearchEnvironment {
            path: Some(std::env::join_paths(path_directories).expect("serialize test PATH")),
            pnpm_home: None,
            volta_home: None,
            home: Some(home.to_path_buf()),
            nvm_dir,
        }
    }

    #[cfg(unix)]
    fn canonical(path: &Path) -> PathBuf {
        fs::canonicalize(path).expect("canonicalize fake executable")
    }

    #[cfg(unix)]
    #[test]
    fn finds_verified_official_home_install_when_gui_path_is_empty() {
        let temp = TestDirectory::new("empty-path-home");
        let official_codex = fake_codex(&temp.child("home/.local/bin"), "0.144.1");
        let environment = search_environment(&[], &temp.child("home"), None);

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&official_codex)),
            "a GUI with an empty PATH must still discover the official HOME installation"
        );
    }

    #[cfg(unix)]
    #[test]
    fn selects_verified_official_install_over_incompatible_path_candidate() {
        let temp = TestDirectory::new("stale-path");
        let stale_codex = fake_codex(&temp.child("stale-path"), "0.143.9");
        let official_codex = fake_codex(&temp.child("home/.local/bin"), "0.144.1");
        let environment = search_environment(
            &[stale_codex.parent().unwrap().to_path_buf()],
            &temp.child("home"),
            None,
        );

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&official_codex)),
            "an incompatible PATH executable must not mask a verified official installation"
        );
    }

    #[cfg(unix)]
    #[test]
    fn prefers_0_144_1_over_0_144_0_regardless_of_directory_order() {
        let temp = TestDirectory::new("version-priority");
        let fallback = fake_codex(&temp.child("path-0-144-0"), "0.144.0");
        let preferred = fake_codex(&temp.child("home/.local/bin"), "0.144.1");
        let environment = search_environment(
            &[fallback.parent().unwrap().to_path_buf()],
            &temp.child("home"),
            None,
        );

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&preferred)),
            "the verified 0.144.1 protocol revision is authoritative after 0.144.0"
        );
    }

    #[cfg(unix)]
    #[test]
    fn selects_later_compatible_stable_release_after_incompatible_path_candidate() {
        let temp = TestDirectory::new("stable-after-incompatible");
        let incompatible = fake_codex(&temp.child("path-0-143"), "0.143.9");
        let compatible = fake_codex(&temp.child("path-0-145"), "0.145.0");
        let environment = search_environment(
            &[
                incompatible.parent().unwrap().to_path_buf(),
                compatible.parent().unwrap().to_path_buf(),
            ],
            &temp.child("home"),
            None,
        );

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&compatible)),
            "a later stable compatible release must outrank an earlier incompatible candidate"
        );
    }

    #[cfg(unix)]
    #[test]
    fn does_not_treat_a_0_145_prerelease_as_a_compatible_stable_release() {
        let temp = TestDirectory::new("prerelease-is-incompatible");
        let prerelease = fake_codex(&temp.child("path-0-145-prerelease"), "0.145.0-beta.1");
        let verified = fake_codex(&temp.child("path-0-144-0"), "0.144.0");
        let environment = search_environment(
            &[
                prerelease.parent().unwrap().to_path_buf(),
                verified.parent().unwrap().to_path_buf(),
            ],
            &temp.child("home"),
            None,
        );

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&verified)),
            "a prerelease must not outrank a verified stable Codex release"
        );
    }

    #[cfg(unix)]
    #[test]
    fn prefers_verified_0_144_1_over_a_newer_compatible_stable_release() {
        let temp = TestDirectory::new("verified-over-newer-compatible");
        let compatible = fake_codex(&temp.child("path-0-145"), "0.145.0");
        let verified = fake_codex(&temp.child("path-0-144-1"), "0.144.1");
        let environment = search_environment(
            &[
                compatible.parent().unwrap().to_path_buf(),
                verified.parent().unwrap().to_path_buf(),
            ],
            &temp.child("home"),
            None,
        );

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&verified)),
            "the verified 0.144.1 release must outrank a newer compatible stable release"
        );
    }

    #[cfg(unix)]
    #[test]
    fn falls_back_to_first_0_144_0_when_no_0_144_1_is_available() {
        let temp = TestDirectory::new("version-fallback");
        let fallback = fake_codex(&temp.child("fallback"), "0.144.0");
        let mut path_directories = vec![fallback.parent().unwrap().to_path_buf()];
        for index in 0..7 {
            let incompatible = fake_codex(&temp.child(&format!("incompatible-{index}")), "0.143.9");
            path_directories.push(incompatible.parent().unwrap().to_path_buf());
        }
        let environment = search_environment(&path_directories, &temp.child("home"), None);

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&fallback)),
            "0.144.0 remains usable when the preferred 0.144.1 protocol is unavailable"
        );
    }

    #[cfg(unix)]
    #[test]
    fn returns_first_executable_when_all_discovered_versions_are_incompatible() {
        let temp = TestDirectory::new("incompatible-fallback");
        let first = fake_codex(&temp.child("first"), "0.143.9");
        let mut path_directories = vec![first.parent().unwrap().to_path_buf()];
        for index in 0..7 {
            let incompatible = fake_codex(&temp.child(&format!("incompatible-{index}")), "0.142.0");
            path_directories.push(incompatible.parent().unwrap().to_path_buf());
        }
        let environment = search_environment(&path_directories, &temp.child("home"), None);

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&first)),
            "a detected executable is retained for diagnostics even when no compatible version is found"
        );
    }

    #[cfg(unix)]
    #[test]
    fn deduplicates_canonical_candidates_before_applying_the_eight_candidate_limit() {
        let temp = TestDirectory::new("deduplicate-candidates");
        let shared = fake_codex(&temp.child("shared"), "0.143.9");
        let mut path_directories = Vec::new();
        for index in 0..8 {
            let directory = temp.child(&format!("alias-{index}"));
            fs::create_dir_all(&directory).expect("create alias directory");
            symlink(&shared, directory.join("codex")).expect("create duplicate Codex symlink");
            path_directories.push(directory);
        }
        let verified = fake_codex(&temp.child("verified"), "0.144.1");
        path_directories.push(verified.parent().unwrap().to_path_buf());
        let environment = search_environment(&path_directories, &temp.child("home"), None);

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&verified)),
            "aliases for one executable must not consume the bounded candidate budget"
        );
    }

    #[cfg(unix)]
    #[test]
    fn stops_after_eight_distinct_candidates() {
        let temp = TestDirectory::new("candidate-limit");
        let mut path_directories = Vec::new();
        let mut first = None;
        for index in 0..8 {
            let executable = fake_codex(&temp.child(&format!("incompatible-{index}")), "0.143.9");
            if first.is_none() {
                first = Some(executable.clone());
            }
            path_directories.push(executable.parent().unwrap().to_path_buf());
        }
        let ignored_verified = fake_codex(&temp.child("ignored-verified"), "0.144.1");
        path_directories.push(ignored_verified.parent().unwrap().to_path_buf());
        let environment = search_environment(&path_directories, &temp.child("home"), None);

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(first.as_ref().expect("first candidate"))),
            "the ninth distinct executable must not be probed after the eight-candidate cap"
        );
    }

    #[cfg(unix)]
    #[test]
    fn selects_newest_nvm_version_when_verified_versions_tie() {
        let temp = TestDirectory::new("nvm-order");
        let nvm_root = temp.child("nvm");
        fake_codex(&nvm_root.join("versions/node/v20.10.0/bin"), "0.144.1");
        let newest = fake_codex(&nvm_root.join("versions/node/v22.1.0/bin"), "0.144.1");
        let environment = search_environment(&[], &temp.child("home"), Some(nvm_root));

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&newest)),
            "NVM discovery must deterministically prefer the numerically newest Node version"
        );
    }

    #[cfg(unix)]
    #[test]
    fn kills_and_reaps_a_timed_out_candidate_before_finding_a_verified_one() {
        let temp = TestDirectory::new("timeout-reap");
        let (hung, pid_file) = hanging_codex(&temp.child("hung"));
        let verified = fake_codex(&temp.child("verified"), "0.144.1");
        let environment = search_environment(
            &[
                hung.parent().unwrap().to_path_buf(),
                verified.parent().unwrap().to_path_buf(),
            ],
            &temp.child("home"),
            None,
        );

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&verified)),
            "a hanging version probe must not prevent discovery of a later verified executable"
        );
        #[cfg(target_os = "linux")]
        {
            let process_id = fs::read_to_string(&pid_file)
                .expect("hanging candidate recorded its process id")
                .parse::<u32>()
                .expect("hanging candidate process id is numeric");
            assert!(
                !Path::new(&format!("/proc/{process_id}")).exists(),
                "the timed-out candidate process must be reaped before resolution returns"
            );
        }
    }
}
