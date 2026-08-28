use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use jsonwebtoken::jwk::JwkSet;
use jsonwebtoken::{decode, decode_header, Algorithm, DecodingKey, Validation};
use rand::RngCore;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use url::Url;

use super::client::{http_agent, managed_api_base_url};
use crate::error::AppError;

const CALLBACK_SCHEME: &str = "meeting-supporter";
const CALLBACK_HOST: &str = "oauth";
const CALLBACK_PATH: &str = "/callback";

#[derive(Clone)]
pub(super) struct PendingAuthorization {
    pub(super) generation: u64,
    pub(super) state: String,
    pub(super) nonce: String,
    pub(super) verifier: String,
    pub(super) token_url: Url,
    pub(super) issuer: String,
    pub(super) jwks_url: Url,
    pub(super) client_id: String,
    pub(super) redirect_url: String,
}

#[derive(Debug, Deserialize)]
pub(super) struct TokenResponse {
    pub(super) access_token: String,
    pub(super) refresh_token: Option<String>,
    id_token: Option<String>,
    pub(super) expires_in: Option<u64>,
}

pub(super) enum RefreshError {
    Rejected,
    Other(AppError),
}

#[derive(Debug, Clone, Deserialize)]
struct PublicConfigEnvelope {
    auth: PublicAuthConfig,
}

#[derive(Debug, Clone, Deserialize)]
struct PublicAuthConfig {
    authorization_url: String,
    token_url: String,
    issuer: String,
    jwks_url: String,
    client_id: String,
    redirect_url: String,
    providers: Vec<String>,
    pkce_method: String,
}

#[derive(Debug, Deserialize)]
struct IdTokenClaims {
    nonce: Option<String>,
}

pub(super) fn begin_authorization() -> Result<(PendingAuthorization, Url), AppError> {
    let base = managed_api_base_url()?;
    let config = fetch_public_auth_config(&base)?;
    authorization_request(config)
}

fn fetch_public_auth_config(base: &Url) -> Result<PublicAuthConfig, AppError> {
    let url = base
        .join("v1/public-config")
        .map_err(|_| AppError::Other("invalid managed service URL".into()))?;
    let mut response = http_agent()
        .get(url.as_str())
        .call()
        .map_err(|_| AppError::Other("managed service is unavailable".into()))?;
    let envelope: PublicConfigEnvelope = response
        .body_mut()
        .read_json()
        .map_err(|_| AppError::Other("invalid managed service configuration".into()))?;
    validate_public_auth_config(envelope.auth)
}

fn validate_public_auth_config(config: PublicAuthConfig) -> Result<PublicAuthConfig, AppError> {
    let authorization = Url::parse(&config.authorization_url)
        .map_err(|_| AppError::Other("invalid authorization configuration".into()))?;
    let token = Url::parse(&config.token_url)
        .map_err(|_| AppError::Other("invalid authorization configuration".into()))?;
    let issuer = Url::parse(&config.issuer)
        .map_err(|_| AppError::Other("invalid authorization configuration".into()))?;
    let jwks = Url::parse(&config.jwks_url)
        .map_err(|_| AppError::Other("invalid authorization configuration".into()))?;
    if [&authorization, &token, &issuer, &jwks]
        .iter()
        .any(|url| url.scheme() != "https" || url.host_str().is_none())
        || config.redirect_url != "meeting-supporter://oauth/callback"
        || config.client_id.is_empty()
        || config.pkce_method != "S256"
        || config.providers != ["google", "microsoft"]
    {
        return Err(AppError::Other(
            "invalid authorization configuration".into(),
        ));
    }
    Ok(config)
}

fn random_urlsafe(bytes: usize) -> String {
    let mut value = vec![0_u8; bytes];
    rand::thread_rng().fill_bytes(&mut value);
    URL_SAFE_NO_PAD.encode(value)
}

pub(super) fn pkce_challenge(verifier: &str) -> String {
    URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()))
}

fn authorization_request(
    config: PublicAuthConfig,
) -> Result<(PendingAuthorization, Url), AppError> {
    let state = random_urlsafe(32);
    let nonce = random_urlsafe(32);
    let verifier = random_urlsafe(64);
    let token_url = Url::parse(&config.token_url)
        .map_err(|_| AppError::Other("invalid authorization configuration".into()))?;
    let jwks_url = Url::parse(&config.jwks_url)
        .map_err(|_| AppError::Other("invalid authorization configuration".into()))?;
    let mut url = Url::parse(&config.authorization_url)
        .map_err(|_| AppError::Other("invalid authorization configuration".into()))?;
    url.query_pairs_mut()
        .clear()
        .append_pair("response_type", "code")
        .append_pair("client_id", &config.client_id)
        .append_pair("redirect_uri", &config.redirect_url)
        .append_pair("scope", "openid profile email offline_access")
        .append_pair("state", &state)
        .append_pair("nonce", &nonce)
        .append_pair("code_challenge", &pkce_challenge(&verifier))
        .append_pair("code_challenge_method", "S256");
    let pending = PendingAuthorization {
        generation: 0,
        state,
        nonce,
        issuer: config.issuer,
        jwks_url,
        verifier,
        token_url,
        client_id: config.client_id,
        redirect_url: config.redirect_url,
    };
    Ok((pending, url))
}

