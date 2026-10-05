use super::*;
use argon2::{Algorithm, Argon2, Params, PasswordHasher, Version, password_hash::SaltString};

#[test]
fn rejects_pre_epoch_times_and_uses_whole_unix_seconds() {
    assert_eq!(unix_seconds(UNIX_EPOCH).unwrap(), 0);
    assert_eq!(
        unix_seconds(UNIX_EPOCH + Duration::from_millis(1500)).unwrap(),
        1
    );
    let error = unix_seconds(UNIX_EPOCH - Duration::from_nanos(1)).unwrap_err();
    assert!(matches!(error, DatabaseError::InvalidClock));
    assert!(error.source().is_none());
}

#[tokio::test]
async fn preserves_existing_bootstrap_users_and_propagates_failed_provisioning() {
    let url =
        std::env::var("FLASHCARDS_TEST_DATABASE_URL").expect("disposable PostgreSQL URL required");
    let secret = "bootstrap-test-secret-0123456789abcdef";
    let crypto = AuthCrypto::new(secret, secret).unwrap();
    let existing_lookup = crypto.password_lookup("senha-de-teste-123");
    let new_password = "new-bootstrap-password";
    let new_lookup = crypto.password_lookup(new_password);
    let database = WebDatabase::connect(&url, crypto).await.unwrap();
    database.migrate().await.unwrap();
    let user = "dddddddddddddddddddddddddddddddd";
    let hash = "$argon2id$v=19$m=65536,t=3,p=4$MDEyMzQ1Njc4OWFiY2RlZg$kKr72i2WB9JrNMB9uauJyw/68h0u+OMS+tTC13fIk4A";
    sqlx::query("INSERT INTO web_users (id, password_lookup, password_hash) VALUES ($1, $2, $3)")
        .bind(user)
        .bind(existing_lookup)
        .bind(hash)
        .execute(&database.pool)
        .await
        .unwrap();
    sqlx::raw_sql(
        "ALTER TABLE web_users ADD CONSTRAINT reject_bootstrap_insert CHECK (false) NOT VALID",
    )
    .execute(&database.pool)
    .await
    .unwrap();
    database
        .ensure_bootstrap(Some("senha-de-teste-123"))
        .await
        .unwrap();
    let error = database
        .ensure_bootstrap(Some(new_password))
        .await
        .unwrap_err();
    assert!(!error.to_string().contains(new_password));
    let DatabaseError::Sql(error) = error else {
        panic!("Failed bootstrap insertion must retain its database cause");
    };
    assert_eq!(
        error.as_database_error().unwrap().constraint(),
        Some("reject_bootstrap_insert")
    );
    let created: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM web_users WHERE password_lookup = $1)")
            .bind(new_lookup)
            .fetch_one(&database.pool)
            .await
            .unwrap();
    assert!(!created);
    let preserved: String = sqlx::query_scalar("SELECT password_hash FROM web_users WHERE id = $1")
        .bind(user)
        .fetch_one(&database.pool)
        .await
        .unwrap();
    assert_eq!(preserved, hash);
    sqlx::raw_sql("ALTER TABLE web_users DROP CONSTRAINT reject_bootstrap_insert")
        .execute(&database.pool)
        .await
        .unwrap();
    sqlx::query("DELETE FROM web_users WHERE id = $1")
        .bind(user)
        .execute(&database.pool)
        .await
        .unwrap();
    database.close().await;
}

#[test]
fn upgrades_verified_legacy_passwords_without_applying_new_user_length_limits() {
    let secret = "test-lookup-secret-0123456789abcdef";
    let crypto = AuthCrypto::new(secret, secret).unwrap();
    let legacy = Argon2::new(
        Algorithm::Argon2id,
        Version::V0x13,
        Params::new(8192, 1, 1, Some(16)).unwrap(),
    );
    let hash = legacy
        .hash_password(
            b"legacy",
            &SaltString::encode_b64(b"0123456789abcdef").unwrap(),
        )
        .unwrap()
        .to_string();
    assert!(crypto.verify_password(&hash, "legacy"));
    let (verified, upgraded) = crypto.verify_and_rehash_password(&hash, "legacy").unwrap();
    let upgraded = upgraded.expect("verified legacy password is upgraded");
    assert!(verified);
    assert!(crypto.verify_password(&upgraded, "legacy"));
    assert!(!crypto.password_needs_rehash(&upgraded));
    assert!(crypto.hash_password("legacy").is_err());
    assert_eq!(
        crypto.verify_and_rehash_password(&hash, "wrong").unwrap(),
        (false, None)
    );
}

#[tokio::test]
async fn rejects_hash_work_when_capacity_is_closed_without_using_the_database() {
    let secret = "test-lookup-secret-0123456789abcdef";
    let db = WebDatabase {
        pool: PgPoolOptions::new()
            .connect_lazy("postgresql://localhost/unused")
            .unwrap(),
        crypto: Arc::new(AuthCrypto::new(secret, secret).unwrap()),
        hash_limiter: Arc::new(Semaphore::new(2)),
    };
    db.hash_limiter.close();
    for result in [
        db.hash_password("private-password".to_owned())
            .await
            .map(|_| ()),
        db.verify_password("private-hash".to_owned(), "private-password".to_owned())
            .await
            .map(|_| ()),
    ] {
        let error = result.unwrap_err();
        assert!(matches!(error, DatabaseError::HashCapacity(_)));
        assert!(error.source().unwrap().is::<AcquireError>());
        assert!(!error.to_string().contains("private-password"));
        assert!(!error.to_string().contains("private-hash"));
    }
    db.close().await;
}

#[tokio::test]
async fn preserves_database_and_worker_causes_without_leaking_private_details() {
    let task = tokio::spawn(std::future::pending::<()>());
    task.abort();
    let private = "private-database-password";
    let errors = [
        DatabaseError::from(sqlx::Error::Io(std::io::Error::other(private))),
        DatabaseError::from(AuthCryptoError::Json(serde_json::Error::io(
            std::io::Error::other(private),
        ))),
        DatabaseError::Migration(sqlx::migrate::MigrateError::Execute(
            sqlx::Error::PoolClosed,
        )),
        DatabaseError::HashWorker(task.await.unwrap_err()),
    ];
    for error in errors {
        let source = error.source().unwrap();
        match &error {
            DatabaseError::Sql(_) => assert!(source.is::<sqlx::Error>()),
            DatabaseError::Crypto(_) => assert!(source.is::<AuthCryptoError>()),
            DatabaseError::Migration(_) => assert!(source.is::<sqlx::migrate::MigrateError>()),
            DatabaseError::HashWorker(_) => assert!(source.is::<JoinError>()),
            DatabaseError::HashCapacity(_)
            | DatabaseError::PasswordAlreadyProvisioned
            | DatabaseError::InvalidClock => unreachable!(),
        }
        assert!(!error.to_string().contains(private));
    }
    for error in [
        DatabaseError::PasswordAlreadyProvisioned,
        DatabaseError::InvalidClock,
    ] {
        assert!(error.source().is_none());
        assert_ne!(error.to_string(), "");
    }
}
