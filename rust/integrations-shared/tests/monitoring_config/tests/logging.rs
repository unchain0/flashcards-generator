use super::{Service, monitoring};
use sentry::{
    Client, Envelope, EventSamplingStrategy, Hub, Scope, Transport, protocol::EnvelopeItem,
};
use serde_json::{Value, json};
use std::{
    sync::{Arc, Mutex},
    time::Duration,
};

#[derive(Default)]
struct CaptureTransport(Mutex<Vec<Envelope>>);

impl Transport for CaptureTransport {
    fn send_envelope(&self, envelope: Envelope) {
        self.0.lock().unwrap().push(envelope);
    }
}

pub(super) fn report(service: Service) -> Value {
    flashcards_integrations_shared::logging::initialize();
    let guard = monitoring::initialize(service).unwrap();
    let transport = Arc::new(CaptureTransport::default());
    let mut options = guard.options().clone();
    options.event_sampling_strategy = EventSamplingStrategy::FixedRate(1.0);
    options.transport = Some(Arc::new(transport.clone()));
    let client = Arc::new(Client::from_config(options));
    let hub = Arc::new(Hub::new(Some(client.clone()), Arc::new(Scope::default())));
    Hub::run(hub, || {
        let span = tracing::error_span!("private-span", document = "private-document");
        let _entered = span.enter();
        tracing::trace!(target: "private-target", "private-trace");
        tracing::debug!(target: "private-target", "private-debug");
        tracing::info!(target: "private-target", "private-info");
        tracing::warn!(target: "private-target", token = "private-session", "private-warning");
        tracing::error!(target: "private-target", error = ?std::io::Error::other("private-password"), "private-error");
    });
    assert!(client.flush(Some(Duration::from_secs(1))));
    let envelopes = transport.0.lock().unwrap();
    assert_eq!(envelopes.len(), 2);
    let mut levels = Vec::new();
    for envelope in envelopes.iter() {
        assert_eq!(envelope.items().count(), 1);
        let EnvelopeItem::Event(event) = envelope.items().next().unwrap() else {
            panic!("Unexpected monitoring item");
        };
        assert_eq!(event.message.as_deref(), Some("Rust application error"));
        assert_eq!(event.tags.len(), 1);
        assert_eq!(event.tags.get("service").unwrap(), service.name());
        assert!(event.breadcrumbs.is_empty());
        assert!(event.contexts.is_empty());
        assert!(event.extra.is_empty());
        assert!(event.user.is_none());
        assert!(event.request.is_none());
        assert!(!serde_json::to_string(event).unwrap().contains("private"));
        levels.push(serde_json::to_value(event.level).unwrap());
    }
    json!({"levels":levels, "service":service.name()})
}
