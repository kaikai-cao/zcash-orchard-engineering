//! Record the *actual row layout* of the Orchard Action circuit under
//! `floor_planner::V1`, i.e. the planner Orchard really uses.
//!
//! Why this exists
//! ---------------
//! `ConstraintSystem` tells us the gate polynomials and the number of columns,
//! but not how many rows each gadget occupies, nor where each selector is
//! enabled. `halo2_proofs` has no public `CircuitLayout` in 0.4, and
//! `Gate`/`Selector`'s indices are `pub(crate)`. What *is* public is the
//! `Assignment` trait, which every floor planner drives. So we implement a
//! recording `Assignment` and hand it to `floor_planner::V1::synthesize`:
//! V1's internal measurement pass is its own `Assignment`, so the recorder only
//! sees the single real assignment pass.
//!
//! One deliberate shortcut: `Column::new` and `ConstraintSystem::constants` are
//! both `pub(crate)`, so there is no way to hand V1 the list of fixed columns
//! that Orchard equality-enabled for constants (it uses `lagrange_coeffs[0]`).
//! We therefore pass an empty list, which makes V1 return
//! `Error::NotEnoughColumnsForConstants` *after* the assignment pass. Region and
//! selector records are complete at that point; we assert the error is exactly
//! that one so no other failure is silently swallowed.
//!
//! Row counts here are *layout*, not proving cost: a 126-row region and a
//! 1-row region both live inside the same K=11 domain, and gate cost depends on
//! enabled selectors and polynomial degrees, not on rows alone.

use std::collections::{BTreeMap, BTreeSet};

use halo2_proofs::{
    circuit::{floor_planner::V1, Value},
    plonk::{
        Advice, Assigned, Assignment, Circuit as PlonkCircuit, Column, ColumnType, ConstraintSystem,
        Error, Fixed, FloorPlanner, Instance, Selector,
    },
};
use incrementalmerkletree::{Hashable, Level};
use orchard::{
    builder::SpendInfo,
    bundle::BundleVersion,
    circuit::{Circuit as ActionCircuit, OrchardCircuitVersion},
    keys::{FullViewingKey, Scope, SpendingKey},
    note::{Note, RandomSeed, Rho},
    tree::{MerkleHashOrchard, MerklePath},
    value::{NoteValue, ValueCommitTrapdoor},
};
use pasta_curves::pallas;
use rand::{rngs::StdRng, Rng, SeedableRng};

const MERKLE_DEPTH: usize = 32;

// ---------------------------------------------------------------------------
// Column / selector naming
//
// `Column::index()` and `Selector.0` are `pub(crate)`; the derived `Debug`
// impls are the only public view. They render as
//   Column { index: 3, column_type: Advice }
//   Selector(16, true)
// so we scrape them once here rather than sprinkling format! calls around.
// ---------------------------------------------------------------------------

fn col_tag<C: ColumnType>(c: &Column<C>) -> String {
    let s = format!("{c:?}");
    let idx = s
        .split("index: ")
        .nth(1)
        .and_then(|r| r.split(',').next())
        .expect("column index");
    let ty = s
        .split("column_type: ")
        .nth(1)
        .map(|r| r.trim_end_matches(" }"))
        .unwrap_or("?");
    let letter = match ty {
        "Advice" => "A",
        "Fixed" => "F",
        "Instance" => "I",
        other => other,
    };
    format!("{letter}{}", idx.trim())
}

fn sel_tag(s: &Selector) -> String {
    let dbg = format!("{s:?}"); // "Selector(16, true)"
    let idx = dbg
        .split('(')
        .nth(1)
        .and_then(|r| r.split(',').next())
        .expect("selector index");
    format!("S{}", idx.trim())
}

// ---------------------------------------------------------------------------
// Recording assignment
// ---------------------------------------------------------------------------

#[derive(Default)]
struct RegionRec {
    name: String,
    used: bool,
    min: usize,
    max: usize,
    rows: BTreeSet<usize>,
    cols: BTreeSet<String>,
    selectors: BTreeMap<String, usize>,
}

#[derive(Default)]
struct Recorder {
    ns: Vec<String>,
    cur: Option<usize>,
    regions: Vec<RegionRec>,
    col_rows: BTreeMap<String, BTreeSet<usize>>,
    sel_acts: BTreeMap<String, usize>,
    copies: usize,
    fixed_rows: BTreeSet<usize>,
}

