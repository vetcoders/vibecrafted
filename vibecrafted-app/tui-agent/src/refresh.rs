//! Console reads off the input loop.
//!
//! Loading the projection walks `runs/`, replays `events.jsonl`, probes
//! worker processes, and Mission Control walks every `*.meta.json` under the
//! artifact root; an Observe transcript is a file read or a server request
//! plus its human rendering. Sampled on the operator host, that kind of work
//! filled the gaps between key presses for seconds at a time. Two worker
//! threads now own those reads, each with a single waiting slot: the input
//! loop only asks for them and applies what comes back. The control plane
//! stays the sole owner of the state; this module owns no state of its own
//! beyond the last answer it produced.

use crate::mission_control::MissionControlState;
use crate::polarize::PolarizeIntent;
use crate::state::ControlPlaneState;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

/// Which reads a refresh must perform. Requests merge by union, so an
/// automatic invalidation and an explicit refresh share one pass and neither
/// is dropped.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct RefreshNeeds {
    /// Reload the projection when its on-disk revision moved.
    pub control_plane: bool,
    /// Reload the projection even when the revision did not move.
    pub force_control_plane: bool,
    pub polarize: bool,
    pub mission_control: bool,
}

impl RefreshNeeds {
    pub fn everything() -> Self {
        Self {
            control_plane: true,
            force_control_plane: true,
            polarize: true,
            mission_control: true,
        }
    }

    pub fn is_empty(self) -> bool {
        self == Self::default()
    }

    pub fn merge(self, other: Self) -> Self {
        Self {
            control_plane: self.control_plane || other.control_plane,
            force_control_plane: self.force_control_plane || other.force_control_plane,
            polarize: self.polarize || other.polarize,
            mission_control: self.mission_control || other.mission_control,
        }
    }

    fn touches_mission_control(self) -> bool {
        !self.is_empty()
    }
}

/// The console's side of the hand-off: reads not yet handed to the worker,
/// and which generation the board currently shows.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RefreshState {
    pub pending: RefreshNeeds,
    pub requested_generation: u64,
    pub applied_generation: u64,
}

/// One hand-off to the worker, numbered so its answer can be ordered.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RefreshJob {
    pub generation: u64,
    pub needs: RefreshNeeds,
    pub state_root: PathBuf,
    pub repo: PathBuf,
    pub artifact_root: PathBuf,
}

impl RefreshJob {
    /// Fold a newer request into one still waiting: the newest roots and
    /// generation win, and nothing either request asked for is lost.
    fn absorb(self, newer: RefreshJob) -> RefreshJob {
        RefreshJob {
            generation: self.generation.max(newer.generation),
            needs: self.needs.merge(newer.needs),
            ..newer
        }
    }
}

/// What one pass produced. Absent parts were not re-read in this pass.
#[derive(Debug)]
pub struct RefreshResult {
    pub generation: u64,
    /// The repository the polarize and Mission Control parts were read for.
    pub repo: PathBuf,
    /// `None` when the projection revision did not move and no reload was forced.
    pub control_plane: Option<Result<ControlPlaneState, String>>,
    pub polarize: Option<Vec<PolarizeIntent>>,
    pub mission_control: Option<MissionControlState>,
}

/// The reads a refresh performs. Production uses the canonical loaders; tests
/// substitute a source that can be held in the middle of a scan.
pub trait RefreshSource: Send + 'static {
    fn projection_revision(&mut self, state_root: &Path) -> u64;
    fn control_plane(&mut self, state_root: &Path) -> io::Result<ControlPlaneState>;
    fn polarize(&mut self, repo: &Path) -> Vec<PolarizeIntent>;
    fn mission_control(
        &mut self,
        state: &ControlPlaneState,
        artifact_root: &Path,
        intents: &[PolarizeIntent],
    ) -> MissionControlState;
}

/// The loaders the console used to call inline.
#[derive(Debug, Default, Clone, Copy)]
pub struct CanonicalRefreshSource;

impl RefreshSource for CanonicalRefreshSource {
    fn projection_revision(&mut self, state_root: &Path) -> u64 {
        crate::projection_revision(state_root)
    }

    fn control_plane(&mut self, state_root: &Path) -> io::Result<ControlPlaneState> {
        if let Ok(raw) = std::env::var("VOC_SOURCE_DELAY_MS")
            && let Ok(delay_ms) = raw.parse::<u64>()
            && delay_ms > 0
        {
            thread::sleep(Duration::from_millis(delay_ms));
        }
        ControlPlaneState::load(state_root)
    }

