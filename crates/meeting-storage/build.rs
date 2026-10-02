// SQLx macros validate against this disposable schema, never a user's database.
use sqlx::{sqlite::SqliteConnectOptions, Connection};
fn main() {
    println!("cargo:rerun-if-changed=src/schema.sql");
    let path = std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("OUT_DIR"))
        .join("schema.sqlite3");
    if path.exists() {
        std::fs::remove_file(&path).expect("remove build schema");
    }
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("build runtime")
        .block_on(async {
            let options = SqliteConnectOptions::new()
                .filename(&path)
                .create_if_missing(true);
            let mut connection = sqlx::SqliteConnection::connect_with(&options)
                .await
                .expect("create build schema");
            sqlx::raw_sql(include_str!("src/schema.sql"))
                .execute(&mut connection)
                .await
                .expect("apply build schema");
            connection.close().await.expect("close build schema");
        });
    println!("cargo:rustc-env=DATABASE_URL=sqlite://{}", path.display());
    println!("cargo:rustc-env=SQLX_OFFLINE=false");
}
