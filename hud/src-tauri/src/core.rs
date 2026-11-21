//! Zugang zum JARVIS-Core. Nur dieser Rechner (ADR-003): die Adresse muss auf 127.0.0.1,
//! localhost oder ::1 zeigen. Die Webview spricht nie selbst mit dem Core (ADR-024), deshalb gibt
//! es keinen Browser-Origin und der CSRF-Schutz des Core bleibt unverändert. Ein Zugangstoken
//! (`require_api_token = true`) kann das HUD noch nicht senden (ADR-024).

use std::time::Duration;

use futures_util::StreamExt;

use crate::sse::{ServerEvent, SseParser};

pub const DEFAULT_CORE_URL: &str = "http://127.0.0.1:8765";
const LOCAL_HOSTS: [&str; 3] = ["127.0.0.1", "localhost", "[::1]"];

/// Prüft die Core-Adresse: nur `http://<lokal>:<port>` ohne Pfad.
pub fn local_base(raw: &str) -> Result<String, String> {
    let rest = raw
        .trim()
        .trim_end_matches('/')
        .strip_prefix("http://")
        .ok_or_else(|| format!("Core-Adresse muss mit http:// beginnen: {raw}"))?;
    if rest.contains('/') || rest.contains('@') || rest.contains('?') || rest.contains('#') {
        return Err(format!(
            "Core-Adresse ohne Pfad und ohne Zugangsdaten angeben: {raw}"
        ));
    }
    let (host, port) = match rest.rfind(':') {
        Some(index) if !rest.ends_with(']') => (&rest[..index], Some(&rest[index + 1..])),
        _ => (rest, None),
    };
    if !LOCAL_HOSTS.contains(&host) {
        return Err(format!(
            "Das HUD spricht nur mit diesem Rechner, nicht mit {host}."
        ));
    }
    if let Some(port) = port {
        port.parse::<u16>()
            .map_err(|_| format!("Ungültiger Port: {port}"))?;
    }
    Ok(format!("http://{rest}"))
}

/// Sammelt Bytes und gibt nur vollständige UTF-8-Zeichen weiter (Stücke enden mitten im Zeichen).
#[derive(Default)]
pub struct Utf8Buffer {
    pending: Vec<u8>,
}

impl Utf8Buffer {
    pub fn push(&mut self, bytes: &[u8]) -> String {
        self.pending.extend_from_slice(bytes);
        let mut out = String::new();
        loop {
            match std::str::from_utf8(&self.pending) {
                Ok(text) => {
                    out.push_str(text);
                    self.pending.clear();
                    return out;
                }
                Err(error) => {
                    let valid = error.valid_up_to();
                    out.push_str(&String::from_utf8_lossy(&self.pending[..valid]));
                    match error.error_len() {
                        None => {
                            self.pending.drain(..valid); // der Rest des Zeichens kommt noch
                            return out;
                        }
                        Some(len) => {
                            out.push('\u{fffd}'); // kaputtes Byte ersetzen und weiterlesen
                            self.pending.drain(..valid + len);
                        }
                    }
                }
            }
        }
    }
}

pub fn problem_of(status: u16) -> String {
    match status {
        401 => "Der Core verlangt ein Zugangstoken; das HUD kann noch keins senden (localhost ohne Token nutzen).".into(),
        403 => "Der Core lehnt die Anfrage ab.".into(),
        _ => format!("Der Core antwortet mit Status {status}."),
    }
}

/// Höchstens so lange Funkstille im Strom: der Core schickt alle 15 s ein Lebenszeichen. Hängt
/// er bei offener Verbindung, merkt das HUD es so nach spätestens 45 s.
const READ_TIMEOUT: Duration = Duration::from_secs(45);

pub struct Core {
    /// Err: die eingestellte Adresse war nicht lokal; dann geht keine Anfrage hinaus, und das HUD
    /// zeigt den Grund (statt still zu verschwinden).
    base: Result<String, String>,
    http: reqwest::Client,
}

impl Core {
    pub fn new(base: Result<String, String>) -> Self {
        let http = reqwest::Client::builder()
            .no_proxy() // localhost nie über einen Proxy
            .redirect(reqwest::redirect::Policy::none()) // nie auf einen anderen Host umleiten lassen
            .connect_timeout(Duration::from_secs(3))
            .read_timeout(READ_TIMEOUT)
            .build()
            .expect("HTTP-Client");
        Self { base, http }
    }

