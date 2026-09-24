//! Generation-aware event tailing — the SSE substrate.
//!
//! Python owns the append/rotate protocol. Every v1 segment begins with a
//! `stream.segment` header carrying one epoch UUID and a monotonic generation.
//! A wire cursor is therefore `v2:<epoch>:<generation>:<offset>` rather than a
//! generation-less byte offset. Archived segments bridge reconnects across
//! rotation; a missing generation yields an explicit gap and never silently
//! replays the current file.

use std::collections::{HashMap, HashSet};
use std::fmt;
use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, Read, Seek, SeekFrom};
use std::path::{Component, Path, PathBuf};
use std::str::FromStr;
use std::sync::{Mutex, OnceLock};
use std::time::SystemTime;

#[cfg(unix)]
use std::ffi::CString;
#[cfg(unix)]
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
#[cfg(unix)]
use std::os::unix::ffi::OsStrExt;
#[cfg(unix)]
use std::os::unix::fs::MetadataExt;

/// Unix `st_dev`. Used for segment inode identity.
#[cfg(unix)]
fn metadata_device(metadata: &fs::Metadata) -> u64 {
    metadata.dev()
}

#[cfg(windows)]
fn metadata_device(_metadata: &fs::Metadata) -> u64 {
    // Windows limitation: stable std has no st_dev equivalent (`volume_serial_number`
    // needs unstable `windows_by_handle`). Device identity is not checked here.
    0
}

/// Unix `st_ino`. Used for segment inode identity.
#[cfg(unix)]
fn metadata_inode(metadata: &fs::Metadata) -> u64 {
    metadata.ino()
}

#[cfg(windows)]
fn metadata_inode(_metadata: &fs::Metadata) -> u64 {
    // Windows limitation: stable std has no st_ino equivalent (`file_index` needs
    // unstable `windows_by_handle`). Inode identity is not checked here.
    0
}

/// Unix owner uid. Windows has no POSIX uid — sentinel 0 skips owner checks.
#[cfg(unix)]
fn metadata_owner_uid(metadata: &fs::Metadata) -> u32 {
    metadata.uid()
}

#[cfg(windows)]
fn metadata_owner_uid(_metadata: &fs::Metadata) -> u32 {
    // Windows limitation: POSIX uid does not exist; owner-uid identity checks
    // are not performed (both sides compare as sentinel 0).
    0
}

use crate::model::Event;

pub const STREAM_SEGMENT_SCHEMA: &str = "vibecrafted.event-stream-segment.v1";
pub const STREAM_BATCH_MAX_EVENTS: usize = 128;
pub const STREAM_BATCH_MAX_BYTES: usize = 1024 * 1024;
pub const STREAM_LINE_MAX_BYTES: usize = 256 * 1024;

/// Durable position in the event stream.
///
/// `Legacy` exists only for generation-less clients during the v1 -> v2
/// migration. New clients should persist the opaque `Display` value.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum StreamCursor {
    Legacy(u64),
    V2 {
        epoch: String,
        generation: u64,
        offset: u64,
    },
}

impl StreamCursor {
    #[must_use]
    pub fn offset(&self) -> u64 {
        match self {
            Self::Legacy(offset) | Self::V2 { offset, .. } => *offset,
        }
    }

    #[must_use]
    pub fn is_v2(&self) -> bool {
        matches!(self, Self::V2 { .. })
    }

    /// Whether this cursor has reached a connection's captured high-watermark.
    #[must_use]
    pub fn reaches(&self, target: &Self) -> bool {
        match (self, target) {
            (Self::Legacy(current), Self::Legacy(target)) => current >= target,
            (
                Self::V2 {
                    epoch: current_epoch,
                    generation: current_generation,
                    offset: current_offset,
                },
                Self::V2 {
                    epoch: target_epoch,
                    generation: target_generation,
                    offset: target_offset,
                },
            ) if current_epoch == target_epoch => {
                current_generation > target_generation
                    || (current_generation == target_generation && current_offset >= target_offset)
            }
            _ => false,
        }
    }
}

impl fmt::Display for StreamCursor {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Legacy(offset) => write!(formatter, "{offset}"),
            Self::V2 {
                epoch,
                generation,
                offset,
            } => write!(formatter, "v2:{epoch}:{generation}:{offset}"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CursorParseError;

impl fmt::Display for CursorParseError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("invalid event stream cursor")
    }
}

impl std::error::Error for CursorParseError {}

impl FromStr for StreamCursor {
    type Err = CursorParseError;

    fn from_str(raw: &str) -> Result<Self, Self::Err> {
        let value = raw.trim();
        if let Ok(offset) = value.parse::<u64>() {
            return Ok(Self::Legacy(offset));
        }
        let mut parts = value.split(':');
        if parts.next() != Some("v2") {
            return Err(CursorParseError);
        }
        let epoch = parts.next().unwrap_or_default();
        let generation = parts
            .next()
            .and_then(|part| part.parse::<u64>().ok())
            .ok_or(CursorParseError)?;
        let offset = parts
            .next()
            .and_then(|part| part.parse::<u64>().ok())
            .ok_or(CursorParseError)?;
        if parts.next().is_some()
            || epoch.is_empty()
            || !epoch
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
        {
            return Err(CursorParseError);
        }
        Ok(Self::V2 {
            epoch: epoch.to_string(),
            generation,
            offset,
        })
    }
}

#[derive(Debug, Clone)]
pub struct StreamRecord {
    pub event: Event,
    pub cursor: StreamCursor,
}

#[derive(Debug, Clone)]
pub struct StreamBoundary {
    pub from: StreamCursor,
    pub to: StreamCursor,
}

#[derive(Debug, Clone)]
pub struct StreamGap {
    pub requested: String,
    pub resumed_at: StreamCursor,
    pub reason: String,
}

#[derive(Debug, Clone)]
pub enum StreamItem {
    Event(StreamRecord),
    Boundary(StreamBoundary),
    Gap(StreamGap),
}

#[derive(Debug, Clone)]
pub struct StreamBatch {
    pub items: Vec<StreamItem>,
    pub cursor: StreamCursor,
    pub scanned_events: usize,
    pub scanned_bytes: usize,
}

/// Atomic connection baseline captured from one active file descriptor.
#[derive(Debug, Clone)]
pub struct ConnectionWindow {
    pub cursor: StreamCursor,
    pub high_watermark: StreamCursor,
}

