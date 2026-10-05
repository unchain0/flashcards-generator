use axum::Router;
use std::{future::IntoFuture, io};
use tokio::net::TcpListener;
use tokio_util::sync::CancellationToken;

/// # Errors
/// Propagates shutdown signal registration and serving failures after draining accepted requests.
pub async fn serve(listener: TcpListener, app: Router) -> io::Result<()> {
    let signal = shutdown_signal()?;
    let stop = CancellationToken::new();
    let shutdown = stop.clone();
    let server = axum::serve(listener, app)
        .with_graceful_shutdown(async move { shutdown.cancelled().await })
        .into_future();
    tokio::pin!(server);
    tokio::select! {
        result = &mut server => result,
        signal_result = signal => {
            stop.cancel();
            let result = server.await;
            signal_result?;
            result
        }
    }
}

fn shutdown_signal() -> io::Result<impl Future<Output = io::Result<()>>> {
    #[cfg(unix)]
    let mut terminate = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    Ok(async move {
        #[cfg(unix)]
        {
            tokio::select! {
                result = tokio::signal::ctrl_c() => result,
                _ = terminate.recv() => Ok(()),
            }
        }
        #[cfg(not(unix))]
        tokio::signal::ctrl_c().await
    })
}