    fn request(
        &self,
        method: reqwest::Method,
        path: &str,
    ) -> Result<reqwest::RequestBuilder, String> {
        let base = self.base.as_ref().map_err(Clone::clone)?;
        Ok(self.http.request(method, format!("{base}{path}")))
    }

    pub async fn stop(&self, timeout: Duration) -> bool {
        let Ok(request) = self.request(reqwest::Method::POST, "/v1/stop") else {
            return false;
        };
        matches!(request.timeout(timeout).send().await, Ok(response) if response.status().is_success())
    }

    pub async fn confirm(&self, id: &str, approve: bool) -> bool {
        let id: String = id
            .chars()
            .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
            .collect();
        let Ok(request) = self.request(reqwest::Method::POST, &format!("/v1/confirm/{id}")) else {
            return false;
        };
        let request = request
            .json(&serde_json::json!({ "approve": approve }))
            .timeout(Duration::from_secs(5));
        matches!(request.send().await, Ok(response) if response.status().is_success())
    }

    /// Öffnet einen SSE-Strom und reicht jedes Ereignis weiter. Err mit Grund, wenn der Core
    /// nicht annimmt; Ok, wenn der Strom endet.
    pub async fn stream(
        &self,
        request: Result<reqwest::RequestBuilder, String>,
        mut on_event: impl FnMut(ServerEvent),
        mut on_open: impl FnMut(),
    ) -> Result<(), String> {
        let response = request?
            .send()
            .await
            .map_err(|_| "Der Core ist nicht erreichbar.".to_string())?;
        if !response.status().is_success() {
            return Err(problem_of(response.status().as_u16()));
        }
        on_open();
        let mut parser = SseParser::default();
        let mut text = Utf8Buffer::default();
        let mut body = response.bytes_stream();
        while let Some(chunk) = body.next().await {
            let chunk = chunk.map_err(|_| "Die Verbindung zum Core ist abgerissen.".to_string())?;
            for event in parser.feed(&text.push(&chunk)) {
                on_event(event);
            }
        }
        Ok(())
    }

    /// Live-Kacheln (M3.6): GET /v1/tiles als JSON.
    pub async fn tiles(&self) -> Result<serde_json::Value, String> {
        let request = self.request(reqwest::Method::GET, "/v1/tiles")?;
        let response = request
            .timeout(Duration::from_secs(30))
            .send()
            .await
            .map_err(|_| "Der Core ist nicht erreichbar.".to_string())?;
        if !response.status().is_success() {
            return Err(problem_of(response.status().as_u16()));
        }
        response
            .json()
            .await
            .map_err(|_| "Die Kacheln waren unlesbar.".to_string())
    }

    pub fn events_request(&self) -> Result<reqwest::RequestBuilder, String> {
        self.request(reqwest::Method::GET, "/v1/events")
    }

    pub fn chat_request(
        &self,
        message: &str,
        session_id: Option<&str>,
    ) -> Result<reqwest::RequestBuilder, String> {
        let mut body = serde_json::json!({ "message": message });
        if let Some(id) = session_id {
            body["session_id"] = serde_json::Value::from(id);
        }
        Ok(self.request(reqwest::Method::POST, "/v1/chat")?.json(&body))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_local_addresses() {
        assert_eq!(
            local_base("http://127.0.0.1:8765/").unwrap(),
            "http://127.0.0.1:8765"
        );
        assert_eq!(
            local_base("http://localhost:9000").unwrap(),
            "http://localhost:9000"
        );
        assert_eq!(
            local_base("http://[::1]:8765").unwrap(),
            "http://[::1]:8765"
        );
        for bad in [
            "https://127.0.0.1:8765",
            "http://192.168.1.5:8765",
            "http://example.com",
            "http://127.0.0.1.evil.com:80",
            "http://user@127.0.0.1:8765",
            "http://127.0.0.1:8765/v1",
            "http://127.0.0.1:99999",
            "127.0.0.1:8765",
        ] {
            assert!(
                local_base(bad).is_err(),
                "{bad} hätte abgelehnt werden müssen"
            );
        }
    }

    #[test]
    fn utf8_buffer_waits_for_complete_characters() {
        let mut buffer = Utf8Buffer::default();
        let bytes = "HÖRT".as_bytes();
        assert_eq!(buffer.push(&bytes[..2]), "H"); // Ö ist zwei Bytes lang
        assert_eq!(buffer.push(&bytes[2..]), "ÖRT");
        assert_eq!(buffer.push(&[0xff, b'a']), "\u{fffd}a"); // kaputtes Byte stört nicht
    }
}