/// Compatibility batch for the old active-file-only byte-offset API.
#[derive(Debug, Clone)]
pub struct EventBatch {
    pub events: Vec<Event>,
    pub cursor: u64,
}

#[derive(Debug, Clone)]
pub struct EventStream {
    path: PathBuf,
}

#[derive(Debug, Clone)]
struct Segment {
    archive_name: Option<PathBuf>,
    epoch: String,
    generation: u64,
    data_start: u64,
    len: u64,
    active: bool,
    modified: SystemTime,
    device: u64,
    inode: u64,
    owner_uid: u32,
}

#[derive(Debug, Clone)]
struct ArchiveCache {
    modified: Option<SystemTime>,
    segments: Vec<Segment>,
}

static ARCHIVE_CACHES: OnceLock<Mutex<HashMap<PathBuf, ArchiveCache>>> = OnceLock::new();

fn validate_opened_archive_file(
    file: &File,
    expected: &fs::Metadata,
    owner_uid: u32,
) -> io::Result<()> {
    let opened = file.metadata()?;
    if !opened.file_type().is_file()
        || metadata_owner_uid(&opened) != owner_uid
        || metadata_device(&opened) != metadata_device(expected)
        || metadata_inode(&opened) != metadata_inode(expected)
    {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "event archive entry changed ownership or inode before open",
        ));
    }
    Ok(())
}

#[cfg(unix)]
fn open_archive_child_no_follow(directory: &File, name: &Path) -> io::Result<File> {
    let encoded = CString::new(name.as_os_str().as_bytes()).map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "event archive name contains NUL",
        )
    })?;
    // SAFETY: `directory` is an open, canonical archive directory. `name` was
    // validated as one normal component. `openat` returns a fresh owned fd or
    // a negative errno; O_NOFOLLOW rejects a last-component symlink.
    let raw = unsafe {
        libc::openat(
            directory.as_raw_fd(),
            encoded.as_ptr(),
            libc::O_RDONLY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
        )
    };
    if raw < 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: a successful `openat` returned one fresh descriptor, transferred
    // exactly once into `OwnedFd` and then `File`.
    let owned = unsafe { OwnedFd::from_raw_fd(raw) };
    Ok(File::from(owned))
}

#[derive(Debug, Clone)]
enum CursorStyle {
    Legacy,
    V2 { epoch: String, generation: u64 },
}

impl CursorStyle {
    fn at(&self, offset: u64) -> StreamCursor {
        match self {
            Self::Legacy => StreamCursor::Legacy(offset),
            Self::V2 { epoch, generation } => StreamCursor::V2 {
                epoch: epoch.clone(),
                generation: *generation,
                offset,
            },
        }
    }
}

#[derive(Debug)]
struct Drain {
    items: Vec<StreamItem>,
    cursor: u64,
    scanned_events: usize,
    scanned_bytes: usize,
    at_eof: bool,
}

#[derive(Debug)]
struct BoundedLine {
    data: Vec<u8>,
    bytes: usize,
    complete: bool,
    too_long: bool,
}

#[derive(Debug, Clone, Copy, Default)]
struct DrainBudget {
    events: usize,
    bytes: usize,
}

