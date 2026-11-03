use ff::Field;
use rand_core::Rng;
use std::iter;
use std::ops::RangeTo;

use super::{
    circuit::{
        Advice, Any, Assignment, Circuit, Column, ConstraintSystem, Fixed, FloorPlanner, Instance,
        Selector,
    },
    lookup, permutation, vanishing, ChallengeBeta, ChallengeGamma, ChallengeTheta, ChallengeX,
    ChallengeY, Error, ProvingKey,
};
use crate::{
    arithmetic::{eval_polynomial, CurveAffine, CurveExt},
    circuit::Value,
    plonk::Assigned,
    poly::{
        self,
        commitment::{Blind, Params},
        multiopen::{self, ProverQuery},
        Coeff, ExtendedLagrangeCoeff, LagrangeCoeff, Polynomial,
    },
};
use crate::{
    poly::batch_invert_assigned,
    transcript::{EncodedChallenge, TranscriptWrite},
};

// ===== Stage E2: optional source-level phase timing (env-gated, default OFF) =====
// Enabled only when `ORCHARD_E2_TIMING` is present in the environment. When off,
// every `e2_add` call early-returns and the only cost is a couple of
// `Instant::now()` snapshots (sub-nanosecond, negligible vs a multi-second proof).
// Stages are measured with contiguous, non-overlapping wall-clock intervals so the
// sum of stages never double-counts a region (no nested accumulation).
use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

static E2_ENABLED: OnceLock<bool> = OnceLock::new();
static E2_TIMINGS: OnceLock<Mutex<HashMap<&'static str, std::time::Duration>>> = OnceLock::new();

fn e2_enabled() -> bool {
    *E2_ENABLED.get_or_init(|| std::env::var("ORCHARD_E2_TIMING").is_ok())
}

#[allow(dead_code)]
fn e2_add(stage: &'static str, d: std::time::Duration) {
    if !e2_enabled() {
        return;
    }
    let mut guard = E2_TIMINGS
        .get_or_init(|| Mutex::new(HashMap::new()))
        .lock()
        .unwrap();
    let e = guard
        .entry(stage)
        .or_insert_with(std::time::Duration::default);
    *e = *e + d;
}

/// Take and reset the accumulated stage timings (stage -> seconds).
/// Returns an empty vector when timing is disabled, so callers can emit
/// unconditionally and simply skip empty results.
pub fn take_e2_timings() -> Vec<(String, f64)> {
    if !e2_enabled() {
        return Vec::new();
    }
    let mut guard = E2_TIMINGS
        .get_or_init(|| Mutex::new(HashMap::new()))
        .lock()
        .unwrap();
    let out: Vec<(String, f64)> = guard
        .drain()
        .map(|(k, v)| (k.to_string(), v.as_secs_f64()))
        .collect();
    out
}

// ===== Stage E3: publish the current phase for the in-process sampler =====
// The sampler runs inside the same process, so it can read this atomic at each
// sampling tick and tag every captured stack with the phase it was executing.
// This turns "which function belongs to which E2 stage" into a measurement
// instead of a guess. Codes match the order of the E2 stages in `create_proof`;
// they are only written when E2 timing is enabled, so release builds are
// untouched.
/// Current `create_proof` phase, published for the Stage E3 in-process sampler.
pub static E3_STAGE: std::sync::atomic::AtomicU8 = std::sync::atomic::AtomicU8::new(0);

/// Human-readable name for a stage code (0 = outside create_proof).
pub fn e3_stage_name(code: u8) -> &'static str {
    match code {
        1 => "instance",
        2 => "synthesis",
        3 => "advice_commit",
        4 => "eval_setup",
        5 => "lookup_permute",
        6 => "permutation",
        7 => "lookup_product",
        8 => "quotient",
        9 => "opening",
        _ => "outside",
    }
}

#[inline]
fn e3_set(code: u8) {
    if e2_enabled() {
        E3_STAGE.store(code, std::sync::atomic::Ordering::Relaxed);
    }
}


