use meeting_desktop_runtime::{settings::Secrets, Error};
pub struct OsSecrets;
impl Secrets for OsSecrets {
    fn get(&self, key: &str) -> Result<Option<String>, Error> {
        let entry =
            keyring::Entry::new("net.ouvill.meeting-supporter", key).map_err(|_| Error::Secrets)?;
        match entry.get_password() {
            Ok(value) => Ok(Some(value)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(_) => Err(Error::Secrets),
        }
    }
    fn set(&self, key: &str, value: Option<&str>) -> Result<(), Error> {
        let entry =
            keyring::Entry::new("net.ouvill.meeting-supporter", key).map_err(|_| Error::Secrets)?;
        match value {
            Some(value) => entry.set_password(value).map_err(|_| Error::Secrets),
            None => match entry.delete_credential() {
                Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
                Err(_) => Err(Error::Secrets),
            },
        }
    }
}