fn single_query_value(url: &Url, key: &str) -> Result<Option<String>, AppError> {
    let values = url
        .query_pairs()
        .filter_map(|(name, value)| (name == key).then(|| value.into_owned()))
        .collect::<Vec<_>>();
    if values.len() > 1 {
        return Err(AppError::Other("invalid OAuth callback".into()));
    }
    Ok(values.into_iter().next())
}

pub(super) fn validate_callback(
    url: &Url,
    pending: &PendingAuthorization,
) -> Result<String, AppError> {
    if url.scheme() != CALLBACK_SCHEME
        || url.host_str() != Some(CALLBACK_HOST)
        || url.path() != CALLBACK_PATH
        || url.port().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
    {
        return Err(AppError::Other("invalid OAuth callback".into()));
    }
    if single_query_value(url, "state")?.as_deref() != Some(pending.state.as_str()) {
        return Err(AppError::Other("invalid OAuth state".into()));
    }
    if single_query_value(url, "error")?.is_some() {
        return Err(AppError::Other("authorization was cancelled".into()));
    }
    single_query_value(url, "code")?
        .filter(|code| !code.is_empty())
        .ok_or_else(|| AppError::Other("authorization code missing".into()))
}

fn verify_id_token(
    issuer: &str,
    jwks_url: &Url,
    client_id: &str,
    expected_nonce: Option<&str>,
    id_token: &str,
) -> Result<(), AppError> {
    let header =
        decode_header(id_token).map_err(|_| AppError::Other("invalid identity token".into()))?;
    if header.alg != Algorithm::RS256 {
        return Err(AppError::Other("invalid identity token algorithm".into()));
    }
    let key_id = header
        .kid
        .ok_or_else(|| AppError::Other("identity token key missing".into()))?;
    let mut response = http_agent()
        .get(jwks_url.as_str())
        .call()
        .map_err(|_| AppError::Other("identity verification unavailable".into()))?;
    let jwks: JwkSet = response
        .body_mut()
        .read_json()
        .map_err(|_| AppError::Other("invalid identity key set".into()))?;
    let jwk = jwks
        .find(&key_id)
        .ok_or_else(|| AppError::Other("identity token key unknown".into()))?;
    let key =
        DecodingKey::from_jwk(jwk).map_err(|_| AppError::Other("invalid identity key".into()))?;
    let mut validation = Validation::new(Algorithm::RS256);
    validation.set_audience(&[client_id]);
    validation.set_issuer(&[issuer]);
    validation.set_required_spec_claims(&["exp", "iss", "aud"]);
    let claims = decode::<IdTokenClaims>(id_token, &key, &validation)
        .map_err(|_| AppError::Other("invalid identity token".into()))?
        .claims;
    if let Some(expected_nonce) = expected_nonce {
        if claims.nonce.as_deref() != Some(expected_nonce) {
            return Err(AppError::Other("invalid identity nonce".into()));
        }
    }
    Ok(())
}

pub(super) fn exchange_authorization_code(
    pending: &PendingAuthorization,
    code: &str,
) -> Result<TokenResponse, AppError> {
    let mut response = http_agent()
        .post(pending.token_url.as_str())
        .send_form([
            ("grant_type", "authorization_code"),
            ("client_id", pending.client_id.as_str()),
            ("code", code),
            ("code_verifier", pending.verifier.as_str()),
            ("redirect_uri", pending.redirect_url.as_str()),
        ])
        .map_err(|_| AppError::Other("authorization exchange failed".into()))?;
    let tokens: TokenResponse = response
        .body_mut()
        .read_json()
        .map_err(|_| AppError::Other("invalid authorization response".into()))?;
    let id_token = tokens
        .id_token
        .as_deref()
        .ok_or_else(|| AppError::Other("identity token missing".into()))?;
    verify_id_token(
        &pending.issuer,
        &pending.jwks_url,
        &pending.client_id,
        Some(&pending.nonce),
        id_token,
    )?;
    Ok(tokens)
}

pub(super) fn refresh_authorization(refresh_token: &str) -> Result<TokenResponse, RefreshError> {
    let base = managed_api_base_url().map_err(RefreshError::Other)?;
    let config = fetch_public_auth_config(&base).map_err(RefreshError::Other)?;
    let result = http_agent().post(&config.token_url).send_form([
        ("grant_type", "refresh_token"),
        ("client_id", config.client_id.as_str()),
        ("refresh_token", refresh_token),
    ]);
    let mut response = match result {
        Ok(response) => response,
        Err(ureq::Error::StatusCode(400 | 401)) => return Err(RefreshError::Rejected),
        Err(_) => {
            return Err(RefreshError::Other(AppError::Other(
                "managed authentication unavailable".into(),
            )))
        }
    };
    let tokens: TokenResponse = response.body_mut().read_json().map_err(|_| {
        RefreshError::Other(AppError::Other("invalid authorization response".into()))
    })?;
    if let Some(id_token) = tokens.id_token.as_deref() {
        let jwks_url = Url::parse(&config.jwks_url).map_err(|_| {
            RefreshError::Other(AppError::Other(
                "invalid authorization configuration".into(),
            ))
        })?;
        verify_id_token(&config.issuer, &jwks_url, &config.client_id, None, id_token)
            .map_err(RefreshError::Other)?;
    }
    Ok(tokens)
}