/// This creates a proof for the provided `circuit` when given the public
/// parameters `params` and the proving key [`ProvingKey`] that was
/// generated previously for the same circuit. The provided `instances`
/// are zero-padded internally.
pub fn create_proof<
    C: CurveAffine,
    E: EncodedChallenge<C>,
    R: Rng,
    T: TranscriptWrite<C, E>,
    ConcreteCircuit: Circuit<C::Scalar>,
>(
    params: &Params<C>,
    pk: &ProvingKey<C>,
    circuits: &[ConcreteCircuit],
    instances: &[&[&[C::Scalar]]],
    mut rng: R,
    transcript: &mut T,
) -> Result<(), Error> {
    if circuits.len() != instances.len() {
        return Err(Error::InvalidInstances);
    }

    for instance in instances.iter() {
        if instance.len() != pk.vk.cs.num_instance_columns {
            return Err(Error::InvalidInstances);
        }
    }

    // Hash verification key into transcript
    pk.vk.hash_into(transcript)?;

    let domain = &pk.vk.domain;
    let mut meta = ConstraintSystem::default();
    let config = ConcreteCircuit::configure(&mut meta);

    // Selector optimizations cannot be applied here; use the ConstraintSystem
    // from the verification key.
    let meta = &pk.vk.cs;

    struct InstanceSingle<C: CurveAffine> {
        pub instance_values: Vec<Polynomial<C::Scalar, LagrangeCoeff>>,
        pub instance_polys: Vec<Polynomial<C::Scalar, Coeff>>,
        pub instance_cosets: Vec<Polynomial<C::Scalar, ExtendedLagrangeCoeff>>,
    }

    e3_set(1);
    let t_instance = std::time::Instant::now();
    let instance: Vec<InstanceSingle<C>> = instances
        .iter()
        .map(|instance| -> Result<InstanceSingle<C>, Error> {
            let instance_values = instance
                .iter()
                .map(|values| {
                    let mut poly = domain.empty_lagrange();
                    assert_eq!(poly.len(), params.n as usize);
                    if values.len() > (poly.len() - (meta.blinding_factors() + 1)) {
                        return Err(Error::InstanceTooLarge);
                    }
                    for (poly, value) in poly.iter_mut().zip(values.iter()) {
                        *poly = *value;
                    }
                    Ok(poly)
                })
                .collect::<Result<Vec<_>, _>>()?;
            let instance_commitments_projective: Vec<_> = instance_values
                .iter()
                .map(|poly| params.commit_lagrange(poly, Blind::default()))
                .collect();
            let mut instance_commitments =
                vec![C::identity(); instance_commitments_projective.len()];
            C::Curve::batch_normalize_vartime(
                &instance_commitments_projective,
                &mut instance_commitments,
            );
            let instance_commitments = instance_commitments;
            drop(instance_commitments_projective);

            for commitment in &instance_commitments {
                transcript.common_point(*commitment)?;
            }

            let instance_polys: Vec<_> = instance_values
                .iter()
                .map(|poly| {
                    let lagrange_vec = domain.lagrange_from_vec(poly.to_vec());
                    domain.lagrange_to_coeff(lagrange_vec)
                })
                .collect();

            let instance_cosets: Vec<_> = instance_polys
                .iter()
                .map(|poly| domain.coeff_to_extended(poly.clone()))
                .collect();

            Ok(InstanceSingle {
                instance_values,
                instance_polys,
                instance_cosets,
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    e2_add("instance", t_instance.elapsed());

    struct AdviceSingle<C: CurveAffine> {
        pub advice_values: Vec<Polynomial<C::Scalar, LagrangeCoeff>>,
        pub advice_polys: Vec<Polynomial<C::Scalar, Coeff>>,
        pub advice_cosets: Vec<Polynomial<C::Scalar, ExtendedLagrangeCoeff>>,
        pub advice_blinds: Vec<Blind<C::Scalar>>,
    }

    let advice: Vec<AdviceSingle<C>> = circuits
        .iter()
        .zip(instances.iter())
        .map(|(circuit, instances)| -> Result<AdviceSingle<C>, Error> {
            struct WitnessCollection<'a, F: Field> {
                k: u32,
                pub advice: Vec<Polynomial<Assigned<F>, LagrangeCoeff>>,
                instances: &'a [&'a [F]],
                usable_rows: RangeTo<usize>,
                _marker: std::marker::PhantomData<F>,
            }

            impl<'a, F: Field> Assignment<F> for WitnessCollection<'a, F> {
                fn enter_region<NR, N>(&mut self, _: N)
                where
                    NR: Into<String>,
                    N: FnOnce() -> NR,
                {
                    // Do nothing; we don't care about regions in this context.
                }

                fn exit_region(&mut self) {
                    // Do nothing; we don't care about regions in this context.
                }

                fn enable_selector<A, AR>(
                    &mut self,
                    _: A,
                    _: &Selector,
                    _: usize,
                ) -> Result<(), Error>
                where
                    A: FnOnce() -> AR,
                    AR: Into<String>,
                {
                    // We only care about advice columns here

                    Ok(())
                }

                fn query_instance(
                    &self,
                    column: Column<Instance>,
                    row: usize,
                ) -> Result<Value<F>, Error> {
                    if !self.usable_rows.contains(&row) {
                        return Err(Error::not_enough_rows_available(self.k));
                    }

                    self.instances
                        .get(column.index())
                        .and_then(|column| column.get(row))
                        .map(|v| Value::known(*v))
                        .ok_or(Error::BoundsFailure)
                }

                fn assign_advice<V, VR, A, AR>(
                    &mut self,
                    _: A,
                    column: Column<Advice>,
                    row: usize,
                    to: V,
                ) -> Result<(), Error>
                where
                    V: FnOnce() -> Value<VR>,
                    VR: Into<Assigned<F>>,
                    A: FnOnce() -> AR,
                    AR: Into<String>,
                {
                    if !self.usable_rows.contains(&row) {
                        return Err(Error::not_enough_rows_available(self.k));
                    }

                    *self
                        .advice
                        .get_mut(column.index())
                        .and_then(|v| v.get_mut(row))
                        .ok_or(Error::BoundsFailure)? = to().into_field().assign()?;

                    Ok(())
                }

                fn assign_fixed<V, VR, A, AR>(
                    &mut self,
                    _: A,
                    _: Column<Fixed>,
                    _: usize,
                    _: V,
                ) -> Result<(), Error>
                where
                    V: FnOnce() -> Value<VR>,
                    VR: Into<Assigned<F>>,
                    A: FnOnce() -> AR,
                    AR: Into<String>,
                {
                    // We only care about advice columns here

                    Ok(())
                }

                fn copy(
                    &mut self,
                    _: Column<Any>,
                    _: usize,
                    _: Column<Any>,
                    _: usize,
                ) -> Result<(), Error> {
                    // We only care about advice columns here

                    Ok(())
                }

                fn fill_from_row(
                    &mut self,
                    _: Column<Fixed>,
                    _: usize,
                    _: Value<Assigned<F>>,
                ) -> Result<(), Error> {
                    Ok(())
                }

                fn push_namespace<NR, N>(&mut self, _: N)
                where
                    NR: Into<String>,
                    N: FnOnce() -> NR,
                {
                    // Do nothing; we don't care about namespaces in this context.
                }

                fn pop_namespace(&mut self, _: Option<String>) {
                    // Do nothing; we don't care about namespaces in this context.
                }
            }

            let unusable_rows_start = params.n as usize - (meta.blinding_factors() + 1);

            let mut witness = WitnessCollection {
                k: params.k,
                advice: vec![domain.empty_lagrange_assigned(); meta.num_advice_columns],
                instances,
                // The prover will not be allowed to assign values to advice
                // cells that exist within inactive rows, which include some
                // number of blinding factors and an extra row for use in the
                // permutation argument.
                usable_rows: ..unusable_rows_start,
                _marker: std::marker::PhantomData,
            };

            // Synthesize the circuit to obtain the witness and other information.
            e3_set(2);
            let t_synth = std::time::Instant::now();
            ConcreteCircuit::FloorPlanner::synthesize(
                &mut witness,
                circuit,
                config.clone(),
                meta.constants.clone(),
            )?;

            let mut advice = batch_invert_assigned(witness.advice);

            // Add blinding factors to advice columns
            for advice in &mut advice {
                for cell in &mut advice[unusable_rows_start..] {
                    *cell = C::Scalar::random(&mut rng);
                }
            }
            e2_add("synthesis", t_synth.elapsed());

            // Compute commitments to advice column polynomials
            e3_set(3);
            let t_acommit = std::time::Instant::now();
            let advice_blinds: Vec<_> = advice
                .iter()
                .map(|_| Blind(C::Scalar::random(&mut rng)))
                .collect();
            let advice_commitments_projective: Vec<_> = advice
                .iter()
                .zip(advice_blinds.iter())
                .map(|(poly, blind)| params.commit_lagrange(poly, *blind))
                .collect();
            let mut advice_commitments = vec![C::identity(); advice_commitments_projective.len()];
            C::Curve::batch_normalize_vartime(
                &advice_commitments_projective,
                &mut advice_commitments,
            );
            let advice_commitments = advice_commitments;
            drop(advice_commitments_projective);

            for commitment in &advice_commitments {
                transcript.write_point(*commitment)?;
            }

            let advice_polys: Vec<_> = advice
                .clone()
                .into_iter()
                .map(|poly| domain.lagrange_to_coeff(poly))
                .collect();

            let advice_cosets: Vec<_> = advice_polys
                .iter()
                .map(|poly| domain.coeff_to_extended(poly.clone()))
                .collect();
            e2_add("advice_commit", t_acommit.elapsed());

            Ok(AdviceSingle {
                advice_values: advice,
                advice_polys,
                advice_cosets,
                advice_blinds,
            })
        })
        .collect::<Result<Vec<_>, _>>()?;

    // Create polynomial evaluator context for values.
    e3_set(4);
    let t_eval = std::time::Instant::now();
    let mut value_evaluator = poly::new_evaluator(|| {});

    // Register fixed values with the polynomial evaluator.
    let fixed_values: Vec<_> = pk
        .fixed_values
        .iter()
        .map(|poly| value_evaluator.register_poly(poly.clone()))
        .collect();

    // Register advice values with the polynomial evaluator.
    let advice_values: Vec<_> = advice
        .iter()
        .map(|advice| {
            advice
                .advice_values
                .iter()
                .map(|poly| value_evaluator.register_poly(poly.clone()))
                .collect::<Vec<_>>()
        })
        .collect();

    // Register instance values with the polynomial evaluator.
    let instance_values: Vec<_> = instance
        .iter()
        .map(|instance| {
            instance
                .instance_values
                .iter()
                .map(|poly| value_evaluator.register_poly(poly.clone()))
                .collect::<Vec<_>>()
        })
        .collect();

    // Create polynomial evaluator context for cosets.
    let mut coset_evaluator = poly::new_evaluator(|| {});

    // Register fixed cosets with the polynomial evaluator.
    let fixed_cosets: Vec<_> = pk
        .fixed_cosets
        .iter()
        .map(|poly| coset_evaluator.register_poly(poly.clone()))
        .collect();

    // Register advice cosets with the polynomial evaluator.
    let advice_cosets: Vec<_> = advice
        .iter()
        .map(|advice| {
            advice
                .advice_cosets
                .iter()
                .map(|poly| coset_evaluator.register_poly(poly.clone()))
                .collect::<Vec<_>>()
        })
        .collect();

    // Register instance cosets with the polynomial evaluator.
    let instance_cosets: Vec<_> = instance
        .iter()
        .map(|instance| {
            instance
                .instance_cosets
                .iter()
                .map(|poly| coset_evaluator.register_poly(poly.clone()))
                .collect::<Vec<_>>()
        })
        .collect();

    // Register permutation cosets with the polynomial evaluator.
    let permutation_cosets: Vec<_> = pk
        .permutation
        .cosets
        .iter()
        .map(|poly| coset_evaluator.register_poly(poly.clone()))
        .collect();
    e2_add("eval_setup", t_eval.elapsed());

    // Register boundary polynomials used in the lookup and permutation arguments.
    let l0 = coset_evaluator.register_poly(pk.l0.clone());
    let l_blind = coset_evaluator.register_poly(pk.l_blind.clone());
    let l_last = coset_evaluator.register_poly(pk.l_last.clone());

    // Sample theta challenge for keeping lookup columns linearly independent
    let theta: ChallengeTheta<_> = transcript.squeeze_challenge_scalar();

    e3_set(5);
    let t_lperm = std::time::Instant::now();
    let lookups: Vec<Vec<lookup::prover::Permuted<C, _>>> = instance_values
        .iter()
        .zip(instance_cosets.iter())
        .zip(advice_values.iter())
        .zip(advice_cosets.iter())
        .map(|(((instance_values, instance_cosets), advice_values), advice_cosets)| -> Result<Vec<_>, Error> {
            // Construct and commit to permuted values for each lookup
            pk.vk
                .cs
                .lookups
                .iter()
                .map(|lookup| {
                    lookup.commit_permuted(
                        pk,
                        params,
                        domain,
                        &value_evaluator,
                        &mut coset_evaluator,
                        theta,
                        advice_values,
                        &fixed_values,
                        instance_values,
                        advice_cosets,
                        &fixed_cosets,
                        instance_cosets,
                        &mut rng,
                        transcript,
                    )
                })
                .collect()
        })
        .collect::<Result<Vec<_>, _>>()?;
    e2_add("lookup_permute", t_lperm.elapsed());

    // Sample beta challenge
    let beta: ChallengeBeta<_> = transcript.squeeze_challenge_scalar();

    // Sample gamma challenge
    let gamma: ChallengeGamma<_> = transcript.squeeze_challenge_scalar();

    // Commit to permutations.
    e3_set(6);
    let t_perm = std::time::Instant::now();
    let permutations: Vec<permutation::prover::Committed<C, _>> = instance
        .iter()
        .zip(advice.iter())
        .map(|(instance, advice)| {
            pk.vk.cs.permutation.commit(
                params,
                pk,
                &pk.permutation,
                &advice.advice_values,
                &pk.fixed_values,
                &instance.instance_values,
                beta,
                gamma,
                &mut coset_evaluator,
                &mut rng,
                transcript,
            )
        })
        .collect::<Result<Vec<_>, _>>()?;
    e2_add("permutation", t_perm.elapsed());

    e3_set(7);
    let t_lprod = std::time::Instant::now();
    let lookups: Vec<Vec<lookup::prover::Committed<C, _>>> = lookups
        .into_iter()
        .map(|lookups| -> Result<Vec<_>, _> {
            // Construct and commit to products for each lookup
            lookups
                .into_iter()
                .map(|lookup| {
                    lookup.commit_product(
                        pk,
                        params,
                        beta,
                        gamma,
                        &mut coset_evaluator,
                        &mut rng,
                        transcript,
                    )
                })
                .collect::<Result<Vec<_>, _>>()
        })
        .collect::<Result<Vec<_>, _>>()?;
    e2_add("lookup_product", t_lprod.elapsed());

    // Commit to the vanishing argument's random polynomial for blinding h(x_3)
    e3_set(8);
    let t_quot = std::time::Instant::now();
    let vanishing = vanishing::Argument::commit(params, domain, &mut rng, transcript)?;

    // Obtain challenge for keeping all separate gates linearly independent
    let y: ChallengeY<_> = transcript.squeeze_challenge_scalar();

    // Evaluate the h(X) polynomial's constraint system expressions for the permutation constraints.
    let (permutations, permutation_expressions): (Vec<_>, Vec<_>) = permutations
        .into_iter()
        .zip(advice_cosets.iter())
        .zip(instance_cosets.iter())
        .map(|((permutation, advice), instance)| {
            permutation.construct(
                pk,
                &pk.vk.cs.permutation,
                advice,
                &fixed_cosets,
                instance,
                &permutation_cosets,
                l0,
                l_blind,
                l_last,
                beta,
                gamma,
            )
        })
        .unzip();

    let (lookups, lookup_expressions): (Vec<Vec<_>>, Vec<Vec<_>>) = lookups
        .into_iter()
        .map(|lookups| {
            // Evaluate the h(X) polynomial's constraint system expressions for the lookup constraints, if any.
            lookups
                .into_iter()
                .map(|p| p.construct(beta, gamma, l0, l_blind, l_last))
                .unzip()
        })
        .unzip();

    // Stage F2: the three sources below feed one fused `distribute_powers` tree.
    // Their term counts cannot be inferred from the constraint system alone — the
    // permutation argument emits a different number of `Ast`s per advice column —
    // so each chain is wrapped in a counting adapter. The wrapper only observes.
    let expressions = advice_cosets
        .iter()
        .zip(instance_cosets.iter())
        .zip(permutation_expressions)
        .zip(lookup_expressions)
        .flat_map(
            |(((advice_cosets, instance_cosets), permutation_expressions), lookup_expressions)| {
                let fixed_cosets = &fixed_cosets;
                iter::empty()
                    // Custom constraints
                    .chain(crate::f2::counted(0, meta.gates.iter().flat_map(move |gate| {
                        gate.polynomials().iter().map(move |expr| {
                            expr.evaluate(
                                &poly::Ast::ConstantTerm,
                                &|_| panic!("virtual selectors are removed during optimization"),
                                &|query| {
                                    fixed_cosets[query.column_index]
                                        .with_rotation(query.rotation)
                                        .into()
                                },
                                &|query| {
                                    advice_cosets[query.column_index]
                                        .with_rotation(query.rotation)
                                        .into()
                                },
                                &|query| {
                                    instance_cosets[query.column_index]
                                        .with_rotation(query.rotation)
                                        .into()
                                },
                                &|a| -a,
                                &|a, b| a + b,
                                &|a, b| a * b,
                                &|a, scalar| a * scalar,
                            )
                        })
                    })))
                    // Permutation constraints, if any.
                    .chain(crate::f2::counted(1, permutation_expressions))
                    // Lookup constraints, if any.
                    .chain(crate::f2::counted(
                        2,
                        lookup_expressions.into_iter().flatten(),
                    ))
            },
        );

    // Construct the vanishing argument's h(X) commitments
    let vanishing = vanishing.construct(
        params,
        domain,
        coset_evaluator,
        expressions,
        y,
        &mut rng,
        transcript,
    )?;
    e2_add("quotient", t_quot.elapsed());

    e3_set(9);
    let t_open = std::time::Instant::now();
    let x: ChallengeX<_> = transcript.squeeze_challenge_scalar();
    let xn = x.pow([params.n, 0, 0, 0]);

    // Compute and hash instance evals for each circuit instance
    for instance in instance.iter() {
        // Evaluate polynomials at omega^i x
        let instance_evals: Vec<_> = meta
            .instance_queries
            .iter()
            .map(|&(column, at)| {
                eval_polynomial(
                    &instance.instance_polys[column.index()],
                    domain.rotate_omega(*x, at),
                )
            })
            .collect();

        // Hash each instance column evaluation
        for eval in instance_evals.iter() {
            transcript.write_scalar(*eval)?;
        }
    }

    // Compute and hash advice evals for each circuit instance
    for advice in advice.iter() {
        // Evaluate polynomials at omega^i x
        let advice_evals: Vec<_> = meta
            .advice_queries
            .iter()
            .map(|&(column, at)| {
                eval_polynomial(
                    &advice.advice_polys[column.index()],
                    domain.rotate_omega(*x, at),
                )
            })
            .collect();

        // Hash each advice column evaluation
        for eval in advice_evals.iter() {
            transcript.write_scalar(*eval)?;
        }
    }

    // Compute and hash fixed evals (shared across all circuit instances)
    let fixed_evals: Vec<_> = meta
        .fixed_queries
        .iter()
        .map(|&(column, at)| {
            eval_polynomial(&pk.fixed_polys[column.index()], domain.rotate_omega(*x, at))
        })
        .collect();

    // Hash each fixed column evaluation
    for eval in fixed_evals.iter() {
        transcript.write_scalar(*eval)?;
    }

    let vanishing = vanishing.evaluate(x, xn, domain, transcript)?;

    // Evaluate common permutation data
    pk.permutation.evaluate(x, transcript)?;

    // Evaluate the permutations, if any, at omega^i x.
    let permutations: Vec<permutation::prover::Evaluated<C>> = permutations
        .into_iter()
        .map(|permutation| -> Result<_, _> { permutation.evaluate(pk, x, transcript) })
        .collect::<Result<Vec<_>, _>>()?;

    // Evaluate the lookups, if any, at omega^i x.
    let lookups: Vec<Vec<lookup::prover::Evaluated<C>>> = lookups
        .into_iter()
        .map(|lookups| -> Result<Vec<_>, _> {
            lookups
                .into_iter()
                .map(|p| p.evaluate(pk, x, transcript))
                .collect::<Result<Vec<_>, _>>()
        })
        .collect::<Result<Vec<_>, _>>()?;

    let instances = instance
        .iter()
        .zip(advice.iter())
        .zip(permutations.iter())
        .zip(lookups.iter())
        .flat_map(|(((instance, advice), permutation), lookups)| {
            iter::empty()
                .chain(
                    pk.vk
                        .cs
                        .instance_queries
                        .iter()
                        .map(move |&(column, at)| ProverQuery {
                            point: domain.rotate_omega(*x, at),
                            poly: &instance.instance_polys[column.index()],
                            blind: Blind::default(),
                        }),
                )
                .chain(
                    pk.vk
                        .cs
                        .advice_queries
                        .iter()
                        .map(move |&(column, at)| ProverQuery {
                            point: domain.rotate_omega(*x, at),
                            poly: &advice.advice_polys[column.index()],
                            blind: advice.advice_blinds[column.index()],
                        }),
                )
                .chain(permutation.open(pk, x))
                .chain(lookups.iter().flat_map(move |p| p.open(pk, x)))
        })
        .chain(
            pk.vk
                .cs
                .fixed_queries
                .iter()
                .map(|&(column, at)| ProverQuery {
                    point: domain.rotate_omega(*x, at),
                    poly: &pk.fixed_polys[column.index()],
                    blind: Blind::default(),
                }),
        )
        .chain(pk.permutation.open(x))
        // We query the h(X) polynomial at x
        .chain(vanishing.open(x));

    let opening_result = multiopen::create_proof(params, rng, transcript, instances)
        .map_err(|_| Error::Opening);
    e2_add("opening", t_open.elapsed());
    e3_set(0);
    opening_result
}

#[test]
fn test_create_proof() {
    use crate::{
        circuit::SimpleFloorPlanner,
        plonk::{keygen_pk, keygen_vk},
        transcript::{Blake2bWrite, Challenge255},
    };
    use pasta_curves::EqAffine;
    use rand::rngs::SysRng;
    use rand_core::UnwrapErr;

    #[derive(Clone, Copy)]
    struct MyCircuit;

    impl<F: Field> Circuit<F> for MyCircuit {
        type Config = ();

        type FloorPlanner = SimpleFloorPlanner;

        fn without_witnesses(&self) -> Self {
            *self
        }

        fn configure(_meta: &mut ConstraintSystem<F>) -> Self::Config {}

        fn synthesize(
            &self,
            _config: Self::Config,
            _layouter: impl crate::circuit::Layouter<F>,
        ) -> Result<(), Error> {
            Ok(())
        }
    }

    let params: Params<EqAffine> = Params::new(3);
    let vk = keygen_vk(&params, &MyCircuit).expect("keygen_vk should not fail");
    let pk = keygen_pk(&params, vk, &MyCircuit).expect("keygen_pk should not fail");
    let mut transcript = Blake2bWrite::<_, _, Challenge255<_>>::init(vec![]);

    // Create proof with wrong number of instances
    let proof = create_proof(
        &params,
        &pk,
        &[MyCircuit, MyCircuit],
        &[],
        UnwrapErr(SysRng),
        &mut transcript,
    );
    assert!(matches!(proof.unwrap_err(), Error::InvalidInstances));

    // Create proof with correct number of instances
    create_proof(
        &params,
        &pk,
        &[MyCircuit, MyCircuit],
        &[&[], &[]],
        UnwrapErr(SysRng),
        &mut transcript,
    )
    .expect("proof generation should not fail");
}
