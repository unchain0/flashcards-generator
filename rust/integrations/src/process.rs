use std::{
    error::Error,
    fmt, io,
    process::{ExitStatus, Stdio},
    time::Duration,
};
use tokio::{
    io::{AsyncRead, AsyncReadExt},
    process::{Child, Command},
};

#[derive(Debug)]
pub enum ProcessError {
    Io(io::Error),
    Timeout,
    OutputLimit,
}
impl fmt::Display for ProcessError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Io(_) => "Document processor could not run",
            Self::Timeout => "Document processor timed out",
            Self::OutputLimit => "Document processor exceeded its output limit",
        })
    }
}
impl Error for ProcessError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Io(error) => Some(error),
            _ => None,
        }
    }
}
impl From<io::Error> for ProcessError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

pub struct ProcessOutput {
    pub status: ExitStatus,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
}

struct ManagedChild {
    child: Child,
    #[cfg(unix)]
    group: Option<rustix::process::Pid>,
}

impl ManagedChild {
    fn spawn(command: &mut Command) -> Result<Self, ProcessError> {
        command
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        #[cfg(unix)]
        command.process_group(0);
        let child = command.spawn()?;
        #[cfg(unix)]
        let group = child
            .id()
            .and_then(|id| i32::try_from(id).ok())
            .and_then(rustix::process::Pid::from_raw)
            .ok_or_else(|| io::Error::other("Document processor PID unavailable"))?;
        Ok(Self {
            child,
            #[cfg(unix)]
            group: Some(group),
        })
    }

    fn kill_group(&mut self) {
        #[cfg(unix)]
        if let Some(group) = self.group.take()
            && let Err(error) =
                rustix::process::kill_process_group(group, rustix::process::Signal::KILL)
            && error != rustix::io::Errno::SRCH
        {
            tracing::warn!("Document processor group cleanup could not be confirmed");
        }
        let _ = self.child.start_kill();
    }
}

impl Drop for ManagedChild {
    fn drop(&mut self) {
        self.kill_group();
    }
}

/// # Errors
/// Propagates subprocess and pipe failures; rejects elapsed deadlines or exceeded output limits.
pub async fn run_bounded(
    command: &mut Command,
    timeout: Duration,
    maximum_output_bytes: usize,
) -> Result<ProcessOutput, ProcessError> {
    let mut managed = ManagedChild::spawn(command)?;
    let stdout = managed
        .child
        .stdout
        .take()
        .ok_or_else(|| io::Error::other("Missing processor stdout"))?;
    let stderr = managed
        .child
        .stderr
        .take()
        .ok_or_else(|| io::Error::other("Missing processor stderr"))?;
    let result = tokio::time::timeout(timeout, async {
        let (stdout, stderr) = tokio::try_join!(
            read_bounded(stdout, maximum_output_bytes),
            read_bounded(stderr, maximum_output_bytes)
        )?;
        let status = managed.child.wait().await?;
        Ok::<_, ProcessError>(ProcessOutput {
            status,
            stdout,
            stderr,
        })
    })
    .await
    .unwrap_or(Err(ProcessError::Timeout));
    managed.kill_group();
    tokio::time::timeout(Duration::from_secs(5), managed.child.wait())
        .await
        .map_err(|_| ProcessError::Timeout)??;
    result
}

async fn read_bounded(
    mut stream: impl AsyncRead + Unpin,
    maximum: usize,
) -> Result<Vec<u8>, ProcessError> {
    let mut output = Vec::new();
    let mut buffer = vec![0_u8; 8_192];
    loop {
        let count = stream.read(&mut buffer).await?;
        if count == 0 {
            return Ok(output);
        }
        if count > maximum.saturating_sub(output.len()) {
            return Err(ProcessError::OutputLimit);
        }
        output.extend_from_slice(&buffer[..count]);
    }
}

#[cfg(test)]
mod tests;