impl EventStream {
    #[must_use]
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }

    #[must_use]
    pub fn path(&self) -> &Path {
        &self.path
    }

    fn open_active(&self) -> io::Result<Option<(File, Option<Segment>)>> {
        let mut file = match File::open(&self.path) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error),
        };
        let segment = read_v1_segment_from_file(&mut file, true)?;
        Ok(Some((file, segment)))
    }

    fn open_owned_segment(&self, segment: &Segment) -> io::Result<File> {
        if segment.active {
            let Some((file, observed)) = self.open_active()? else {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    "active event segment disappeared",
                ));
            };
            let opened = file.metadata()?;
            let identity_matches = match observed {
                Some(observed) => {
                    observed.epoch == segment.epoch
                        && observed.generation == segment.generation
                        && observed.device == segment.device
                        && observed.inode == segment.inode
                }
                None => {
                    segment.epoch.is_empty()
                        && metadata_device(&opened) == segment.device
                        && metadata_inode(&opened) == segment.inode
                }
            };
            if !identity_matches {
                return Err(io::Error::new(
                    io::ErrorKind::Interrupted,
                    "active event segment changed before read",
                ));
            }
            return Ok(file);
        }

        let archive = self
            .path
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join("events_archive");
        let canonical_archive = fs::canonicalize(&archive)?;
        let expected_archive = fs::metadata(&canonical_archive)?;
        let archive_owner = metadata_owner_uid(&expected_archive);
        let name = segment.archive_name.as_ref().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                "retained event segment has no archive name",
            )
        })?;
        let mut components = name.components();
        if !matches!(components.next(), Some(Component::Normal(_)))
            || components.next().is_some()
            || !name
                .to_str()
                .is_some_and(|name| name.starts_with("events-") && name.ends_with(".jsonl"))
        {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "invalid retained event segment name",
            ));
        }
        #[cfg(unix)]
        let mut file = {
            let archive_directory = File::open(&canonical_archive)?;
            let archive_metadata = archive_directory.metadata()?;
            if !archive_metadata.file_type().is_dir()
                || metadata_owner_uid(&archive_metadata) != archive_owner
                || metadata_device(&archive_metadata) != metadata_device(&expected_archive)
                || metadata_inode(&archive_metadata) != metadata_inode(&expected_archive)
            {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    "invalid event archive owner",
                ));
            }
            open_archive_child_no_follow(&archive_directory, name)?
        };
        #[cfg(windows)]
        let mut file = {
            // Windows limitation: File::open on a directory returns ACCESS_DENIED
            // (no dirfd for openat). Validate via fs::metadata, then open by path.
            let archive_metadata = fs::metadata(&canonical_archive)?;
            if !archive_metadata.file_type().is_dir()
                || metadata_owner_uid(&archive_metadata) != archive_owner
                || metadata_device(&archive_metadata) != metadata_device(&expected_archive)
                || metadata_inode(&archive_metadata) != metadata_inode(&expected_archive)
            {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    "invalid event archive owner",
                ));
            }
            // No openat(O_NOFOLLOW) here. The opened path comes from the
            // canonical archive's own listing, never from joining the retained
            // name, and DirEntry::file_type does not follow links: a symlink
            // or reparse point is not a regular file.
            let entry = fs::read_dir(&canonical_archive)?
                .flatten()
                .find(|entry| Path::new(&entry.file_name()) == name.as_path())
                .ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::NotFound,
                        "retained event segment disappeared",
                    )
                })?;
            if !entry.file_type()?.is_file() {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    "event archive entry is not a regular file",
                ));
            }
            File::open(entry.path())?
        };
        let opened = file.metadata()?;
        if !opened.file_type().is_file()
            || metadata_owner_uid(&opened) != archive_owner
            || metadata_owner_uid(&opened) != segment.owner_uid
            || metadata_device(&opened) != segment.device
            || metadata_inode(&opened) != segment.inode
        {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "retained event segment identity changed",
            ));
        }
        let observed = read_v1_segment_from_file(&mut file, false)?;
        if observed.as_ref().is_some_and(|observed| {
            observed.epoch == segment.epoch
                && observed.generation == segment.generation
                && observed.device == segment.device
                && observed.inode == segment.inode
        }) {
            return Ok(file);
        }
        Err(io::Error::new(
            io::ErrorKind::Interrupted,
            "retained event segment changed before read",
        ))
    }

    /// Cursor at the first event of the active segment.
    pub fn start_cursor(&self) -> io::Result<StreamCursor> {
        self.connection_window(None).map(|window| window.cursor)
    }

    /// Last complete record boundary in the active segment.
    pub fn high_watermark(&self) -> io::Result<StreamCursor> {
        self.connection_window(None)
            .map(|window| window.high_watermark)
    }

    /// Capture the connection start and high-watermark from one open inode.
    ///
    /// A segmented stream upgrades only the unambiguous legacy origin
    /// (`Legacy(0)`) to its v2 data start. Non-zero legacy offsets cannot name
    /// a generation and remain intact so `read_stream` can emit an explicit
    /// resnapshot gap. Headerless streams preserve legacy byte cursors.
    pub fn connection_window(
        &self,
        requested: Option<&StreamCursor>,
    ) -> io::Result<ConnectionWindow> {
        let Some((mut file, segment)) = self.open_active()? else {
            return Ok(ConnectionWindow {
                cursor: requested.cloned().unwrap_or(StreamCursor::Legacy(0)),
                high_watermark: StreamCursor::Legacy(0),
            });
        };
        if let Some(segment) = segment {
            let offset = last_complete_offset_in_file(&mut file, segment.data_start)?;
            let start = StreamCursor::V2 {
                epoch: segment.epoch.clone(),
                generation: segment.generation,
                offset: segment.data_start,
            };
            let cursor = match requested {
                None | Some(StreamCursor::Legacy(0)) => start,
                Some(cursor) => cursor.clone(),
            };
            return Ok(ConnectionWindow {
                cursor,
                high_watermark: StreamCursor::V2 {
                    epoch: segment.epoch,
                    generation: segment.generation,
                    offset,
                },
            });
        }
        Ok(ConnectionWindow {
            cursor: requested.cloned().unwrap_or(StreamCursor::Legacy(0)),
            high_watermark: StreamCursor::Legacy(last_complete_offset_in_file(&mut file, 0)?),
        })
    }

    /// Bounded generation-aware drain used by the SSE server.
    pub fn read_stream(&self, cursor: &StreamCursor, kinds: &[String]) -> io::Result<StreamBatch> {
        match cursor {
            StreamCursor::Legacy(offset) => self.read_legacy_stream(*offset, kinds),
            StreamCursor::V2 {
                epoch,
                generation,
                offset,
            } => self.read_v2_stream(epoch, *generation, *offset, kinds),
        }
    }

    fn read_legacy_stream(&self, offset: u64, kinds: &[String]) -> io::Result<StreamBatch> {
        let Some((file, segment)) = self.open_active()? else {
            return Ok(StreamBatch {
                items: Vec::new(),
                cursor: StreamCursor::Legacy(offset),
                scanned_events: 0,
                scanned_bytes: 0,
            });
        };
        if let Some(segment) = segment {
            if offset != 0 {
                return self.gap_batch(cursor_string(offset), "legacy_cursor_generation_unknown");
            }
            let start = StreamCursor::V2 {
                epoch: segment.epoch.clone(),
                generation: segment.generation,
                offset: segment.data_start,
            };
            let mut batch = self.read_v2_stream(
                &segment.epoch,
                segment.generation,
                segment.data_start,
                kinds,
            )?;
            batch.items.insert(
                0,
                StreamItem::Boundary(StreamBoundary {
                    from: StreamCursor::Legacy(0),
                    to: start,
                }),
            );
            return Ok(batch);
        }
        let file_len = file.metadata()?.len();
        if offset > file_len {
            return self.gap_batch(cursor_string(offset), "legacy_cursor_beyond_eof");
        }
        let style = CursorStyle::Legacy;
        let drain = drain_file(
            file,
            offset,
            true,
            &style,
            None,
            kinds,
            DrainBudget::default(),
        )?;
        Ok(StreamBatch {
            items: drain.items,
            cursor: style.at(drain.cursor),
            scanned_events: drain.scanned_events,
            scanned_bytes: drain.scanned_bytes,
        })
    }

    fn read_v2_stream(
        &self,
        epoch: &str,
        generation: u64,
        offset: u64,
        kinds: &[String],
    ) -> io::Result<StreamBatch> {
        let requested = StreamCursor::V2 {
            epoch: epoch.to_string(),
            generation,
            offset,
        };
        let segments = self.v1_segments()?;
        let matching: Vec<_> = segments
            .iter()
            .filter(|segment| segment.epoch == epoch && segment.generation == generation)
            .cloned()
            .collect();
        if matching.len() != 1 {
            let reason = if matching.is_empty() {
                "generation_expired_or_unknown"
            } else {
                "ambiguous_generation"
            };
            return self.gap_batch(requested.to_string(), reason);
        }
        let mut current = matching[0].clone();
        if offset < current.data_start || offset > current.len {
            return self.gap_batch(requested.to_string(), "cursor_outside_segment");
        }
        let mut cursor = requested;
        let mut items = Vec::new();
        let mut scanned_events = 0;
        let mut scanned_bytes = 0;

        loop {
            let style = CursorStyle::V2 {
                epoch: current.epoch.clone(),
                generation: current.generation,
            };
            let drain = drain_file(
                self.open_owned_segment(&current)?,
                cursor.offset(),
                current.active,
                &style,
                Some((&current.epoch, current.generation)),
                kinds,
                DrainBudget {
                    events: scanned_events,
                    bytes: scanned_bytes,
                },
            )?;
            items.extend(drain.items);
            scanned_events += drain.scanned_events;
            scanned_bytes += drain.scanned_bytes;
            cursor = style.at(drain.cursor);

            if !drain.at_eof
                || scanned_events >= STREAM_BATCH_MAX_EVENTS
                || scanned_bytes >= STREAM_BATCH_MAX_BYTES
            {
                break;
            }

            let next_generation = current.generation + 1;
            let next: Vec<_> = segments
                .iter()
                .filter(|segment| segment.epoch == epoch && segment.generation == next_generation)
                .cloned()
                .collect();
            if next.is_empty() {
                break;
            }
            if next.len() != 1 {
                return self.gap_batch(cursor.to_string(), "ambiguous_generation");
            }
            let next = next[0].clone();
            let next_cursor = StreamCursor::V2 {
                epoch: next.epoch.clone(),
                generation: next.generation,
                offset: next.data_start,
            };
            items.push(StreamItem::Boundary(StreamBoundary {
                from: cursor,
                to: next_cursor.clone(),
            }));
            cursor = next_cursor;
            current = next;
        }

        Ok(StreamBatch {
            items,
            cursor,
            scanned_events,
            scanned_bytes,
        })
    }

    fn gap_batch(&self, requested: String, reason: &str) -> io::Result<StreamBatch> {
        // Recovery skips to the active high-watermark. Replaying retained
        // effects after an unknown gap would be worse than missing them
        // silently; the explicit gap instructs the client to re-snapshot.
        let resumed_at = self.high_watermark()?;
        Ok(StreamBatch {
            items: vec![StreamItem::Gap(StreamGap {
                requested,
                resumed_at: resumed_at.clone(),
                reason: reason.to_string(),
            })],
            cursor: resumed_at,
            scanned_events: 0,
            scanned_bytes: 0,
        })
    }

    fn v1_segments(&self) -> io::Result<Vec<Segment>> {
        let archive = self
            .path
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join("events_archive");
        let archive_modified = match fs::metadata(&archive) {
            Ok(metadata) => metadata.modified().ok(),
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(error) => return Err(error),
        };
        let caches = ARCHIVE_CACHES.get_or_init(|| Mutex::new(HashMap::new()));
        let cached = {
            let guard = caches
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            guard
                .get(&archive)
                .filter(|cache| cache.modified == archive_modified)
                .map(|cache| cache.segments.clone())
        };
        let mut segments = if let Some(cached) = cached {
            cached
        } else {
            let mut discovered = Vec::new();
            match fs::read_dir(&archive) {
                Ok(entries) => {
                    let canonical_archive = fs::canonicalize(&archive)?;
                    let archive_owner = metadata_owner_uid(&fs::metadata(&canonical_archive)?);
                    for entry in entries.flatten() {
                        let name = entry.file_name();
                        let Some(name) = name.to_str() else {
                            continue;
                        };
                        if !name.starts_with("events-") || !name.ends_with(".jsonl") {
                            continue;
                        }
                        let canonical = match fs::canonicalize(entry.path()) {
                            Ok(path) => path,
                            Err(_) => continue,
                        };
                        if canonical.parent() != Some(canonical_archive.as_path()) {
                            continue;
                        }
                        let expected = fs::metadata(&canonical)?;
                        if !expected.file_type().is_file()
                            || metadata_owner_uid(&expected) != archive_owner
                        {
                            continue;
                        }
                        let mut file = File::open(&canonical)?;
                        validate_opened_archive_file(&file, &expected, archive_owner)?;
                        if let Some(mut segment) = read_v1_segment_from_file(&mut file, false)? {
                            segment.archive_name = Some(PathBuf::from(name));
                            discovered.push(segment);
                        }
                    }
                }
                Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                Err(error) => return Err(error),
            }
            let mut guard = caches
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            guard.insert(
                archive.clone(),
                ArchiveCache {
                    modified: archive_modified,
                    segments: discovered.clone(),
                },
            );
            discovered
        };
        if let Some((_, Some(active))) = self.open_active()? {
            segments.push(active);
        }
        segments.sort_by(|left, right| {
            left.active
                .cmp(&right.active)
                .then(left.modified.cmp(&right.modified))
                .then(left.epoch.cmp(&right.epoch))
                .then(left.generation.cmp(&right.generation))
        });
        Ok(segments)
    }

    fn ordered_unique_segments(&self) -> io::Result<Vec<Segment>> {
        let segments = self.v1_segments()?;
        let mut seen = HashSet::new();
        let mut unique_newest_first = Vec::new();
        for segment in segments.into_iter().rev() {
            if seen.insert((segment.epoch.clone(), segment.generation)) {
                unique_newest_first.push(segment);
            }
        }
        unique_newest_first.reverse();
        Ok(unique_newest_first)
    }

    fn headerless_active(&self) -> io::Result<Option<Segment>> {
        let Some((file, segment)) = self.open_active()? else {
            return Ok(None);
        };
        if segment.is_some() {
            return Ok(None);
        }
        let metadata = file.metadata()?;
        Ok(Some(Segment {
            archive_name: None,
            epoch: String::new(),
            generation: 0,
            data_start: 0,
            len: metadata.len(),
            active: true,
            modified: metadata.modified().unwrap_or(SystemTime::UNIX_EPOCH),
            device: metadata_device(&metadata),
            inode: metadata_inode(&metadata),
            owner_uid: metadata_owner_uid(&metadata),
        }))
    }

    /// Legacy active-file-only reader retained for Rust API compatibility.
    pub fn read_since(&self, since_cursor: u64, kinds: &[String]) -> io::Result<EventBatch> {
        let mut file = match File::open(&self.path) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                return Ok(EventBatch {
                    events: Vec::new(),
                    cursor: since_cursor,
                });
            }
            Err(error) => return Err(error),
        };
        let file_len = file.metadata()?.len();
        let start_cursor = if since_cursor > file_len {
            0
        } else {
            since_cursor
        };
        file.seek(SeekFrom::Start(start_cursor))?;
        let mut reader = BufReader::new(file);
        let mut cursor = start_cursor;
        let mut events = Vec::new();
        let mut line = String::new();
        loop {
            line.clear();
            let read = reader.read_line(&mut line)?;
            if read == 0 {
                break;
            }
            if !line.ends_with('\n') {
                break;
            }
            cursor += u64::try_from(read).unwrap_or(u64::MAX);
            let trimmed = line.trim_end();
            if trimmed.is_empty() {
                continue;
            }
            let Ok(mut event) = serde_json::from_str::<Event>(trimmed) else {
                continue;
            };
            if event.kind == "stream.segment" {
                continue;
            }
            if !kinds.is_empty() && !kinds.iter().any(|kind| kind == &event.kind) {
                continue;
            }
            event.cursor = cursor;
            events.push(event);
        }
        Ok(EventBatch { events, cursor })
    }

    /// Read all retained v1 generations in chronological order.
    ///
    /// Segment identity is deduplicated before reading, so an interrupted
    /// rotation cannot replay a generation twice. Headerless streams retain
    /// the legacy active-file-only behavior.
    pub fn read_all(&self) -> io::Result<EventBatch> {
        let segments = self.ordered_unique_segments()?;
        if segments.is_empty() {
            return self.read_since(0, &[]);
        }
        let mut events = Vec::new();
        let mut cursor = 0;
        for segment in segments {
            let (mut segment_events, end) =
                read_complete_segment_events(self.open_owned_segment(&segment)?, &segment)?;
            events.append(&mut segment_events);
            if segment.active {
                cursor = end;
            }
        }
        Ok(EventBatch { events, cursor })
    }

    /// Read a bounded newest-first tail across retained generations.
    pub fn tail(&self, limit: usize) -> io::Result<Vec<Event>> {
        if limit == 0 {
            return Ok(Vec::new());
        }
        let mut segments = self.ordered_unique_segments()?;
        if segments.is_empty() {
            if let Some(active) = self.headerless_active()? {
                segments.push(active);
            }
        }
        let mut events = Vec::new();
        for segment in segments.iter().rev() {
            let remaining = limit.saturating_sub(events.len());
            if remaining == 0 {
                break;
            }
            events.extend(read_segment_tail(
                self.open_owned_segment(segment)?,
                segment,
                remaining,
            )?);
        }
        Ok(events)
    }
}

