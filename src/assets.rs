//! Champion icons and splash art from Riot's Data Dragon CDN.
//!
//! Each image is downloaded once into `<data>/assets/champions` and served
//! locally afterwards, so the UI never hits the network on repeat views. Only
//! the champion name and the game patch are sent to Riot's public CDN.

use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime};

use serde_json::Value;

const DDRAGON: &str = "https://ddragon.leagueoflegends.com";
const CATALOG_MAX_AGE: Duration = Duration::from_secs(7 * 24 * 3600);
const MAX_IMAGE_BYTES: u64 = 6 * 1024 * 1024;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Art {
    Icon,
    Splash,
}

impl Art {
    pub fn parse(kind: &str) -> Option<Art> {
        match kind {
            "icon" => Some(Art::Icon),
            "splash" => Some(Art::Splash),
            _ => None,
        }
    }
}

struct Catalog {
    version: String,
    /// normalised name or id -> Data Dragon id ("kaisa" -> "Kaisa")
    ids: HashMap<String, String>,
}

pub struct Assets {
    dir: PathBuf,
    agent: ureq::Agent,
    catalog: Mutex<Option<Arc<Catalog>>>,
    failures: Mutex<HashMap<String, Instant>>,
    download: Mutex<()>,
}

fn normalise(name: &str) -> String {
    name.chars()
        .filter(|c| c.is_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect()
}

impl Assets {
    pub fn new(dir: PathBuf) -> Assets {
        let mut builder = ureq::AgentBuilder::new()
            .timeout_connect(Duration::from_secs(5))
            .timeout(Duration::from_secs(20))
            .user_agent("LeagueHighlights/1.0");
        if let Ok(tls) = native_tls::TlsConnector::new() {
            builder = builder.tls_connector(Arc::new(tls));
        }
        Assets {
            dir,
            agent: builder.build(),
            catalog: Mutex::new(None),
            failures: Mutex::new(HashMap::new()),
            download: Mutex::new(()),
        }
    }

    /// Local file for a champion image, downloading it on first use.
    pub fn champion(&self, name: &str, art: Art) -> Option<(PathBuf, &'static str)> {
        let key = normalise(name);
        if key.is_empty() || key.len() > 40 {
            return None;
        }
        let (file, ctype) = match art {
            Art::Icon => (format!("{key}_icon.png"), "image/png"),
            Art::Splash => (format!("{key}_splash.jpg"), "image/jpeg"),
        };
        let path = self.dir.join(&file);
        if path.is_file() {
            return Some((path, ctype));
        }
        if self
            .failures
            .lock()
            .unwrap()
            .get(&file)
            .is_some_and(|t| t.elapsed() < Duration::from_secs(600))
        {
            return None;
        }
        let _one_at_a_time = self.download.lock().unwrap_or_else(|e| e.into_inner());
        if path.is_file() {
            return Some((path, ctype));
        }
        let result = self.fetch(&key, art, &path);
        if result.is_none() {
            self.failures.lock().unwrap().insert(file, Instant::now());
        }
        result.map(|()| (path, ctype))
    }

    fn fetch(&self, key: &str, art: Art, path: &PathBuf) -> Option<()> {
        let catalog = self.catalog()?;
        let id = catalog.ids.get(key)?;
        let url = match art {
            Art::Icon => format!("{DDRAGON}/cdn/{}/img/champion/{id}.png", catalog.version),
            Art::Splash => format!("{DDRAGON}/cdn/img/champion/splash/{id}_0.jpg"),
        };
        let response = self.agent.get(&url).call().ok()?;
        let mut bytes = Vec::new();
        response
            .into_reader()
            .take(MAX_IMAGE_BYTES)
            .read_to_end(&mut bytes)
            .ok()?;
        if bytes.len() < 256 {
            return None;
        }
        fs::create_dir_all(&self.dir).ok()?;
        let temp = path.with_extension("part");
        fs::write(&temp, &bytes).ok()?;
        fs::rename(&temp, path).ok()?;
        log::info!("Cached champion art {}", path.file_name()?.to_string_lossy());
        Some(())
    }

    fn catalog(&self) -> Option<Arc<Catalog>> {
        if let Some(catalog) = self.catalog.lock().unwrap().clone() {
            return Some(catalog);
        }
        let cache = self.dir.join("catalog.json");
        let fresh = fs::metadata(&cache)
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| SystemTime::now().duration_since(t).ok())
            .is_some_and(|age| age < CATALOG_MAX_AGE);
        let cached = fs::read_to_string(&cache).ok().and_then(|t| parse_catalog(&t));
        let catalog = match (fresh, cached) {
            (true, Some(catalog)) => catalog,
            (_, cached) => match self.download_catalog() {
                Some((catalog, text)) => {
                    let _ = fs::create_dir_all(&self.dir);
                    let _ = fs::write(&cache, text);
                    catalog
                }
                // Offline: an old catalog is still fine for champion ids.
                None => cached?,
            },
        };
        let catalog = Arc::new(catalog);
        *self.catalog.lock().unwrap() = Some(catalog.clone());
        Some(catalog)
    }

    fn download_catalog(&self) -> Option<(Catalog, String)> {
        let versions: Value = serde_json::from_str(
            &self.agent.get(&format!("{DDRAGON}/api/versions.json")).call().ok()?.into_string().ok()?,
        )
        .ok()?;
        let version = versions.get(0)?.as_str()?.to_string();
        let data: Value = serde_json::from_str(
            &self
                .agent
                .get(&format!("{DDRAGON}/cdn/{version}/data/en_US/champion.json"))
                .call()
                .ok()?
                .into_string()
                .ok()?,
        )
        .ok()?;
        let mut names = serde_json::Map::new();
        for (id, champion) in data.get("data")?.as_object()? {
            let name = champion.get("name").and_then(Value::as_str).unwrap_or(id);
            names.insert(id.clone(), Value::String(name.to_string()));
        }
        let text = serde_json::json!({ "version": version, "champions": names }).to_string();
        log::info!("Loaded champion catalog for patch {version} ({} champions)", names.len());
        Some((parse_catalog(&text)?, text))
    }
}

