//! Update check: compares this build with the latest GitHub release of the
//! project, at most once every 6 hours and only while the setting is on.
//! "Update now" downloads that release's installer, checks it against the
//! release's SHA256SUMS.txt, runs it silently and quits; the installer
//! starts the new version.

use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

const RELEASES: &str = "https://api.github.com/repos/Kirbyy1/League-Highlights/releases/latest";
const RECHECK: Duration = Duration::from_secs(6 * 3600);
pub const CURRENT: &str = env!("CARGO_PKG_VERSION");

static CACHE: Mutex<Option<(Instant, Value)>> = Mutex::new(None);

fn parse(version: &str) -> Option<(u64, u64, u64)> {
    let v = version.trim().trim_start_matches(['v', 'V']);
    let core = v.split(['-', '+']).next()?;
    let mut parts = core.split('.').map(|p| p.parse::<u64>().ok());
    Some((parts.next()??, parts.next().flatten().unwrap_or(0), parts.next().flatten().unwrap_or(0)))
}

pub fn is_newer(candidate: &str, current: &str) -> bool {
    matches!((parse(candidate), parse(current)), (Some(a), Some(b)) if a > b)
}

/// Release page URL of the cached newer release, if any.
pub fn release_url() -> Option<String> {
    let cache = CACHE.lock().unwrap();
    let (_, value) = cache.as_ref()?;
    value["available"].as_bool().filter(|a| *a)?;
    value["url"].as_str().map(str::to_string)
}

pub fn status(enabled: bool) -> Value {
    if !enabled {
        return json!({ "enabled": false, "current": CURRENT, "available": false });
    }
    if let Some((at, value)) = CACHE.lock().unwrap().as_ref() {
        if at.elapsed() < RECHECK {
            return value.clone();
        }
    }
    let value = fetch().unwrap_or_else(|e| {
        log::info!("Update check failed: {e}");
        json!({ "enabled": true, "current": CURRENT, "available": false, "error": e })
    });
    *CACHE.lock().unwrap() = Some((Instant::now(), value.clone()));
    value
}

fn fetch() -> Result<Value, String> {
    let mut builder = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(5))
        .timeout(Duration::from_secs(10))
        .user_agent(&format!("LeagueHighlights/{CURRENT}"));
    if let Ok(tls) = native_tls::TlsConnector::new() {
        builder = builder.tls_connector(std::sync::Arc::new(tls));
    }
    let text = builder
        .build()
        .get(RELEASES)
        .set("Accept", "application/vnd.github+json")
        .call()
        .map_err(|e| e.to_string())?
        .into_string()
        .map_err(|e| e.to_string())?;
    let release: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
    let tag = release["tag_name"].as_str().unwrap_or("").to_string();
    let available = is_newer(&tag, CURRENT);
    let assets = release["assets"].as_array().cloned().unwrap_or_default();
    let asset = |pick: &dyn Fn(&str) -> bool| {
        assets
            .iter()
            .find(|a| a["name"].as_str().is_some_and(|n| pick(n)))
            .map(|a| (a["name"].as_str().unwrap_or("").to_string(), a["browser_download_url"].as_str().unwrap_or("").to_string()))
    };
    let installer = asset(&|n| n.starts_with("LeagueHighlightsSetup-") && n.ends_with(".exe"));
    let sums = asset(&|n| n == "SHA256SUMS.txt");
    if available {
        log::info!("Update available: {tag} (running {CURRENT})");
    }
    Ok(json!({
        "enabled": true,
        "current": CURRENT,
        "latest": tag,
        "name": release["name"].as_str().unwrap_or(""),
        "url": release["html_url"].as_str().unwrap_or(""),
        "available": available,
        "installer": installer.as_ref().map(|i| i.0.clone()),
        "installer_url": installer.map(|i| i.1),
        "sums_url": sums.map(|s| s.1),
    }))
}

const GITHUB_DOWNLOADS: [&str; 2] = ["https://github.com/Kirbyy1/League-Highlights/releases/download/", "https://objects.githubusercontent.com/"];
const MAX_INSTALLER_BYTES: u64 = 200 * 1024 * 1024;

fn agent() -> ureq::Agent {
    let mut builder = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(10))
        .timeout(Duration::from_secs(300))
        .user_agent(&format!("LeagueHighlights/{CURRENT}"));
    if let Ok(tls) = native_tls::TlsConnector::new() {
        builder = builder.tls_connector(std::sync::Arc::new(tls));
    }
    builder.build()
}

