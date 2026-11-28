//! Liest Server-Sent Events stückweise (wie `src/api/sse.ts` im Frontend).

use serde_json::{Map, Value};

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct ServerEvent {
    pub event: String,
    pub data: Map<String, Value>,
}

#[derive(Default)]
pub struct SseParser {
    buffer: String,
}

impl SseParser {
    /// Nimmt ein Textstück an und gibt alle darin vollständigen Ereignisse zurück.
    pub fn feed(&mut self, chunk: &str) -> Vec<ServerEvent> {
        self.buffer
            .push_str(&chunk.replace("\r\n", "\n").replace('\r', "\n"));
        let mut events = Vec::new();
        while let Some(end) = self.buffer.find("\n\n") {
            let block: String = self.buffer.drain(..end + 2).collect();
            if let Some(event) = parse_block(&block[..end]) {
                events.push(event);
            }
        }
        events
    }
}

fn parse_block(block: &str) -> Option<ServerEvent> {
    let mut event = String::from("message");
    let mut data: Vec<&str> = Vec::new();
    for line in block.split('\n') {
        if line.starts_with(':') {
            continue; // Lebenszeichen (": ping")
        }
        if let Some(rest) = line.strip_prefix("event:") {
            event = rest.trim().to_string();
        } else if let Some(rest) = line.strip_prefix("data:") {
            data.push(rest.trim_start());
        }
    }
    if data.is_empty() {
        return None;
    }
    match serde_json::from_str::<Value>(&data.join("\n")) {
        Ok(Value::Object(map)) => Some(ServerEvent { event, data: map }),
        _ => None, // kaputtes Ereignis überspringen, den Strom nicht abbrechen
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn joins_chunks_split_anywhere() {
        let mut parser = SseParser::default();
        assert!(parser.feed("event: delta\ndata: {\"te").is_empty());
        let events = parser.feed("xt\":\"Hal\"}\n\nevent: delta\ndata: {\"text\":\"lo\"}\n");
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].data["text"], "Hal");
        let events = parser.feed("\n");
        assert_eq!(events[0].data["text"], "lo");
    }

    #[test]
    fn skips_pings_crlf_broken_and_non_objects() {
        let mut parser = SseParser::default();
        let events = parser.feed(
            ": ping\r\n\r\nevent: x\r\ndata: kaputt\r\n\r\nevent: l\ndata: [1]\n\nevent: ok\r\ndata: {\"a\":1}\r\n\r\n",
        );
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].event, "ok");
    }

    #[test]
    fn handles_multibyte_text() {
        let mut parser = SseParser::default();
        let events = parser.feed("event: state\ndata: {\"state\":\"HÖRT ZU\"}\n\n");
        assert_eq!(events[0].data["state"], "HÖRT ZU");
    }
}
