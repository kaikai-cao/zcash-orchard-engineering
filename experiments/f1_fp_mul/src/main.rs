//! Stage F1 -- Pallas base field `Fp::mul` microbenchmark + assembly audit.
//!
//! Purpose: measure the primitive that E3 attributed 33.08% of prover CPU to,
//! WITHOUT touching Orchard production code or `pasta_curves` sources.
//!
//! What this binary does:
//!   1. `correctness` -- checks `Fp::mul` against externally generated vectors
//!      (Python bigint, independent of any Rust arithmetic) plus algebraic laws.
//!   2. `bench`       -- dependency-chain latency and 8-way throughput, bound to
//!      one P-core, reporting ns/op and TSC ticks/op.
//!   3. `info`        -- affinity, TSC frequency, field modulus sanity.
//!
//! The exported `f1_mul_blackbox` exists only so the compiler emits a standalone
//! symbol whose assembly can be audited (`--emit asm`). It is called once from
//! `main` so it cannot be dropped as dead code.

use ff::{Field, PrimeField};
use pasta_curves::Fp;
use std::hint::black_box;
use std::time::Instant;

// ---------------------------------------------------------------------------
// Assembly audit target: one full field multiplication, no inlining.
// The `sret` argument + this body is exactly what the prover executes per
// `Fp::mul` call (mul_unreduced -> montgomery_reduce -> final conditional sub).
// ---------------------------------------------------------------------------
#[no_mangle]
#[inline(never)]
pub fn f1_mul_blackbox(a: Fp, b: Fp) -> Fp {
    a * b
}

#[no_mangle]
#[inline(never)]
pub fn f1_square_blackbox(a: Fp) -> Fp {
    a.square()
}

#[no_mangle]
#[inline(never)]
pub fn f1_add_blackbox(a: Fp, b: Fp) -> Fp {
    a + b
}

// ---------------------------------------------------------------------------
// Timing
// ---------------------------------------------------------------------------

#[inline(always)]
fn rdtsc() -> u64 {
    unsafe { core::arch::x86_64::_rdtsc() }
}

/// Invariant-TSC frequency in Hz (nominal, not core clock).
fn calibrate_tsc() -> f64 {
    let t0 = Instant::now();
    let c0 = rdtsc();
    while t0.elapsed().as_millis() < 150 {
        std::hint::spin_loop();
    }
    let c1 = rdtsc();
    let dt = t0.elapsed().as_secs_f64();
    (c1 - c0) as f64 / dt
}

#[inline(never)]
fn chain_mul(mut acc: Fp, b: Fp, n: u64) -> Fp {
    for _ in 0..n {
        acc = acc * b;
    }
    acc
}

#[inline(never)]
fn chain_square(mut acc: Fp, n: u64) -> Fp {
    for _ in 0..n {
        acc = acc.square();
    }
    acc
}

#[inline(never)]
fn chain_add(mut acc: Fp, b: Fp, n: u64) -> Fp {
    for _ in 0..n {
        acc = acc + b;
    }
    acc
}

#[inline(never)]
fn thru_mul(mut acc: [Fp; 8], b: [Fp; 8], n: u64) -> [Fp; 8] {
    for _ in 0..n {
        acc[0] = acc[0] * b[0];
        acc[1] = acc[1] * b[1];
        acc[2] = acc[2] * b[2];
        acc[3] = acc[3] * b[3];
        acc[4] = acc[4] * b[4];
        acc[5] = acc[5] * b[5];
        acc[6] = acc[6] * b[6];
        acc[7] = acc[7] * b[7];
    }
    acc
}

// --- Non-inlined call path -------------------------------------------------
// E3 found a *standalone* `Fp::mul` symbol with 33.08% self time, i.e. in the
// real prover not every multiply is inlined into `evaluate::recurse`
// (codegen-units=16 + cross-crate boundary). These two loops measure the
// primitive as the prover often sees it: one call per multiply.
#[inline(never)]
fn call_mul(a: Fp, b: Fp) -> Fp {
    a * b
}