pub fn sha256_hex(data: &[u8]) -> String {
    use windows::Win32::Security::Cryptography::{BCryptHash, BCRYPT_SHA256_ALG_HANDLE};
    let mut out = [0u8; 32];
    unsafe {
        let _ = BCryptHash(BCRYPT_SHA256_ALG_HANDLE, None, data, &mut out);
    }
    out.iter().map(|b| format!("{b:02x}")).collect()
}

/// The checksum listed for `name` in a SHA256SUMS.txt.
pub fn listed_sum(sums: &str, name: &str) -> Option<String> {
    sums.lines().find_map(|line| {
        let mut parts = line.split_whitespace();
        let hash = parts.next()?;
        let file = parts.next()?.trim_start_matches('*');
        (file == name && hash.len() == 64).then(|| hash.to_ascii_lowercase())
    })
}

/// Download, verify and start the newer release's installer. On success the
/// caller quits the app so the installer can replace it.
pub fn install() -> Result<String, String> {
    let info = CACHE.lock().unwrap().as_ref().map(|(_, v)| v.clone()).ok_or("Check for updates first.")?;
    if !info["available"].as_bool().unwrap_or(false) {
        return Err("You already have the latest version.".into());
    }
    let name = info["installer"].as_str().ok_or("This release has no installer to download.")?.to_string();
    let url = info["installer_url"].as_str().unwrap_or("");
    let sums_url = info["sums_url"].as_str().ok_or("This release has no checksums, so it can't be installed automatically.")?;
    for u in [url, sums_url] {
        if !GITHUB_DOWNLOADS.iter().any(|p| u.starts_with(p)) {
            return Err("The download link doesn't point to this project's GitHub releases.".into());
        }
    }
    if name.contains(['/', '\\', ':']) || name.contains("..") {
        return Err("Unexpected installer name.".into());
    }
    let agent = agent();
    let sums = agent.get(sums_url).call().map_err(|e| format!("Could not download the checksums: {e}"))?
        .into_string().map_err(|e| e.to_string())?;
    let expected = listed_sum(&sums, &name).ok_or("The installer isn't listed in the release checksums.")?;
    let mut data = Vec::new();
    use std::io::Read;
    agent.get(url).call().map_err(|e| format!("Could not download the update: {e}"))?
        .into_reader().take(MAX_INSTALLER_BYTES).read_to_end(&mut data).map_err(|e| e.to_string())?;
    if sha256_hex(&data) != expected {
        return Err("The downloaded installer doesn't match the release checksum, so it wasn't run.".into());
    }
    let dir = std::env::temp_dir().join("LeagueHighlightsUpdate");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let path = dir.join(&name);
    std::fs::write(&path, &data).map_err(|e| e.to_string())?;
    std::process::Command::new(&path)
        .args(["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        .spawn()
        .map_err(|e| format!("Could not start the installer: {e}"))?;
    log::info!("Started the {} installer ({} bytes, checksum verified)", info["latest"].as_str().unwrap_or("?"), data.len());
    Ok(info["latest"].as_str().unwrap_or("").to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compares_versions() {
        assert!(is_newer("v2.1.0", "2.0.0"));
        assert!(is_newer("2.0.1", "2.0.0"));
        assert!(!is_newer("v1.0.5", "2.0.0")); // Python-era releases never count as updates
        assert!(!is_newer("v2.0.0", "2.0.0"));
        assert!(!is_newer("garbage", "2.0.0"));
        assert!(is_newer("v3", "2.9.9"));
    }

    #[test]
    fn checksums_are_read_and_verified() {
        let sums = "757a1362aa  LeagueHighlights-2.1.0.zip\n7b1c72f6e560e560e560e560e560e560e560e560e560e560e560e560e560e560  LeagueHighlightsSetup-2.1.0.exe\n";
        assert_eq!(listed_sum(sums, "LeagueHighlightsSetup-2.1.0.exe").as_deref(), Some("7b1c72f6e560e560e560e560e560e560e560e560e560e560e560e560e560e560"));
        assert!(listed_sum(sums, "LeagueHighlights-2.1.0.zip").is_none()); // not a full hash
        assert!(listed_sum(sums, "other.exe").is_none());
        assert_eq!(sha256_hex(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    }
}
