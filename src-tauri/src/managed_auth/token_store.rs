use keyring::Entry;

use crate::error::AppError;

const KEYRING_SERVICE: &str = "net.ouvill.meeting-supporter.managed";
const KEYRING_ACCOUNT: &str = "refresh-token";

fn keyring_entry() -> Result<Entry, AppError> {
    Entry::new(KEYRING_SERVICE, KEYRING_ACCOUNT)
        .map_err(|_| AppError::Other("credential store unavailable".into()))
}

pub(super) fn store_refresh_token(token: &str) -> Result<(), AppError> {
    keyring_entry()?
        .set_password(token)
        .map_err(|_| AppError::Other("credential store unavailable".into()))
}

pub(super) fn read_refresh_token() -> Result<String, AppError> {
    keyring_entry()?
        .get_password()
        .map_err(|_| AppError::Other("sign in required".into()))
}

pub(super) fn remove_refresh_token() -> Result<(), AppError> {
    match keyring_entry()?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(_) => Err(AppError::Other("credential store unavailable".into())),
    }
}
