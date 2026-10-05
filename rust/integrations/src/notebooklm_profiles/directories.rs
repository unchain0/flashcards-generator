use super::ProfileError;
use rustix::{
    fd::OwnedFd,
    fs::{Mode, OFlags, fchmod, mkdirat, open, openat},
    io::Errno,
};
use std::{
    ffi::OsStr,
    io,
    path::{Component, Path},
};

const DIRECTORY_FLAGS: OFlags = OFlags::RDONLY
    .union(OFlags::DIRECTORY)
    .union(OFlags::NOFOLLOW)
    .union(OFlags::CLOEXEC);
const SEARCH_FLAGS: OFlags = OFlags::PATH
    .union(OFlags::DIRECTORY)
    .union(OFlags::NOFOLLOW)
    .union(OFlags::CLOEXEC);

pub(super) fn create(path: &Path) -> Result<(), ProfileError> {
    if !path.is_absolute() || path.file_name().is_none() {
        return Err(ProfileError::InvalidPath);
    }
    let mut directory = open("/", SEARCH_FLAGS, Mode::empty()).map_err(profile_error)?;
    for component in path.components() {
        match component {
            Component::RootDir => {}
            Component::Normal(name) => directory = child_directory(&directory, name)?,
            _ => return Err(ProfileError::InvalidPath),
        }
    }
    set_private_permissions(&directory)?;
    Ok(())
}

fn set_private_permissions(directory: &OwnedFd) -> Result<(), ProfileError> {
    let opened = openat(directory, ".", DIRECTORY_FLAGS, Mode::empty()).map_err(profile_error)?;
    fchmod(opened, Mode::RWXU).map_err(profile_error)?;
    Ok(())
}

fn child_directory(parent: &OwnedFd, name: &OsStr) -> Result<OwnedFd, ProfileError> {
    match openat(parent, name, SEARCH_FLAGS, Mode::empty()) {
        Ok(directory) => Ok(directory),
        Err(Errno::NOENT) => create_child(parent, name),
        Err(error) => Err(profile_error(error)),
    }
}

fn create_child(parent: &OwnedFd, name: &OsStr) -> Result<OwnedFd, ProfileError> {
    match mkdirat(parent, name, Mode::RWXU) {
        Ok(()) | Err(Errno::EXIST) => {}
        Err(error) => return Err(profile_error(error)),
    }
    openat(parent, name, SEARCH_FLAGS, Mode::empty()).map_err(profile_error)
}

fn profile_error(error: Errno) -> ProfileError {
    match error {
        Errno::NOTDIR | Errno::LOOP => ProfileError::InvalidPath,
        _ => ProfileError::Io(io::Error::from(error)),
    }
}

#[cfg(test)]
mod tests;
