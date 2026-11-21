//! Zugang zum JARVIS-Core. Nur dieser Rechner (ADR-003): die Adresse muss auf 127.0.0.1,
//! localhost oder ::1 zeigen. Die Webview spricht nie selbst mit dem Core (ADR-024), deshalb gibt
//! es keinen Browser-Origin und der CSRF-Schutz des Core bleibt unverändert.

use std::sync::Mutex;
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
        401 => "Der Core verlangt ein Zugangstoken.".into(),
        403 => "Der Core lehnt die Anfrage ab.".into(),
        _ => format!("Der Core antwortet mit Status {status}."),
    }
}

pub struct Core {
    pub base: String,
    http: reqwest::Client,
    /// Nur im Arbeitsspeicher, nie auf der Platte oder im Log (ADR-024).
    token: Mutex<Option<String>>,
}

impl Core {
    pub fn new(base: String) -> Self {
        let http = reqwest::Client::builder()
            .no_proxy() // localhost nie über einen Proxy
            .connect_timeout(Duration::from_secs(3))
            .build()
            .expect("HTTP-Client");
        Self {
            base,
            http,
            token: Mutex::new(None),
        }
    }

    pub fn set_token(&self, token: Option<String>) {
        *self.token.lock().expect("token") = token.filter(|t| !t.trim().is_empty());
    }

    fn request(&self, method: reqwest::Method, path: &str) -> reqwest::RequestBuilder {
        let builder = self.http.request(method, format!("{}{}", self.base, path));
        match self.token.lock().expect("token").as_deref() {
            Some(token) => builder.bearer_auth(token),
            None => builder,
        }
    }

    pub async fn stop(&self, timeout: Duration) -> bool {
        let request = self
            .request(reqwest::Method::POST, "/v1/stop")
            .timeout(timeout);
        matches!(request.send().await, Ok(response) if response.status().is_success())
    }

    pub async fn confirm(&self, id: &str, approve: bool) -> bool {
        let id: String = id
            .chars()
            .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
            .collect();
        let request = self
            .request(reqwest::Method::POST, &format!("/v1/confirm/{id}"))
            .json(&serde_json::json!({ "approve": approve }))
            .timeout(Duration::from_secs(5));
        matches!(request.send().await, Ok(response) if response.status().is_success())
    }

    /// Öffnet einen SSE-Strom und reicht jedes Ereignis weiter. Err mit Grund, wenn der Core
    /// nicht annimmt; Ok, wenn der Strom endet.
    pub async fn stream(
        &self,
        request: reqwest::RequestBuilder,
        mut on_event: impl FnMut(ServerEvent),
        mut on_open: impl FnMut(),
    ) -> Result<(), String> {
        let response = request
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

    pub fn events_request(&self) -> reqwest::RequestBuilder {
        self.request(reqwest::Method::GET, "/v1/events")
    }

    pub fn chat_request(&self, message: &str, session_id: Option<&str>) -> reqwest::RequestBuilder {
        let mut body = serde_json::json!({ "message": message });
        if let Some(id) = session_id {
            body["session_id"] = serde_json::Value::from(id);
        }
        self.request(reqwest::Method::POST, "/v1/chat").json(&body)
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