impl Recorder {
    fn touch(&mut self, row: usize, col: Option<String>) {
        if let Some(i) = self.cur {
            let r = &mut self.regions[i];
            if !r.used {
                r.used = true;
                r.min = row;
                r.max = row;
            }
            r.min = r.min.min(row);
            r.max = r.max.max(row);
            r.rows.insert(row);
            if let Some(c) = col {
                r.cols.insert(c);
            }
        }
    }
}

impl Assignment<pallas::Base> for Recorder {
    fn enter_region<NR, N>(&mut self, name_fn: N)
    where
        NR: Into<String>,
        N: FnOnce() -> NR,
    {
        let own: String = name_fn().into();
        let name = if self.ns.is_empty() {
            own
        } else {
            format!("{}/{}", self.ns.join("/"), own)
        };
        self.regions.push(RegionRec {
            name,
            ..Default::default()
        });
        self.cur = Some(self.regions.len() - 1);
    }

    fn exit_region(&mut self) {
        self.cur = None;
    }

    fn enable_selector<A, AR>(
        &mut self,
        _annotation: A,
        selector: &Selector,
        row: usize,
    ) -> Result<(), Error>
    where
        A: FnOnce() -> AR,
        AR: Into<String>,
    {
        let tag = sel_tag(selector);
        self.touch(row, None);
        if let Some(i) = self.cur {
            *self.regions[i].selectors.entry(tag.clone()).or_insert(0) += 1;
        }
        *self.sel_acts.entry(tag).or_insert(0) += 1;
        Ok(())
    }

    fn query_instance(&self, _column: Column<Instance>, _row: usize) -> Result<Value<pallas::Base>, Error> {
        Ok(Value::unknown())
    }

    fn assign_advice<V, VR, A, AR>(
        &mut self,
        _annotation: A,
        column: Column<Advice>,
        row: usize,
        _to: V,
    ) -> Result<(), Error>
    where
        V: FnOnce() -> Value<VR>,
        VR: Into<Assigned<pallas::Base>>,
        A: FnOnce() -> AR,
        AR: Into<String>,
    {
        let tag = col_tag(&column);
        self.touch(row, Some(tag.clone()));
        self.col_rows.entry(tag).or_default().insert(row);
        Ok(())
    }

    fn assign_fixed<V, VR, A, AR>(
        &mut self,
        _annotation: A,
        column: Column<Fixed>,
        row: usize,
        _to: V,
    ) -> Result<(), Error>
    where
        V: FnOnce() -> Value<VR>,
        VR: Into<Assigned<pallas::Base>>,
        A: FnOnce() -> AR,
        AR: Into<String>,
    {
        let tag = col_tag(&column);
        self.touch(row, Some(tag.clone()));
        self.col_rows.entry(tag).or_default().insert(row);
        self.fixed_rows.insert(row);
        Ok(())
    }

    fn copy(
        &mut self,
        _left_column: Column<halo2_proofs::plonk::Any>,
        _left_row: usize,
        _right_column: Column<halo2_proofs::plonk::Any>,
        _right_row: usize,
    ) -> Result<(), Error> {
        self.copies += 1;
        Ok(())
    }

    fn fill_from_row(
        &mut self,
        _column: Column<Fixed>,
        _row: usize,
        _to: Value<Assigned<pallas::Base>>,
    ) -> Result<(), Error> {
        Ok(())
    }

    fn push_namespace<NR, N>(&mut self, name_fn: N)
    where
        NR: Into<String>,
        N: FnOnce() -> NR,
    {
        self.ns.push(name_fn().into());
    }

    fn pop_namespace(&mut self, _gadget_name: Option<String>) {
        self.ns.pop();
    }
}

// ---------------------------------------------------------------------------
// Fixture: one real 1-spend Action
// ---------------------------------------------------------------------------

