#[cfg(test)]
mod tests {
    use flashcards_integrations_server::{
        auth_crypto::{AuthCrypto, AuthCryptoError},
        web_database::{DatabaseError, WebDatabase},
    };
    use flashcards_services::authentication::{WebAuthStore, WebAuthentication};
    use sqlx::PgPool;

    #[tokio::test]
    async fn preserves_existing_users_and_sessions_and_revokes_companion_access() {
        let url = std::env::var("FLASHCARDS_TEST_DATABASE_URL").expect(
            "run PostgreSQL authentication tests with a disposable FLASHCARDS_TEST_DATABASE_URL",
        );
        let pool = PgPool::connect(&url)
            .await
            .expect("test database available");
        sqlx::raw_sql(include_str!("../migrations/0001_initial.sql"))
            .execute(&pool)
            .await
            .expect("legacy schema created");
        let secret = "test-lookup-secret-0123456789abcdef";
        let crypto = AuthCrypto::new(secret, secret).expect("valid test secrets");
        let user_id = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        let hash = "$argon2id$v=19$m=65536,t=3,p=4$MDEyMzQ1Njc4OWFiY2RlZg$kKr72i2WB9JrNMB9uauJyw/68h0u+OMS+tTC13fIk4A";
        sqlx::query(
            "INSERT INTO web_users (id, password_lookup, password_hash) VALUES ($1, $2, $3)",
        )
        .bind(user_id)
        .bind("02f70f8c5e1d016742180185afbec38234a8fcceed79af622aba2e9d1e418409")
        .bind(hash)
        .execute(&pool)
        .await
        .expect("existing Python user inserted");
        sqlx::query("INSERT INTO web_sessions (token_hash, user_id, expires_at) VALUES ($1, $2, clock_timestamp() + INTERVAL '1 hour')")
        .bind(crypto.session_hash("existing-session"))
        .bind(user_id).execute(&pool).await.expect("existing session inserted");
        let db = WebDatabase::connect(&url, crypto)
            .await
            .expect("Rust store connects");
        db.migrate()
            .await
            .expect("Rust migration adopts existing schema");
        db.migrate().await.expect("migration is idempotent");
        db.check().await.expect("schema ready");
        let (auth, session) = check_legacy_login(&db, &pool, user_id, hash).await;
        check_rehash(&auth, &pool, user_id, secret).await;
        check_failed_login_and_transaction_rollback(&auth, &pool, user_id, secret).await;
        check_short_legacy_password(&auth, &pool, secret).await;
        check_revocation(&auth, &pool, user_id, secret, &session).await;
        check_expiration_provisioning_and_restart(&db, &pool, &auth, &url, secret).await;
    }

    async fn check_legacy_login(
        db: &WebDatabase,
        pool: &PgPool,
        user_id: &str,
        hash: &str,
    ) -> (WebAuthentication<WebDatabase>, String) {
        assert_eq!(
            db.user_for_session(Some("existing-session"))
                .await
                .unwrap()
                .as_deref(),
            Some(user_id)
        );
        db.ensure_bootstrap(None).await.unwrap();
        db.ensure_bootstrap(Some("")).await.unwrap();
        db.ensure_bootstrap(Some("senha-de-teste-123"))
            .await
            .unwrap();
        assert!(matches!(
            db.create_user("senha-de-teste-123").await,
            Err(DatabaseError::PasswordAlreadyProvisioned)
        ));
        assert!(db.create_user("short").await.is_err());
        let auth = WebAuthentication::new(db.clone(), 3600);
        assert!(auth.login("wrong-password").await.unwrap().is_none());
        assert!(auth.login("").await.unwrap().is_none());
        assert!(auth.login(&"a".repeat(257)).await.unwrap().is_none());
        let session = auth
            .login("senha-de-teste-123")
            .await
            .unwrap()
            .expect("existing password accepted");
        assert_eq!(
            auth.user_for_session(Some(&session))
                .await
                .unwrap()
                .as_deref(),
            Some(user_id)
        );
        let unchanged: String =
            sqlx::query_scalar("SELECT password_hash FROM web_users WHERE id = $1")
                .bind(user_id)
                .fetch_one(pool)
                .await
                .unwrap();
        assert_eq!(unchanged, hash);
        (auth, session)
    }

