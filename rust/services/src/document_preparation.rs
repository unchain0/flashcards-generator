use std::{
    error::Error,
    future::Future,
    path::{Path, PathBuf},
};

pub trait PreparedDocuments: Send {
    fn files(&self) -> &[PathBuf];
}

pub trait DocumentPreparer: Send + Sync {
    type Error: Error + Send + Sync + 'static;
    type Prepared: PreparedDocuments;
    fn prepare(
        &self,
        source: &Path,
    ) -> impl Future<Output = Result<Self::Prepared, Self::Error>> + Send;
}
