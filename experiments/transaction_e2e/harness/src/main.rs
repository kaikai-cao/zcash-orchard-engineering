//! End-to-end Orchard bundle harness.
//!
//! Modes:
//!   --mode v2  historical pre-NU6.3 shape (BundleVersion::orchard_v2, FixedPostNu6_2)
//!   --mode v3  NU6.3 migration shape: an Orchard V3 bundle (spend + change) and an
//!              Ironwood V3 bundle (output), both on the PostNu6_3 circuit.
//!   --mode f2  Stage F2: constraint-expression accounting. Keygen summarises every
//!              gate expression tree; one proof records the fused `Ast` the
//!              evaluator walks, with the real quotient evaluation re-run
//!              `--reps` times for an isolated per-call figure.
//!   --mode profile  Stage E3: build the Orchard V3 bundle ONCE, then loop
//!              `create_proof` only (no keygen/build/sign/verify in the window).
//!              Prints PROFILE_WINDOW_START/END markers so an ETW session can be
//!              scoped to the prove loop.
//!   --mode lc  Stage F3 · LC: leaf-cache census. One proof, no evaluator
//!              re-runs; counts every `Ast::Poly` materialisation and whether
//!              the position forced a writable buffer or only a read. Also
//!              fingerprints the proof so the probe-on and probe-off runs (same
//!              seed) can be compared bit for bit.
//!   --mode recon  Stage F4: constraint-system census. One proof with the
//!              read-only keygen dump on (gate expressions, selector activation
//!              rows, pre/post-compression fixed columns), plus the price of one
//!              `eval_polynomial` at the real domain size. Same proof fingerprint
//!              as H6/H7, which is how "read-only" is checked.
//!
//! Deliberately lives OUTSIDE the upstream checkout so `D:\Research\orchard-upstream`
//! stays byte-identical to `616a669d`. Consumed via a Cargo path dependency.
//!
//! Stages are timed independently because `Builder::build()` does NOT create the
//! Halo2 proof: it returns a `Bundle<InProgress<Unproven, Unauthorized>, i64>`.

use std::time::Instant;

use incrementalmerkletree::{Hashable, Level};
use orchard::{
    builder::{Builder, BundleType},
    bundle::{BundleVersion, Flags},
    circuit::{OrchardCircuitVersion, ProvingKey, VerifyingKey},
    keys::{FullViewingKey, Scope, SpendingKey},
    note::{ExtractedNoteCommitment, Note, NoteVersion, RandomSeed, Rho},
    tree::{Anchor, MerkleHashOrchard, MerklePath},
    value::NoteValue,
    Address,
};
use rand::{rngs::StdRng, Rng, SeedableRng};

#[cfg(windows)]
mod e3sampler;

// ---------------------------------------------------------------------------
// Stage F4-A.2: 1 ms memory sampling over the proof window
// ---------------------------------------------------------------------------
//
// `PeakWorkingSetSize` is monotone over the process lifetime, and after keygen
// it already sits in the GB range, so a few MiB of change is invisible in it
// (doc 24 §8.3 measured 0.0 MiB for both arms). The only way to get a per-proof
// figure is to sample during the proof. Everything here is reported as
// "1 ms sampled peak", never as "peak".
#[cfg(feature = "f4a2mem")]
mod mem2 {
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;
    use std::time::{Duration, Instant};

    pub struct Window {
        pub ws_start: u64,
        pub private_start: u64,
        pub ws_peak: u64,
        pub private_peak: u64,
        pub peak_ws_kernel: u64,
        pub peak_pagefile_kernel: u64,
        pub samples: u64,
        pub window_ms: f64,
    }

    pub fn run<T>(f: impl FnOnce() -> T) -> (T, Window) {
        let start = super::mem::snapshot();
        let stop = Arc::new(AtomicBool::new(false));
        let flag = stop.clone();
        let sampler = std::thread::spawn(move || {
            let mut ws_peak = 0u64;
            let mut priv_peak = 0u64;
            let mut samples = 0u64;
            // The first sample is taken before the sleep, so a short proof still
            // gets at least one sample inside the window.
            loop {
                let s = super::mem::snapshot();
                ws_peak = ws_peak.max(s.ws);
                priv_peak = priv_peak.max(s.private);
                samples += 1;
                if flag.load(Ordering::Relaxed) {
                    break;
                }
                std::thread::sleep(Duration::from_millis(1));
            }
            (ws_peak, priv_peak, samples)
        });
        let t = Instant::now();
        let out = f();
        let window_ms = t.elapsed().as_secs_f64() * 1e3;
        stop.store(true, Ordering::Relaxed);
        let (ws_peak, priv_peak, samples) = sampler.join().expect("mem sampler");
        let end = super::mem::snapshot();
        (
            out,
            Window {
                ws_start: start.ws,
                private_start: start.private,
                ws_peak: ws_peak.max(start.ws),
                private_peak: priv_peak.max(start.private),
                peak_ws_kernel: end.peak_ws,
                peak_pagefile_kernel: end.peak_pagefile,
                samples,
                window_ms,
            },
        )
    }
}

// ---------------------------------------------------------------------------
// Stage F4-A.2: counting allocator (diagnostic build only)
// ---------------------------------------------------------------------------
//
// Compiled only with `--features f4a2mem`, so the performance build keeps the
// plain `System` allocator and the 22% comparison is untouched (doc 25 §2.2).
#[cfg(feature = "f4a2mem")]
mod counting_alloc {
    use std::alloc::{GlobalAlloc, Layout, System};

    pub struct Counting;

    // `realloc` / `alloc_zeroed` are deliberately not overridden: the default
    // implementations route through `alloc` + `dealloc`, so they are counted
    // exactly once each.
    unsafe impl GlobalAlloc for Counting {
        unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
            let p = unsafe { System.alloc(layout) };
            if !p.is_null() {
                halo2_proofs::f4a2::on_alloc(layout.size());
            }
            p
        }

        unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
            halo2_proofs::f4a2::on_dealloc(layout.size());
            unsafe { System.dealloc(ptr, layout) }
        }
    }
}

#[cfg(feature = "f4a2mem")]
#[global_allocator]
static GLOBAL: counting_alloc::Counting = counting_alloc::Counting;

const MERKLE_DEPTH: usize = 32; // orchard::NOTE_COMMITMENT_TREE_DEPTH

// ---------------------------------------------------------------------------
// Process memory counters (no external crate: raw FFI)
// ---------------------------------------------------------------------------

#[cfg(windows)]
mod mem {
    /// `PROCESS_MEMORY_COUNTERS_EX`: the base counters plus `PrivateUsage`.
    /// Passing `cb = size_of::<Self>()` is what makes Windows fill the tail.
    #[repr(C)]
    #[derive(Default)]
    struct ProcessMemoryCounters {
        cb: u32,
        page_fault_count: u32,
        peak_working_set_size: usize,
        working_set_size: usize,
        quota_peak_paged_pool_usage: usize,
        quota_paged_pool_usage: usize,
        quota_peak_non_paged_pool_usage: usize,
        quota_non_paged_pool_usage: usize,
        pagefile_usage: usize,
        peak_pagefile_usage: usize,
        private_usage: usize,
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn GetCurrentProcess() -> *mut core::ffi::c_void;
    }

    // Not in kernel32 for every link environment: ask for psapi explicitly.
    #[link(name = "psapi")]
    extern "system" {
        fn GetProcessMemoryInfo(
            process: *mut core::ffi::c_void,
            counters: *mut ProcessMemoryCounters,
            cb: u32,
        ) -> i32;
    }

    fn counters() -> Option<ProcessMemoryCounters> {
        let mut c = ProcessMemoryCounters {
            cb: core::mem::size_of::<ProcessMemoryCounters>() as u32,
            ..Default::default()
        };
        // SAFETY: `c` is a correctly sized, writable PROCESS_MEMORY_COUNTERS.
        let ok = unsafe { GetProcessMemoryInfo(GetCurrentProcess(), &mut c, c.cb) };
        (ok != 0).then_some(c)
    }

    /// Current resident working set, in MiB.
    pub fn working_set_mib() -> f64 {
        counters().map_or(0.0, |c| c.working_set_size as f64 / (1024.0 * 1024.0))
    }

    /// Process-lifetime peak working set, in MiB.
    pub fn peak_working_set_mib() -> f64 {
        counters().map_or(0.0, |c| c.peak_working_set_size as f64 / (1024.0 * 1024.0))
    }

    /// One snapshot of everything `GetProcessMemoryInfo` reports, in bytes.
    #[allow(dead_code)] // only the `f4a2mem` build reads it
    #[derive(Clone, Copy, Default)]
    pub struct Snapshot {
        pub ws: u64,
        pub private: u64,
        /// Monotone kernel counters: only usable as an upper bound.
        pub peak_ws: u64,
        pub peak_pagefile: u64,
    }

    #[allow(dead_code)] // only the `f4a2mem` build reads it
    pub fn snapshot() -> Snapshot {
        counters().map_or(Snapshot::default(), |c| Snapshot {
            ws: c.working_set_size as u64,
            private: c.private_usage as u64,
            peak_ws: c.peak_working_set_size as u64,
            peak_pagefile: c.peak_pagefile_usage as u64,
        })
    }
}

#[cfg(not(windows))]
mod mem {
    pub fn working_set_mib() -> f64 {
        0.0
    }
    pub fn peak_working_set_mib() -> f64 {
        0.0
    }

    #[derive(Clone, Copy, Default)]
    pub struct Snapshot {
        pub ws: u64,
        pub private: u64,
        pub peak_ws: u64,
        pub peak_pagefile: u64,
    }

    #[allow(dead_code)] // only the `f4a2mem` build reads it
    pub fn snapshot() -> Snapshot {
        Snapshot::default()
    }
}