fn build_circuit(version: OrchardCircuitVersion) -> ActionCircuit {
    let mut rng = StdRng::seed_from_u64(7);
    let sk = SpendingKey::from_bytes([7u8; 32]).expect("spending key");
    let fvk = FullViewingKey::from(&sk);
    let recipient = fvk.address_at(0u32, Scope::External);
    let note_version = BundleVersion::orchard_v2().note_version();

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
                NoteValue::from_raw(100_000_000),
                rho,
                rseed,
                note_version,
            )) {
                break n;
            }
        }
    };

    let auth_path: [MerkleHashOrchard; MERKLE_DEPTH] =
        core::array::from_fn(|l| MerkleHashOrchard::empty_root(Level::from(l as u8)));
    let path = MerklePath::from_parts(0, auth_path);
    let spend = SpendInfo::new(fvk.clone(), note, path).expect("spend info owns note");

    // The output note's rho must be the spent note's nullifier, otherwise
    // `from_action_context` refuses to build the circuit.
    // `Rho::from_nf_old` is `pub(crate)`; the public equivalent is to re-read the
    // nullifier's x-coordinate bytes as a canonical base-field element.
    let nf = note.nullifier(&fvk);
    let rho_new = Option::<Rho>::from(Rho::from_bytes(&nf.to_bytes())).expect("rho from nf");
    let out = loop {
        let mut s = [0u8; 32];
        rng.fill_bytes(&mut s);
        if let Some(rseed) = Option::<RandomSeed>::from(RandomSeed::from_bytes(s, &rho_new)) {
            if let Some(n) = Option::<Note>::from(Note::from_parts(
                recipient,
                NoteValue::from_raw(90_000_000),
                rho_new,
                rseed,
                note_version,
            )) {
                break n;
            }
        }
    };

    let alpha = pallas::Scalar::from(7u64);
    let rcv = Option::<ValueCommitTrapdoor>::from(ValueCommitTrapdoor::from_bytes([3u8; 32]))
        .expect("canonical trapdoor");

    ActionCircuit::from_action_context(spend, out, alpha, rcv, version).expect("action circuit")
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

fn main() {
    let version = match std::env::args().nth(1).as_deref() {
        Some("v3") | Some("postnu6_3") => OrchardCircuitVersion::PostNu6_3,
        _ => OrchardCircuitVersion::FixedPostNu6_2,
    };

    let mut cs = ConstraintSystem::<pallas::Base>::default();
    let config = <ActionCircuit as PlonkCircuit<pallas::Base>>::configure(&mut cs);
    let circuit = build_circuit(version);

    let mut rec = Recorder::default();
    let res = V1::synthesize(&mut rec, &circuit, config, Vec::new());

    match &res {
        Ok(()) => println!("NOTE V1::synthesize returned Ok (constants list accepted)"),
        Err(e) => {
            println!("NOTE V1::synthesize returned Err: {e:?}");
            let expected = matches!(e, Error::NotEnoughColumnsForConstants);
            println!("NOTE error_is_NotEnoughColumnsForConstants {expected}");
            if !expected {
                panic!("unexpected floor-planner error: {e:?}");
            }
        }
    }

    println!("VERSION {version:?}");
    println!("DEGREE {}", cs.degree());
    println!("REGIONS {}", rec.regions.len());
    println!("COPIES {}", rec.copies);
    println!();

    // ---- per-region table -------------------------------------------------
    println!("# region,first_row,last_row,rows_used,extent,advice_cols,selectors");
    let mut order: Vec<usize> = (0..rec.regions.len()).collect();
    order.sort_by_key(|&i| (rec.regions[i].min, i));
    for i in order {
        let r = &rec.regions[i];
        if !r.used {
            println!("{},{},-,-,-,-,-", i, r.name);
            continue;
        }
        let cols: Vec<&str> = r.cols.iter().map(|s| s.as_str()).collect();
        let sels: Vec<String> = r
            .selectors
            .iter()
            .map(|(k, v)| format!("{k}x{v}"))
            .collect();
        println!(
            "{},{},{},{},{},{},{},{}",
            i,
            r.name,
            r.min,
            r.max,
            r.rows.len(),
            r.max - r.min + 1,
            cols.join("|"),
            sels.join("|")
        );
    }
    println!();

    // ---- advice-column occupancy -----------------------------------------
    println!("# advice column,distinct_rows,first,last");
    for (tag, rows) in &rec.col_rows {
        if !tag.starts_with('A') {
            continue;
        }
        if let (Some(f), Some(l)) = (rows.iter().next(), rows.iter().next_back()) {
            println!("{tag},{},{f},{l}", rows.len());
        }
    }
    println!();

    // ---- selector activation totals --------------------------------------
    println!("# selector,activations");
    for (tag, n) in &rec.sel_acts {
        println!("{tag},{n}");
    }
    println!();

    // ---- the biggest regions (by extent) ---------------------------------
    let mut by_extent: Vec<(usize, &RegionRec)> = rec
        .regions
        .iter()
        .filter(|r| r.used)
        .map(|r| (r.max - r.min + 1, r))
        .collect();
    by_extent.sort_by_key(|(e, _)| std::cmp::Reverse(*e));
    println!("# top 12 regions by row extent");
    for (ext, r) in by_extent.iter().take(12) {
        println!("{ext:4}  {}", r.name);
    }
}
