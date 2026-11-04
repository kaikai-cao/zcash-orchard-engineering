Stage F3 / H7 -- Borrowed/Owned instead of a Vec per node
==========================================================================
ABBA blocks (A1 B1 B2 A2); the bootstrap resamples pairs, not samples.

[D1] end-to-end prove, plain release, no instrumentation
  1P  pairs=26  samples/side=130
    A  median   2778.8 ms   CV  2.01%   peak WS  115.3 MiB
    B  median   2725.8 ms   CV  2.78%   peak WS  115.1 MiB
    paired delta  -1.45%   95% CI [ -2.14%,  -0.59%]
    per pair:  r1.1:-2.78%  r1.2:-4.36%  r2.1:-1.89%  r2.2:-2.44%  r3.1:-0.97%  r3.2:-1.20%  r4.1:+0.04%  r4.2:-1.48%  r5.1:-4.11%  r5.2:-1.79%  r6.1:-5.03%  r6.2:-1.32%  r7.1:-1.95%  r7.2:-0.89%  r8.1:-1.59%  r8.2:-1.22%  r9.1:-1.37%  r9.2:+6.22%  r10.1:-3.66%  r10.2:+0.28%  r11.1:+0.08%  r11.2:-1.26%  r12.1:-0.96%  r12.2:+0.25%  r13.1:-1.20%  r13.2:-2.48%
  8P  pairs=26  samples/side=130
    A  median    908.0 ms   CV  8.22%   peak WS  117.9 MiB
    B  median    913.0 ms   CV  7.34%   peak WS  117.5 MiB
    paired delta  +0.18%   95% CI [ -2.85%,  +3.02%]
    per pair:  r1.1:+10.28%  r1.2:-0.42%  r2.1:-0.12%  r2.2:-1.04%  r3.1:+0.03%  r3.2:+5.78%  r4.1:+4.26%  r4.2:+4.20%  r5.1:-13.20%  r5.2:-0.20%  r6.1:+13.95%  r6.2:+4.90%  r7.1:+8.64%  r7.2:+10.22%  r8.1:+2.08%  r8.2:-1.89%  r9.1:-6.00%  r9.2:-6.87%  r10.1:-5.71%  r10.2:-0.87%  r11.1:-2.58%  r11.2:-1.91%  r12.1:+3.22%  r12.2:-8.29%  r13.1:-22.08%  r13.2:+7.72%

[D2] stage breakdown (ORCHARD_E2_TIMING=1; attribution only)
  1P  pairs=10
    advice_commit    A    371.1 ms   B    363.3 ms   delta  -0.07%  [ -2.95,  +3.23]
    eval_setup       A      8.8 ms   B      8.6 ms   delta  -3.32%  [-10.99,  +2.39]
    instance         A     12.4 ms   B     12.4 ms   delta  +0.79%  [ -3.83,  +5.17]
    lookup_permute   A    231.0 ms   B    224.7 ms   delta  -1.19%  [ -4.09,  +1.74]
    lookup_product   A    150.5 ms   B    144.2 ms   delta  -1.42%  [ -4.23,  +0.87]
    opening          A    474.4 ms   B    471.0 ms   delta  +0.77%  [ -2.00,  +4.45]
    permutation      A    115.7 ms   B    111.6 ms   delta  -0.46%  [ -4.24,  +3.92]
    quotient         A   1347.4 ms   B   1269.4 ms   delta  -4.11%  [ -6.46,  -1.67]  faster
    synthesis        A    129.8 ms   B    131.3 ms   delta  +3.03%  [ +0.41,  +5.87]  REGRESSION
    all-but-quotient A   1496.4 ms   B   1457.6 ms   delta  +0.11%  [ -2.71,  +3.41]

  8P  pairs=10
    advice_commit    A    102.7 ms   B    101.8 ms   delta  +0.03%  [ -2.32,  +2.22]
    eval_setup       A      8.3 ms   B      8.3 ms   delta  -1.33%  [ -7.50,  +3.59]
    instance         A      4.7 ms   B      4.8 ms   delta  +1.40%  [ -1.41,  +4.22]
    lookup_permute   A     70.1 ms   B     70.6 ms   delta  +0.19%  [ -2.53,  +3.29]
    lookup_product   A     41.4 ms   B     41.4 ms   delta  -1.11%  [ -2.72,  +0.26]
    opening          A    105.3 ms   B    104.9 ms   delta  -0.74%  [ -6.11,  +2.74]
    permutation      A     35.2 ms   B     35.0 ms   delta  -1.31%  [ -3.28,  +0.66]
    quotient         A    342.0 ms   B    332.2 ms   delta  -3.77%  [ -6.72,  -1.54]  faster
    synthesis        A    130.1 ms   B    135.3 ms   delta  +2.95%  [ +0.73,  +5.12]  REGRESSION
    all-but-quotient A    496.1 ms   B    505.8 ms   delta  +0.39%  [ -1.83,  +2.19]


[D3] evaluator: the quotient `evaluate()` call itself (mode f2)
  1P  pairs=6
    A   1174.3 ms   B   1125.0 ms   delta  -3.28%  [ -7.74,  +1.59]
  8P  pairs=6
    A    284.3 ms   B    275.8 ms   delta  -2.71%  [ -4.41,  -0.57]

