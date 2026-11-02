# Baseline run: run_001

## Source
| Field | Value |
| --- | --- |
| Repository | $Upstream |
| Commit | `616a669df8c9a59c33d47064d3ce04b25d026b2e` |
| Describe | `0.16.0-1-g616a669` |
| Working tree | clean |
| Cargo.lock SHA256 | `FAC8ACD70B798D588A5199C82B1008F103E01ED56B035A0CE76A54A9470F31FB` |

## Toolchain
| Field | Value |
| --- | --- |
| rustc | `rustc 1.88.0 (6b00bc388 2025-06-23)` |
| cargo | `cargo 1.88.0 (873a06493 2025-05-10)` |
| Toolchain override | `1.88` (from rust-toolchain.toml) |

## Machine
| Field | Value |
| --- | --- |
| OS | Microsoft Windows 11 家庭版 Insider Preview 中文版 (build 26220) |
| CPU | Intel(R) Core(TM) i7-14700 |
| Cores / logical | 20 / 28 |
| Memory | 31.69 GiB |
| RAYON_NUM_THREADS | 1 |

## Command
```
cargo +1.88 bench --locked -p orchard --features circuit --bench orchard_k11_prover
```

Started: 2026-11-02T21:38:40.2241147+08:00

Finished: 2026-11-02T21:39:43.3533313+08:00
Exit code: 0
