//! End-to-end Orchard bundle harness: fixture -> keygen -> build -> prove -> sign -> verify.
//!
//! Deliberately lives OUTSIDE the upstream checkout so `D:\Research\orchard-upstream`
//! stays byte-identical to `616a669d`. Consumed via a Cargo path dependency.
//!
//! Stages are timed independently because `Builder::build()` does NOT create the
//! Halo2 proof: it returns a `Bundle<InProgress<Unproven, Unauthorized>, i64>`.
//! Proof creation is a separate `Bundle::create_proof` call, and authorization is a
//! third `apply_signatures` call. Collapsing them into one number hides the
//! attribution we actually want.

use std::time::Instant;

use incrementalmerkletree::{Hashable, Level};
use orchard::{
    builder::{Builder, BundleType},
    bundle::{BundleVersion, Flags},
    circuit::{OrchardCircuitVersion, ProvingKey, VerifyingKey},
    keys::{FullViewingKey, Scope, SpendingKey},
    note::{ExtractedNoteCommitment, Note, NoteVersion, RandomSeed, Rho},
    tree::{MerkleHashOrchard, MerklePath},
    value::NoteValue,
    Address,
};
use rand::{rngs::StdRng, Rng, SeedableRng};

const MERKLE_DEPTH: usize = 32; // orchard::NOTE_COMMITMENT_TREE_DEPTH

// ---------------------------------------------------------------------------
// Process memory counters (no external crate: raw FFI to kernel32)
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
// Start-Process when both HTTP_PROXY and http_proxy are present), and it also
// pins the *launcher*, not just the measured work. Pinning here confines every
// rayon worker created afterwards, which is what we actually want.

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
    version: String,
    spends: usize,
    outputs: usize,
    reps: usize,
    seed: u64,
    tag: String,
    mask: usize,
}

fn parse_args() -> Args {
    let mut a = Args {
        version: "v2".into(),
        spends: 1,
        outputs: 2,
        reps: 3,
        seed: 7,
        tag: String::new(),
        mask: 0,
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
) -> (Note, MerklePath, orchard::tree::Anchor) {
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
    let auth_path: [MerkleHashOrchard; MERKLE_DEPTH] = core::array::from_fn(|l| {
        MerkleHashOrchard::empty_root(Level::from(l as u8))
    });
    let path = MerklePath::from_parts(0, auth_path);
    let anchor = path.root(cmx);

    (note, path, anchor)
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

    let mut rng = StdRng::from_seed([args.seed as u8; 32]);

    let (bundle_version, circuit_version) = match args.version.as_str() {
        "v2" => (BundleVersion::orchard_v2(), OrchardCircuitVersion::FixedPostNu6_2),
        "v3" => (BundleVersion::orchard_v3(), OrchardCircuitVersion::PostNu6_3),
        other => panic!("unsupported --version {other} (expected v2 or v3)"),
    };
    if args.version == "v3" {
        panic!("v3 (cross-address disabled) needs change outputs; not wired up yet");
    }
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
        let ws0 = mem::working_set_mib();
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
        let _ = ws0;

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

    // Orchard bundle encoding size, from the protocol layout:
    //   per Action: cv_net 32 + nf 32 + rk 32 + cmx 32 + epk 32 + enc_ct 580 + out_ct 80
    //   bundle:     flags 1 + value_balance 8 + anchor 32 + zkproof + bsk_sig 64
    let encoded_bytes = num_actions * 820 + 1 + 8 + 32 + proof_bytes + 64;

    let mean = |v: &[f64]| v.iter().sum::<f64>() / v.len() as f64;
    let fmt = |v: &[f64]| {
        v.iter()
            .map(|x| format!("{x:.6}"))
            .collect::<Vec<_>>()
            .join(",")
    };

    println!("STAGE fixture_s={fixture_s:.6}");
    println!("STAGE keygen_s={keygen_s:.6}");
    println!("STAGE build_ms={}", fmt(&build_ms));
    println!("STAGE prove_s={}", fmt(&prove_s));
    println!("STAGE sign_ms={}", fmt(&sign_ms));
    println!("STAGE verify_ms={}", fmt(&verify_ms));
    println!(
        "RESULT {{\"tag\":\"{}\",\"version\":\"{}\",\"spends\":{},\"outputs\":{},\"actions\":{},\
\"seed\":{},\"reps\":{},\"fixture_s\":{:.6},\"keygen_s\":{:.6},\"build_ms_mean\":{:.4},\
\"prove_s_mean\":{:.6},\"prove_s_min\":{:.6},\"sign_ms_mean\":{:.4},\"verify_ms_mean\":{:.4},\
\"proof_bytes\":{},\"encoded_bytes\":{},\"ws_before_prove_mib\":{:.1},\
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