#[inline(never)]
fn chain_call_mul(mut acc: Fp, b: Fp, n: u64) -> Fp {
    for _ in 0..n {
        acc = call_mul(acc, b);
    }
    acc
}

#[inline(never)]
fn thru_call_mul(mut acc: [Fp; 8], b: [Fp; 8], n: u64) -> [Fp; 8] {
    for _ in 0..n {
        acc[0] = call_mul(acc[0], b[0]);
        acc[1] = call_mul(acc[1], b[1]);
        acc[2] = call_mul(acc[2], b[2]);
        acc[3] = call_mul(acc[3], b[3]);
        acc[4] = call_mul(acc[4], b[4]);
        acc[5] = call_mul(acc[5], b[5]);
        acc[6] = call_mul(acc[6], b[6]);
        acc[7] = call_mul(acc[7], b[7]);
    }
    acc
}

fn median(mut v: Vec<f64>) -> f64 {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let m = v.len() / 2;
    if v.len() % 2 == 0 {
        (v[m - 1] + v[m]) / 2.0
    } else {
        v[m]
    }
}

struct Sample {
    median_ns: f64,
    min_ns: f64,
    max_ns: f64,
    cv_pct: f64,
}

fn summarize(per_op_ns: Vec<f64>) -> Sample {
    let n = per_op_ns.len() as f64;
    let mean: f64 = per_op_ns.iter().sum::<f64>() / n;
    let var: f64 = per_op_ns.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / (n - 1.0).max(1.0);
    Sample {
        median_ns: median(per_op_ns.clone()),
        min_ns: per_op_ns.iter().cloned().fold(f64::INFINITY, f64::min),
        max_ns: per_op_ns.iter().cloned().fold(0.0, f64::max),
        cv_pct: if mean > 0.0 { var.sqrt() / mean * 100.0 } else { 0.0 },
    }
}

// ---------------------------------------------------------------------------
// Benchmarks
// ---------------------------------------------------------------------------

const REPS: usize = 7;
const N_CHAIN: u64 = 1_000_000;
const N_THRU: u64 = 400_000;

fn det_u64(state: &mut u64) -> u64 {
    // xorshift64*
    let mut x = *state;
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    *state = x;
    x.wrapping_mul(0x2545F4914F6CDD1D)
}