fn read_complete_segment_events(
    mut file: File,
    segment: &Segment,
) -> io::Result<(Vec<Event>, u64)> {
    if !segment.epoch.is_empty() {
        let observed = read_segment_header_from_file(&mut file)?;
        if observed
            .as_ref()
            .map(|(epoch, generation, _)| (epoch.as_str(), *generation))
            != Some((segment.epoch.as_str(), segment.generation))
        {
            return Err(io::Error::new(
                io::ErrorKind::Interrupted,
                "event segment changed before full read",
            ));
        }
    }
    file.seek(SeekFrom::Start(segment.data_start))?;
    let mut reader = BufReader::new(file);
    let mut cursor = segment.data_start;
    let mut events = Vec::new();
    loop {
        let line = read_bounded_line(&mut reader)?;
        if line.bytes == 0 || !line.complete {
            break;
        }
        cursor = cursor.saturating_add(u64::try_from(line.bytes).unwrap_or(u64::MAX));
        if line.too_long {
            continue;
        }
        let Ok(mut event) = serde_json::from_slice::<Event>(&line.data) else {
            continue;
        };
        if event.kind == "stream.segment" {
            continue;
        }
        event.cursor = cursor;
        events.push(event);
    }
    Ok((events, cursor))
}

fn read_segment_tail(mut file: File, segment: &Segment, limit: usize) -> io::Result<Vec<Event>> {
    if limit == 0 || segment.len <= segment.data_start {
        return Ok(Vec::new());
    }
    let max_bytes = limit
        .saturating_add(1)
        .saturating_mul(STREAM_LINE_MAX_BYTES.saturating_add(1));
    let max_bytes_u64 = u64::try_from(max_bytes).unwrap_or(u64::MAX);
    let start = segment
        .len
        .saturating_sub(max_bytes_u64)
        .max(segment.data_start);
    let to_read = usize::try_from(segment.len.saturating_sub(start)).unwrap_or(max_bytes);
    file.seek(SeekFrom::Start(start))?;
    let mut raw = vec![0_u8; to_read.min(max_bytes)];
    file.read_exact(&mut raw)?;

    let mut base = start;
    if start > segment.data_start {
        let Some(first_newline) = raw.iter().position(|byte| *byte == b'\n') else {
            return Ok(Vec::new());
        };
        let consumed = first_newline + 1;
        raw.drain(..consumed);
        base = base.saturating_add(u64::try_from(consumed).unwrap_or(u64::MAX));
    }
    if raw.last().copied() != Some(b'\n') {
        if let Some(last_newline) = raw.iter().rposition(|byte| *byte == b'\n') {
            raw.truncate(last_newline + 1);
        } else {
            return Ok(Vec::new());
        }
    }

    let total_len = raw.len();
    raw.pop();
    let mut line_end = base.saturating_add(u64::try_from(total_len).unwrap_or(u64::MAX));
    let mut events = Vec::new();
    for line in raw.split(|byte| *byte == b'\n').rev() {
        let bytes = line.len().saturating_add(1);
        if line.len() <= STREAM_LINE_MAX_BYTES {
            if let Ok(mut event) = serde_json::from_slice::<Event>(line) {
                if event.kind != "stream.segment" {
                    event.cursor = line_end;
                    events.push(event);
                    if events.len() >= limit {
                        break;
                    }
                }
            }
        }
        line_end = line_end.saturating_sub(u64::try_from(bytes).unwrap_or(u64::MAX));
    }
    Ok(events)
}

