use crate::CheckResult;
use std::{
    fs,
    io::ErrorKind,
    path::{Path, PathBuf},
};

const MAXIMUM_DEPTH: usize = 16;
const MAXIMUM_FILES: usize = 10_000;

pub(super) fn collect(path: &Path, depth: usize, paths: &mut Vec<PathBuf>) -> CheckResult<()> {
    if depth > MAXIMUM_DEPTH {
        return Err("Coverage input directory depth exceeds limit".into());
    }
    let info = match fs::symlink_metadata(path) {
        Ok(info) => info,
        Err(error) if error.kind() == ErrorKind::NotFound && depth == 0 => return Ok(()),
        Err(error) => return Err(error.into()),
    };
    if info.file_type().is_file() {
        if paths.len() >= MAXIMUM_FILES {
            return Err("Coverage input file count exceeds limit".into());
        }
        paths.push(path.to_owned());
    } else if info.file_type().is_dir() {
        for entry in fs::read_dir(path)? {
            collect(&entry?.path(), depth + 1, paths)?;
        }
    } else {
        return Err("Coverage inputs must be regular files or directories".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn evidence_tree_limits_fail_closed() -> CheckResult<()> {
        let root = tempfile::tempdir()?;
        let file = root.path().join("test.rs");
        fs::write(&file, "fn test() {}")?;
        let mut paths = vec![PathBuf::new(); MAXIMUM_FILES - 1];
        collect(&file, 0, &mut paths)?;
        assert_eq!(paths.len(), MAXIMUM_FILES);
        assert!(
            collect(&file, 0, &mut paths)
                .unwrap_err()
                .to_string()
                .contains("file count")
        );
        assert!(
            collect(root.path(), MAXIMUM_DEPTH + 1, &mut Vec::new())
                .unwrap_err()
                .to_string()
                .contains("directory depth")
        );
        collect(&file, MAXIMUM_DEPTH, &mut Vec::new())?;
        Ok(())
    }
}