fn parse_catalog(text: &str) -> Option<Catalog> {
    let value: Value = serde_json::from_str(text).ok()?;
    let version = value.get("version")?.as_str()?.to_string();
    let mut ids = HashMap::new();
    for (id, name) in value.get("champions")?.as_object()? {
        ids.insert(normalise(id), id.clone());
        if let Some(name) = name.as_str() {
            ids.insert(normalise(name), id.clone());
        }
    }
    // Names whose Data Dragon id differs from the display name.
    for (alias, id) in [("wukong", "MonkeyKing"), ("nunuwillump", "Nunu"), ("renataglasc", "Renata")] {
        if ids.values().any(|v| v == id) {
            ids.insert(alias.into(), id.into());
        }
    }
    Some(Catalog { version, ids })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn maps_display_names_to_ids() {
        let text = r#"{"version":"15.1.1","champions":{"Kaisa":"Kai'Sa","MonkeyKing":"Wukong","Nunu":"Nunu & Willump","TahmKench":"Tahm Kench","DrMundo":"Dr. Mundo"}}"#;
        let c = parse_catalog(text).unwrap();
        assert_eq!(c.ids[&normalise("Kai'Sa")], "Kaisa");
        assert_eq!(c.ids[&normalise("Wukong")], "MonkeyKing");
        assert_eq!(c.ids[&normalise("Nunu & Willump")], "Nunu");
        assert_eq!(c.ids[&normalise("Tahm Kench")], "TahmKench");
        assert_eq!(c.ids[&normalise("Dr. Mundo")], "DrMundo");
        assert!(normalise("../etc").chars().all(char::is_alphanumeric));
    }
}