[D4] mechanism: who allocates (mode h7, one run per thread count)

  h7_stats_1p_r1.log
    tree: poly_len 16384, chunk 4096 x 4   (F2's independent walk: 4632 leaves, 2198 const, 30 linear)
    leaf visits   A    18528   B    18528   (borrowed    16064 / copied   2464)
    copy bytes    A    2.429 GB  B    0.323 GB    -86.70%
    allocations   A    27444     B    21316      -22.33%
    alloc bytes   A    3.597 GB  B    2.794 GB    -22.33%
    of which: both-borrowed 1288, unary 8648, fills 8916 (1.169 GB), copied 2464
    in place: left 24192, right(swapped) 3808
    invariants: leaf closed True, alloc closed True

  h7_stats_1p_r2.log
    tree: poly_len 16384, chunk 4096 x 4   (F2's independent walk: 4632 leaves, 2198 const, 30 linear)
    leaf visits   A    18528   B    18528   (borrowed    16064 / copied   2464)
    copy bytes    A    2.429 GB  B    0.323 GB    -86.70%
    allocations   A    27444     B    21316      -22.33%
    alloc bytes   A    3.597 GB  B    2.794 GB    -22.33%
    of which: both-borrowed 1288, unary 8648, fills 8916 (1.169 GB), copied 2464
    in place: left 24192, right(swapped) 3808
    invariants: leaf closed True, alloc closed True

  h7_stats_8p_r1.log
    tree: poly_len 16384, chunk 512 x 32   (F2's independent walk: 4632 leaves, 2198 const, 30 linear)
    leaf visits   A   148224   B   148224   (borrowed   128512 / copied  19712)
    copy bytes    A    2.429 GB  B    0.323 GB    -86.70%
    allocations   A   219552     B   170528      -22.33%
    alloc bytes   A    3.597 GB  B    2.794 GB    -22.33%
    of which: both-borrowed 10304, unary 69184, fills 71328 (1.169 GB), copied 19712
    in place: left 193536, right(swapped) 30464
    invariants: leaf closed True, alloc closed True

  h7_stats_8p_r2.log
    tree: poly_len 16384, chunk 512 x 32   (F2's independent walk: 4632 leaves, 2198 const, 30 linear)
    leaf visits   A   148224   B   148224   (borrowed   128512 / copied  19712)
    copy bytes    A    2.429 GB  B    0.323 GB    -86.70%
    allocations   A   219552     B   170528      -22.33%
    alloc bytes   A    3.597 GB  B    2.794 GB    -22.33%
    of which: both-borrowed 10304, unary 69184, fills 71328 (1.169 GB), copied 19712
    in place: left 193536, right(swapped) 30464
    invariants: leaf closed True, alloc closed True

  formulation price (single thread, after the timed window)
    chunk  len   4096: copy+inplace 14.289 ns/el   collect 12.287 ns/el   copy only 0.499 ns/el   ratio 1.163
    poly   len  16384: copy+inplace 16.209 ns/el   collect 17.215 ns/el   copy only 1.223 ns/el   ratio 0.942
    priced at the chunk geometry (0.499 ns/element), the leaf copies in one evaluate are worth:
      A rule (every leaf copied):   37.85 ms
      B rule (identity rotation borrowed):    5.03 ms
      difference:   32.81 ms — compare with D3, which measures the evaluator itself

  proof fingerprint (probe on vs off; same seed, same binary)
    h7_stats_1p_off.log          threads 1  probe False 0xa6bcd171d69e969a
    h7_stats_1p_r1.log           threads 1  probe True  0xa6bcd171d69e969a
    h7_stats_1p_r2.log           threads 1  probe True  0xa6bcd171d69e969a
    h7_stats_8p_off.log          threads 8  probe False 0xa6bcd171d69e969a
    h7_stats_8p_r1.log           threads 8  probe True  0xa6bcd171d69e969a
    h7_stats_8p_r2.log           threads 8  probe True  0xa6bcd171d69e969a
    -> identical: counting changes no field element

--------------------------------------------------------------------------
Gates (fixed in docs/orchard/19_h7_materialization_prereg.md section 6)
  H7-A    PASS  counters close (leaf True, alloc True); probe on/off proof identical; lib tests + verify_proof recorded in the doc
  H7-B    FAIL  alloc bytes -22.3% (needs <= -30%), leaf copy -86.7% (needs <= -80%)
  H7-C    PASS  allocations -22.3% (27444 -> 21316)
  H7-D    FAIL  evaluator -3.28% [-7.74, +1.59] (needs <= -5% and CI excluding 0)
  H7-E    PASS  1P prove -1.45% [-2.14, -0.59] (needs <= -1.0% and CI excluding 0)
  H7-F    FAIL  8P prove +0.18% [-2.85, +3.02] (needs point <= +0.5% and CI top <= +1%)
  H7-C2   peak WS 117.9 -> 117.5 MiB (-0.3%, gate <= +10%)
  H7-G    FAIL  all-but-quotient +0.11% [-2.71, +3.41] (CI top <= +1%)

  H7-H  preregistered rule: CLOSE (prereg: 'D 不过 -> 关档'): the evaluator did not improve, so the ownership discipline costs as much as it saves.
        applied:           KEEP the patch, close H7.5: H7-E held (the end-to-end gate is the one that decides) and H7-F showed no real 8P regression, so the remaining H7.5 ideas are capped at ~0.29% and are not worth another round. Deviation recorded in docs/orchard/20_h7_borrowed_ownership.md section 9.4.

verdicts: H7-A=PASS  H7-B=FAIL  H7-C=PASS  H7-D=FAIL  H7-E=PASS  H7-F=FAIL  H7-G=FAIL  fnv=PASS
