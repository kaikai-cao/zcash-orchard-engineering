//! F4-A.4 cross-circuit census: a Poseidon-only Halo2 circuit.
//!
//! This binary contains **no Orchard gate**. It configures
//! `halo2_gadgets::poseidon::Pow5Chip` and hashes one two-element message, so
//! its `ConstraintSystem` differs from Orchard's in every gate, every selector
//! and every column, while still going through the same `compress_selectors`
//! path inside `halo2_proofs`.
//!
//! Why this circuit is the right second sample: each of its three gates carries
//! `WIDTH` (3) constraints under **one** selector, which is exactly the shape
//! that makes the selector-recovery expression appear more than once. Orchard's
//! `Orchard circuit checks` gate is 4 constraints under one selector; this one
//! is 3 under one selector. The mechanism can therefore be checked for
//! presence, independently of Orchard's size.
//!
//! Run with the read-only probe patch applied and `ORCHARD_F4A=1`, which makes
//! `keygen` dump the `F4A_CS` / `F4A_CHAIN` / `F4A_OCC` lines consumed by
//! `scripts/analysis/xcensus_report.py`.
//!
//! Read-only: the F4-A implementation patch is not applied, so no `Ast::Memo`
//! exists and the evaluator is the frozen one.

use halo2_gadgets::poseidon::{
    primitives::{ConstantLength, P128Pow5T3},
    Hash, Pow5Chip, Pow5Config,
};
use halo2_proofs::{
    circuit::{Layouter, SimpleFloorPlanner, Value},
    plonk::{Circuit, ConstraintSystem, Error},
};
use pasta_curves::{pallas, vesta};

const WIDTH: usize = 3;
const RATE: usize = 2;
/// Message length absorbed by the sponge.
const LEN: usize = 2;

#[derive(Clone, Debug)]
struct PoseidonConfig {
    pow: Pow5Config<pallas::Base, WIDTH, RATE>,
    /// The state columns are ours (we allocate them in `configure`), so the
    /// message can be assigned without touching `Pow5Config`'s private fields.
    state: [halo2_proofs::plonk::Column<halo2_proofs::plonk::Advice>; WIDTH],
}

#[derive(Clone, Debug, Default)]
struct PoseidonOnly {
    message: [pallas::Base; LEN],
}

impl Circuit<pallas::Base> for PoseidonOnly {
    type Config = PoseidonConfig;
    type FloorPlanner = SimpleFloorPlanner;

    fn without_witnesses(&self) -> Self {
        Self::default()
    }

    fn configure(meta: &mut ConstraintSystem<pallas::Base>) -> Self::Config {
        let state = [(); WIDTH].map(|_| meta.advice_column());
        let partial_sbox = meta.advice_column();
        let rc_a = [(); WIDTH].map(|_| meta.fixed_column());
        let rc_b = [(); WIDTH].map(|_| meta.fixed_column());
        // Columns for constants (the floor planner needs at least one).
        let consts = [(); 4].map(|_| meta.fixed_column());
        for c in consts {
            meta.enable_constant(c);
        }
        let pow = Pow5Chip::configure::<P128Pow5T3>(meta, state, partial_sbox, rc_a, rc_b);

        let hi = if std::env::var("XCENSUS_VARIANT").as_deref() == Ok("hi") {
            let q_hi = meta.selector();
            meta.create_gate("degree filler", |meta| {
                let q = meta.query_selector(q_hi);
                let x = meta.query_advice(state[0], halo2_proofs::poly::Rotation::cur());
                let x2 = x.clone() * x.clone();
                let x4 = x2.clone() * x2.clone();
                let x8 = x4.clone() * x4.clone();
                // degree 9, so `ConstraintSystem::degree()` becomes 9.
                vec![q * (x8 * x.clone() - x)]
            });
            Some(q_hi)
        } else {
            None
        };

        let _ = hi;
        PoseidonConfig { pow, state }
    }

    fn synthesize(
        &self,
        config: Self::Config,
        mut layouter: impl Layouter<pallas::Base>,
    ) -> Result<(), Error> {
        let chip = Pow5Chip::construct(config.pow.clone());
        let msg = self.message;

        let cells = layouter.assign_region(
            || "message",
            |mut region| {
                let mut out = Vec::with_capacity(LEN);
                for (row, v) in msg.iter().enumerate() {
                    out.push(region.assign_advice(
                        || format!("m{row}"),
                        config.state[0],
                        row,
                        || Value::known(*v),
                    )?);
                }
                Ok::<_, Error>(out)
            },
        )?;

        let hasher = Hash::<pallas::Base, _, P128Pow5T3, ConstantLength<LEN>, WIDTH, RATE>::init(
            chip,
            layouter.namespace(|| "init"),
        )?;
        let _output = hasher.hash(
            layouter.namespace(|| "hash"),
            [cells[0].clone(), cells[1].clone()],
        )?;
        Ok(())
    }
}

fn main() {
    use halo2_proofs::{
        plonk::{create_proof, keygen_pk, keygen_vk, verify_proof, SingleVerifier},
        poly::commitment::Params,
        transcript::{Blake2bRead, Blake2bWrite, Challenge255},
    };
    use rand::{rngs::StdRng, SeedableRng};

    let k: u32 = std::env::var("XCENSUS_K")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(11);

    let circuit = PoseidonOnly {
        message: [pallas::Base::from(1u64), pallas::Base::from(2u64)],
    };

    let params: Params<vesta::Affine> = Params::new(k);
    let vk = keygen_vk(&params, &circuit).expect("keygen_vk");
    let pk = keygen_pk(&params, vk.clone(), &circuit).expect("keygen_pk");

    let mut rng = StdRng::from_seed([0x5au8; 32]);
    let mut transcript = Blake2bWrite::<_, vesta::Affine, Challenge255<_>>::init(vec![]);
    create_proof(
        &params,
        &pk,
        &[circuit.clone()],
        &[&[]],
        &mut rng,
        &mut transcript,
    )
    .expect("create_proof");
    let proof = transcript.finalize();

    let mut transcript = Blake2bRead::<_, vesta::Affine, Challenge255<_>>::init(&proof[..]);
    let strategy = SingleVerifier::new(&params);
    verify_proof(&params, &vk, strategy, &[&[]], &mut transcript).expect("verify_proof");

    let mut fnv: u64 = 0xcbf29ce484222325;
    for b in &proof {
        fnv ^= *b as u64;
        fnv = fnv.wrapping_mul(0x100000001b3);
    }
    println!(
        "XCENSUS_RUN {{\"circuit\":\"poseidon_only\",\"variant\":\"{}\",\"k\":{},\"proof_bytes\":{},\"proof_fnv\":\"0x{:016x}\"}}",
        std::env::var("XCENSUS_VARIANT").unwrap_or_else(|_| "base".into()),
        k,
        proof.len(),
        fnv
    );
}
