use super::*;

#[test]
fn process_diagnostics_keep_private_causes_out_of_public_messages() {
    let errors = [
        ProcessError::from(io::Error::other("synthetic-private-processor-path")),
        ProcessError::Timeout,
        ProcessError::OutputLimit,
    ];
    for (index, error) in errors.iter().enumerate() {
        assert_eq!(error.source().is_some(), index == 0);
        assert!(
            !error
                .to_string()
                .contains("synthetic-private-processor-path")
        );
    }
    assert_eq!(
        errors[0].source().unwrap().to_string(),
        "synthetic-private-processor-path"
    );
}

struct BrokenPipeReader;

impl AsyncRead for BrokenPipeReader {
    fn poll_read(
        self: std::pin::Pin<&mut Self>,
        _context: &mut std::task::Context<'_>,
        _buffer: &mut tokio::io::ReadBuf<'_>,
    ) -> std::task::Poll<io::Result<()>> {
        std::task::Poll::Ready(Err(io::Error::from(io::ErrorKind::BrokenPipe)))
    }
}

#[tokio::test]
async fn bounded_pipe_reads_accept_exact_limits_and_preserve_read_failures() {
    assert_eq!(read_bounded(b"".as_slice(), 0).await.unwrap(), b"");
    assert_eq!(read_bounded(b"data".as_slice(), 4).await.unwrap(), b"data");
    assert!(matches!(
        read_bounded(b"data".as_slice(), 3).await,
        Err(ProcessError::OutputLimit)
    ));
    let error = read_bounded(BrokenPipeReader, 1024).await.unwrap_err();
    let ProcessError::Io(cause) = error else {
        panic!("Pipe errors must retain the I/O cause")
    };
    assert_eq!(cause.kind(), io::ErrorKind::BrokenPipe);
}

#[cfg(unix)]
#[tokio::test]
async fn bounds_both_pipes_and_handles_timeouts_and_missing_processors() {
    let output = run_bounded(
        Command::new("sh").args(["-c", "printf out; printf err >&2; exit 3"]),
        Duration::from_secs(5),
        1024,
    )
    .await
    .unwrap();
    assert_eq!(output.status.code(), Some(3));
    assert_eq!(output.stdout, b"out");
    assert_eq!(output.stderr, b"err");
    for script in ["head -c 8192 /dev/zero", "head -c 8192 /dev/zero >&2"] {
        assert!(matches!(
            run_bounded(
                Command::new("sh").args(["-c", script]),
                Duration::from_secs(5),
                1024
            )
            .await,
            Err(ProcessError::OutputLimit)
        ));
    }
    assert!(matches!(
        run_bounded(
            Command::new("sleep").arg("30"),
            Duration::from_millis(50),
            1024
        )
        .await,
        Err(ProcessError::Timeout)
    ));
    assert!(matches!(
        run_bounded(
            &mut Command::new("/missing-document-processor"),
            Duration::from_secs(5),
            1024
        )
        .await,
        Err(ProcessError::Io(_))
    ));
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn cancellation_cleans_the_spawned_process_group() {
    let temporary = tempfile::tempdir().unwrap();
    let pid_file = temporary.path().join("child.pid");
    let written = pid_file.clone();
    let task = tokio::spawn(async move {
        run_bounded(
            Command::new("sh")
                .args(["-c", "sleep 30 & echo $! > \"$1\"; wait", "processor-test"])
                .arg(written),
            Duration::from_secs(60),
            1024,
        )
        .await
    });
    let pid = tokio::time::timeout(Duration::from_secs(5), wait_for_child_pid(&pid_file))
        .await
        .unwrap();
    task.abort();
    assert!(matches!(task.await, Err(error) if error.is_cancelled()));
    tokio::time::timeout(Duration::from_secs(5), wait_for_child_exit(pid))
        .await
        .unwrap();
}

#[cfg(target_os = "linux")]
async fn wait_for_child_pid(path: &std::path::Path) -> u32 {
    loop {
        if let Ok(raw) = tokio::fs::read_to_string(path).await
            && let Ok(pid) = raw.trim().parse::<u32>()
        {
            return pid;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
}

#[cfg(target_os = "linux")]
async fn wait_for_child_exit(pid: u32) {
    loop {
        let stat = tokio::fs::read_to_string(format!("/proc/{pid}/stat")).await;
        if stat
            .as_ref()
            .is_err_and(|error| error.kind() == io::ErrorKind::NotFound)
            || stat.as_ref().is_ok_and(|stat| {
                stat.rsplit_once(") ")
                    .is_some_and(|(_, tail)| tail.starts_with('Z'))
            })
        {
            return;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
}