/*
 * The compatibility reader used to live below `v1_segments`; keeping the
 * generation-aware helpers together above makes it much harder to accidentally
 * regress `read_all` or `tail` back to active-file-only behavior.
 */

fn cursor_string(offset: u64) -> String {
    StreamCursor::Legacy(offset).to_string()
}

fn read_v1_segment_from_file(file: &mut File, active: bool) -> io::Result<Option<Segment>> {
    let metadata = file.metadata()?;
    let len = metadata.len();
    let Some((epoch, generation, data_start)) = read_segment_header_from_file(file)? else {
        return Ok(None);
    };
    Ok(Some(Segment {
        archive_name: None,
        epoch,
        generation,
        data_start,
        len,
        active,
        modified: metadata.modified().unwrap_or(SystemTime::UNIX_EPOCH),
        device: metadata_device(&metadata),
        inode: metadata_inode(&metadata),
        owner_uid: metadata_owner_uid(&metadata),
    }))
}

fn read_segment_header_from_file(file: &mut File) -> io::Result<Option<(String, u64, u64)>> {
    file.seek(SeekFrom::Start(0))?;
    let mut reader = BufReader::new(&mut *file);
    let mut raw = Vec::new();
    let read = reader
        .by_ref()
        .take(u64::try_from(STREAM_LINE_MAX_BYTES + 1).unwrap_or(u64::MAX))
        .read_until(b'\n', &mut raw)?;
    if read == 0 || read > STREAM_LINE_MAX_BYTES || raw.last().copied() != Some(b'\n') {
        return Ok(None);
    }
    Ok(parse_segment_header(&raw)
        .map(|(epoch, generation)| (epoch, generation, u64::try_from(read).unwrap_or(u64::MAX))))
}