    fn polarize(&mut self, repo: &Path) -> Vec<PolarizeIntent> {
        crate::polarize::current_intents(repo)
    }

    fn mission_control(
        &mut self,
        state: &ControlPlaneState,
        artifact_root: &Path,
        intents: &[PolarizeIntent],
    ) -> MissionControlState {
        MissionControlState::build_with_intents(state, artifact_root, intents)
    }
}

/// The data the console already holds when the worker starts, so a pass that
/// re-reads only one input still rebuilds Mission Control from the rest.
#[derive(Debug, Clone)]
pub struct RefreshSeed {
    pub state: ControlPlaneState,
    pub intents: Vec<PolarizeIntent>,
}

struct Cache {
    state: ControlPlaneState,
    intents: Vec<PolarizeIntent>,
    /// Revision of the projection `state` was loaded from. Unknown at start,
    /// so the first pass that asks for the projection reloads it.
    revision: Option<(PathBuf, u64)>,
}

/// The control-plane worker: however many invalidations arrive during a slow
/// scan, at most one merged pass follows it.
#[derive(Debug)]
pub struct RefreshWorker(SlotWorker<RefreshJob>);

impl RefreshWorker {
    /// Start the worker. `deliver` hands each result to the console and
    /// returns `false` once nobody is listening, which ends the worker.
    pub fn spawn<S, D>(mut source: S, seed: RefreshSeed, mut deliver: D) -> io::Result<Self>
    where
        S: RefreshSource,
        D: FnMut(RefreshResult) -> bool + Send + 'static,
    {
        let mut cache = Cache {
            state: seed.state,
            intents: seed.intents,
            revision: None,
        };
        SlotWorker::spawn("voc-refresh", RefreshJob::absorb, move |job| {
            deliver(execute(&mut source, &mut cache, job))
        })
        .map(Self)
    }

    /// Queue a pass. A request that arrives while another is still waiting is
    /// merged into it; the running pass is never interrupted.
    pub fn submit(&self, job: RefreshJob) {
        self.0.submit(job);
    }

    /// Stop taking work and wait up to `wait` for a pass in progress. A scan
    /// blocked in the filesystem is left to end on its own: exiting the
    /// console must not wait for it, and nobody applies what it returns.
    pub fn shutdown(&mut self, wait: Duration) -> bool {
        self.0.shutdown(wait)
    }
}

/// A transcript read for one Observe selection, numbered so an answer for a
/// selection the operator has already left is recognised and dropped.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TranscriptJob {
    pub generation: u64,
    pub run_id: String,
    pub path: Option<String>,
    pub origin: String,
}

impl TranscriptJob {
    /// Only the newest selection matters: it replaces a request still waiting.
    fn absorb(self, newer: TranscriptJob) -> TranscriptJob {
        let _ = self;
        newer
    }
}

/// A transcript and its human rendering, both produced off the input loop.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoadedTranscript {
    pub raw: String,
    pub human: String,
}

#[derive(Debug)]
pub struct TranscriptResult {
    pub generation: u64,
    pub run_id: String,
    pub body: Result<LoadedTranscript, String>,
}

/// Where a transcript comes from. Production reads the run's transcript file
/// and falls back to the server; tests hold the read at a barrier.
pub trait TranscriptSource: Send + 'static {
    fn read(&mut self, job: &TranscriptJob) -> Result<String, String>;
}

/// The reads the console used to do inline when the Observe selection moved.
#[derive(Debug, Default, Clone, Copy)]
pub struct CanonicalTranscriptSource;

impl TranscriptSource for CanonicalTranscriptSource {
    fn read(&mut self, job: &TranscriptJob) -> Result<String, String> {
        if let Some(path) = &job.path
            && let Ok(body) = std::fs::read_to_string(path)
        {
            return Ok(body);
        }
        crate::observe::fetch_transcript(&job.origin, &job.run_id)
            .map_err(|error| error.to_string())
    }
}

/// Read one transcript and render it for humans.
pub fn load_transcript<S: TranscriptSource>(
    source: &mut S,
    job: TranscriptJob,
) -> TranscriptResult {
    let body = source.read(&job).map(|raw| LoadedTranscript {
        human: crate::run_detail::humanize_transcript(&raw),
        raw,
    });
    TranscriptResult {
        generation: job.generation,
        run_id: job.run_id,
        body,
    }
}

/// The transcript worker: one read at a time, only the newest selection waiting.
#[derive(Debug)]
pub struct TranscriptWorker(SlotWorker<TranscriptJob>);