// ---------------------------------------------------------------------------
// CPU affinity, set from inside the process
// ---------------------------------------------------------------------------
//
// Pinning from the launcher is brittle on this box (PowerShell 5.1 refuses to
// Start-Process when both HTTP_PROXY and http_proxy are present), and it would
// also pin the launcher, not just the measured work.

#[cfg(windows)]
mod affinity {
    #[link(name = "kernel32")]
    extern "system" {
        fn GetCurrentProcess() -> *mut core::ffi::c_void;
        fn SetProcessAffinityMask(process: *mut core::ffi::c_void, mask: usize) -> i32;
        fn GetProcessAffinityMask(
            process: *mut core::ffi::c_void,
            process_mask: *mut usize,
            system_mask: *mut usize,
        ) -> i32;
    }

    pub fn pin(mask: usize) -> Result<usize, String> {
        // SAFETY: plain Win32 calls with out-params we own.
        unsafe {
            if SetProcessAffinityMask(GetCurrentProcess(), mask) == 0 {
                return Err(format!("SetProcessAffinityMask(0x{mask:x}) failed"));
            }
            let (mut proc_mask, mut sys_mask) = (0usize, 0usize);
            if GetProcessAffinityMask(GetCurrentProcess(), &mut proc_mask, &mut sys_mask) == 0 {
                return Err("GetProcessAffinityMask failed".into());
            }
            if proc_mask != mask {
                return Err(format!(
                    "affinity did not take: asked 0x{mask:x}, got 0x{proc_mask:x} (system 0x{sys_mask:x})"
                ));
            }
            Ok(proc_mask)
        }
    }
}

