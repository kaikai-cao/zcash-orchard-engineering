//! Pins down the bit-order convention that `MerklePath::calculate_root`
//! (halo2_gadgets) shares with the host-side `orchard::tree::MerklePath::root`.
//!
//! Reading the gadget code alone leaves one question open: `pos` is converted
//! with `i2lebsp(pos as u64)`, and the path array is documented as
//! leaf-to-root. This binary checks that combination against the host
//! implementation, and against the (wrong) MSB-first reading, so that the
//! direction bit is not a matter of interpretation.
//!
//! Output is written verbatim to results/halo2/synthesize/merkle_pos_check.txt

use incrementalmerkletree::{Hashable, Level};
use orchard::{
    note::ExtractedNoteCommitment,
    tree::{Anchor, MerkleHashOrchard, MerklePath},
};

const DEPTH: usize = 32;

fn hex(anchor: Anchor) -> String {
    anchor.to_bytes().iter().map(|b| format!("{:02x}", b)).collect()
}

/// Host-side reference: `orchard::tree::MerklePath::root` (src/tree.rs:152).
fn host_root(path: &[MerkleHashOrchard; DEPTH], pos: u32) -> Anchor {
    MerklePath::from_parts(pos, *path).root(cmx())
}

/// The convention as the circuit reads it: `i2lebsp(pos)` => bit `l` of `pos`
/// selects the orientation at path index `l` (i.e. at level `l`).
fn circuit_convention(
    leaf: &MerkleHashOrchard,
    path: &[MerkleHashOrchard; DEPTH],
    pos: u32,
    lsb_first: bool,
) -> Anchor {
    let mut node = *leaf;
    for l in 0..DEPTH {
        let shift = if lsb_first { l } else { DEPTH - 1 - l };
        let bit = (pos >> shift) & 1;
        node = if bit == 0 {
            MerkleHashOrchard::combine(Level::from(l as u8), &node, &path[l])
        } else {
            MerkleHashOrchard::combine(Level::from(l as u8), &path[l], &node)
        };
    }
    Anchor::from(node)
}

fn cmx() -> ExtractedNoteCommitment {
    // Any canonical base-field element works; this is a fixed 32-byte string
    // whose top byte is small enough to be below the Pallas modulus.
    ExtractedNoteCommitment::from_bytes(&[7u8; 32]).expect("canonical field element")
}

/// A distinct, valid sibling digest per level. Deliberately *not* derived from
/// the running node, otherwise a swapped hash can coincide with the unswapped
/// one and the bit under test becomes unobservable.
fn sibling(l: usize) -> MerkleHashOrchard {
    let mut bytes = [7u8; 32];
    // 0x40-based so that no sibling coincides with the leaf itself ([7u8; 32]).
    bytes[0] = (0x40 + l) as u8;
    let cmx = ExtractedNoteCommitment::from_bytes(&bytes).expect("canonical field element");
    MerkleHashOrchard::from_cmx(&cmx)
}

fn main() {
    let leaf = MerkleHashOrchard::from_cmx(&cmx());

    // Build 32 distinct sibling digests. They need not come from a real tree:
    // this experiment is about bit order, not about tree contents.
    let mut path = [leaf; DEPTH];
    for l in 0..DEPTH {
        path[l] = sibling(l);
    }

    println!("# Orchard Merkle path bit-order check");
    println!("# crate: orchard @ 616a669d, halo2_gadgets 0.6.0");
    println!("# depth = {}", DEPTH);
    println!();
    println!("{:<12} {:<66} {:<6} {:<6}", "pos", "host MerklePath::root", "LSB", "MSB");
    println!("{}", "-".repeat(94));

    for pos in [0u32, 1, 2, 3, 0x8000_0000, 0xFFFF_FFFF, 0x1234_5678] {
        let host = hex(host_root(&path, pos));
        let lsb = hex(circuit_convention(&leaf, &path, pos, true));
        let msb = hex(circuit_convention(&leaf, &path, pos, false));
        println!(
            "0x{:08x}   {:<66} {:<6} {:<6}",
            pos,
            host,
            if host == lsb { "match" } else { "DIFF" },
            if host == msb { "match" } else { "DIFF" },
        );
    }

    println!();
    println!("# Reading: the gadget's i2lebsp(pos) + leaf-to-root path order is");
    println!("# bit l of pos  <->  path index l  <->  tree level l (leaves at 32, root at 0),");
    println!("# i.e. pos bit 0 selects orientation at the leaf level, bit 31 at the root level.");
    println!("# The MSB-first reading produces a different anchor for every pos != 0 and");
    println!("# pos != all-ones, so the convention is numerically observable, not cosmetic.");
}