impl TranscriptWorker {
    pub fn spawn<S, D>(mut source: S, mut deliver: D) -> io::Result<Self>
    where
        S: TranscriptSource,
        D: FnMut(TranscriptResult) -> bool + Send + 'static,
    {
        SlotWorker::spawn("voc-transcript", TranscriptJob::absorb, move |job| {
            deliver(load_transcript(&mut source, job))
        })
        .map(Self)
    }

    pub fn submit(&self, job: TranscriptJob) {
        self.0.submit(job);
    }

    pub fn shutdown(&mut self, wait: Duration) -> bool {
        self.0.shutdown(wait)
    }
}

struct Slot<J> {
    pending: Option<J>,
    shutdown: bool,
}

type Shared<J> = Arc<(Mutex<Slot<J>>, Condvar)>;

fn lock<J>(shared: &Shared<J>) -> MutexGuard<'_, Slot<J>> {
    shared.0.lock().unwrap_or_else(PoisonError::into_inner)
}

/// One thread and one waiting slot. A request that arrives while another is
/// still waiting is folded into it by `absorb`, so nothing queues behind it.
struct SlotWorker<J> {
    shared: Shared<J>,
    absorb: fn(J, J) -> J,
    thread: Option<JoinHandle<()>>,
}

impl<J: Send + 'static> SlotWorker<J> {
    fn spawn<P>(name: &str, absorb: fn(J, J) -> J, mut pass: P) -> io::Result<Self>
    where
        P: FnMut(J) -> bool + Send + 'static,
    {
        let shared: Shared<J> = Arc::new((
            Mutex::new(Slot {
                pending: None,
                shutdown: false,
            }),
            Condvar::new(),
        ));
        let worker_shared = Arc::clone(&shared);
        let thread = thread::Builder::new()
            .name(name.to_string())
            .spawn(move || {
                while let Some(job) = next_job(&worker_shared) {
                    if !pass(job) {
                        break;
                    }
                }
            })?;
        Ok(Self {
            shared,
            absorb,
            thread: Some(thread),
        })
    }

    fn submit(&self, job: J) {
        let mut slot = lock(&self.shared);
        slot.pending = Some(match slot.pending.take() {
            Some(waiting) => (self.absorb)(waiting, job),
            None => job,
        });
        self.shared.1.notify_one();
    }

    fn shutdown(&mut self, wait: Duration) -> bool {
        signal_shutdown(&self.shared);
        let Some(thread) = self.thread.take() else {
            return true;
        };
        let deadline = Instant::now() + wait;
        while !thread.is_finished() {
            if Instant::now() >= deadline {
                return false;
            }
            thread::sleep(Duration::from_millis(5));
        }
        let _ = thread.join();
        true
    }
}

fn signal_shutdown<J>(shared: &Shared<J>) {
    lock(shared).shutdown = true;
    shared.1.notify_all();
}

impl<J> Drop for SlotWorker<J> {
    fn drop(&mut self) {
        signal_shutdown(&self.shared);
    }
}

impl<J> std::fmt::Debug for SlotWorker<J> {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("SlotWorker")
            .field("running", &self.thread.is_some())
            .finish()
    }
}

fn next_job<J>(shared: &Shared<J>) -> Option<J> {
    let mut slot = lock(shared);
    loop {
        if slot.shutdown {
            return None;
        }
        if let Some(job) = slot.pending.take() {
            return Some(job);
        }
        slot = shared.1.wait(slot).unwrap_or_else(PoisonError::into_inner);
    }
}

