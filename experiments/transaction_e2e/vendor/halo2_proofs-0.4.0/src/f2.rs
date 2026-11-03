//! Stage F2 diagnostics: what the constraint expressions look like, and what the
//! evaluator actually does with them.
//!
//! Inert unless `ORCHARD_F2` is set. Two recording points:
//!
//! * [`maybe_run`] — called once from `keygen_vk`, after the `ConstraintSystem`
//!   is finalised (selectors compressed). Summarises every gate expression tree:
//!   node counts by variant, depth distribution, repeated subtrees, leaf reuse.
//! * [`record_eval`] — called from `Evaluator::evaluate` for every invocation.
//!   Carries the exact chunk geometry plus the node counts of the *fused* tree
//!   (gates + permutation + lookup + `distribute_powers`), which is the tree the
//!   prover really walks.
//!
//! Why this exists: Stage E3 measured `Fp::mul` at ~33% of prover self-samples,
//! with 92% of those samples sitting under `evaluate::recurse`. Sampling cannot
//! say where those multiplications come from — the recursion visits a `Vec<F>` of
//! 8k elements per node, so one source-level `*=` becomes 8192 multiplications.
//! Here the arithmetic is *counting*: field-op totals are derived as
//! `nodes × polynomial length`, which makes them checkable against the sampler
//! rather than a replacement for it.
//!
//! This is measurement scaffolding in a vendored copy, not an upstream patch.

// Measurement scaffolding: per-counter doc comments and Debug impls would be noise
// on a module that only ever prints JSON, and the crate lints both by default.
#![allow(missing_docs, missing_debug_implementations)]

use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

use ff::PrimeField;

use crate::plonk::{ConstraintSystem, Expression, VerifyingKey};

// ---------------------------------------------------------------------------
// Enabling / report plumbing
// ---------------------------------------------------------------------------

/// True when `ORCHARD_F2` is present. Cached: this is called on every `evaluate`.
pub fn enabled() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("ORCHARD_F2").is_some())
}

/// How many extra times to re-evaluate the real quotient tree (0 = don't).
pub fn bench_reps() -> usize {
    static N: OnceLock<usize> = OnceLock::new();
    *N.get_or_init(|| {
        std::env::var("ORCHARD_F2_BENCH")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(0)
    })
}

/// Term counts of the three sources that feed the fused quotient tree.
///
/// Not derivable from the `ConstraintSystem`: the permutation argument emits
/// several `Ast`s per advice column (first set, last set, the shifted
/// constraints, and the grand-product step), and the lookup argument does
/// likewise. The prover therefore reports the real lengths instead of the
/// analysis guessing them.
static SEG_COUNTS: [std::sync::atomic::AtomicUsize; 3] = [
    std::sync::atomic::AtomicUsize::new(0),
    std::sync::atomic::AtomicUsize::new(0),
    std::sync::atomic::AtomicUsize::new(0),
];

/// Wrap one of the three sources feeding the fused tree so the number of `Ast`
/// terms it actually yields can be counted.
///
/// The sources are lazy iterators, so their lengths are not knowable up front
/// (the permutation argument, for instance, emits a different number of terms
/// per advice column). Counting as they are pulled keeps the ordering and the
/// work identical — `inspect` adds one relaxed increment per term, ~430 per
/// proof.
///
/// `slot`: 0 = gate polynomials, 1 = permutation argument, 2 = lookup argument.
pub fn counted<I>(slot: usize, inner: I) -> impl Iterator<Item = I::Item>
where
    I: Iterator,
{
    inner.inspect(move |_| {
        if enabled() {
            SEG_COUNTS[slot].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        }
    })
}

