//! Shared by every provider usage adapter; each adapter includes this file
//! with `#[path]` so the adapters stay self-contained for their own tests.

use std::fs::{File, OpenOptions};
use std::io;
use std::path::Path;

/// Open one provider transcript for a usage adapter. Transcripts are local
/// files under the provider's own home. Refuse a final-component symlink or
/// reparse point atomically, then validate the opened handle is a regular file.
pub fn open_provider_transcript(path: &Path) -> io::Result<File> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        // O_NONBLOCK also prevents a swapped-in FIFO from blocking the reader
        // before its opened metadata can be checked. Regular files ignore it.
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        // CreateFile opens the reparse point itself instead of its target.
        // BACKUP_SEMANTICS lets directories reach the same metadata refusal.
        options.custom_flags(0x0020_0000 | 0x0200_0000);
    }
    // The catalog supplies a local path; no request input reaches this open.
    let file = options.open(path).map_err(|error| {
        #[cfg(unix)]
        if error.raw_os_error() == Some(libc::ELOOP) {
            return io::Error::new(
                io::ErrorKind::InvalidInput,
                "provider transcript is a symlink; refused",
            );
        }
        error
    })?; // nosemgrep: rust.actix.path-traversal.tainted-path.tainted-path
    let meta = file.metadata()?;
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if meta.file_attributes() & 0x0000_0400 != 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "provider transcript is a reparse point; refused",
            ));
        }
    }
    if !meta.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "provider transcript is not a regular file",
        ));
    }
    Ok(file)
}