fn cmd_bench() {
    let tsc_hz = calibrate_tsc();

    let mut st = 0x9E3779B97F4A7C15u64;
    let mut mk = || Fp::from(det_u64(&mut st) | 1);
    let a = mk();
    let b = mk();
    let mut acc8 = [Fp::from(0u64); 8];
    let mut b8 = [Fp::from(0u64); 8];
    for i in 0..8 {
        acc8[i] = mk();
        b8[i] = mk();
    }

    // Warmup: make sure the multiply path is paged in / branch predictors settled.
    black_box(chain_mul(a, b, 50_000));
    black_box(thru_mul(acc8, b8, 50_000));

    println!("# tsc_hz={:.0}", tsc_hz);

    // ---- latency: acc = acc * b (serial dependency on the full 256-bit result)
    let mut lat = Vec::new();
    let mut lat_tsc = Vec::new();
    for _ in 0..REPS {
        let t0 = Instant::now();
        let c0 = rdtsc();
        let r = chain_mul(black_box(a), black_box(b), N_CHAIN);
        let c1 = rdtsc();
        let dt = t0.elapsed().as_secs_f64();
        black_box(r);
        lat.push(dt * 1e9 / N_CHAIN as f64);
        lat_tsc.push((c1 - c0) as f64 / N_CHAIN as f64);
    }
    let s = summarize(lat.clone());
    println!(
        "lat_mul,median_ns={:.3},min_ns={:.3},max_ns={:.3},cv_pct={:.2},median_tsc={:.1}",
        s.median_ns,
        s.min_ns,
        s.max_ns,
        s.cv_pct,
        median(lat_tsc.clone())
    );

    // ---- latency reference: Fp::add (same repr, cheaper ALU-only op)
    let mut lad = Vec::new();
    for _ in 0..REPS {
        let t0 = Instant::now();
        let r = chain_add(black_box(a), black_box(b), N_CHAIN);
        let dt = t0.elapsed().as_secs_f64();
        black_box(r);
        lad.push(dt * 1e9 / N_CHAIN as f64);
    }
    let s = summarize(lad);
    let add_med = s.median_ns;
    println!(
        "lat_add,median_ns={:.3},min_ns={:.3},max_ns={:.3},cv_pct={:.2}",
        s.median_ns, s.min_ns, s.max_ns, s.cv_pct
    );

    // ---- latency: square (separate code path: square_unreduced, 10 mul + shifts)
    let mut lsq = Vec::new();
    for _ in 0..REPS {
        let t0 = Instant::now();
        let r = chain_square(black_box(a), N_CHAIN);
        let dt = t0.elapsed().as_secs_f64();
        black_box(r);
        lsq.push(dt * 1e9 / N_CHAIN as f64);
    }
    let s = summarize(lsq);
    println!(
        "lat_square,median_ns={:.3},min_ns={:.3},max_ns={:.3},cv_pct={:.2}",
        s.median_ns, s.min_ns, s.max_ns, s.cv_pct
    );

    // ---- throughput: 8 independent accumulators (latency hidden by ILP)
    let mut thr = Vec::new();
    let mut thr_tsc = Vec::new();
    for _ in 0..REPS {
        let t0 = Instant::now();
        let c0 = rdtsc();
        let r = thru_mul(black_box(acc8), black_box(b8), N_THRU);
        let c1 = rdtsc();
        let dt = t0.elapsed().as_secs_f64();
        black_box(r);
        let ops = (N_THRU * 8) as f64;
        thr.push(dt * 1e9 / ops);
        thr_tsc.push((c1 - c0) as f64 / ops);
    }
    let s = summarize(thr.clone());
    println!(
        "thr_mul8,median_ns={:.3},min_ns={:.3},max_ns={:.3},cv_pct={:.2},median_tsc={:.1}",
        s.median_ns,
        s.min_ns,
        s.max_ns,
        s.cv_pct,
        median(thr_tsc)
    );

    let lat_med = median(lat);
    let thr_med = median(thr);

    // ---- call path: one `call_mul` per multiply, no inlining
    let mut lcall = Vec::new();
    for _ in 0..REPS {
        let t0 = Instant::now();
        let r = chain_call_mul(black_box(a), black_box(b), N_CHAIN);
        let dt = t0.elapsed().as_secs_f64();
        black_box(r);
        lcall.push(dt * 1e9 / N_CHAIN as f64);
    }
    let s = summarize(lcall.clone());
    println!(
        "lat_call,median_ns={:.3},min_ns={:.3},max_ns={:.3},cv_pct={:.2}",
        s.median_ns, s.min_ns, s.max_ns, s.cv_pct
    );

    let mut tcall = Vec::new();
    for _ in 0..REPS {
        let t0 = Instant::now();
        let r = thru_call_mul(black_box(acc8), black_box(b8), N_THRU / 2);
        let dt = t0.elapsed().as_secs_f64();
        black_box(r);
        let ops = (N_THRU / 2 * 8) as f64;
        tcall.push(dt * 1e9 / ops);
    }
    let s = summarize(tcall.clone());
    println!(
        "thr_call,median_ns={:.3},min_ns={:.3},max_ns={:.3},cv_pct={:.2}",
        s.median_ns, s.min_ns, s.max_ns, s.cv_pct
    );

    println!(
        "summary,lat_ns={:.3},thr_ns={:.3},lat_call_ns={:.3},thr_call_ns={:.3},ilp_ratio={:.2},add_ns={:.3},call_overhead_ns={:.3}",
        lat_med,
        thr_med,
        median(lcall.clone()),
        median(tcall),
        lat_med / thr_med,
        add_med,
        median(lcall) - lat_med
    );
}

