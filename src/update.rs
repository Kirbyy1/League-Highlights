//! Update check: compares this build with the latest GitHub release of the
//! project. Notify-only (the user downloads the installer); at most one
//! request every 6 hours, and only while the setting is on.

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
    }))
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
}