    async fn check_rehash(
        auth: &WebAuthentication<WebDatabase>,
        pool: &PgPool,
        user_id: &str,
        secret: &str,
    ) {
        let old_hash = {
            use argon2::{
                Algorithm, Argon2, Params, PasswordHasher, Version, password_hash::SaltString,
            };
            Argon2::new(
                Algorithm::Argon2id,
                Version::V0x13,
                Params::new(8192, 1, 1, Some(16)).unwrap(),
            )
            .hash_password(
                b"senha-de-teste-123",
                &SaltString::encode_b64(b"0123456789abcdef").unwrap(),
            )
            .unwrap()
            .to_string()
        };
        sqlx::query("UPDATE web_users SET password_hash = $1 WHERE id = $2")
            .bind(&old_hash)
            .bind(user_id)
            .execute(pool)
            .await
            .unwrap();
        let upgraded_session = auth.login("senha-de-teste-123").await.unwrap().unwrap();
        let upgraded: String =
            sqlx::query_scalar("SELECT password_hash FROM web_users WHERE id = $1")
                .bind(user_id)
                .fetch_one(pool)
                .await
                .unwrap();
        let crypto = AuthCrypto::new(secret, secret).unwrap();
        assert_ne!(upgraded, old_hash);
        assert!(crypto.verify_password(&upgraded, "senha-de-teste-123"));
        assert!(!crypto.password_needs_rehash(&upgraded));
        assert_eq!(
            auth.user_for_session(Some(&upgraded_session))
                .await
                .unwrap()
                .as_deref(),
            Some(user_id)
        );
        assert_eq!(
            auth.user_for_session(Some("existing-session"))
                .await
                .unwrap()
                .as_deref(),
            Some(user_id)
        );
        auth.logout(Some(&upgraded_session)).await.unwrap();
    }

    async fn check_revocation(
        auth: &WebAuthentication<WebDatabase>,
        pool: &PgPool,
        user_id: &str,
        secret: &str,
        session: &str,
    ) {
        let capability = auth
            .create_companion_token(Some(session))
            .await
            .unwrap()
            .expect("capability issued");
        assert_eq!(
            auth.user_for_companion_token(&capability)
                .await
                .unwrap()
                .as_deref(),
            Some(user_id)
        );
        let stored_hash: String =
            sqlx::query_scalar("SELECT token_hash FROM web_sessions WHERE token_hash = $1")
                .bind(
                    AuthCrypto::new(secret, secret)
                        .unwrap()
                        .session_hash(session),
                )
                .fetch_one(pool)
                .await
                .unwrap();
        assert_ne!(stored_hash, session);
        auth.logout(Some(session)).await.unwrap();
        assert!(
            auth.user_for_session(Some(session))
                .await
                .unwrap()
                .is_none()
        );
        assert!(
            auth.user_for_companion_token(&capability)
                .await
                .unwrap()
                .is_none()
        );
        assert!(
            auth.create_companion_token(Some(session))
                .await
                .unwrap()
                .is_none()
        );
        assert!(auth.create_companion_token(None).await.unwrap().is_none());
        assert!(
            auth.user_for_companion_token("invalid")
                .await
                .unwrap()
                .is_none()
        );
        assert!(auth.user_for_session(None).await.unwrap().is_none());
        assert!(auth.user_for_session(Some("")).await.unwrap().is_none());
        auth.logout(None).await.unwrap();
        auth.logout(Some("")).await.unwrap();
    }

    fn legacy_hash(password: &str) -> String {
        use argon2::{
            Algorithm, Argon2, Params, PasswordHasher, Version, password_hash::SaltString,
        };
        Argon2::new(
            Algorithm::Argon2id,
            Version::V0x13,
            Params::new(8192, 1, 1, Some(16)).unwrap(),
        )
        .hash_password(
            password.as_bytes(),
            &SaltString::encode_b64(b"0123456789abcdef").unwrap(),
        )
        .unwrap()
        .to_string()
    }

    async fn check_failed_login_and_transaction_rollback(
        auth: &WebAuthentication<WebDatabase>,
        pool: &PgPool,
        user_id: &str,
        secret: &str,
    ) {
        let password = "senha-de-teste-123";
        let crypto = AuthCrypto::new(secret, secret).unwrap();
        let original: String =
            sqlx::query_scalar("SELECT password_hash FROM web_users WHERE id = $1")
                .bind(user_id)
                .fetch_one(pool)
                .await
                .unwrap();
        let sessions: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM web_sessions")
            .fetch_one(pool)
            .await
            .unwrap();
        for rejected in [
            "malformed".to_owned(),
            crypto.hash_password("another-valid-password").unwrap(),
        ] {
            sqlx::query("UPDATE web_users SET password_hash = $1 WHERE id = $2")
                .bind(rejected)
                .bind(user_id)
                .execute(pool)
                .await
                .unwrap();
            assert!(auth.login(password).await.unwrap().is_none());
        }
        let weak = legacy_hash(password);
        sqlx::query("UPDATE web_users SET password_hash = $1 WHERE id = $2")
            .bind(&weak)
            .bind(user_id)
            .execute(pool)
            .await
            .unwrap();
        sqlx::raw_sql("ALTER TABLE web_sessions ADD CONSTRAINT reject_test_sessions CHECK (user_id <> 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb') NOT VALID")
            .execute(pool).await.unwrap();
        let error = auth.login(password).await.unwrap_err();
        assert!(matches!(error, DatabaseError::Sql(_)));
        assert!(std::error::Error::source(&error).is_some());
        assert!(!error.to_string().contains(password));
        let retained: String =
            sqlx::query_scalar("SELECT password_hash FROM web_users WHERE id = $1")
                .bind(user_id)
                .fetch_one(pool)
                .await
                .unwrap();
        assert_eq!(retained, weak);
        let retained_sessions: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM web_sessions")
            .fetch_one(pool)
            .await
            .unwrap();
        assert_eq!(retained_sessions, sessions);
        sqlx::raw_sql("ALTER TABLE web_sessions DROP CONSTRAINT reject_test_sessions")
            .execute(pool)
            .await
            .unwrap();
        sqlx::query("UPDATE web_users SET password_hash = $1 WHERE id = $2")
            .bind(original)
            .bind(user_id)
            .execute(pool)
            .await
            .unwrap();
    }