fn parts() -> &'static Mutex<Vec<(&'static str, String)>> {
    static PARTS: OnceLock<Mutex<Vec<(&'static str, String)>>> = OnceLock::new();
    PARTS.get_or_init(|| Mutex::new(Vec::new()))
}

fn push(key: &'static str, json: String) {
    parts().lock().unwrap().push((key, json));
}

/// Drain everything recorded so far as one JSON object.
pub fn take_report() -> String {
    if enabled() {
        let seg: [usize; 3] = std::array::from_fn(|i| {
            SEG_COUNTS[i].load(std::sync::atomic::Ordering::Relaxed)
        });
        if seg != [0, 0, 0] {
            push(
                "segments",
                format!(
                    "{{\"gates\":{},\"permutation\":{},\"lookup\":{}}}",
                    seg[0], seg[1], seg[2]
                ),
            );
        }
    }
    let drained: Vec<(&'static str, String)> = std::mem::take(&mut *parts().lock().unwrap());
    let body = drained
        .into_iter()
        .map(|(k, v)| format!("\"{k}\":{v}"))
        .collect::<Vec<_>>()
        .join(",");
    format!("{{{body}}}")
}

// ---------------------------------------------------------------------------
// Expression trees
// ---------------------------------------------------------------------------

/// Node counts of one `Expression` tree, by variant.
#[derive(Default, Clone, Copy)]
pub struct Nodes {
    pub constant: u64,
    pub selector: u64,
    pub fixed: u64,
    pub advice: u64,
    pub instance: u64,
    pub negated: u64,
    pub sum: u64,
    pub product: u64,
    pub scaled: u64,
}

impl Nodes {
    pub fn total(&self) -> u64 {
        self.constant
            + self.selector
            + self.fixed
            + self.advice
            + self.instance
            + self.negated
            + self.sum
            + self.product
            + self.scaled
    }

    fn add(&mut self, o: &Nodes) {
        self.constant += o.constant;
        self.selector += o.selector;
        self.fixed += o.fixed;
        self.advice += o.advice;
        self.instance += o.instance;
        self.negated += o.negated;
        self.sum += o.sum;
        self.product += o.product;
        self.scaled += o.scaled;
    }

    pub fn to_json(&self) -> String {
        format!(
            "{{\"constant\":{},\"selector\":{},\"fixed\":{},\"advice\":{},\"instance\":{},\
\"negated\":{},\"sum\":{},\"product\":{},\"scaled\":{},\"total\":{}}}",
            self.constant,
            self.selector,
            self.fixed,
            self.advice,
            self.instance,
            self.negated,
            self.sum,
            self.product,
            self.scaled,
            self.total()
        )
    }
}

/// FNV-1a over bytes; shared with the `Ast` accounting in `poly::evaluator`.
#[inline]
pub(crate) fn fnv(bytes: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in bytes {
        h ^= *b as u64;
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    h
}

/// Tagged pair combiner; shared with the `Ast` accounting in `poly::evaluator`.
#[inline]
pub(crate) fn comb(tag: u64, a: u64, b: u64) -> u64 {
    let mut h = 0xcbf2_9ce4_8422_2325u64 ^ tag;
    h = h.wrapping_mul(0x0000_0100_0000_01b3) ^ a;
    h = h.wrapping_mul(0x0000_0100_0000_01b3) ^ b;
    h
}

#[inline]
fn query_key(index: usize, column: usize, rotation: i32) -> u64 {
    fnv(&[
        index.to_le_bytes().as_slice(),
        column.to_le_bytes().as_slice(),
        (rotation as i64).to_le_bytes().as_slice(),
    ]
    .concat())
}

/// One-pass accounting of a single expression tree.
///
/// Returns node counts, depth and the structural hash of the root, and (as side
/// effects) the number of times each *distinct* subtree hash occurs. No memo:
/// every node is visited exactly once, so repeats are counted as repeats. Trees
/// here are 10^2 nodes, so linear re-walking is not a concern.
fn analyze<F: PrimeField>(
    expr: &Expression<F>,
    hashes: &mut HashMap<u64, (u64, u64)>,
    leaves: &mut HashMap<(u8, u64), u64>,
    leaf_occ: &mut u64,
) -> (Nodes, u32, u64) {
    let mut n = Nodes::default();
    let (depth, hash) = match expr {
        Expression::Constant(c) => {
            n.constant = 1;
            (0, comb(1, fnv(c.to_repr().as_ref()), 0))
        }
        // Virtual selectors are compressed away before the prover sees the CS;
        // if one survives, `selector` in the report is non-zero and that is a bug.
        Expression::Selector(s) => {
            n.selector = 1;
            *leaf_occ += 1;
            *leaves.entry((2, fnv(&s.0.to_le_bytes()))).or_insert(0) += 1;
            (0, comb(2, fnv(&s.0.to_le_bytes()), 0))
        }
        Expression::Fixed(q) => {
            n.fixed = 1;
            let k = query_key(q.index, q.column_index, q.rotation.0);
            *leaf_occ += 1;
            *leaves.entry((3, k)).or_insert(0) += 1;
            (0, comb(3, k, 0))
        }
        Expression::Advice(q) => {
            n.advice = 1;
            let k = query_key(q.index, q.column_index, q.rotation.0);
            *leaf_occ += 1;
            *leaves.entry((4, k)).or_insert(0) += 1;
            (0, comb(4, k, 0))
        }
        Expression::Instance(q) => {
            n.instance = 1;
            let k = query_key(q.index, q.column_index, q.rotation.0);
            *leaf_occ += 1;
            *leaves.entry((5, k)).or_insert(0) += 1;
            (0, comb(5, k, 0))
        }
        Expression::Negated(a) => {
            let (na, da, ha) = analyze(a, hashes, leaves, leaf_occ);
            n.add(&na);
            n.negated += 1;
            (da + 1, comb(6, ha, 0))
        }
        Expression::Sum(a, b) => {
            let (na, da, ha) = analyze(a, hashes, leaves, leaf_occ);
            let (nb, db, hb) = analyze(b, hashes, leaves, leaf_occ);
            n.add(&na);
            n.add(&nb);
            n.sum += 1;
            (da.max(db) + 1, comb(7, ha, hb))
        }
        Expression::Product(a, b) => {
            let (na, da, ha) = analyze(a, hashes, leaves, leaf_occ);
            let (nb, db, hb) = analyze(b, hashes, leaves, leaf_occ);
            n.add(&na);
            n.add(&nb);
            n.product += 1;
            (da.max(db) + 1, comb(8, ha, hb))
        }
        Expression::Scaled(a, c) => {
            let (na, da, ha) = analyze(a, hashes, leaves, leaf_occ);
            n.add(&na);
            n.scaled += 1;
            (da + 1, comb(9, ha, fnv(c.to_repr().as_ref())))
        }
    };
    // Record this node's structural identity. `count` accumulates so repeats
    // stand out; `local` is 1 when this node itself multiplies two whole chunks
    // (Product / Scaled) and 0 otherwise.
    //
    // Recording the *local* cost rather than the subtree cost matters: a subtree
    // cost would be counted again at every enclosing node, and a tree of nested
    // repeats would appear to save more multiplications than it contains. With
    // local costs, the saving is simply
    //     (multiplications in the tree) - (multiplications in its DAG)
    // which is exactly what a shared-evaluation plan could remove, and can never
    // exceed the total.
    let local = u64::from(matches!(expr, Expression::Product(..) | Expression::Scaled(..)));
    let entry = hashes.entry(hash).or_insert((0, local));
    entry.0 += 1;
    entry.1 = local;
    (n, depth, hash)
}

/// Aggregate accounting for a whole `ConstraintSystem`.
pub struct CsStats {
    pub gates: usize,
    pub gate_polys: usize,
    pub nodes: Nodes,
    pub max_depth: u32,
    pub depth_hist: Vec<(u32, u64)>,
    pub distinct_subtrees: u64,
    pub repeat_nodes: u64,
    pub max_repeat: u64,
    /// Vector multiplications a perfect CSE over the gate expressions would save,
    /// in `Vec<F>` units: multiplications in the tree minus multiplications in
    /// its DAG. Never exceeds the tree total.
    pub cse_saving_vector_muls: u64,
    pub leaf_occ: u64,
    pub leaf_distinct: u64,
    pub advice_queries: usize,
    pub fixed_queries: usize,
    pub instance_queries: usize,
    pub advice_columns: usize,
    pub fixed_columns: usize,
    pub min_degree: usize,
    pub max_degree: usize,
}

fn cs_stats<F: PrimeField>(cs: &ConstraintSystem<F>) -> CsStats {
    let mut nodes = Nodes::default();
    let mut max_depth = 0u32;
    let mut depth_hist: HashMap<u32, u64> = HashMap::new();
    let mut subtree_hashes: HashMap<u64, (u64, u64)> = HashMap::new();
    let mut leaves: HashMap<(u8, u64), u64> = HashMap::new();
    let mut leaf_occ = 0u64;
    let mut gate_polys = 0usize;

    for gate in cs.gates.iter() {
        for expr in gate.polynomials().iter() {
            gate_polys += 1;
            let (n, d, _) = analyze(expr, &mut subtree_hashes, &mut leaves, &mut leaf_occ);
            nodes.add(&n);
            max_depth = max_depth.max(d);
            *depth_hist.entry(d).or_insert(0) += 1;
        }
    }

    let repeat_nodes = subtree_hashes.values().map(|(c, _)| c - 1).sum::<u64>();
    let max_repeat = subtree_hashes.values().map(|(c, _)| *c).max().unwrap_or(0);
    // CSE headroom: multiplications that vanish if every structural repeat is
    // evaluated once. Equals `sum(local) over all nodes - sum(local) over
    // distinct nodes`, because the fused tree already shares nothing.
    let total_local: u64 = nodes.product + nodes.scaled;
    let distinct_local: u64 = subtree_hashes.values().map(|(_, local)| local).sum();
    let cse_saving_vector_muls = total_local.saturating_sub(distinct_local);
    let mut depth_hist: Vec<(u32, u64)> = depth_hist.into_iter().collect();
    depth_hist.sort_unstable();

    CsStats {
        gates: cs.gates.len(),
        gate_polys,
        nodes,
        max_depth,
        depth_hist,
        distinct_subtrees: subtree_hashes.len() as u64,
        repeat_nodes,
        max_repeat,
        cse_saving_vector_muls,
        leaf_occ,
        leaf_distinct: leaves.len() as u64,
        advice_queries: cs.advice_queries.len(),
        fixed_queries: cs.fixed_queries.len(),
        instance_queries: cs.instance_queries.len(),
        advice_columns: cs.num_advice_columns,
        fixed_columns: cs.num_fixed_columns,
        min_degree: cs.minimum_degree.unwrap_or(0),
        max_degree: cs.degree(),
    }
}

impl CsStats {
    fn to_json(&self) -> String {
        let hist = self
            .depth_hist
            .iter()
            .map(|(d, c)| format!("{{\"depth\":{d},\"polys\":{c}}}"))
            .collect::<Vec<_>>()
            .join(",");
        format!(
            "{{\"gates\":{},\"gate_polys\":{},\"nodes\":{},\"max_depth\":{},\
\"depth_hist\":[{}],\"distinct_subtrees\":{},\"repeat_nodes\":{},\"max_repeat\":{},\
\"cse_saving_vector_muls\":{},\
\"leaf_occ\":{},\"leaf_distinct\":{},\"advice_queries\":{},\"fixed_queries\":{},\
\"instance_queries\":{},\"advice_columns\":{},\"fixed_columns\":{},\
\"min_degree\":{},\"max_degree\":{}}}",
            self.gates,
            self.gate_polys,
            self.nodes.to_json(),
            self.max_depth,
            hist,
            self.distinct_subtrees,
            self.repeat_nodes,
            self.max_repeat,
            self.cse_saving_vector_muls,
            self.leaf_occ,
            self.leaf_distinct,
            self.advice_queries,
            self.fixed_queries,
            self.instance_queries,
            self.advice_columns,
            self.fixed_columns,
            self.min_degree,
            self.max_degree
        )
    }
}

/// Entry point from `keygen_vk`. No-op unless `ORCHARD_F2` is set.
pub fn maybe_run<C>(vk: &VerifyingKey<C>)
where
    C: crate::arithmetic::CurveAffine,
    C::Scalar: PrimeField,
{
    if !enabled() {
        return;
    }
    let stats = cs_stats(vk.cs_f2());
    push("cs", stats.to_json());
}

// ---------------------------------------------------------------------------
// Evaluator accounting
// ---------------------------------------------------------------------------

/// Node counts of the fused `Ast` the prover actually evaluates.
#[derive(Default, Clone)]
pub struct AstStats {
    pub poly: u64,
    pub add: u64,
    pub mul: u64,
    pub scale: u64,
    pub distribute: u64,
    pub distribute_terms: u64,
    pub linear: u64,
    pub constant: u64,
    pub max_depth: u32,
    /// Per top-level `distribute_powers` term: (mul, scale, poly, add, nodes).
    /// The terms arrive in a known order — every gate expression first, then the
    /// permutation arguments, then the lookup arguments — so this breakdown is
    /// what separates "gate constraints" from "the rest" in the fused tree.
    pub terms: Vec<[u64; 5]>,
}

impl AstStats {
    /// Multiplications of a whole `Vec<F>` (i.e. `len` field muls each):
    /// `Mul`, `Scale`, and the `acc *= base` step of every `distribute_powers`
    /// term. This is the number Stage E3's `Fp::mul` samples should account for.
    pub fn vector_muls(&self) -> u64 {
        self.mul + self.scale + self.distribute_terms
    }

    fn add(&mut self, o: &AstStats) {
        self.poly += o.poly;
        self.add += o.add;
        self.mul += o.mul;
        self.scale += o.scale;
        self.distribute += o.distribute;
        self.distribute_terms += o.distribute_terms;
        self.linear += o.linear;
        self.constant += o.constant;
        self.max_depth = self.max_depth.max(o.max_depth);
    }

    fn to_json(&self) -> String {
        let terms = self
            .terms
            .iter()
            .map(|t| format!("[{},{},{},{},{}]", t[0], t[1], t[2], t[3], t[4]))
            .collect::<Vec<_>>()
            .join(",");
        format!(
            "{{\"poly\":{},\"add\":{},\"mul\":{},\"scale\":{},\"distribute\":{},\
\"distribute_terms\":{},\"linear\":{},\"constant\":{},\"nodes\":{},\
\"max_depth\":{},\"vector_muls\":{},\"terms\":[{}]}}",
            self.poly,
            self.add,
            self.mul,
            self.scale,
            self.distribute,
            self.distribute_terms,
            self.linear,
            self.constant,
            self.poly
                + self.add
                + self.mul
                + self.scale
                + self.distribute
                + self.linear
                + self.constant,
            self.max_depth,
            self.vector_muls(),
            terms
        )
    }
}

/// One `Evaluator::evaluate` invocation as observed from inside.
pub struct EvalRecord {
    pub poly_len: usize,
    pub chunk_size: usize,
    pub num_chunks: usize,
    pub ast: AstStats,
    /// Wall time of the single call that ran as part of the proof.
    pub first_ms: f64,
    /// Median over the optional re-runs (`ORCHARD_F2_BENCH`); 0 when not repeated.
    pub bench_median_ms: f64,
    pub bench_min_ms: f64,
    pub bench_max_ms: f64,
    pub bench_reps: usize,
}

static EVALS: OnceLock<Mutex<Vec<EvalRecord>>> = OnceLock::new();

fn evals() -> &'static Mutex<Vec<EvalRecord>> {
    EVALS.get_or_init(|| Mutex::new(Vec::new()))
}

/// Record one evaluation. `bench` carries the re-run samples, if any.
pub fn record_eval(
    poly_len: usize,
    chunk_size: usize,
    num_chunks: usize,
    ast: AstStats,
    first_ms: f64,
    bench: &mut Vec<f64>,
) {
    if !enabled() {
        return;
    }
    bench.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let rec = EvalRecord {
        poly_len,
        chunk_size,
        num_chunks,
        ast,
        first_ms,
        bench_median_ms: if bench.is_empty() {
            0.0
        } else {
            bench[bench.len() / 2]
        },
        bench_min_ms: bench.first().copied().unwrap_or(0.0),
        bench_max_ms: bench.last().copied().unwrap_or(0.0),
        bench_reps: bench.len(),
    };
    evals().lock().unwrap().push(rec);
}

/// Fold all recorded evaluations into a report part. Called by `take_report`
/// callers after the proving run; kept separate so the numbers stay per-call.
pub fn flush_evals() {
    let drained: Vec<EvalRecord> = std::mem::take(&mut *evals().lock().unwrap());
    if drained.is_empty() {
        push("evals", "[]".to_string());
        return;
    }
    let mut total = AstStats::default();
    let mut total_poly_len = 0u64;
    let mut total_muls = 0u64;
    let mut total_adds = 0u64;
    let mut total_leaf_elems = 0u64;
    let mut calls = 0u64;
    let mut items = Vec::new();

    for r in &drained {
        total.add(&r.ast);
        total_poly_len += r.poly_len as u64;
        total_muls += r.ast.vector_muls() * r.poly_len as u64;
        total_adds += r.ast.add * r.poly_len as u64;
        total_leaf_elems += r.ast.poly * r.poly_len as u64;
        calls += 1;
        items.push(format!(
            "{{\"poly_len\":{},\"chunk_size\":{},\"num_chunks\":{},\"ast\":{},\
\"first_ms\":{:.6},\"bench_reps\":{},\"bench_median_ms\":{:.6},\
\"bench_min_ms\":{:.6},\"bench_max_ms\":{:.6}}}",
            r.poly_len,
            r.chunk_size,
            r.num_chunks,
            r.ast.to_json(),
            r.first_ms,
            r.bench_reps,
            r.bench_median_ms,
            r.bench_min_ms,
            r.bench_max_ms
        ));
    }

    let summary = format!(
        "{{\"calls\":{},\"sum_poly_len\":{},\"total_nodes\":{},\"vector_muls_total\":{},\
\"field_muls_total\":{},\"field_adds_total\":{},\"leaf_elements_copied\":{},\"items\":[{}]}}",
        calls,
        total_poly_len,
        total.poly + total.add + total.mul + total.scale + total.distribute + total.linear + total.constant,
        total.vector_muls(),
        total_muls,
        total_adds,
        total_leaf_elems,
        items.join(",")
    );
    push("evals", summary);
}