// ---------------------------------------------------------------------------
// Correctness
// ---------------------------------------------------------------------------

fn hex32_to_bytes(s: &str) -> [u8; 32] {
    let s = s.trim();
    assert_eq!(s.len(), 64, "expected 64 hex chars, got {}", s.len());
    let mut out = [0u8; 32];
    for i in 0..32 {
        out[i] = u8::from_str_radix(&s[2 * i..2 * i + 2], 16).expect("bad hex");
    }
    out
}

fn bytes_to_hex32(b: &[u8; 32]) -> String {
    let mut s = String::with_capacity(64);
    for x in b {
        s.push_str(&format!("{:02x}", x));
    }
    s
}

fn read_vectors(path: &str) -> Vec<(String, [u8; 32], [u8; 32], [u8; 32], [u8; 32])> {
    let text = std::fs::read_to_string(path).unwrap_or_else(|e| panic!("read {}: {}", path, e));
    let mut out = Vec::new();
    for (ln, line) in text.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') || line.starts_with("kind,") {
            continue;
        }
        let f: Vec<&str> = line.split(',').collect();
        assert_eq!(f.len(), 5, "line {}: expected 5 fields", ln + 1);
        out.push((
            f[0].to_string(),
            hex32_to_bytes(f[1]),
            hex32_to_bytes(f[2]),
            hex32_to_bytes(f[3]),
            hex32_to_bytes(f[4]),
        ));
    }
    out
}

fn cmd_correctness(path: &str) {
    let vecs = read_vectors(path);
    let mut fail = 0usize;
    for (kind, ah, bh, ph, sh) in &vecs {
        let a = Fp::from_repr(*ah).unwrap();
        let b = Fp::from_repr(*bh).unwrap();
        let got = a * b;
        let got_bytes = got.to_repr();
        if bytes_to_hex32(&got_bytes) != bytes_to_hex32(ph) {
            eprintln!("MUL MISMATCH [{}] a={} b={}", kind, bytes_to_hex32(ah), bytes_to_hex32(bh));
            fail += 1;
        }
        let sq = a.square();
        if bytes_to_hex32(&sq.to_repr()) != bytes_to_hex32(sh) {
            eprintln!("SQUARE MISMATCH [{}] a={}", kind, bytes_to_hex32(ah));
            fail += 1;
        }
        // When b == a, the generic multiply path must agree with the dedicated
        // square path (different limb schedule inside `square_unreduced`).
        if a == b && got != sq {
            eprintln!("a*a != a.square() [{}]", kind);
            fail += 1;
        }
    }
    println!(
        "vectors,total={},mismatches={}",
        vecs.len() * 2,
        fail
    );

    // Algebraic laws with derived pseudo-random elements (all < p by construction).
    let mut st = 0xDEADBEEFCAFEF00Du64;
    let mut law_fail = 0usize;
    let n = 2000;
    for _ in 0..n {
        let a = Fp::from(det_u64(&mut st));
        let b = Fp::from(det_u64(&mut st));
        let c = Fp::from(det_u64(&mut st));
        let one = Fp::from(1u64);
        let zero = Fp::from(0u64);
        if a * b != b * a {
            law_fail += 1;
        }
        if (a * b) * c != a * (b * c) {
            law_fail += 1;
        }
        if a * (b + c) != a * b + a * c {
            law_fail += 1;
        }
        if a * one != a {
            law_fail += 1;
        }
        if a * zero != zero {
            law_fail += 1;
        }
        if a * a != a.square() {
            law_fail += 1;
        }
        if let Some(inv) = Option::<Fp>::from(a.invert()) {
            if a * inv != one {
                law_fail += 1;
            }
        }
    }
    println!("laws,cases={},failures={}", n * 6, law_fail);
    if fail == 0 && law_fail == 0 {
        println!("RESULT,PASS");
    } else {
        println!("RESULT,FAIL");
        std::process::exit(1);
    }
}

// ---------------------------------------------------------------------------
// Entry
// ---------------------------------------------------------------------------