    async fn check_short_legacy_password(
        auth: &WebAuthentication<WebDatabase>,
        pool: &PgPool,
        secret: &str,
    ) {
        let crypto = AuthCrypto::new(secret, secret).unwrap();
        let user = "cccccccccccccccccccccccccccccccc";
        let old = legacy_hash("legacy");
        sqlx::query(
            "INSERT INTO web_users (id, password_lookup, password_hash) VALUES ($1, $2, $3)",
        )
        .bind(user)
        .bind(crypto.password_lookup("legacy"))
        .bind(&old)
        .execute(pool)
        .await
        .unwrap();
        let session = auth
            .login("legacy")
            .await
            .unwrap()
            .expect("legacy password remains usable");
        assert_eq!(
            auth.user_for_session(Some(&session))
                .await
                .unwrap()
                .as_deref(),
            Some(user)
        );
        let upgraded: String =
            sqlx::query_scalar("SELECT password_hash FROM web_users WHERE id = $1")
                .bind(user)
                .fetch_one(pool)
                .await
                .unwrap();
        assert_ne!(upgraded, old);
        assert!(crypto.verify_password(&upgraded, "legacy"));
        assert!(!crypto.password_needs_rehash(&upgraded));
        sqlx::query("DELETE FROM web_users WHERE id = $1")
            .bind(user)
            .execute(pool)
            .await
            .unwrap();
    }

    async fn check_expiration_provisioning_and_restart(
        db: &WebDatabase,
        pool: &PgPool,
        auth: &WebAuthentication<WebDatabase>,
        url: &str,
        secret: &str,
    ) {
        let expired = WebAuthentication::new(db.clone(), 0)
            .login("senha-de-teste-123")
            .await
            .unwrap()
            .unwrap();
        assert!(
            auth.user_for_session(Some(&expired))
                .await
                .unwrap()
                .is_none()
        );
        let new_id = db.create_user("nova-senha-de-teste").await.unwrap();
        assert_eq!(new_id.len(), 32);
        let (first, second) = tokio::join!(
            db.ensure_bootstrap(Some("outra-senha-de-teste")),
            db.ensure_bootstrap(Some("outra-senha-de-teste")),
        );
        first.unwrap();
        second.unwrap();
        db.ensure_bootstrap(Some("outra-senha-de-teste"))
            .await
            .unwrap();
        let users: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM web_users")
            .fetch_one(pool)
            .await
            .unwrap();
        assert_eq!(users, 3);
        db.close().await;
        check_closed_store(db).await;
        let restarted = WebDatabase::connect(url, AuthCrypto::new(secret, secret).unwrap())
            .await
            .unwrap();
        restarted.ensure_bootstrap(None).await.unwrap();
        assert!(
            WebAuthentication::new(restarted.clone(), 3600)
                .login("senha-de-teste-123")
                .await
                .unwrap()
                .is_some()
        );
        restarted.close().await;
        pool.close().await;
    }

    async fn check_closed_store(db: &WebDatabase) {
        use std::error::Error;
        for result in [
            db.check().await,
            db.ensure_bootstrap(Some("valid-bootstrap-password")).await,
            db.create_user("valid-new-password").await.map(|_| ()),
            db.authenticate_and_create_session("senha-de-teste-123", 3600)
                .await
                .map(|_| ()),
            db.delete_session(Some("private-session-token")).await,
            db.user_for_session(Some("private-session-token"))
                .await
                .map(|_| ()),
            db.create_companion_token(Some("private-session-token"))
                .await
                .map(|_| ()),
        ] {
            let error = result.unwrap_err();
            assert!(matches!(error, DatabaseError::Sql(sqlx::Error::PoolClosed)));
            assert!(error.source().unwrap().is::<sqlx::Error>());
            assert!(!error.to_string().contains("private-session-token"));
        }
        let error = db.migrate().await.unwrap_err();
        assert!(matches!(error, DatabaseError::Migration(_)));
        assert!(error.source().unwrap().is::<sqlx::migrate::MigrateError>());
        let error = db.ensure_bootstrap(Some("short")).await.unwrap_err();
        assert!(matches!(error, DatabaseError::Crypto(_)));
        assert!(error.source().unwrap().is::<AuthCryptoError>());
        assert!(db.user_for_session(None).await.unwrap().is_none());
        db.delete_session(None).await.unwrap();
    }
}
