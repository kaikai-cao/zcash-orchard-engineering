#!/usr/bin/env python3
"""Generate F1 correctness vectors for Pallas Fp::mul.

The reference arithmetic is Python's arbitrary-precision integers, i.e.
completely independent of any Rust code. Each row is

    kind, a_hex_le32, b_hex_le32, (a*b mod p)_hex_le32, (a*a mod p)_hex_le32

Values are little-endian 32-byte encodings, matching `Fp::to_repr()`.
Only canonical inputs (0 <= x < p) are emitted, because that is what
`Fp::from_repr` accepts.
"""

import random

P = 0x40000000000000000000000000000000224698FC094CF91B992D30ED00000001

LIMBS = [
    0x992D30ED00000001,
    0x224698FC094CF91B,
    0x0000000000000000,
    0x4000000000000000,
]

R = 2**256 % P
R2 = 2**512 % P


def le32(x: int) -> str:
    return x.to_bytes(32, "little").hex()


def main() -> None:
    assert P == sum(l << (64 * i) for i, l in enumerate(LIMBS))

    cases = []

    # 1. identities and zeros
    cases += [("zero", 0, 0), ("zero_mul", 0, P - 1), ("one", 1, P - 1)]
    cases += [("one_one", 1, 1), ("two", 2, 3)]

    # 2. modulus-adjacent boundaries: the conditional final subtraction in
    #    montgomery_reduce is exercised hardest here (result in [p, 2p) before sub)
    cases += [("p-1_sq", P - 1, P - 1), ("p-1_p-2", P - 1, P - 2)]
    cases += [("half", (P - 1) // 2, (P + 1) // 2)]
    cases += [("p-1_one", P - 1, 1), ("p-2_two", P - 2, 2)]

    # 3. single-limb maxima (carry propagation stress)
    for i in range(4):
        v = (1 << (64 * (i + 1))) - 1
        if v < P:
            cases.append((f"limb{i}_max", v, v))
            cases.append((f"limb{i}_max_x_p-1", v, P - 1))

    # 4. one limb set to 1 (sparse products, zero carry chains)
    for i in range(4):
        v = 1 << (64 * i)
        cases.append((f"limb{i}_one", v, v))

    # 5. high-limb-heavy values just under p (top limb 0x3fff...)
    hi = 0x3FFFFFFFFFFFFFFF
    v = (hi << 192) | ((1 << 192) - 1)  # < p because top limb < 0x4000...
    assert v < P
    cases += [("near_p_high", v, v), ("near_p_high_p-1", v, P - 1)]

    # 6. Montgomery constants as plain field elements
    cases += [("R", R, R), ("R2", R2, R2), ("R_x_R2", R, R2)]

    # 7. powers of two (shifts inside square_unreduced)
    for e in (1, 63, 64, 65, 127, 128, 129, 191, 192, 193, 252):
        v = 1 << e
        if v < P:
            cases.append((f"pow2_{e}", v, v))

    # 8. random
    rng = random.Random(20261103)
    for i in range(200):
        a = rng.randrange(1, P)
        b = rng.randrange(1, P)
        cases.append((f"rand_{i:03d}", a, b))

    # 9. random with a == b (mul vs square path agreement)
    for i in range(40):
        a = rng.randrange(1, P)
        cases.append((f"same_{i:03d}", a, a))

    # 10. small-times-large (partial products confined to few limbs)
    for i in range(20):
        a = rng.randrange(1, 1 << 32)
        b = rng.randrange(1, P)
        cases.append((f"small_large_{i:02d}", a, b))

    with open("vectors.csv", "w", newline="\n") as f:
        f.write("kind,a_hex,b_hex,prod_hex,sq_hex\n")
        for kind, a, b in cases:
            assert 0 <= a < P and 0 <= b < P, kind
            f.write(f"{kind},{le32(a)},{le32(b)},{le32(a * b % P)},{le32(a * a % P)}\n")

    print(f"wrote {len(cases)} vectors")


if __name__ == "__main__":
    main()
