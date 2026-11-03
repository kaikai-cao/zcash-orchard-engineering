//! Dumps the Orchard Action circuit's *constraint-system shape* without touching
//! the upstream checkout.
//!
//! `Config::configure` is private, but `Circuit::configure` (the halo2 trait
//! method) is public and just forwards to it, so we can build the CS from
//! outside the crate. Everything printed here is read off halo2's own
//! `ConstraintSystem`, not inferred from reading `configure()`.
//!
//! Usage: cargo run --release --bin cs_dump -- [out_dir]

use std::path::PathBuf;

use halo2_proofs::plonk::{Circuit as PlonkCircuit, ConstraintSystem};
use orchard::circuit::Circuit as OrchardCircuit;
use pasta_curves::pallas;

fn main() {
    let out_dir: PathBuf = std::env::args()
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));

    // NOTE: `configure` is circuit-version independent — the trait impl calls
    // `Config::configure(meta)` and never looks at `self.circuit_version`.
    let mut cs = ConstraintSystem::<pallas::Base>::default();
    let _config = <OrchardCircuit as PlonkCircuit<pallas::Base>>::configure(&mut cs);

    println!("DEGREE {}", cs.degree());
    println!("BLINDING_FACTORS {}", cs.blinding_factors());
    println!("MINIMUM_ROWS {}", cs.minimum_rows());

    let pinned = format!("{:#?}", cs.pinned());
    let path = out_dir.join("cs_pinned.txt");
    std::fs::write(&path, &pinned).expect("write cs_pinned.txt");
    println!("WROTE {} ({} bytes)", path.display(), pinned.len());

    // Structural head of the pinned CS: the four column/selector counts.
    for line in pinned.lines().take(8) {
        println!("HEAD {}", line.trim());
    }
}
