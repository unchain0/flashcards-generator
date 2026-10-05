use super::*;

#[cfg(unix)]
#[test]
fn a_final_component_replaced_by_a_symlink_is_not_opened() {
    use std::os::unix::fs::{PermissionsExt, symlink};
    let root = tempfile::tempdir().unwrap();
    let source = root.path().join("document.pdf");
    let target = root.path().join("private.pdf");
    fs::write(&source, b"original source").unwrap();
    fs::write(&target, b"private replacement").unwrap();
    fs::set_permissions(&target, fs::Permissions::from_mode(0o640)).unwrap();
    let checked = fs::symlink_metadata(&source).unwrap();
    assert!(checked.is_file());
    fs::remove_file(&source).unwrap();
    symlink(&target, &source).unwrap();
    let result = readonly_file_options().open(&source);
    assert!(
        matches!(result, Err(error) if error.raw_os_error() == Some(rustix::io::Errno::LOOP.raw_os_error()))
    );
    assert!(
        matches!(open_regular(&source), Err(error) if error.kind() == io::ErrorKind::InvalidInput)
    );
    assert_eq!(fs::read(&target).unwrap(), b"private replacement");
    assert_eq!(
        fs::metadata(&target).unwrap().permissions().mode() & 0o777,
        0o640
    );
}

#[cfg(target_os = "linux")]
#[test]
fn fifo_replacements_use_nonblocking_reads() {
    use rustix::fs::{Mode, OFlags, fcntl_getfl, mkfifoat, open};
    use std::io::Read;
    let root = tempfile::tempdir().unwrap();
    let source = root.path().join("document.pdf");
    fs::write(&source, b"original source").unwrap();
    assert!(fs::symlink_metadata(&source).unwrap().is_file());
    fs::remove_file(&source).unwrap();
    mkfifoat(rustix::fs::CWD, &source, Mode::RUSR | Mode::WUSR).unwrap();
    let peer = open(&source, OFlags::RDWR | OFlags::NONBLOCK, Mode::empty()).unwrap();
    let opened = readonly_file_options().open(&source).unwrap();
    let flags = fcntl_getfl(&opened).unwrap();
    drop(peer);
    assert!(flags.contains(OFlags::NONBLOCK));
    let mut without_writer = readonly_file_options().open(&source).unwrap();
    assert_eq!(without_writer.read(&mut [0_u8; 1]).unwrap(), 0);
    assert!(!opened.metadata().unwrap().is_file());
    assert!(
        matches!(open_regular(&source), Err(error) if error.kind() == io::ErrorKind::InvalidInput)
    );
}

#[test]
fn readonly_options_preserve_regular_files_and_missing_file_errors() {
    let root = tempfile::tempdir().unwrap();
    let source = root.path().join("document.pdf");
    fs::write(&source, b"original source").unwrap();
    assert_eq!(open_regular(&source).unwrap().metadata().unwrap().len(), 15);
    assert!(
        matches!(open_regular(&root.path().join("missing.pdf")), Err(error) if error.kind() == io::ErrorKind::NotFound)
    );
    assert_eq!(fs::read(&source).unwrap(), b"original source");
}