fn execute<S: RefreshSource>(source: &mut S, cache: &mut Cache, job: RefreshJob) -> RefreshResult {
    let mut result = RefreshResult {
        generation: job.generation,
        repo: job.repo.clone(),
        control_plane: None,
        polarize: None,
        mission_control: None,
    };
    let needs = job.needs;
    if needs.control_plane || needs.force_control_plane {
        // The revision is taken before the load, so a change landing during a
        // slow load still reads as moved on the next pass.
        let revision = (
            job.state_root.clone(),
            source.projection_revision(&job.state_root),
        );
        if needs.force_control_plane || cache.revision.as_ref() != Some(&revision) {
            result.control_plane = Some(match source.control_plane(&job.state_root) {
                Ok(state) => {
                    cache.state = state.clone();
                    cache.revision = Some(revision);
                    Ok(state)
                }
                // The last good projection stays cached, and the revision stays
                // unknown so the next pass tries the load again.
                Err(error) => Err(error.to_string()),
            });
        }
    }
    if needs.polarize {
        let intents = source.polarize(&job.repo);
        cache.intents = intents.clone();
        result.polarize = Some(intents);
    }
    if needs.touches_mission_control() {
        result.mission_control =
            Some(source.mission_control(&cache.state, &job.artifact_root, &cache.intents));
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::RunSnapshot;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::mpsc;

    fn state_with(runs: &[&str]) -> ControlPlaneState {
        let mut state = ControlPlaneState::empty("/tmp/voc-refresh-state");
        state.runs = runs
            .iter()
            .map(|run_id| {
                serde_json::from_value::<RunSnapshot>(serde_json::json!({ "run_id": run_id }))
                    .expect("snapshot")
            })
            .collect();
        state.retained_runs = state.runs.clone();
        state
    }

    fn job(generation: u64, needs: RefreshNeeds) -> RefreshJob {
        RefreshJob {
            generation,
            needs,
            state_root: PathBuf::from("/tmp/voc-refresh-state"),
            repo: PathBuf::from("/tmp/voc-refresh-repo"),
            artifact_root: PathBuf::from("/tmp/voc-refresh-artifacts"),
        }
    }

    fn only_control_plane() -> RefreshNeeds {
        RefreshNeeds {
            control_plane: true,
            ..RefreshNeeds::default()
        }
    }

    #[derive(Default)]
    struct Ledger {
        loads: usize,
        polarize: usize,
        mission_runs: Vec<usize>,
    }

    /// A source whose first projection load can be held at a barrier.
    struct HeldSource {
        ledger: Arc<Mutex<Ledger>>,
        revision: Arc<AtomicU64>,
        entered: Option<mpsc::Sender<()>>,
        release: Option<mpsc::Receiver<()>>,
        loads: Vec<io::Result<ControlPlaneState>>,
    }

    impl RefreshSource for HeldSource {
        fn projection_revision(&mut self, _state_root: &Path) -> u64 {
            self.revision.load(Ordering::SeqCst)
        }

        fn control_plane(&mut self, _state_root: &Path) -> io::Result<ControlPlaneState> {
            self.ledger.lock().unwrap().loads += 1;
            if let (Some(entered), Some(release)) = (self.entered.take(), self.release.take()) {
                entered.send(()).unwrap();
                release.recv().unwrap();
            }
            if self.loads.is_empty() {
                Ok(state_with(&["loaded"]))
            } else {
                self.loads.remove(0)
            }
        }

        fn polarize(&mut self, _repo: &Path) -> Vec<PolarizeIntent> {
            self.ledger.lock().unwrap().polarize += 1;
            Vec::new()
        }

        fn mission_control(
            &mut self,
            state: &ControlPlaneState,
            _artifact_root: &Path,
            _intents: &[PolarizeIntent],
        ) -> MissionControlState {
            self.ledger
                .lock()
                .unwrap()
                .mission_runs
                .push(state.runs.len());
            MissionControlState::default()
        }
    }

    struct Harness {
        worker: RefreshWorker,
        results: mpsc::Receiver<RefreshResult>,
        ledger: Arc<Mutex<Ledger>>,
        revision: Arc<AtomicU64>,
        entered: Option<mpsc::Receiver<()>>,
        release: Option<mpsc::Sender<()>>,
    }

    fn harness(held: bool, loads: Vec<io::Result<ControlPlaneState>>, seed: &[&str]) -> Harness {
        let ledger = Arc::new(Mutex::new(Ledger::default()));
        let revision = Arc::new(AtomicU64::new(1));
        let (entered_tx, entered_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let source = HeldSource {
            ledger: Arc::clone(&ledger),
            revision: Arc::clone(&revision),
            entered: held.then_some(entered_tx),
            release: held.then_some(release_rx),
            loads,
        };
        let (results_tx, results) = mpsc::channel();
        let worker = RefreshWorker::spawn(
            source,
            RefreshSeed {
                state: state_with(seed),
                intents: Vec::new(),
            },
            move |result| results_tx.send(result).is_ok(),
        )
        .expect("worker");
        Harness {
            worker,
            results,
            ledger,
            revision,
            entered: held.then_some(entered_rx),
            release: held.then_some(release_tx),
        }
    }

    const GUARD: Duration = Duration::from_secs(10);

    #[test]
    fn a_waiting_request_absorbs_newer_ones_without_losing_a_need() {
        let waiting = job(1, only_control_plane());
        let newer = RefreshJob {
            repo: PathBuf::from("/tmp/other-repo"),
            ..job(
                3,
                RefreshNeeds {
                    polarize: true,
                    ..RefreshNeeds::default()
                },
            )
        };
        let merged = waiting.absorb(newer);
        assert_eq!(merged.generation, 3);
        assert_eq!(merged.repo, PathBuf::from("/tmp/other-repo"));
        assert!(merged.needs.control_plane && merged.needs.polarize);
        assert!(!merged.needs.force_control_plane);
    }

    #[test]
    fn requests_during_a_held_scan_collapse_into_one_following_pass() {
        let mut harness = harness(true, Vec::new(), &[]);
        harness.worker.submit(job(1, only_control_plane()));
        harness
            .entered
            .as_ref()
            .unwrap()
            .recv_timeout(GUARD)
            .expect("the first pass is inside the scan");

        // Invalidations and an explicit refresh pile up behind the held scan.
        for generation in 2..=6 {
            harness.worker.submit(job(generation, only_control_plane()));
        }
        harness.worker.submit(job(
            7,
            RefreshNeeds {
                force_control_plane: true,
                polarize: true,
                ..RefreshNeeds::default()
            },
        ));
        harness.release.as_ref().unwrap().send(()).unwrap();

        let first = harness.results.recv_timeout(GUARD).expect("first result");
        let second = harness.results.recv_timeout(GUARD).expect("merged result");
        assert_eq!(first.generation, 1);
        assert_eq!(second.generation, 7);
        assert!(second.control_plane.is_some(), "the forced reload was kept");
        assert!(second.polarize.is_some(), "the polarize need was kept");

        assert!(harness.worker.shutdown(GUARD));
        assert!(harness.results.try_recv().is_err(), "no third pass ran");
        let ledger = harness.ledger.lock().unwrap();
        assert_eq!(ledger.loads, 2);
        assert_eq!(ledger.polarize, 1);
    }

    #[test]
    fn an_unmoved_projection_is_not_reloaded_unless_forced() {
        let mut harness = harness(false, Vec::new(), &[]);
        harness.worker.submit(job(1, only_control_plane()));
        let first = harness.results.recv_timeout(GUARD).unwrap();
        assert!(matches!(first.control_plane, Some(Ok(_))));

        harness.worker.submit(job(2, only_control_plane()));
        let unmoved = harness.results.recv_timeout(GUARD).unwrap();
        assert!(unmoved.control_plane.is_none());
        assert!(
            unmoved.mission_control.is_some(),
            "Mission Control still follows the request"
        );

        harness.revision.store(2, Ordering::SeqCst);
        harness.worker.submit(job(3, only_control_plane()));
        assert!(
            harness
                .results
                .recv_timeout(GUARD)
                .unwrap()
                .control_plane
                .is_some()
        );

        harness.worker.submit(job(
            4,
            RefreshNeeds {
                force_control_plane: true,
                ..RefreshNeeds::default()
            },
        ));
        assert!(
            harness
                .results
                .recv_timeout(GUARD)
                .unwrap()
                .control_plane
                .is_some()
        );
        assert!(harness.worker.shutdown(GUARD));
        assert_eq!(harness.ledger.lock().unwrap().loads, 3);
    }

    #[test]
    fn a_failed_load_reports_the_error_and_keeps_building_from_last_good_data() {
        let mut harness = harness(
            false,
            vec![Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "runs/ is unreadable",
            ))],
            &["seeded-a", "seeded-b"],
        );
        harness.worker.submit(job(1, only_control_plane()));
        let failed = harness.results.recv_timeout(GUARD).unwrap();
        match failed.control_plane {
            Some(Err(message)) => assert!(message.contains("unreadable"), "{message}"),
            other => panic!("expected an honest error, got {other:?}"),
        }

        // The revision stayed unknown, so the next ordinary request retries.
        harness.worker.submit(job(2, only_control_plane()));
        assert!(matches!(
            harness.results.recv_timeout(GUARD).unwrap().control_plane,
            Some(Ok(_))
        ));
        assert!(harness.worker.shutdown(GUARD));
        let ledger = harness.ledger.lock().unwrap();
        assert_eq!(ledger.mission_runs, vec![2, 1]);
    }

    #[test]
    fn shutdown_does_not_wait_for_a_scan_blocked_in_the_filesystem() {
        let mut harness = harness(true, Vec::new(), &[]);
        harness.worker.submit(job(1, only_control_plane()));
        harness
            .entered
            .as_ref()
            .unwrap()
            .recv_timeout(GUARD)
            .unwrap();

        assert!(!harness.worker.shutdown(Duration::from_millis(20)));
        harness.release.as_ref().unwrap().send(()).unwrap();
    }
}
