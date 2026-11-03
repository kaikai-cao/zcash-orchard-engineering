//! End-to-end Orchard bundle harness.
//!
//! Modes:
//!   --mode v2  historical pre-NU6.3 shape (BundleVersion::orchard_v2, FixedPostNu6_2)
//!   --mode v3  NU6.3 migration shape: an Orchard V3 bundle (spend + change) and an
//!              Ironwood V3 bundle (output), both on the PostNu6_3 circuit.
//!   --mode profile  Stage E3: build the Orchard V3 bundle ONCE, then loop
//!              `create_proof` only (no keygen/build/sign/verify in the window).
//!              Prints PROFILE_WINDOW_START/END markers so an ETW session can be
//!              scoped to the prove loop.
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

const MERKLE_DEPTH: usize = 32; // orchard::NOTE_COMMITMENT_TREE_DEPTH

// ---------------------------------------------------------------------------
// Process memory counters (no external crate: raw FFI)
// ---------------------------------------------------------------------------

#[cfg(windows)]
mod mem {
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
}

#[cfg(not(windows))]
mod mem {
    pub fn working_set_mib() -> f64 {
        0.0
    }
    pub fn peak_working_set_mib() -> f64 {
        0.0
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
        other => panic!("unknown --mode {other} (expected v2, v3 or profile)"),
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