fn parse_segment_header(raw: &[u8]) -> Option<(String, u64)> {
    let Ok(value) = serde_json::from_slice::<serde_json::Value>(raw) else {
        return None;
    };
    let payload = value.get("payload").and_then(serde_json::Value::as_object);
    let schema = payload
        .and_then(|object| object.get("schema"))
        .and_then(serde_json::Value::as_str);
    let epoch = payload
        .and_then(|object| object.get("epoch"))
        .and_then(serde_json::Value::as_str);
    let generation = payload
        .and_then(|object| object.get("generation"))
        .and_then(serde_json::Value::as_u64);
    if value.get("kind").and_then(serde_json::Value::as_str) != Some("stream.segment")
        || schema != Some(STREAM_SEGMENT_SCHEMA)
        || epoch.is_none()
        || generation.is_none()
    {
        return None;
    }
    Some((
        epoch.unwrap_or_default().to_string(),
        generation.unwrap_or_default(),
    ))
}

fn drain_file(
    mut file: File,
    start: u64,
    active: bool,
    style: &CursorStyle,
    expected_segment: Option<(&str, u64)>,
    kinds: &[String],
    prior: DrainBudget,
) -> io::Result<Drain> {
    if let Some((expected_epoch, expected_generation)) = expected_segment {
        let observed = read_segment_header_from_file(&mut file)?;
        if observed
            .as_ref()
            .map(|(epoch, generation, _)| (epoch.as_str(), *generation))
            != Some((expected_epoch, expected_generation))
        {
            return Err(io::Error::new(
                io::ErrorKind::Interrupted,
                "event segment rotated before drain",
            ));
        }
    }
    file.seek(SeekFrom::Start(start))?;
    let mut reader = BufReader::new(file);
    let mut cursor = start;
    let mut items = Vec::new();
    let mut scanned_events = 0;
    let mut scanned_bytes = 0;
    let mut at_eof = false;

    while prior.events + scanned_events < STREAM_BATCH_MAX_EVENTS
        && prior.bytes + scanned_bytes < STREAM_BATCH_MAX_BYTES
    {
        let line_start = cursor;
        let line = read_bounded_line(&mut reader)?;
        if line.bytes == 0 {
            at_eof = true;
            break;
        }
        if prior.bytes + scanned_bytes + line.bytes > STREAM_BATCH_MAX_BYTES
            && prior.events + scanned_events > 0
        {
            break;
        }
        if line.too_long {
            cursor = cursor.saturating_add(u64::try_from(line.bytes).unwrap_or(u64::MAX));
            scanned_events += 1;
            scanned_bytes += line.bytes;
            items.push(StreamItem::Gap(StreamGap {
                requested: style.at(line_start).to_string(),
                resumed_at: style.at(cursor),
                reason: "line_too_large".to_string(),
            }));
            continue;
        }
        if !line.complete {
            if active {
                break;
            }
            cursor = cursor.saturating_add(u64::try_from(line.bytes).unwrap_or(u64::MAX));
            scanned_bytes += line.bytes;
            items.push(StreamItem::Gap(StreamGap {
                requested: style.at(line_start).to_string(),
                resumed_at: style.at(cursor),
                reason: "partial_archived_line".to_string(),
            }));
            at_eof = true;
            break;
        }

        cursor = cursor.saturating_add(u64::try_from(line.bytes).unwrap_or(u64::MAX));
        scanned_events += 1;
        scanned_bytes += line.bytes;
        let Ok(mut event) = serde_json::from_slice::<Event>(&line.data) else {
            items.push(StreamItem::Gap(StreamGap {
                requested: style.at(line_start).to_string(),
                resumed_at: style.at(cursor),
                reason: "malformed_event".to_string(),
            }));
            continue;
        };
        if event.kind == "stream.segment" {
            continue;
        }
        if !kinds.is_empty() && !kinds.iter().any(|kind| kind == &event.kind) {
            continue;
        }
        event.cursor = cursor;
        items.push(StreamItem::Event(StreamRecord {
            event,
            cursor: style.at(cursor),
        }));
    }

    Ok(Drain {
        items,
        cursor,
        scanned_events,
        scanned_bytes,
        at_eof,
    })
}

fn read_bounded_line(reader: &mut impl BufRead) -> io::Result<BoundedLine> {
    let mut data = Vec::new();
    let mut bytes = 0;
    let mut complete = false;
    let mut too_long = false;

    loop {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            break;
        }
        let budget = STREAM_LINE_MAX_BYTES + 1 - bytes;
        let visible = available.len().min(budget);
        let newline = available[..visible].iter().position(|byte| *byte == b'\n');
        let take = newline.map_or(visible, |position| position + 1);
        if data.len() + take <= STREAM_LINE_MAX_BYTES {
            data.extend_from_slice(&available[..take]);
        } else {
            too_long = true;
            data.clear();
        }
        reader.consume(take);
        bytes += take;
        if newline.is_some() {
            complete = true;
            break;
        }
        if bytes > STREAM_LINE_MAX_BYTES {
            too_long = true;
            break;
        }
    }
    Ok(BoundedLine {
        data,
        bytes,
        complete,
        too_long,
    })
}

