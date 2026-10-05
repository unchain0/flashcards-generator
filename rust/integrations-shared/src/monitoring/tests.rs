use super::*;

#[test]
fn removes_documents_credentials_identity_paths_and_local_variables() {
    let event: Event<'static> = serde_json::from_value(serde_json::json!({
        "message":"secret document", "server_name":"private-machine", "user":{"email":"private@example.com"},
        "request":{"headers":{"Authorization":"secret token"},"data":"secret password"},
        "extra":{"cards":"secret flashcards"}, "tags":{"user_id":"secret identity"},
        "breadcrumbs":{"values":[{"message":"secret profile"}]},
        "stacktrace":{"frames":[{
            "filename":"C:\\Users\\private-user\\src\\companion.rs", "function":"run", "module":"delivery", "lineno":7,
            "vars":{"cookie":"secret"}, "pre_context":["secret source"], "post_context":["secret source"],
            "abs_path":"secret location"
        },{"function":"without_filename", "context_line":"secret source"}]},
        "exception":{"values":[{"type":"secret type","value":"secret cookie","stacktrace":{"frames":[{
            "filename":"/home/private-user/src/web.rs", "function":"handle_error", "lineno":42,
            "vars":{"password":"secret"}, "context_line":"secret source", "abs_path":"secret location"
        }]}},{"type":"secret type","value":"secret cookie"}]}
    })).unwrap();
    let clean = sanitize(event, Service::Companion);
    assert!(clean.user.is_none() && clean.request.is_none() && clean.server_name.is_none());
    assert_eq!(
        clean.exception[0].stacktrace.as_ref().unwrap().frames[0]
            .filename
            .as_deref(),
        Some("web.rs")
    );
    assert_eq!(
        clean.exception[0].stacktrace.as_ref().unwrap().frames[0].lineno,
        Some(42)
    );
    let frames = &clean.stacktrace.as_ref().unwrap().frames;
    assert_eq!(frames[0].filename.as_deref(), Some("companion.rs"));
    assert_eq!(frames[0].function.as_deref(), Some("run"));
    assert_eq!(frames[0].module.as_deref(), Some("delivery"));
    assert_eq!(frames[0].lineno, Some(7));
    assert!(frames[1].filename.is_none());
    assert!(clean.exception[1].stacktrace.is_none());
    let json = serde_json::to_string(&clean).unwrap();
    for forbidden in ["secret", "private", "password", "Authorization", "user_id"] {
        assert!(!json.contains(forbidden));
    }
    assert_eq!(clean.tags["service"], "flashcards-companion");
    assert!(Service::Web.dsn().parse::<sentry::types::Dsn>().is_ok());
    assert!(
        Service::Companion
            .dsn()
            .parse::<sentry::types::Dsn>()
            .is_ok()
    );
}