fn set_affinity(mask: usize) {
    unsafe {
        use windows_sys::Win32::System::Threading::{GetCurrentProcess, SetProcessAffinityMask};
        let h = GetCurrentProcess();
        let ok = SetProcessAffinityMask(h, mask);
        if ok == 0 {
            eprintln!("warning: SetProcessAffinityMask(0x{:x}) failed", mask);
        }
    }
}

/// Stage F2 follow-up: what does `Ast::Scale` cost when the scalar is -1?
///
/// `Neg for Ast` is implemented as `Scale(self, -F::ONE)` (poly/evaluator.rs), so
/// every `Negated` node in a constraint expression becomes a full Montgomery
/// multiplication by -1 over a whole chunk. This measures the two forms head to
/// head on the same buffer size the quotient evaluator uses (16384 elements),
/// with the same access pattern (streaming a Vec<Fp>).
fn cmd_scale_cost() {
    use std::time::Instant;
    const N: usize = 16384;
    const REPS: usize = 200;

    let minus_one = Fp::from(0) - Fp::from(1); // -1 mod p, not a literal
    let two = Fp::from(2u64);

    let mut neg = Vec::new();
    let mut scale = Vec::new();
    let mut mul = Vec::new();

    for _ in 0..5 {
        let mut v = vec![Fp::from(7u64); N];

        let t = Instant::now();
        for _ in 0..REPS {
            for x in v.iter_mut() {
                *x = -*x;
            }
        }
        neg.push(t.elapsed().as_secs_f64() * 1e9 / (REPS * N) as f64);

        let t = Instant::now();
        for _ in 0..REPS {
            for x in v.iter_mut() {
                *x *= minus_one;
            }
        }
        scale.push(t.elapsed().as_secs_f64() * 1e9 / (REPS * N) as f64);

        let t = Instant::now();
        for _ in 0..REPS {
            for x in v.iter_mut() {
                *x *= two;
            }
        }
        mul.push(t.elapsed().as_secs_f64() * 1e9 / (REPS * N) as f64);

        black_box(&v);
    }

    let m = |v: &mut Vec<f64>| {
        v.sort_by(|a, b| a.partial_cmp(b).unwrap());
        v[v.len() / 2]
    };
    println!("mode,ns_per_element");
    println!("negate,{:.4}", m(&mut neg));
    println!("scale_by_minus_one,{:.4}", m(&mut scale));
    println!("scale_by_two,{:.4}", m(&mut mul));
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let cmd = args.get(1).map(|s| s.as_str()).unwrap_or("info");

    let mut mask = 1usize;
    for i in 0..args.len() {
        if args[i] == "--mask" && i + 1 < args.len() {
            mask = usize::from_str_radix(args[i + 1].trim_start_matches("0x"), 16).unwrap();
        }
    }
    set_affinity(mask);

    match cmd {
        "correctness" => {
            let path = args
                .iter()
                .position(|a| a == "--vectors")
                .map(|i| args[i + 1].clone())
                .unwrap_or_else(|| "vectors.csv".to_string());
            cmd_correctness(&path);
        }
        "bench" => {
            // Keep the audit symbols alive so their code is emitted.
            black_box(f1_mul_blackbox(black_box(Fp::from(3u64)), black_box(Fp::from(5u64))));
            black_box(f1_square_blackbox(black_box(Fp::from(7u64))));
            black_box(f1_add_blackbox(black_box(Fp::from(11u64)), black_box(Fp::from(13u64))));
            cmd_bench();
        }
        "scale-cost" => {
            cmd_scale_cost();
        }
        "info" => {
            println!("affinity_mask_requested=0x{:x}", mask);
            println!("tsc_hz={:.0}", calibrate_tsc());
            println!("modulus=0x40000000000000000000000000000000224698fc094cf91b992d30ed00000001");
            let a = Fp::from(3u64);
            println!("3*5={:?}", (a * Fp::from(5u64)).to_repr());
        }
        other => {
            eprintln!("unknown command: {}", other);
            std::process::exit(2);
        }
    }
}