fn last_complete_offset_in_file(file: &mut File, minimum: u64) -> io::Result<u64> {
    let len = file.metadata()?.len();
    if len <= minimum {
        return Ok(minimum.min(len));
    }
    file.seek(SeekFrom::End(-1))?;
    let mut final_byte = [0_u8; 1];
    file.read_exact(&mut final_byte)?;
    if final_byte[0] == b'\n' {
        return Ok(len);
    }

    let mut end = len;
    let mut buffer = vec![0_u8; 8192];
    while end > minimum {
        let start = end.saturating_sub(u64::try_from(buffer.len()).unwrap_or(u64::MAX));
        let start = start.max(minimum);
        let size = usize::try_from(end - start).unwrap_or(buffer.len());
        file.seek(SeekFrom::Start(start))?;
        file.read_exact(&mut buffer[..size])?;
        if let Some(position) = buffer[..size].iter().rposition(|byte| *byte == b'\n') {
            return Ok(start + u64::try_from(position + 1).unwrap_or(u64::MAX));
        }
        end = start;
    }
    Ok(minimum)
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    use super::{
        EventStream, STREAM_BATCH_MAX_BYTES, STREAM_BATCH_MAX_EVENTS, STREAM_SEGMENT_SCHEMA,
        StreamCursor, StreamItem,
    };

    fn temp_dir(label: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!(
            "vc-control-events-{label}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|duration| duration.as_nanos())
                .unwrap_or(0)
        ))
    }

    fn header(epoch: &str, generation: u64) -> String {
        format!(
            r#"{{"ts":"2026-07-26T06:00:00+00:00","run_id":"","kind":"stream.segment","message":"generation","payload":{{"schema":"{STREAM_SEGMENT_SCHEMA}","epoch":"{epoch}","generation":{generation}}}}}"#
        )
    }

    fn event_line(run_id: &str, message: &str) -> String {
        format!(
            r#"{{"ts":"2026-07-26T06:00:00+00:00","run_id":"{run_id}","kind":"state","message":"{message}","payload":{{}}}}"#
        )
    }

    fn write_segment(path: &std::path::Path, epoch: &str, generation: u64, events: &[String]) {
        let mut lines = vec![header(epoch, generation)];
        lines.extend_from_slice(events);
        fs::write(path, format!("{}\n", lines.join("\n"))).expect("write segment");
    }

    fn event_records(batch: &super::StreamBatch) -> Vec<&super::StreamRecord> {
        batch
            .items
            .iter()
            .filter_map(|item| match item {
                StreamItem::Event(record) => Some(record),
                StreamItem::Boundary(_) | StreamItem::Gap(_) => None,
            })
            .collect()
    }

    #[test]
    fn v2_cursor_round_trips() {
        let cursor: StreamCursor = "v2:123e4567-e89b-12d3-a456-426614174000:7:991"
            .parse()
            .expect("parse cursor");
        assert_eq!(
            cursor.to_string(),
            "v2:123e4567-e89b-12d3-a456-426614174000:7:991"
        );
    }

    #[test]
    fn same_generation_resume_has_no_skip_or_duplicate() {
        let dir = temp_dir("same-generation");
        fs::create_dir_all(&dir).expect("mkdir");
        let path = dir.join("events.jsonl");
        write_segment(
            &path,
            "epoch-a",
            0,
            &[
                event_line("run-a", "one"),
                event_line("run-a", "two"),
                event_line("run-a", "three"),
            ],
        );
        let stream = EventStream::new(&path);
        let first = stream
            .read_stream(&stream.start_cursor().expect("start"), &[])
            .expect("first drain");
        let records = event_records(&first);
        assert_eq!(records.len(), 3);
        let middle = records[1].cursor.clone();
        let resumed = stream.read_stream(&middle, &[]).expect("resume");
        let messages: Vec<_> = event_records(&resumed)
            .iter()
            .map(|record| record.event.message.as_str())
            .collect();
        assert_eq!(messages, vec!["three"]);
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn connection_window_start_and_highwater_share_generation() {
        let dir = temp_dir("connection-window");
        fs::create_dir_all(&dir).expect("mkdir");
        let path = dir.join("events.jsonl");
        write_segment(
            &path,
            "epoch-window",
            7,
            &[event_line("window", "baseline")],
        );
        let stream = EventStream::new(&path);

        let window = stream
            .connection_window(Some(&StreamCursor::Legacy(0)))
            .expect("connection window");

        assert!(matches!(
            (&window.cursor, &window.high_watermark),
            (
                StreamCursor::V2 {
                    epoch: start_epoch,
                    generation: 7,
                    ..
                },
                StreamCursor::V2 {
                    epoch: high_epoch,
                    generation: 7,
                    ..
                }
            ) if start_epoch == high_epoch && window.high_watermark.reaches(&window.cursor)
        ));
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn legacy_zero_migrates_to_v2_with_explicit_boundary() {
        let dir = temp_dir("legacy-zero");
        fs::create_dir_all(&dir).expect("mkdir");
        let path = dir.join("events.jsonl");
        write_segment(
            &path,
            "epoch-legacy-zero",
            0,
            &[event_line("legacy-zero", "baseline")],
        );
        let stream = EventStream::new(&path);

        let batch = stream
            .read_stream(&StreamCursor::Legacy(0), &[])
            .expect("legacy zero migration");

        assert!(matches!(
            batch.items.first(),
            Some(StreamItem::Boundary(boundary))
                if boundary.from == StreamCursor::Legacy(0) && boundary.to.is_v2()
        ));
        assert!(batch.cursor.is_v2());
        assert_eq!(event_records(&batch).len(), 1);
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn legacy_nonzero_segmented_emits_generation_unknown_gap() {
        let dir = temp_dir("legacy-nonzero");
        fs::create_dir_all(&dir).expect("mkdir");
        let path = dir.join("events.jsonl");
        write_segment(
            &path,
            "epoch-legacy-nonzero",
            3,
            &[event_line("legacy-nonzero", "must not replay")],
        );
        let stream = EventStream::new(&path);

        let batch = stream
            .read_stream(&StreamCursor::Legacy(42), &[])
            .expect("legacy nonzero gap");

        assert!(matches!(
            batch.items.as_slice(),
            [StreamItem::Gap(gap)]
                if gap.reason == "legacy_cursor_generation_unknown"
                    && gap.requested == "42"
                    && gap.resumed_at.is_v2()
        ));
        assert!(event_records(&batch).is_empty());
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn headerless_legacy_stream_keeps_numeric_cursor() {
        let dir = temp_dir("headerless-legacy");
        fs::create_dir_all(&dir).expect("mkdir");
        let path = dir.join("events.jsonl");
        fs::write(&path, format!("{}\n", event_line("legacy", "one"))).expect("write legacy");
        let stream = EventStream::new(&path);

        let window = stream
            .connection_window(Some(&StreamCursor::Legacy(0)))
            .expect("legacy window");
        let batch = stream
            .read_stream(&window.cursor, &[])
            .expect("legacy drain");

        assert_eq!(window.cursor, StreamCursor::Legacy(0));
        assert!(matches!(window.high_watermark, StreamCursor::Legacy(_)));
        assert!(matches!(batch.cursor, StreamCursor::Legacy(_)));
        assert_eq!(event_records(&batch).len(), 1);
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn archived_generation_bridges_to_larger_new_generation() {
        let dir = temp_dir("archive-bridge");
        let archive = dir.join("events_archive");
        fs::create_dir_all(&archive).expect("mkdir archive");
        let path = dir.join("events.jsonl");
        let old = archive.join("events-epoch-b-g00000000000000000000.jsonl");
        write_segment(&old, "epoch-b", 0, &[event_line("old", "old")]);
        let old_stream = EventStream::new(&old);
        let old_batch = old_stream
            .read_stream(&old_stream.start_cursor().expect("old start"), &[])
            .expect("old drain");
        let saved = old_batch.cursor;
        write_segment(
            &path,
            "epoch-b",
            1,
            &[event_line("new", &"new".repeat(400))],
        );
        assert!(fs::metadata(&path).expect("new metadata").len() > saved.offset());

        let stream = EventStream::new(&path);
        let bridged = stream.read_stream(&saved, &[]).expect("bridge");
        assert!(
            bridged
                .items
                .iter()
                .any(|item| matches!(item, StreamItem::Boundary(_)))
        );
        let records = event_records(&bridged);
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].event.run_id, "new");
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn expired_generation_emits_gap_without_replaying_active_effects() {
        let dir = temp_dir("expired");
        fs::create_dir_all(&dir).expect("mkdir");
        let path = dir.join("events.jsonl");
        write_segment(&path, "epoch-c", 2, &[event_line("new", "do not replay")]);
        let stream = EventStream::new(&path);
        let expired = StreamCursor::V2 {
            epoch: "epoch-c".to_string(),
            generation: 0,
            offset: 100,
        };
        let batch = stream.read_stream(&expired, &[]).expect("gap");
        assert_eq!(batch.items.len(), 1);
        assert!(matches!(batch.items[0], StreamItem::Gap(_)));
        assert!(event_records(&batch).is_empty());
        assert_eq!(
            batch.cursor,
            stream.high_watermark().expect("high watermark")
        );
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn read_all_bridges_archives_in_order_without_duplicates() {
        let dir = temp_dir("read-all-archives");
        let archive = dir.join("events_archive");
        fs::create_dir_all(&archive).expect("mkdir archive");
        let path = dir.join("events.jsonl");
        write_segment(
            &archive.join("events-epoch-all-g00000000000000000000.jsonl"),
            "epoch-all",
            0,
            &[
                event_line("run-all", "archive-one"),
                event_line("run-all", "archive-two"),
            ],
        );
        write_segment(
            &path,
            "epoch-all",
            1,
            &[event_line("run-all", "active-three")],
        );

        let batch = EventStream::new(&path).read_all().expect("read retained");
        let messages: Vec<_> = batch
            .events
            .iter()
            .map(|event| event.message.as_str())
            .collect();

        assert_eq!(
            messages,
            ["archive-one", "archive-two", "active-three"],
            "each retained generation must contribute once in order"
        );
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn tail_bridges_header_only_active_generation() {
        let dir = temp_dir("tail-header-only");
        let archive = dir.join("events_archive");
        fs::create_dir_all(&archive).expect("mkdir archive");
        let path = dir.join("events.jsonl");
        write_segment(
            &archive.join("events-epoch-tail-g00000000000000000000.jsonl"),
            "epoch-tail",
            0,
            &[
                event_line("run-tail", "older"),
                event_line("run-tail", "newest-retained"),
            ],
        );
        write_segment(&path, "epoch-tail", 1, &[]);

        let tail = EventStream::new(&path).tail(2).expect("retained tail");
        let messages: Vec<_> = tail.iter().map(|event| event.message.as_str()).collect();

        assert_eq!(messages, ["newest-retained", "older"]);
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn generation_drain_is_bounded_and_resumable() {
        let dir = temp_dir("batch-bounds");
        fs::create_dir_all(&dir).expect("mkdir");
        let path = dir.join("events.jsonl");
        let events: Vec<_> = (0..200)
            .map(|index| event_line("busy", &format!("event-{index}")))
            .collect();
        write_segment(&path, "epoch-d", 0, &events);
        let stream = EventStream::new(&path);
        let first = stream
            .read_stream(&stream.start_cursor().expect("start"), &[])
            .expect("first bounded batch");
        assert_eq!(first.scanned_events, STREAM_BATCH_MAX_EVENTS);
        assert!(first.scanned_bytes <= STREAM_BATCH_MAX_BYTES);
        assert_eq!(event_records(&first).len(), STREAM_BATCH_MAX_EVENTS);
        let second = stream
            .read_stream(&first.cursor, &[])
            .expect("second bounded batch");
        assert_eq!(event_records(&second).len(), 200 - STREAM_BATCH_MAX_EVENTS);
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn oversized_line_is_a_bounded_explicit_gap() {
        let dir = temp_dir("line-bound");
        fs::create_dir_all(&dir).expect("mkdir");
        let path = dir.join("events.jsonl");
        write_segment(
            &path,
            "epoch-e",
            0,
            &[event_line(
                "oversized",
                &"x".repeat(STREAM_BATCH_MAX_BYTES * 2),
            )],
        );
        let stream = EventStream::new(&path);
        let batch = stream
            .read_stream(&stream.start_cursor().expect("start"), &[])
            .expect("bounded gap");
        assert!(batch.scanned_bytes <= STREAM_BATCH_MAX_BYTES);
        assert!(batch.items.iter().any(|item| {
            matches!(
                item,
                StreamItem::Gap(gap) if gap.reason == "line_too_large"
            )
        }));
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn legacy_cursor_beyond_eof_resets_for_compatibility_api() {
        let dir = temp_dir("legacy-reset");
        fs::create_dir_all(&dir).expect("mkdir");
        let path = dir.join("events.jsonl");
        let old_line = format!("{}\n", event_line("old-run", &"old".repeat(80)));
        fs::write(&path, &old_line).expect("write old stream");
        let stream = EventStream::new(&path);
        let old = stream.read_since(0, &[]).expect("read old stream");
        let new_line = format!("{}\n", event_line("new-run", "after rotation"));
        fs::write(&path, &new_line).expect("replace shorter");
        let recovered = stream
            .read_since(old.cursor, &[])
            .expect("legacy compatibility read");
        assert_eq!(recovered.events.len(), 1);
        assert_eq!(recovered.events[0].run_id, "new-run");
        fs::remove_dir_all(dir).ok();
    }
}