#[cfg(not(windows))]
mod affinity {
    pub fn pin(_mask: usize) -> Result<usize, String> {
        Err("affinity pinning is only implemented on Windows".into())
    }
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

struct Args {
    mode: String,
    version: String,
    spends: usize,
    outputs: usize,
    reps: usize,
    seed: u64,
    tag: String,
    mask: usize,
    e3_out: String,
    e3_interval_ms: u64,
}

fn parse_args() -> Args {
    let mut a = Args {
        mode: "v2".into(),
        version: "v2".into(),
        spends: 1,
        outputs: 2,
        reps: 3,
        seed: 7,
        tag: String::new(),
        mask: 0,
        e3_out: String::new(),
        e3_interval_ms: 2,
    };
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let mut i = 0;
    while i < argv.len() {
        let key = argv[i].as_str();
        let mut val = || -> String {
            i += 1;
            argv.get(i).cloned().unwrap_or_default()
        };
        match key {
            "--mode" => a.mode = val(),
            "--version" => a.version = val(),
            "--spends" => a.spends = val().parse().expect("--spends"),
            "--outputs" => a.outputs = val().parse().expect("--outputs"),
            "--reps" => a.reps = val().parse().expect("--reps"),
            "--seed" => a.seed = val().parse().expect("--seed"),
            "--mask" => {
                let v = val();
                a.mask = usize::from_str_radix(v.trim_start_matches("0x"), 16)
                    .expect("--mask expects hex, e.g. 0x1");
            }
            "--tag" => a.tag = val(),
            "--e3-out" => a.e3_out = val(),
            "--e3-interval-ms" => a.e3_interval_ms = val().parse().expect("--e3-interval-ms"),
            other => panic!("unknown argument: {other}"),
        }
        i += 1;
    }
    a
}

// ---------------------------------------------------------------------------
// Fixture
// ---------------------------------------------------------------------------

/// A spendable note at leaf 0 of a tree whose other leaves are all empty.
///
/// That is a legitimate tree state (a tree holding exactly one commitment), and the
/// authentication path is simply the empty-subtree root at every level.
fn note_at_leaf_zero(
    fvk: &FullViewingKey,
    value: u64,
    version: NoteVersion,
    rng: &mut StdRng,
) -> (Note, MerklePath, Anchor) {
    let recipient = fvk.address_at(0u32, Scope::External);

    let rho = loop {
        let mut b = [0u8; 32];
        rng.fill_bytes(&mut b);
        if let Some(r) = Option::<Rho>::from(Rho::from_bytes(&b)) {
            break r;
        }
    };

    let note = loop {
        let mut s = [0u8; 32];
        rng.fill_bytes(&mut s);
        if let Some(rseed) = Option::<RandomSeed>::from(RandomSeed::from_bytes(s, &rho)) {
            if let Some(n) = Option::<Note>::from(Note::from_parts(
                recipient,
                NoteValue::from_raw(value),
                rho,
                rseed,
                version,
            )) {
                break n;
            }
        }
    };

    let cmx: ExtractedNoteCommitment = note.commitment().into();
    let auth_path: [MerkleHashOrchard; MERKLE_DEPTH] =
        core::array::from_fn(|l| MerkleHashOrchard::empty_root(Level::from(l as u8)));
    let path = MerklePath::from_parts(0, auth_path);
    let anchor = path.root(cmx);

    (note, path, anchor)
}

fn mean(v: &[f64]) -> f64 {
    v.iter().sum::<f64>() / v.len() as f64
}

/// Bytes → MiB, for the memory lines only.
#[cfg(feature = "f4a2mem")]
fn mib(bytes: u64) -> f64 {
    bytes as f64 / (1024.0 * 1024.0)
}

fn fmt(v: &[f64]) -> String {
    v.iter()
        .map(|x| format!("{x:.6}"))
        .collect::<Vec<_>>()
        .join(",")
}

// ---------------------------------------------------------------------------
// Stage E2: drain the per-proof phase timings from the (patched) halo2_proofs
// crate and print them. Returns nothing when ORCHARD_E2_TIMING is unset, so this
// is safe to call unconditionally on every rep.
// ---------------------------------------------------------------------------
fn emit_e2(pool: &str, rep: usize) {
    let stages = halo2_proofs::plonk::take_e2_timings();
    if stages.is_empty() {
        return;
    }
    let mut parts: Vec<String> = stages
        .into_iter()
        .map(|(k, v)| format!("{k}={v:.6}"))
        .collect();
    parts.sort();
    println!("E2_STAGE pool={pool} rep={rep} {}", parts.join(" "));
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

fn main() {
    // Initialise the hook's flag before the allocator is instrumented: the
    // initialiser allocates and would otherwise be re-entered from the
    // allocator itself (doc 25 §2.2).
    #[cfg(feature = "f4a2mem")]
    let _ = halo2_proofs::f4a2::enabled();

    let args = parse_args();

    // Pin before any rayon pool exists, so every worker inherits the mask.
    let pinned = if args.mask == 0 {
        None
    } else {
        Some(affinity::pin(args.mask).unwrap_or_else(|e| panic!("affinity: {e}")))
    };
    if let Some(m) = pinned {
        eprintln!("pinned to CPU mask 0x{m:x}");
    }

    match args.mode.as_str() {
        "v2" => run_v2(&args, pinned),
        "v3" => run_v3(&args, pinned),
        "profile" => run_profile(&args, pinned),
        "f2" => run_f2(&args, pinned),
        #[cfg(feature = "f3lc")]
        "lc" => run_lc(&args, pinned),
        #[cfg(feature = "f3h7")]
        "h7" => run_h7(&args, pinned),
        #[cfg(feature = "f4")]
        "recon" => run_recon(&args, pinned),
        #[cfg(feature = "f4a")]
        "f4a" => run_f4a(&args, pinned),
        #[cfg(feature = "f4b")]
        "f4b" => run_f4b(&args, pinned),
        #[cfg(feature = "f4aimpl")]
        "f4aimpl" => run_f4aimpl(&args, pinned),
        #[cfg(feature = "f4a2mem")]
        "f4a2mem" => run_f4a2mem(&args, pinned),
        #[cfg(feature = "f4aimpl")]
        "f4a2stage" => run_f4a2stage(&args, pinned),
        other => panic!(
            "unknown --mode {other} (expected v2, v3, profile, f2, lc, h7, recon, f4a, f4b, \
f4aimpl, f4a2mem or f4a2stage)"
        ),
    }
}

// ---------------------------------------------------------------------------
// Mode v2: historical pre-NU6.3 shape
// ---------------------------------------------------------------------------

fn run_v2(args: &Args, pinned: Option<usize>) {
    let mut rng = StdRng::from_seed([args.seed as u8; 32]);

    let (bundle_version, circuit_version) = match args.version.as_str() {
        "v2" => (
            BundleVersion::orchard_v2(),
            OrchardCircuitVersion::FixedPostNu6_2,
        ),
        other => panic!("unsupported --version {other} for --mode v2"),
    };
    let note_version = bundle_version.note_version();
    let flags: Flags = bundle_version.default_flags();

    // ---- stage 0: fixture -------------------------------------------------
    let t_fixture = Instant::now();
    let sk_spend = SpendingKey::from_bytes([args.seed as u8; 32]).expect("spending key");
    let fvk_spend = FullViewingKey::from(&sk_spend);
    let (note, path, anchor) = note_at_leaf_zero(&fvk_spend, 100_000_000, note_version, &mut rng);

    // Payee: a different account, so this is a genuine cross-address transfer
    // (permitted by V2, forbidden by V3).
    let sk_payee = SpendingKey::from_bytes([args.seed as u8 ^ 0xa5; 32]).expect("payee key");
    let fvk_payee = FullViewingKey::from(&sk_payee);
    let payee: Address = fvk_payee.address_at(0u32, Scope::External);
    let fixture_s = t_fixture.elapsed().as_secs_f64();

    // ---- stage 1: keygen --------------------------------------------------
    let t_keygen = Instant::now();
    let pk = ProvingKey::build(circuit_version);
    let keygen_s = t_keygen.elapsed().as_secs_f64();
    let vk: VerifyingKey = pk.verifying_key();
    let ws_after_keygen = mem::working_set_mib();

    let ask = orchard::keys::SpendAuthorizingKey::from(&sk_spend);
    let sighash = [0x11u8; 32];

    let mut build_ms = Vec::new();
    let mut prove_s = Vec::new();
    let mut sign_ms = Vec::new();
    let mut verify_ms = Vec::new();
    let mut proof_bytes = 0usize;
    let mut num_actions = 0usize;
    let mut ws_before_prove = 0.0f64;
    let mut ws_after_prove = 0.0f64;

    for rep in 0..args.reps {
        // ---- stage 2: bundle construction (no proof) ----------------------
        let t_build = Instant::now();

        let mut builder =
            Builder::new(BundleType::DEFAULT, bundle_version, flags, anchor).expect("builder");
        for _ in 0..args.spends {
            builder
                .add_spend(fvk_spend.clone(), note, path.clone())
                .expect("add_spend");
        }
        let per_output = 90_000_000u64 / args.outputs as u64;
        for _ in 0..args.outputs {
            builder
                .add_output(None, payee, NoteValue::from_raw(per_output), [0u8; 512])
                .expect("add_output");
        }
        let (bundle, _meta) = builder
            .build::<i64>(&mut rng)
            .expect("build")
            .expect("bundle is produced");
        build_ms.push(t_build.elapsed().as_secs_f64() * 1e3);
        num_actions = bundle.actions().len();

        // ---- stage 3: Halo2 proof generation ------------------------------
        let ws1 = mem::working_set_mib();
        let t_prove = Instant::now();
        let proven = bundle.create_proof(&pk, &mut rng).expect("create_proof");
        prove_s.push(t_prove.elapsed().as_secs_f64());
        let ws2 = mem::working_set_mib();
        if rep == 0 {
            ws_before_prove = ws1;
            ws_after_prove = ws2;
        }

        // ---- stage 4: signing / finalize ----------------------------------
        let t_sign = Instant::now();
        let authorized = proven
            .apply_signatures(&mut rng, sighash, core::slice::from_ref(&ask))
            .expect("apply_signatures");
        sign_ms.push(t_sign.elapsed().as_secs_f64() * 1e3);

        // ---- stage 5: verification ----------------------------------------
        let t_verify = Instant::now();
        authorized.verify_proof(&vk).expect("verify_proof");
        verify_ms.push(t_verify.elapsed().as_secs_f64() * 1e3);

        proof_bytes = authorized.authorization().proof().as_ref().len();
    }

    let peak_mib = mem::peak_working_set_mib();

    let encoded_bytes = num_actions * 820 + 1 + 8 + 32 + proof_bytes + 64;

    println!("STAGE fixture_s={fixture_s:.6}");
    println!("STAGE keygen_s={keygen_s:.6}");
    println!("STAGE build_ms={}", fmt(&build_ms));
    println!("STAGE prove_s={}", fmt(&prove_s));
    println!("STAGE sign_ms={}", fmt(&sign_ms));
    println!("STAGE verify_ms={}", fmt(&verify_ms));
    println!(
        "RESULT {{\"tag\":\"{}\",\"mode\":\"v2\",\"version\":\"{}\",\"spends\":{},\"outputs\":{},\
\"actions\":{},\"seed\":{},\"reps\":{},\"fixture_s\":{:.6},\"keygen_s\":{:.6},\
\"build_ms_mean\":{:.4},\"prove_s_mean\":{:.6},\"prove_s_min\":{:.6},\"sign_ms_mean\":{:.4},\
\"verify_ms_mean\":{:.4},\"proof_bytes\":{},\"encoded_bytes\":{},\"ws_before_prove_mib\":{:.1},\
\"ws_after_prove_mib\":{:.1},\"peak_ws_mib\":{:.1},\"ws_after_keygen_mib\":{:.1},\
\"rayon_threads\":\"{}\",\"affinity\":\"0x{:x}\"}}",
        args.tag,
        args.version,
        args.spends,
        args.outputs,
        num_actions,
        args.seed,
        args.reps,
        fixture_s,
        keygen_s,
        mean(&build_ms),
        mean(&prove_s),
        prove_s.iter().cloned().fold(f64::INFINITY, f64::min),
        mean(&sign_ms),
        mean(&verify_ms),
        proof_bytes,
        encoded_bytes,
        ws_before_prove,
        ws_after_prove,
        peak_mib,
        ws_after_keygen,
        std::env::var("RAYON_NUM_THREADS").unwrap_or_else(|_| "unset".into()),
        pinned.unwrap_or(0),
    );
}

// ---------------------------------------------------------------------------
// Mode v3: NU6.3 migration shape
// ---------------------------------------------------------------------------
//
// Orchard V3 (cross-address disabled) with 1 real spend + 1 change output gives
// 1 + 1 = 2 actions, NOT max(1,1)=1: when the cross-address check is on, each
// spend and each output gets its own action, paired with a fabricated
// zero-valued counterpart. Ironwood V3 (cross-address permitted) with 1 output
// and UNPADDED gives 1 action. Both use the same PostNu6_3 proving key.

struct PoolRun {
    pool: &'static str,
    bundle_version_label: &'static str,
    actions: usize,
    build_ms: Vec<f64>,
    prove_s: Vec<f64>,
    sign_ms: Vec<f64>,
    verify_ms: Vec<f64>,
    proof_bytes: usize,
    ws_before: f64,
    ws_after: f64,
}

fn run_v3(args: &Args, pinned: Option<usize>) {
    let mut rng = StdRng::from_seed([args.seed as u8; 32]);

    let orchard_bv = BundleVersion::orchard_v3();
    let ironwood_bv = BundleVersion::ironwood_v3();
    assert_eq!(
        orchard_bv.circuit_version(),
        ironwood_bv.circuit_version(),
        "both NU6.3 pools must share the post-NU6.3 circuit"
    );
    let circuit_version = orchard_bv.circuit_version();

    // ---- stage 0: fixture -------------------------------------------------
    let t_fixture = Instant::now();
    let sk = SpendingKey::from_bytes([args.seed as u8; 32]).expect("spending key");
    let fvk = FullViewingKey::from(&sk);
    let (note, path, anchor) = note_at_leaf_zero(&fvk, 100_000_000, NoteVersion::V2, &mut rng);
    // The change address must be the SAME address as the spent note: with the
    // cross-address restriction on, the circuit forces the spend's and the
    // output's (g_d, pk_d) to be equal, and a different diversifier index would
    // already differ in d.
    let own_address: Address = fvk.address_at(0u32, Scope::External);
    let fixture_s = t_fixture.elapsed().as_secs_f64();

    // ---- stage 1: one keygen for both pools -------------------------------
    let t_keygen = Instant::now();
    let pk = ProvingKey::build(circuit_version);
    let keygen_s = t_keygen.elapsed().as_secs_f64();
    let vk: VerifyingKey = pk.verifying_key();

    let ask = orchard::keys::SpendAuthorizingKey::from(&sk);
    let sighash = [0x11u8; 32];

    let mut orchard = PoolRun {
        pool: "orchard",
        bundle_version_label: "orchard_v3",
        actions: 0,
        build_ms: Vec::new(),
        prove_s: Vec::new(),
        sign_ms: Vec::new(),
        verify_ms: Vec::new(),
        proof_bytes: 0,
        ws_before: 0.0,
        ws_after: 0.0,
    };
    let mut ironwood = PoolRun {
        pool: "ironwood",
        bundle_version_label: "ironwood_v3",
        actions: 0,
        build_ms: Vec::new(),
        prove_s: Vec::new(),
        sign_ms: Vec::new(),
        verify_ms: Vec::new(),
        proof_bytes: 0,
        ws_before: 0.0,
        ws_after: 0.0,
    };

    for rep in 0..args.reps {
        // ---------- Orchard V3: 1 spend + 1 change ----------
        let t_build = Instant::now();
        let mut b = Builder::new(
            BundleType::UNPADDED,
            orchard_bv,
            orchard_bv.default_flags(), // cross-address disabled by construction
            anchor,
        )
        .expect("orchard v3 builder");
        b.add_spend(fvk.clone(), note, path.clone())
            .expect("add_spend");
        b.add_change_output(
            fvk.clone(),
            None,
            own_address,
            NoteValue::from_raw(85_000_000),
            [0u8; 512],
        )
        .expect("add_change_output");
        let (bundle, meta) = b.build::<i64>(&mut rng).expect("build").expect("bundle");
        orchard.build_ms.push(t_build.elapsed().as_secs_f64() * 1e3);
        orchard.actions = bundle.actions().len();

        if rep == 0 {
            // Structure assertions are the point of V3-A: an action count alone
            // would not distinguish "spend and change share an action" from
            // "each got its own", and those are different circuits.
            let f = bundle.flags();
            let spend_at: Vec<String> = (0..1)
                .map(|i| match meta.spend_action_index(i) {
                    Some(x) => x.to_string(),
                    None => "none".into(),
                })
                .collect();
            let out_at: Vec<String> = (0..1)
                .map(|i| match meta.output_action_index(i) {
                    Some(x) => x.to_string(),
                    None => "none".into(),
                })
                .collect();
            println!(
                "SHAPE {{\"pool\":\"orchard\",\"bundle_version\":\"{}\",\"flag_byte\":{},\
\"spends_enabled\":{},\"outputs_enabled\":{},\"cross_address_enabled\":{},\
\"value_balance\":{},\"actions\":{},\"spend_action_index\":[{}],\
\"output_action_index\":[{}]}}",
                orchard.bundle_version_label,
                bundle.flag_byte(),
                f.spends_enabled(),
                f.outputs_enabled(),
                f.cross_address_enabled(),
                *bundle.value_balance(),
                bundle.actions().len(),
                spend_at.join(","),
                out_at.join(","),
            );
        }

        let ws1 = mem::working_set_mib();
        let t_prove = Instant::now();
        let proven = bundle.create_proof(&pk, &mut rng).expect("orchard proof");
        orchard.prove_s.push(t_prove.elapsed().as_secs_f64());
        emit_e2("orchard", rep);
        orchard.ws_after = mem::working_set_mib();
        if rep == 0 {
            orchard.ws_before = ws1;
        }

        let t_sign = Instant::now();
        let authorized = proven
            .apply_signatures(&mut rng, sighash, core::slice::from_ref(&ask))
            .expect("orchard signatures");
        orchard.sign_ms.push(t_sign.elapsed().as_secs_f64() * 1e3);

        let t_verify = Instant::now();
        authorized.verify_proof(&vk).expect("orchard verify");
        orchard.verify_ms.push(t_verify.elapsed().as_secs_f64() * 1e3);
        orchard.proof_bytes = authorized.authorization().proof().as_ref().len();

        // ---------- Ironwood V3: 1 output, no real spend ----------
        let t_build = Instant::now();
        let mut b = Builder::new(
            BundleType::UNPADDED,
            ironwood_bv,
            ironwood_bv.default_flags(), // cross-address permitted
            Anchor::empty_tree(),
        )
        .expect("ironwood builder");
        b.add_output(None, own_address, NoteValue::from_raw(10_000_000), [0u8; 512])
            .expect("add_output");
        let (bundle, meta) = b.build::<i64>(&mut rng).expect("build").expect("bundle");
        ironwood.build_ms.push(t_build.elapsed().as_secs_f64() * 1e3);
        ironwood.actions = bundle.actions().len();

        if rep == 0 {
            let f = bundle.flags();
            let out_at: Vec<String> = (0..1)
                .map(|i| match meta.output_action_index(i) {
                    Some(x) => x.to_string(),
                    None => "none".into(),
                })
                .collect();
            println!(
                "SHAPE {{\"pool\":\"ironwood\",\"bundle_version\":\"{}\",\"flag_byte\":{},\
\"spends_enabled\":{},\"outputs_enabled\":{},\"cross_address_enabled\":{},\
\"value_balance\":{},\"actions\":{},\"num_spends\":0,\
\"output_action_index\":[{}]}}",
                ironwood.bundle_version_label,
                bundle.flag_byte(),
                f.spends_enabled(),
                f.outputs_enabled(),
                f.cross_address_enabled(),
                *bundle.value_balance(),
                bundle.actions().len(),
                out_at.join(","),
            );
        }

        let ws1 = mem::working_set_mib();
        let t_prove = Instant::now();
        let proven = bundle.create_proof(&pk, &mut rng).expect("ironwood proof");
        ironwood.prove_s.push(t_prove.elapsed().as_secs_f64());
        emit_e2("ironwood", rep);
        ironwood.ws_after = mem::working_set_mib();
        if rep == 0 {
            ironwood.ws_before = ws1;
        }

        // No real spends: the dummy spend's authorization key is carried in the
        // builder's signing metadata and applied during `prepare`.
        let t_sign = Instant::now();
        let authorized = proven
            .apply_signatures(&mut rng, sighash, &[])
            .expect("ironwood signatures");
        ironwood.sign_ms.push(t_sign.elapsed().as_secs_f64() * 1e3);

        let t_verify = Instant::now();
        authorized.verify_proof(&vk).expect("ironwood verify");
        ironwood.verify_ms.push(t_verify.elapsed().as_secs_f64() * 1e3);
        ironwood.proof_bytes = authorized.authorization().proof().as_ref().len();
    }

    let peak_mib = mem::peak_working_set_mib();

    for r in [&orchard, &ironwood] {
        // Bundle encoding for Orchard-layout bundles; the v6/Ironwood encoding is
        // not implemented in this crate, so we report proof size only for it.
        let encoded = if r.pool == "orchard" {
            format!("\"encoded_bytes\":{},", r.actions * 820 + 1 + 8 + 32 + r.proof_bytes + 64)
        } else {
            String::new()
        };
        println!(
            "BUNDLE {{\"tag\":\"{}\",\"pool\":\"{}\",\"bundle_version\":\"{}\",\
\"circuit\":\"PostNu6_3\",\"actions\":{},\"reps\":{},\"build_ms_mean\":{:.4},\
\"prove_s_mean\":{:.6},\"prove_s_min\":{:.6},\"prove_s\":[{}],\"sign_ms_mean\":{:.4},\
\"verify_ms_mean\":{:.4},\"proof_bytes\":{},{} \"ws_before_mib\":{:.1},\
\"ws_after_mib\":{:.1},\"build_ms\":[{}],\"sign_ms\":[{}],\"verify_ms\":[{}]}}",
            args.tag,
            r.pool,
            r.bundle_version_label,
            r.actions,
            r.prove_s.len(),
            mean(&r.build_ms),
            mean(&r.prove_s),
            r.prove_s.iter().cloned().fold(f64::INFINITY, f64::min),
            fmt(&r.prove_s),
            mean(&r.sign_ms),
            mean(&r.verify_ms),
            r.proof_bytes,
            encoded,
            r.ws_before,
            r.ws_after,
            fmt(&r.build_ms),
            fmt(&r.sign_ms),
            fmt(&r.verify_ms),
        );
    }

    let orchard_prove = mean(&orchard.prove_s);
    let ironwood_prove = mean(&ironwood.prove_s);

    println!(
        "RESULT {{\"tag\":\"{}\",\"mode\":\"v3\",\"seed\":{},\"reps\":{},\"fixture_s\":{:.6},\
\"keygen_s\":{:.6},\"orchard_actions\":{},\"ironwood_actions\":{},\"total_actions\":{},\
\"orchard_prove_s\":{:.6},\"ironwood_prove_s\":{:.6},\"prove_sum_s\":{:.6},\
\"orchard_verify_ms\":{:.4},\"ironwood_verify_ms\":{:.4},\
\"orchard_proof_bytes\":{},\"ironwood_proof_bytes\":{},\
\"orchard_encoded_bytes\":{},\"peak_ws_mib\":{:.1},\
\"rayon_threads\":\"{}\",\"affinity\":\"0x{:x}\"}}",
        args.tag,
        args.seed,
        args.reps,
        fixture_s,
        keygen_s,
        orchard.actions,
        ironwood.actions,
        orchard.actions + ironwood.actions,
        orchard_prove,
        ironwood_prove,
        orchard_prove + ironwood_prove,
        mean(&orchard.verify_ms),
        mean(&ironwood.verify_ms),
        orchard.proof_bytes,
        ironwood.proof_bytes,
        orchard.actions * 820 + 1 + 8 + 32 + orchard.proof_bytes + 64,
        peak_mib,
        std::env::var("RAYON_NUM_THREADS").unwrap_or_else(|_| "unset".into()),
        pinned.unwrap_or(0),
    );
}

// ---------------------------------------------------------------------------
// Mode profile: isolate Orchard create_proof for CPU sampling (Stage E3)
// ---------------------------------------------------------------------------
//
// Mirrors the PostNu6_3 / 2-action shape of v3, but BUILDS the bundle once and
// then loops `create_proof` only. Keygen, Builder, signing and verification are
// all performed outside the sampling window — we print PROFILE_WINDOW_START /
// PROFILE_WINDOW_END markers so the ETW session can be scoped to the prove loop.
// No E2 timers, no signing, no verify: this mode exists purely to put the
// prover's CPU cost under a sampler.

// ---------------------------------------------------------------------------
// Mode f2: constraint-expression accounting + isolated evaluator timing
// ---------------------------------------------------------------------------

/// Stage F2. Answers "why does walking the constraint expression produce millions
/// of field multiplications?" with counts rather than samples.
///
/// Three numbers come out of one run:
///
/// 1. `cs` — every gate expression summarised: node counts by variant, depth
///    distribution, repeated subtrees, leaf reuse. Captured at keygen, when the
///    `ConstraintSystem` is finalised.
/// 2. `evals` — the *fused* tree the prover really walks (gates + permutation +
///    lookup + `distribute_powers`), with the exact chunk geometry. Field-op
///    totals follow from `nodes x polynomial length`.
/// 3. `bench_*` — the real quotient evaluation re-run `--reps` times inside the
///    evaluator, so its cost is isolated from FFT / vanishing division / MSM.
///
/// The env var has to be set before keygen: `f2::enabled()` caches on first use,
/// and the first use is the keygen hook.
fn run_f2(args: &Args, pinned: Option<usize>) {
    std::env::set_var("ORCHARD_F2", "1");
    std::env::set_var("ORCHARD_F2_BENCH", args.reps.to_string());
    // Also arm the E2 phase timers: with `--reps 0` this yields, in the SAME
    // process, both the isolated evaluator time and the quotient stage it sits
    // in, so their ratio is immune to the ~7% drift seen between batches.
    std::env::set_var("ORCHARD_E2_TIMING", "1");

    let mut rng = StdRng::from_seed([args.seed as u8; 32]);
    let orchard_bv = BundleVersion::orchard_v3();
    let circuit_version = orchard_bv.circuit_version();

    let sk = SpendingKey::from_bytes([args.seed as u8; 32]).expect("spending key");
    let fvk = FullViewingKey::from(&sk);
    let (note, path, anchor) = note_at_leaf_zero(&fvk, 100_000_000, NoteVersion::V2, &mut rng);
    let own_address: Address = fvk.address_at(0u32, Scope::External);

    let t_keygen = Instant::now();
    let pk = ProvingKey::build(circuit_version);
    let keygen_s = t_keygen.elapsed().as_secs_f64();

    let mut b = Builder::new(
        BundleType::UNPADDED,
        orchard_bv,
        orchard_bv.default_flags(),
        anchor,
    )
    .expect("orchard v3 builder");
    b.add_spend(fvk.clone(), note, path.clone()).expect("add_spend");
    b.add_change_output(
        fvk.clone(),
        None,
        own_address,
        NoteValue::from_raw(85_000_000),
        [0u8; 512],
    )
    .expect("add_change_output");
    let (bundle, _meta) = b.build::<i64>(&mut rng).expect("build").expect("bundle");

    // One proof: the point is the accounting, not a timing distribution.
    let actions = bundle.actions().len();
    let t_proof = Instant::now();
    let _ = bundle.create_proof(&pk, &mut rng).expect("f2 proof");
    let proof_s = t_proof.elapsed().as_secs_f64();
    emit_e2("f2", 0);

    halo2_proofs::f2::flush_evals();
    halo2_proofs::f2::flush_evals();
    println!("F2_REPORT {}", halo2_proofs::f2::take_report());
    println!(
        "F2_META {{\"mode\":\"f2\",\"circuit\":\"PostNu6_3\",\"actions\":{},\
\"keygen_s\":{:.6},\"proof_s\":{:.6},\"bench_reps\":{},\"rayon_threads\":\"{}\",\
\"affinity\":\"0x{:x}\"}}",
        actions,
        keygen_s,
        proof_s,
        args.reps,
        std::env::var("RAYON_NUM_THREADS").unwrap_or_else(|_| "unset".into()),
        pinned.unwrap_or(0),
    );
}

// ---------------------------------------------------------------------------
// Mode lc: Stage F3 leaf-cache census
// ---------------------------------------------------------------------------

/// FNV-1a, 64 bit. Used only to fingerprint the proof bytes so a run with the
/// probe on and a run with it off can be compared without storing the proofs.
#[cfg(any(
    feature = "f3lc",
    feature = "f3h7",
    feature = "f4",
    feature = "f4a",
    feature = "f4b",
    feature = "f4aimpl"
))]
fn fnv1a(bytes: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in bytes {
        h ^= *b as u64;
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    h
}

/// Stage F3 · LC. Counts leaf materialisations, not time: the census must
/// describe the tree exactly as the prover walks it, so unlike `--mode f2` this
/// never re-runs the evaluator (`ORCHARD_F2_BENCH=0`).
///
/// The proof is computed with the probe on and off from the same seed; the two
/// `proof_fnv` values must match, which is a stronger statement than "the proof
/// verifies" -- it says the probe changed no field element anywhere.
#[cfg(feature = "f3lc")]
fn run_lc(args: &Args, pinned: Option<usize>) {
    // `ORCHARD_F3_LC=0` runs the same binary with the probe off, for the
    // byte-identity control. Absent -> on.
    if std::env::var_os("ORCHARD_F3_LC").is_none() {
        std::env::set_var("ORCHARD_F3_LC", "1");
    }
    std::env::set_var("ORCHARD_F3_LC_BENCH", args.reps.to_string());
    // F2 accounting rides along with its re-runs disabled: its `ast` counts are
    // an independent second walk of the same tree, so `visits == ast.poly` is a
    // real cross-check rather than the same counter printed twice.
    std::env::set_var("ORCHARD_F2", "1");
    std::env::set_var("ORCHARD_F2_BENCH", "0");

    let mut rng = StdRng::from_seed([args.seed as u8; 32]);
    let orchard_bv = BundleVersion::orchard_v3();
    let circuit_version = orchard_bv.circuit_version();

    let sk = SpendingKey::from_bytes([args.seed as u8; 32]).expect("spending key");
    let fvk = FullViewingKey::from(&sk);
    let (note, path, anchor) = note_at_leaf_zero(&fvk, 100_000_000, NoteVersion::V2, &mut rng);
    let own_address: Address = fvk.address_at(0u32, Scope::External);

    let pk = ProvingKey::build(circuit_version);
    let vk = pk.verifying_key();

    let mut b = Builder::new(
        BundleType::UNPADDED,
        orchard_bv,
        orchard_bv.default_flags(),
        anchor,
    )
    .expect("orchard v3 builder");
    b.add_spend(fvk.clone(), note, path.clone()).expect("add_spend");
    b.add_change_output(
        fvk.clone(),
        None,
        own_address,
        NoteValue::from_raw(85_000_000),
        [0u8; 512],
    )
    .expect("add_change_output");
    let (bundle, _meta) = b.build::<i64>(&mut rng).expect("build").expect("bundle");
    let actions = bundle.actions().len();

    let t_proof = Instant::now();
    let proven = bundle.create_proof(&pk, &mut rng).expect("lc proof");
    let proof_ms = t_proof.elapsed().as_secs_f64() * 1e3;

    let ask = orchard::keys::SpendAuthorizingKey::from(&sk);
    let sighash = [0x11u8; 32];
    let authorized = proven
        .apply_signatures(&mut rng, sighash, core::slice::from_ref(&ask))
        .expect("lc signatures");
    let proof = authorized.authorization().proof();
    let proof_bytes = proof.as_ref().len();
    let proof_fnv = fnv1a(proof.as_ref());

    let t_verify = Instant::now();
    authorized.verify_proof(&vk).expect("lc verify");
    let verify_ms = t_verify.elapsed().as_secs_f64() * 1e3;

    halo2_proofs::f2::flush_evals();
    let f2 = halo2_proofs::f2::take_report();
    println!("F2_REPORT {f2}");
    println!("LC_REPORT {}", halo2_proofs::f3lc::take_report());
    println!(
        "LC_META {{\"mode\":\"lc\",\"circuit\":\"PostNu6_3\",\"actions\":{},\
\"proof_ms\":{:.6},\"verify_ms\":{:.6},\"proof_bytes\":{},\"proof_fnv\":\"0x{:016x}\",\
\"probe\":{},\"bench_reps\":{},\"rayon_threads\":\"{}\",\"affinity\":\"0x{:x}\"}}",
        actions,
        proof_ms,
        verify_ms,
        proof_bytes,
        proof_fnv,
        halo2_proofs::f3lc::enabled(),
        args.reps,
        std::env::var("RAYON_NUM_THREADS").unwrap_or_else(|_| "unset".into()),
        pinned.unwrap_or(0),
    );
}

/// Stage F3 / H7: one real Orchard V3 proof with the ownership counters on.
///
/// This mode exists for D3 (mechanism) and D4 (formulation price) only; no
/// timing is compared from it. `ORCHARD_H7=0` runs the same binary with the
/// counters off, which checks that counting changes no field element: the proof
/// fingerprint must be identical either way.
#[cfg(feature = "f3h7")]
fn run_h7(args: &Args, pinned: Option<usize>) {
    if std::env::var_os("ORCHARD_H7").is_none() {
        std::env::set_var("ORCHARD_H7", "1");
    }
    std::env::set_var("ORCHARD_H7_BENCH", args.reps.to_string());
    // F2's `ast.poly` count is an independent second walk of the same tree, so
    // `leaf_visits == ast.poly` is a real cross-check and not the same counter
    // printed twice. Its own re-runs stay off.
    std::env::set_var("ORCHARD_F2", "1");
    std::env::set_var("ORCHARD_F2_BENCH", "0");

    let mut rng = StdRng::from_seed([args.seed as u8; 32]);
    let orchard_bv = BundleVersion::orchard_v3();
    let circuit_version = orchard_bv.circuit_version();

    let sk = SpendingKey::from_bytes([args.seed as u8; 32]).expect("spending key");
    let fvk = FullViewingKey::from(&sk);
    let (note, path, anchor) = note_at_leaf_zero(&fvk, 100_000_000, NoteVersion::V2, &mut rng);
    let own_address: Address = fvk.address_at(0u32, Scope::External);

    let pk = ProvingKey::build(circuit_version);
    let vk = pk.verifying_key();

    let mut b = Builder::new(
        BundleType::UNPADDED,
        orchard_bv,
        orchard_bv.default_flags(),
        anchor,
    )
    .expect("orchard v3 builder");
    b.add_spend(fvk.clone(), note, path.clone()).expect("add_spend");
    b.add_change_output(
        fvk.clone(),
        None,
        own_address,
        NoteValue::from_raw(85_000_000),
        [0u8; 512],
    )
    .expect("add_change_output");
    let (bundle, _meta) = b.build::<i64>(&mut rng).expect("build").expect("bundle");
    let actions = bundle.actions().len();

    let t_proof = Instant::now();
    let proven = bundle.create_proof(&pk, &mut rng).expect("h7 proof");
    let proof_ms = t_proof.elapsed().as_secs_f64() * 1e3;

    let ask = orchard::keys::SpendAuthorizingKey::from(&sk);
    let sighash = [0x11u8; 32];
    let authorized = proven
        .apply_signatures(&mut rng, sighash, core::slice::from_ref(&ask))
        .expect("h7 signatures");
    let proof = authorized.authorization().proof();
    let proof_bytes = proof.as_ref().len();
    let proof_fnv = fnv1a(proof.as_ref());

    let t_verify = Instant::now();
    authorized.verify_proof(&vk).expect("h7 verify");
    let verify_ms = t_verify.elapsed().as_secs_f64() * 1e3;

    halo2_proofs::f2::flush_evals();
    halo2_proofs::f2::flush_evals();
    println!("F2_REPORT {}", halo2_proofs::f2::take_report());
    halo2_proofs::f3h7::flush_evals();
    println!("H7_REPORT {}", halo2_proofs::f3h7::take_report());
    println!(
        "H7_META {{\"mode\":\"h7\",\"circuit\":\"PostNu6_3\",\"actions\":{},\
\"proof_ms\":{:.6},\"verify_ms\":{:.6},\"proof_bytes\":{},\"proof_fnv\":\"0x{:016x}\",\"probe\":{},\"bench_reps\":{},\"rayon_threads\":\"{}\",\"affinity\":\"0x{:x}\"}}",
        actions,
        proof_ms,
        verify_ms,
        proof_bytes,
        proof_fnv,
        halo2_proofs::f3h7::enabled(),
        args.reps,
        std::env::var("RAYON_NUM_THREADS").unwrap_or_else(|_| "unset".into()),
        pinned.unwrap_or(0),
    );
}

/// Stage F4: one real Orchard V3 proof with the constraint-system census on.
///
/// The census is read-only — it walks the `ConstraintSystem` and the selector
/// activation matrix that keygen has already built — so the proof fingerprint
/// must equal the H6/H7 record. `ORCHARD_F4=0` runs the same binary with the
/// census off, which checks "read-only" against bytes instead of against a
/// verification that would also pass with a bug.
///
/// F2 is armed as well: its fused-tree totals are the cross-check for the
/// arithmetic counted here gate by gate.
#[cfg(feature = "f4")]
fn run_recon(args: &Args, pinned: Option<usize>) {
    if std::env::var_os("ORCHARD_F4").is_none() {
        std::env::set_var("ORCHARD_F4", "1");
    }
    std::env::set_var("ORCHARD_F4_BENCH", args.reps.to_string());
    std::env::set_var("ORCHARD_F2", "1");
    std::env::set_var("ORCHARD_F2_BENCH", "0");

    let mut rng = StdRng::from_seed([args.seed as u8; 32]);
    let orchard_bv = BundleVersion::orchard_v3();
    let circuit_version = orchard_bv.circuit_version();

    let sk = SpendingKey::from_bytes([args.seed as u8; 32]).expect("spending key");
    let fvk = FullViewingKey::from(&sk);
    let (note, path, anchor) = note_at_leaf_zero(&fvk, 100_000_000, NoteVersion::V2, &mut rng);
    let own_address: Address = fvk.address_at(0u32, Scope::External);

    let pk = ProvingKey::build(circuit_version);
    let vk = pk.verifying_key();

    let mut b = Builder::new(
        BundleType::UNPADDED,
        orchard_bv,
        orchard_bv.default_flags(),
        anchor,
    )
    .expect("orchard v3 builder");
    b.add_spend(fvk.clone(), note, path.clone()).expect("add_spend");
    b.add_change_output(
        fvk.clone(),
        None,
        own_address,
        NoteValue::from_raw(85_000_000),
        [0u8; 512],
    )
    .expect("add_change_output");
    let (bundle, _meta) = b.build::<i64>(&mut rng).expect("build").expect("bundle");
    let actions = bundle.actions().len();

    let t_proof = Instant::now();
    let proven = bundle.create_proof(&pk, &mut rng).expect("f4 proof");
    let proof_ms = t_proof.elapsed().as_secs_f64() * 1e3;

    let ask = orchard::keys::SpendAuthorizingKey::from(&sk);
    let sighash = [0x11u8; 32];
    let authorized = proven
        .apply_signatures(&mut rng, sighash, core::slice::from_ref(&ask))
        .expect("f4 signatures");
    let proof = authorized.authorization().proof();
    let proof_bytes = proof.as_ref().len();
    let proof_fnv = fnv1a(proof.as_ref());

    let t_verify = Instant::now();
    authorized.verify_proof(&vk).expect("f4 verify");
    let verify_ms = t_verify.elapsed().as_secs_f64() * 1e3;

    // Price one `eval_polynomial` on the real domain (k = 11 -> 2048 points),
    // i.e. what one more fixed-column query would add to the opening argument.
    halo2_proofs::f4::bench_eval_poly::<halo2_proofs::pasta::pallas::Base>(1 << 11);

    halo2_proofs::f2::flush_evals();
    halo2_proofs::f2::flush_evals();
    println!("F2_REPORT {}", halo2_proofs::f2::take_report());
    println!(
        "F4_META {{\"mode\":\"recon\",\"circuit\":\"PostNu6_3\",\"actions\":{},\
\"proof_ms\":{:.6},\"verify_ms\":{:.6},\"proof_bytes\":{},\"proof_fnv\":\"0x{:016x}\",\
\"probe\":{},\"bench_reps\":{},\"rayon_threads\":\"{}\",\"affinity\":\"0x{:x}\"}}",
        actions,
        proof_ms,
        verify_ms,
        proof_bytes,
        proof_fnv,
        halo2_proofs::f4::enabled(),
        args.reps,
        std::env::var("RAYON_NUM_THREADS").unwrap_or_else(|_| "unset".into()),
        pinned.unwrap_or(0),
    );
}

/// F4-A feasibility probe (`--mode f4a`, needs `--features f4a`).
///
/// Same fixture as `--mode recon`: same seed, same V3 bundle, one prove, one
/// verify. The census prints `F4A_CHAIN` / `F4A_OCC` / `F4A_CS` from inside
/// keygen, and two kernels are priced at the two geometries that matter
/// (`recurse`'s 4096-element chunk and the full 16384-element polynomial).
///
/// `ORCHARD_F4A=0` runs the same binary with the probe off: if the proof bytes
/// and fingerprint match, the probe did not change a value.
#[cfg(feature = "f4a")]
fn run_f4a(args: &Args, pinned: Option<usize>) {
    if std::env::var_os("ORCHARD_F4A").is_none() {
        std::env::set_var("ORCHARD_F4A", "1");
    }
    std::env::set_var("ORCHARD_F4A_BENCH", args.reps.to_string());

    let mut rng = StdRng::from_seed([args.seed as u8; 32]);
    let orchard_bv = BundleVersion::orchard_v3();
    let circuit_version = orchard_bv.circuit_version();

    let sk = SpendingKey::from_bytes([args.seed as u8; 32]).expect("spending key");
    let fvk = FullViewingKey::from(&sk);
    let (note, path, anchor) = note_at_leaf_zero(&fvk, 100_000_000, NoteVersion::V2, &mut rng);
    let own_address: Address = fvk.address_at(0u32, Scope::External);

    let pk = ProvingKey::build(circuit_version);
    let vk = pk.verifying_key();

    let mut b = Builder::new(
        BundleType::UNPADDED,
        orchard_bv,
        orchard_bv.default_flags(),
        anchor,
    )
    .expect("orchard v3 builder");
    b.add_spend(fvk.clone(), note, path.clone()).expect("add_spend");
    b.add_change_output(
        fvk.clone(),
        None,
        own_address,
        NoteValue::from_raw(85_000_000),
        [0u8; 512],
    )
    .expect("add_change_output");
    let (bundle, _meta) = b.build::<i64>(&mut rng).expect("build").expect("bundle");
    let actions = bundle.actions().len();

    let t_proof = Instant::now();
    let proven = bundle.create_proof(&pk, &mut rng).expect("f4a proof");
    let proof_ms = t_proof.elapsed().as_secs_f64() * 1e3;

    let ask = orchard::keys::SpendAuthorizingKey::from(&sk);
    let sighash = [0x11u8; 32];
    let authorized = proven
        .apply_signatures(&mut rng, sighash, core::slice::from_ref(&ask))
        .expect("f4a signatures");
    let proof = authorized.authorization().proof();
    let proof_bytes = proof.as_ref().len();
    let proof_fnv = fnv1a(proof.as_ref());

    let t_verify = Instant::now();
    authorized.verify_proof(&vk).expect("f4a verify");
    let verify_ms = t_verify.elapsed().as_secs_f64() * 1e3;

    // The two geometries: the chunk `recurse` actually works on, and one whole
    // quotient polynomial. Prices are only valid per geometry -- H7's lesson.
    halo2_proofs::f4a::bench_kernels::<halo2_proofs::pasta::pallas::Base>(1 << 12, args.reps);
    halo2_proofs::f4a::bench_kernels::<halo2_proofs::pasta::pallas::Base>(1 << 14, args.reps);

    println!(
        "F4A_META {{\"mode\":\"f4a\",\"circuit\":\"PostNu6_3\",\"actions\":{},\
\"proof_ms\":{:.6},\"verify_ms\":{:.6},\"proof_bytes\":{},\"proof_fnv\":\"0x{:016x}\",\
\"probe\":{},\"bench_reps\":{},\"rayon_threads\":\"{}\",\"affinity\":\"0x{:x}\"}}",
        actions,
        proof_ms,
        verify_ms,
        proof_bytes,
        proof_fnv,
        halo2_proofs::f4a::enabled(),
        args.reps,
        std::env::var("RAYON_NUM_THREADS").unwrap_or_else(|_| "unset".into()),
        pinned.unwrap_or(0),
    );
}

/// Stage F4-A · implementation probe. Proves one V3 bundle exactly like
/// `--mode f4a` so the probe-on/off runs can be compared byte for byte, then
/// prices the three bookkeeping mechanisms and the per-chunk model.
#[cfg(feature = "f4b")]
fn run_f4b(args: &Args, pinned: Option<usize>) {
    if std::env::var_os("ORCHARD_F4B").is_none() {
        std::env::set_var("ORCHARD_F4B", "1");
    }
    std::env::set_var("ORCHARD_F4B_BENCH", args.reps.to_string());

    let mut rng = StdRng::from_seed([args.seed as u8; 32]);
    let orchard_bv = BundleVersion::orchard_v3();
    let circuit_version = orchard_bv.circuit_version();

    let sk = SpendingKey::from_bytes([args.seed as u8; 32]).expect("spending key");
    let fvk = FullViewingKey::from(&sk);
    let (note, path, anchor) = note_at_leaf_zero(&fvk, 100_000_000, NoteVersion::V2, &mut rng);
    let own_address: Address = fvk.address_at(0u32, Scope::External);

    let pk = ProvingKey::build(circuit_version);
    let vk = pk.verifying_key();

    let mut b = Builder::new(
        BundleType::UNPADDED,
        orchard_bv,
        orchard_bv.default_flags(),
        anchor,
    )
    .expect("orchard v3 builder");
    b.add_spend(fvk.clone(), note, path.clone()).expect("add_spend");
    b.add_change_output(
        fvk.clone(),
        None,
        own_address,
        NoteValue::from_raw(85_000_000),
        [0u8; 512],
    )
    .expect("add_change_output");
    let (bundle, _meta) = b.build::<i64>(&mut rng).expect("build").expect("bundle");
    let actions = bundle.actions().len();

    let t_proof = Instant::now();
    let proven = bundle.create_proof(&pk, &mut rng).expect("f4b proof");
    let proof_ms = t_proof.elapsed().as_secs_f64() * 1e3;

    let ask = orchard::keys::SpendAuthorizingKey::from(&sk);
    let sighash = [0x11u8; 32];
    let authorized = proven
        .apply_signatures(&mut rng, sighash, core::slice::from_ref(&ask))
        .expect("f4b signatures");
    let proof = authorized.authorization().proof();
    let proof_bytes = proof.as_ref().len();
    let proof_fnv = fnv1a(proof.as_ref());

    let t_verify = Instant::now();
    authorized.verify_proof(&vk).expect("f4b verify");
    let verify_ms = t_verify.elapsed().as_secs_f64() * 1e3;

    // Prices are quoted per chunk, so all three geometries get measured: 512 is
    // the 8P unit, 4096 the 1P unit, 16384 only a cross-check.
    halo2_proofs::f4b::run::<halo2_proofs::pasta::pallas::Base>(1 << 14, args.reps);

    println!(
        "F4B_META {{\"mode\":\"f4b\",\"circuit\":\"PostNu6_3\",\"actions\":{},\
\"proof_ms\":{:.6},\"verify_ms\":{:.6},\"proof_bytes\":{},\"proof_fnv\":\"0x{:016x}\",\
\"probe\":{},\"bench_reps\":{},\"rayon_threads\":\"{}\",\"affinity\":\"0x{:x}\"}}",
        actions,
        proof_ms,
        verify_ms,
        proof_bytes,
        proof_fnv,
        halo2_proofs::f4b::enabled(),
        args.reps,
        std::env::var("RAYON_NUM_THREADS").unwrap_or_else(|_| "unset".into()),
        pinned.unwrap_or(0),
    );
}

/// Stage F4-A implementation A/B.
///
/// One process, one binary, one keygen: every pair alternates `A`
/// (`ORCHARD_F4A=0`) and `B` (`ORCHARD_F4A=1`) on the *same* fixture, with a
/// fresh `StdRng` seeded identically for both arms, so the two proofs must come
/// out byte-identical and the only difference left is how many times the
/// selector-recovery chains were computed.
///
/// `--reps N` is the number of pairs. Set `ORCHARD_F4A_STATS=1` to also hash the
/// `h` coefficients (doc 23 P4) and count memo reads (V1/V2).
#[cfg(feature = "f4aimpl")]
fn run_f4aimpl(args: &Args, pinned: Option<usize>) {
    let orchard_bv = BundleVersion::orchard_v3();
    let circuit_version = orchard_bv.circuit_version();

    let sk = SpendingKey::from_bytes([args.seed as u8; 32]).expect("spending key");
    let fvk = FullViewingKey::from(&sk);
    let ask = orchard::keys::SpendAuthorizingKey::from(&sk);
    let sighash = [0x11u8; 32];
    let own_address: Address = fvk.address_at(0u32, Scope::External);

    let pk = ProvingKey::build(circuit_version);
    let vk = pk.verifying_key();

    let mut actions = 0usize;

    for pair in 0..args.reps {
        for arm in ["A", "B"] {
            let on = arm == "B";
            std::env::set_var("ORCHARD_F4A", if on { "1" } else { "0" });

            // Identical randomness in both arms: `create_proof` draws blinds from
            // this rng and the draw count does not depend on F4-A.
            let mut rng = StdRng::from_seed([args.seed as u8; 32]);
            let (note, path, anchor) =
                note_at_leaf_zero(&fvk, 100_000_000, NoteVersion::V2, &mut rng);

            let mut b = Builder::new(
                BundleType::UNPADDED,
                orchard_bv,
                orchard_bv.default_flags(),
                anchor,
            )
            .expect("orchard v3 builder");
            b.add_spend(fvk.clone(), note, path.clone()).expect("add_spend");
            b.add_change_output(
                fvk.clone(),
                None,
                own_address,
                NoteValue::from_raw(85_000_000),
                [0u8; 512],
            )
            .expect("add_change_output");
            let (bundle, _meta) = b.build::<i64>(&mut rng).expect("build").expect("bundle");
            actions = bundle.actions().len();

            // `peak_working_set_mib` only grows, so the change across one proof
            // is that arm's incremental peak (F4A-4).
            let ws0 = mem::peak_working_set_mib();
            let t_proof = Instant::now();
            let proven = bundle.create_proof(&pk, &mut rng).expect("f4a proof");
            let proof_ms = t_proof.elapsed().as_secs_f64() * 1e3;
            let ws_peak_delta = mem::peak_working_set_mib() - ws0;

            let authorized = proven
                .apply_signatures(&mut rng, sighash, core::slice::from_ref(&ask))
                .expect("f4a signatures");
            let proof = authorized.authorization().proof();
            let proof_bytes = proof.as_ref().len();
            let proof_fnv = fnv1a(proof.as_ref());

            let t_verify = Instant::now();
            authorized.verify_proof(&vk).expect("f4a verify");
            let verify_ms = t_verify.elapsed().as_secs_f64() * 1e3;

            println!(
                "F4A_RUN {{\"pair\":{},\"arm\":\"{}\",\"f4a\":{},\"proof_ms\":{:.6},\
\"verify_ms\":{:.6},\"proof_bytes\":{},\"proof_fnv\":\"0x{:016x}\",\
\"ws_peak_delta_mib\":{:.1}}}",
                pair, arm, on, proof_ms, verify_ms, proof_bytes, proof_fnv, ws_peak_delta
            );
            println!("F4A_IMPL {}", halo2_proofs::f4a_impl::report());
        }
    }

    std::env::set_var("ORCHARD_F4A", "0");

    halo2_proofs::f2::flush_evals();
    println!("F2_REPORT {}", halo2_proofs::f2::take_report());
    println!(
        "F4A_META {{\"mode\":\"f4aimpl\",\"circuit\":\"PostNu6_3\",\"actions\":{},\
\"pairs\":{},\"seed\":{},\"rayon_threads\":\"{}\",\"affinity\":\"0x{:x}\",\"stats\":{}}}",
        actions,
        args.reps,
        args.seed,
        std::env::var("RAYON_NUM_THREADS").unwrap_or_else(|_| "unset".into()),
        pinned.unwrap_or(0),
        std::env::var_os("ORCHARD_F4A_STATS").is_some(),
    );
}

// ---------------------------------------------------------------------------
// Mode f4a2mem: Stage F4-A.2 memory and allocation accounting
// ---------------------------------------------------------------------------
//
// Same A/B protocol as `f4aimpl` (one process, arms interleaved, switch read
// per proof), plus three things that only exist in the diagnostic build:
//
//   1. a 1 ms sampler over the proof window (working set + private bytes),
//   2. the counting allocator installed at the crate root,
//   3. the mem module's `chunk_begin` / `chunk_end` window and `note_memo`.
//
// The times printed here come from an allocator-instrumented build and are NOT
// comparable to doc 24 §5 (doc 25 §2.2). They are printed only so the ratio can
// be sanity-checked; the audit's time evidence is block three.
#[cfg(feature = "f4a2mem")]
fn run_f4a2mem(args: &Args, pinned: Option<usize>) {
    let orchard_bv = BundleVersion::orchard_v3();
    let circuit_version = orchard_bv.circuit_version();

    let sk = SpendingKey::from_bytes([args.seed as u8; 32]).expect("spending key");
    let fvk = FullViewingKey::from(&sk);
    let ask = orchard::keys::SpendAuthorizingKey::from(&sk);
    let sighash = [0x11u8; 32];
    let own_address: Address = fvk.address_at(0u32, Scope::External);

    let pk = ProvingKey::build(circuit_version);
    let vk = pk.verifying_key();
    let mut actions = 0usize;

    for pair in 0..args.reps {
        for arm in ["A", "B"] {
            let on = arm == "B";
            std::env::set_var("ORCHARD_F4A", if on { "1" } else { "0" });

            // Identical randomness in both arms (same reason as `f4aimpl`).
            let mut rng = StdRng::from_seed([args.seed as u8; 32]);
            let (note, path, anchor) =
                note_at_leaf_zero(&fvk, 100_000_000, NoteVersion::V2, &mut rng);

            let mut b = Builder::new(
                BundleType::UNPADDED,
                orchard_bv,
                orchard_bv.default_flags(),
                anchor,
            )
            .expect("orchard v3 builder");
            b.add_spend(fvk.clone(), note, path.clone()).expect("add_spend");
            b.add_change_output(
                fvk.clone(),
                None,
                own_address,
                NoteValue::from_raw(85_000_000),
                [0u8; 512],
            )
            .expect("add_change_output");
            let (bundle, _meta) = b.build::<i64>(&mut rng).expect("build").expect("bundle");
            actions = bundle.actions().len();

            halo2_proofs::f4a2::reset();
            let (proven, win) = mem2::run(|| bundle.create_proof(&pk, &mut rng).expect("proof"));
            let alloc = halo2_proofs::f4a2::report();

            let authorized = proven
                .apply_signatures(&mut rng, sighash, core::slice::from_ref(&ask))
                .expect("f4a2 signatures");
            let proof = authorized.authorization().proof();
            let proof_bytes = proof.as_ref().len();
            let proof_fnv = fnv1a(proof.as_ref());
            authorized.verify_proof(&vk).expect("f4a2 verify");

            println!(
                "F4A2_MEM {{\"pair\":{},\"arm\":\"{}\",\"f4a\":{},\"window_ms\":{:.3},\
\"ws_start_mib\":{:.2},\"ws_peak_mib\":{:.2},\"ws_delta_mib\":{:.2},\
\"priv_start_mib\":{:.2},\"priv_peak_mib\":{:.2},\"priv_delta_mib\":{:.2},\
\"peak_ws_kernel_mib\":{:.2},\"peak_pagefile_kernel_mib\":{:.2},\"samples\":{},\
\"proof_bytes\":{},\"proof_fnv\":\"0x{:016x}\"}}",
                pair,
                arm,
                on,
                win.window_ms,
                mib(win.ws_start),
                mib(win.ws_peak),
                mib(win.ws_peak.saturating_sub(win.ws_start)),
                mib(win.private_start),
                mib(win.private_peak),
                mib(win.private_peak.saturating_sub(win.private_start)),
                mib(win.peak_ws_kernel),
                mib(win.peak_pagefile_kernel),
                win.samples,
                proof_bytes,
                proof_fnv
            );
            println!("F4A2_ALLOC {{\"pair\":{},\"arm\":\"{}\",\"f4a\":{},{}", pair, arm, on,
                &alloc[1..]);
        }
    }

    std::env::set_var("ORCHARD_F4A", "0");
    println!(
        "F4A2_META {{\"mode\":\"f4a2mem\",\"circuit\":\"PostNu6_3\",\"actions\":{},\
\"pairs\":{},\"seed\":{},\"rayon_threads\":\"{}\",\"affinity\":\"0x{:x}\"}}",
        actions,
        args.reps,
        args.seed,
        std::env::var("RAYON_NUM_THREADS").unwrap_or_else(|_| "unset".into()),
        pinned.unwrap_or(0),
    );
}

// ---------------------------------------------------------------------------
// Mode f4a2stage: Stage F4-A.2 per-stage attribution
// ---------------------------------------------------------------------------
//
// This mode adds nothing to the crate: E2's nine contiguous, non-overlapping
// stages are already compiled in and env-gated. The only new thing is that the
// stages are tagged with the A/B arm, so the paired difference per stage can be
// formed inside one process. Absolute times here are not the doc 24 numbers
// (E2 timing is on); only the stage deltas are used (doc 25 §2.3).
#[cfg(feature = "f4aimpl")]
fn run_f4a2stage(args: &Args, pinned: Option<usize>) {
    // Must be set before the first `e2_add` call, which happens inside the first
    // `create_proof`; `e2_enabled()` caches the flag in a `OnceLock`.
    std::env::set_var("ORCHARD_E2_TIMING", "1");

    let orchard_bv = BundleVersion::orchard_v3();
    let circuit_version = orchard_bv.circuit_version();

    let sk = SpendingKey::from_bytes([args.seed as u8; 32]).expect("spending key");
    let fvk = FullViewingKey::from(&sk);
    let ask = orchard::keys::SpendAuthorizingKey::from(&sk);
    let sighash = [0x11u8; 32];
    let own_address: Address = fvk.address_at(0u32, Scope::External);

    let pk = ProvingKey::build(circuit_version);
    let vk = pk.verifying_key();
    let mut actions = 0usize;

    for pair in 0..args.reps {
        for arm in ["A", "B"] {
            let on = arm == "B";
            std::env::set_var("ORCHARD_F4A", if on { "1" } else { "0" });

            let mut rng = StdRng::from_seed([args.seed as u8; 32]);
            let (note, path, anchor) =
                note_at_leaf_zero(&fvk, 100_000_000, NoteVersion::V2, &mut rng);

            let mut b = Builder::new(
                BundleType::UNPADDED,
                orchard_bv,
                orchard_bv.default_flags(),
                anchor,
            )
            .expect("orchard v3 builder");
            b.add_spend(fvk.clone(), note, path.clone()).expect("add_spend");
            b.add_change_output(
                fvk.clone(),
                None,
                own_address,
                NoteValue::from_raw(85_000_000),
                [0u8; 512],
            )
            .expect("add_change_output");
            let (bundle, _meta) = b.build::<i64>(&mut rng).expect("build").expect("bundle");
            actions = bundle.actions().len();

            let t = Instant::now();
            let proven = bundle.create_proof(&pk, &mut rng).expect("stage proof");
            let prove_ms = t.elapsed().as_secs_f64() * 1e3;

            let mut stages = halo2_proofs::plonk::take_e2_timings();
            stages.sort_by(|a, b| a.0.cmp(&b.0));
            let body: Vec<String> = stages
                .iter()
                .map(|(k, v)| format!("\"{}\":{:.6}", k, v * 1e3))
                .collect();

            let authorized = proven
                .apply_signatures(&mut rng, sighash, core::slice::from_ref(&ask))
                .expect("stage signatures");
            let proof = authorized.authorization().proof();
            let proof_bytes = proof.as_ref().len();
            let proof_fnv = fnv1a(proof.as_ref());
            authorized.verify_proof(&vk).expect("stage verify");

            println!(
                "E2_PAIR {{\"pair\":{},\"arm\":\"{}\",\"f4a\":{},\"prove_ms\":{:.3},\
\"proof_bytes\":{},\"proof_fnv\":\"0x{:016x}\",\"stages_ms\":{{{}}}}}",
                pair,
                arm,
                on,
                prove_ms,
                proof_bytes,
                proof_fnv,
                body.join(",")
            );
        }
    }

    std::env::set_var("ORCHARD_F4A", "0");
    println!(
        "F4A2_META {{\"mode\":\"f4a2stage\",\"circuit\":\"PostNu6_3\",\"actions\":{},\
\"pairs\":{},\"seed\":{},\"rayon_threads\":\"{}\",\"affinity\":\"0x{:x}\"}}",
        actions,
        args.reps,
        args.seed,
        std::env::var("RAYON_NUM_THREADS").unwrap_or_else(|_| "unset".into()),
        pinned.unwrap_or(0),
    );
}

fn run_profile(args: &Args, pinned: Option<usize>) {
    let mut rng = StdRng::from_seed([args.seed as u8; 32]);

    let orchard_bv = BundleVersion::orchard_v3();
    let circuit_version = orchard_bv.circuit_version();

    // ---- stage 0: fixture -------------------------------------------------
    let t_fixture = Instant::now();
    let sk = SpendingKey::from_bytes([args.seed as u8; 32]).expect("spending key");
    let fvk = FullViewingKey::from(&sk);
    let (note, path, anchor) = note_at_leaf_zero(&fvk, 100_000_000, NoteVersion::V2, &mut rng);
    // Same address as the spent note: cross-address is off for V3, so the
    // circuit forces spend/output (g_d, pk_d) equality.
    let own_address: Address = fvk.address_at(0u32, Scope::External);
    let fixture_s = t_fixture.elapsed().as_secs_f64();

    // ---- stage 1: keygen (outside sampling window) ------------------------
    let t_keygen = Instant::now();
    let pk = ProvingKey::build(circuit_version);
    let keygen_s = t_keygen.elapsed().as_secs_f64();

    // ---- stage 2: build the single bundle (outside sampling window) -------
    let t_build = Instant::now();
    let mut b = Builder::new(
        BundleType::UNPADDED,
        orchard_bv,
        orchard_bv.default_flags(),
        anchor,
    )
    .expect("orchard v3 builder");
    b.add_spend(fvk.clone(), note, path.clone()).expect("add_spend");
    b.add_change_output(
        fvk.clone(),
        None,
        own_address,
        NoteValue::from_raw(85_000_000),
        [0u8; 512],
    )
    .expect("add_change_output");
    let (bundle, _meta) = b.build::<i64>(&mut rng).expect("build").expect("bundle");
    let actions = bundle.actions().len();
    let build_s = t_build.elapsed().as_secs_f64();

    // ---- stage 3: warmup (outside sampling window) ------------------------
    // Initializes the rayon pool and warms caches/JIT-free code paths so the
    // recorded loop is representative. `create_proof` consumes `self`, so we
    // clone the (already-built) bundle once per iteration — a flat memory copy,
    // not a re-synthesis, so it stays outside the "what the prover costs" story.
    let warmup = 3usize;
    for _ in 0..warmup {
        let _ = bundle.clone().create_proof(&pk, &mut rng).expect("warmup proof");
    }

    // ---- stage 4: sampling window -----------------------------------------
    // Optional in-process CPU sampler (Stage E3): captures where every thread is
    // executing at fixed ticks. Gated by ORCHARD_E3_SAMPLE + a --e3-out dir.
    let mut sampler = if std::env::var("ORCHARD_E3_SAMPLE").is_ok() && !args.e3_out.is_empty() {
        eprintln!(
            "E3 sampler enabled (interval={}ms) -> {}",
            args.e3_interval_ms, args.e3_out
        );
        Some(e3sampler::Sampler::start(args.e3_interval_ms))
    } else {
        None
    };

    println!(
        "PROFILE_WINDOW_START tag={} actions={} warmup={} reps={} rayon_threads={} affinity=0x{:x}",
        args.tag,
        actions,
        warmup,
        args.reps,
        std::env::var("RAYON_NUM_THREADS").unwrap_or_else(|_| "unset".into()),
        pinned.unwrap_or(0),
    );
    let t_loop = Instant::now();
    for rep in 0..args.reps {
        let _ = bundle.clone().create_proof(&pk, &mut rng).expect("profile proof");
        if rep % 10 == 0 && rep > 0 {
            eprintln!("  profile rep {rep}/{} done", args.reps);
        }
    }
    let loop_s = t_loop.elapsed().as_secs_f64();
    if let Some(mut s) = sampler.take() {
        s.stop(&args.e3_out);
    }
    println!(
        "PROFILE_WINDOW_END wall_s={loop_s:.6} reps={} per_proof_ms={:.3}",
        args.reps,
        loop_s / args.reps as f64 * 1e3
    );
    println!(
        "PROFILE_META {{\"tag\":\"{}\",\"mode\":\"profile\",\"circuit\":\"PostNu6_3\",\
\"actions\":{},\"warmup\":{},\"reps\":{},\"fixture_s\":{:.6},\"keygen_s\":{:.6},\
\"build_s\":{:.6},\"loop_wall_s\":{:.6},\"per_proof_ms\":{:.3},\
\"rayon_threads\":\"{}\",\"affinity\":\"0x{:x}\"}}",
        args.tag,
        actions,
        warmup,
        args.reps,
        fixture_s,
        keygen_s,
        build_s,
        loop_s,
        loop_s / args.reps as f64 * 1e3,
        std::env::var("RAYON_NUM_THREADS").unwrap_or_else(|_| "unset".into()),
        pinned.unwrap_or(0),
    );
}
