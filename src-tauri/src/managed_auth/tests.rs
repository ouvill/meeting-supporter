use url::Url;

use super::client::validate_https_origin;
use super::oidc::{pkce_challenge, validate_callback, PendingAuthorization};

fn pending() -> PendingAuthorization {
    PendingAuthorization {
        generation: 1,
        state: "expected-state".into(),
        nonce: "expected-nonce".into(),
        verifier: "verifier".into(),
        token_url: Url::parse("https://clerk.example/oauth/token").unwrap(),
        issuer: "https://clerk.example".into(),
        jwks_url: Url::parse("https://clerk.example/.well-known/jwks.json").unwrap(),
        client_id: "client".into(),
        redirect_url: "meeting-supporter://oauth/callback".into(),
    }
}

#[test]
fn callback_requires_exact_scheme_host_path_and_state() {
    let valid =
        Url::parse("meeting-supporter://oauth/callback?code=abc&state=expected-state").unwrap();
    assert_eq!(validate_callback(&valid, &pending()).unwrap(), "abc");
    for invalid in [
        "meeting-supporter://evil/callback?code=abc&state=expected-state",
        "meeting-supporter://oauth/other?code=abc&state=expected-state",
        "meeting-supporter://oauth/callback?code=abc&state=wrong",
        "meeting-supporter://oauth/callback?code=abc&code=def&state=expected-state",
        "meeting-supporter://oauth/callback?code=abc&state=expected-state#fragment",
    ] {
        assert!(validate_callback(&Url::parse(invalid).unwrap(), &pending()).is_err());
    }
}

#[test]
fn pkce_is_s256_urlsafe_without_padding() {
    assert_eq!(
        pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    );
}

#[test]
fn managed_origin_must_be_an_https_origin() {
    assert!(validate_https_origin("https://managed.example").is_ok());
    for invalid in [
        "http://managed.example",
        "https://user@managed.example",
        "https://managed.example/path",
        "https://managed.example?query=1",
    ] {
        assert!(validate_https_origin(invalid).is_err());
    }
}
