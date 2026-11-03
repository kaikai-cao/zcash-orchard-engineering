//! Dump every custom gate registered by `orchard::circuit::Circuit::configure`,
//! with its constraints rendered as fully-parenthesised infix expressions.
//!
//! Motivation: `ConstraintSystem::degree()` reports a single maximum over all
//! gates, so it cannot tell *which* gate reaches degree 9. halo2 0.4 keeps
//! `Gate`/`polynomials()` at `pub(crate)`, but `dev::CircuitGates` re-runs
//! `Circuit::configure` on a fresh `ConstraintSystem` and stringifies every
//! polynomial, preserving parentheses exactly where precedence is ambiguous.
//! Pairing this dump with a small parser (see `scripts/analysis/gate_degree.py`)
//! gives a per-gate degree, i.e. an attribution rather than an upper bound.
//!
//! `collect` only calls `configure`, so no witness is required.

use halo2_proofs::dev::CircuitGates;
use pasta_curves::pallas;

fn main() {
    let gates = CircuitGates::collect::<pallas::Base, orchard::circuit::Circuit>();
    print!("{gates}");
}
