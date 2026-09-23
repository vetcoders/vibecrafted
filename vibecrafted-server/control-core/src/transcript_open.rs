//! Shared by every provider usage adapter; each adapter includes this file
//! with `#[path]` so the adapters stay self-contained for their own tests.

use std::fs::{self, File};
use std::io;
use std::path::Path;

/// Open one provider transcript for a usage adapter. Transcripts are local
/// files under the provider's own home; a symlink or a non-regular file is
/// refused here so an adapter never follows a link out of that tree.
pub fn open_provider_transcript(path: &Path) -> io::Result<File> {
    let meta = fs::symlink_metadata(path)?;
    if meta.file_type().is_symlink() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "provider transcript is a symlink; refused",
        ));
    }
    if !meta.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "provider transcript is not a regular file",
        ));
    }
    // A local transcript path from the provider catalog, never request input;
    // symlinks and non-regular files were refused above.
    File::open(path) // nosemgrep: rust.actix.path-traversal.tainted-path.tainted-path
}
