Stage F4 -- constraint-system reconnaissance
==============================================================================
source: recon_on.log   proof 7264 B   fnv 0xa6bcd171d69e969a   probe True

[0] the authored tree vs the tree the prover walks
  gates 55   constraints 193   selectors 56   advice 10   instance 1   perm cols 15   lookups 3   constants 1   degree 0..9   n=2048
  fixed columns 14 at configure -> 29 after selector compression (+15 columns)

  component   authored (F4)   compression   predicted   F2 (post)   match
  constant             133         +869        1002        1002   SAME
  product              555         +869        1424        1424   SAME
  sum                  645         +869        1514        1514   SAME
  negated              394         +869        1263        1263   SAME
  fixed                 99        +1062        1161        1161   SAME
  selector             193         -193           0           0   SAME
  nodes               3186        +4345        7531        7531   SAME
  model closes: PASS -- compression is the entire difference

[1] gate structure (authored form, before selector substitution)
   # gate                                       con   nod dep deg  sum  mul  neg  scl  cst  fix  adv  module
   0 Orchard circuit checks                       4    35   4   3    5    8    5    0    2    0   11  orchard/src/circuit.rs:335
   1 Field element addition: c = a + b            1     8   3   2    2    1    1    0    0    0    3  orchard/src/circuit/gadget.rs
   2 Short lookup bitshift                        1     9   4   3    1    2    1    1    0    0    3  halo2_gadgets/utilities/lookup
   3 witness point                                2    34   6   5    4   10    4    0    2    0   12  halo2_gadgets/ecc/chip
   4 witness non-identity point                   1    15   6   4    2    4    2    0    1    0    5  halo2_gadgets/ecc/chip
   5 incomplete addition                          2    49   6   4   12    7    9    0    0    0   19  halo2_gadgets/ecc/chip/add_incomplete
   6 complete addition                           12   225   8   6   39   50   35    0    9    0   80  halo2_gadgets/ecc/chip/add
   7 q_mul_1 == 1 checks                          1    22  10   4    5    3    4    1    0    0    8  halo2_gadgets/ecc/chip/mul
   8 q_mul_2 == 1 checks                          6   135  11   4   30   18   26    7    2    0   46  halo2_gadgets/ecc/chip/mul
   9 q_mul_3 == 1 checks                          4   107  11   4   24   14   21    6    2    0   36  halo2_gadgets/ecc/chip/mul
  10 q_mul_1 == 1 checks                          1    22  10   4    5    3    4    1    0    0    8  halo2_gadgets/ecc/chip/mul
  11 q_mul_2 == 1 checks                          6   135  11   4   30   18   26    7    2    0   46  halo2_gadgets/ecc/chip/mul
  12 q_mul_3 == 1 checks                          4   107  11   4   24   14   21    6    2    0   36  halo2_gadgets/ecc/chip/mul
  13 Decompose scalar for complete bits of vari   2    45   8   3    9    9    7    0    6    0   12  halo2_gadgets/ecc/chip/mul
  14 overflow checks                              5    49   6   5    7   12    6    0    6    0   13  halo2_gadgets/ecc/chip/mul
  15 LSB check                                    3    61   8   3   13    8   10    6    3    0   18  halo2_gadgets/ecc/chip/mul
  16 range check                                  1    70  13   9   15    8   15    8    7    0   16  halo2_gadgets/ecc/chip/mul_fixed
  17 Running sum coordinates check                3   232  14   9   41   43   33   28   10    9   65  halo2_gadgets/ecc/chip/mul_fixed
  18 Full-width fixed-base scalar mul             4   158  11   9   20   51   12    0   17    9   45  halo2_gadgets/ecc/chip/mul_fixed
  19 Short fixed-base mul gate                    4    34   4   3    5    8    4    0    2    0   11  halo2_gadgets/ecc/chip/mul_fixed/short
  20 Canonicity checks                            8    88   8   5   14   18   12    4    8    0   24  halo2_gadgets/sinsemilla
  21 full round                                   3   201   9   6   54   39    3    9    0   45   48  halo2_gadgets/poseidon
  22 partial rounds                               4   162  10   6   50   12    4   30    0   26   36  halo2_gadgets/poseidon
  23 pad-and-add                                  3    22   3   2    5    3    3    0    0    0    8  halo2_gadgets/sinsemilla
  24 Initial y_Q                                  1    22   9   4    5    3    4    1    0    1    7  halo2_gadgets/sinsemilla
  25 Sinsemilla gate                              2    85  12   6   20   13   14    3    3    4   26  halo2_gadgets/sinsemilla
  26 a' = b ⋅ swap + a ⋅ (1-swap)                 3    38   7   3    7    8    5    0    3    0   12  halo2_gadgets/sinsemilla
  27 Decomposition check                          4    44   8   2   10    4    6    6    0    0   14  halo2_gadgets/sinsemilla
  28 Initial y_Q                                  1    22   9   4    5    3    4    1    0    1    7  halo2_gadgets/sinsemilla
  29 Sinsemilla gate                              2    85  12   6   20   13   14    3    3    4   26  halo2_gadgets/sinsemilla
  30 a' = b ⋅ swap + a ⋅ (1-swap)                 3    38   7   3    7    8    5    0    3    0   12  halo2_gadgets/sinsemilla
  31 Decomposition check                          4    44   8   2   10    4    6    6    0    0   14  halo2_gadgets/sinsemilla
  32 CommitIvk canonicity check                  14   119   6   3   21   22   10    9    6    0   37  orchard/src/circuit/commit_ivk.rs
  33 NoteCommit MessagePiece b                    3    31   7   3    6    5    3    3    2    0    9  orchard/src/circuit/note_commit.rs
  34 NoteCommit MessagePiece d                    3    31   7   3    6    5    3    3    2    0    9  orchard/src/circuit/note_commit.rs
  35 NoteCommit MessagePiece e                    1     9   5   2    2    1    1    1    0    0    3  orchard/src/circuit/note_commit.rs
  36 NoteCommit MessagePiece g                    2    20   6   3    4    3    2    2    1    0    6  orchard/src/circuit/note_commit.rs
  37 NoteCommit MessagePiece h                    2    17   5   3    3    3    2    1    1    0    5  orchard/src/circuit/note_commit.rs
  38 NoteCommit input g_d                         5    38   5   3    6    8    3    2    2    0   12  orchard/src/circuit/note_commit.rs
  39 NoteCommit input pk_d                        4    36   6   3    7    6    3    3    2    0   11  orchard/src/circuit/note_commit.rs
  40 NoteCommit input value                       1    12   5   2    3    1    1    2    0    0    4  orchard/src/circuit/note_commit.rs
  41 NoteCommit input rho                         4    36   6   3    7    6    3    3    2    0   11  orchard/src/circuit/note_commit.rs
  42 NoteCommit input psi                         5    44   6   3    8    8    3    4    2    0   14  orchard/src/circuit/note_commit.rs
  43 y coordinate checks                          7    58   6   3   10   11    5    4    3    0   18  orchard/src/circuit/note_commit.rs
  44 NoteCommit MessagePiece b                    3    31   7   3    6    5    3    3    2    0    9  orchard/src/circuit/note_commit.rs
  45 NoteCommit MessagePiece d                    3    31   7   3    6    5    3    3    2    0    9  orchard/src/circuit/note_commit.rs
  46 NoteCommit MessagePiece e                    1     9   5   2    2    1    1    1    0    0    3  orchard/src/circuit/note_commit.rs
  47 NoteCommit MessagePiece g                    2    20   6   3    4    3    2    2    1    0    6  orchard/src/circuit/note_commit.rs
  48 NoteCommit MessagePiece h                    2    17   5   3    3    3    2    1    1    0    5  orchard/src/circuit/note_commit.rs
  49 NoteCommit input g_d                         5    38   5   3    6    8    3    2    2    0   12  orchard/src/circuit/note_commit.rs
  50 NoteCommit input pk_d                        4    36   6   3    7    6    3    3    2    0   11  orchard/src/circuit/note_commit.rs
  51 NoteCommit input value                       1    12   5   2    3    1    1    2    0    0    4  orchard/src/circuit/note_commit.rs
  52 NoteCommit input rho                         4    36   6   3    7    6    3    3    2    0   11  orchard/src/circuit/note_commit.rs
  53 NoteCommit input psi                         5    44   6   3    8    8    3    4    2    0   14  orchard/src/circuit/note_commit.rs
  54 y coordinate checks                          7    58   6   3   10   11    5    4    3    0   18  orchard/src/circuit/note_commit.rs

[2] selectors: activation rows, relations, compression
  sel smp  rows first  last  col  k deg  occ  +muls
    0   y     5  1681  1745   18  6   3    4     20
    1   y     1   273   273   18  6   2    1      5
    2   N   491   247  1777   14  1   0    0      0
    3   N   313   247   578   15  1   0    0      0
    4   y    89   599  1777   18  6   3    1      5
    5   y     1  1762  1762   19  4   5    2      6
    6   y     5  1763  1767   19  4   4    1      3
    7   y   518   248  1678   19  4   4    2      6
    8   y    21     0  1711   19  4   6   12     36
    9   y     1     1     1   18  6   4    1      5
   10   y   124     2   125   18  6   4    6     30
   11   y     1   126   126   18  6   4    4     20
   12   y     1     1     1   20  5   4    1      4
   13   y   125     2   126   20  5   4    6     24
   14   y     1   127   127   20  5   4    4     16
   15   y     3   130   134   20  5   3    2      8
   16   y     1   248   248   20  5   5    5     20
   17   y     1   135   135   21  4   3    3      9
   18   y   107   247  1679   22  1   9    4      0
   19   y   425   333   757   23  1   9    4      0
   20   y     1  1692  1692   21  4   3    4     12
   21   y     1   254   254   21  4   5    8     24
   22   y     8  1606  1641   21  4   6    3      9
   23   y    28  1610  1637   24  4   6    4     12
   24   y     1   251   251   24  4   2    3      9
   25   N   992   137  1656   16  1   6    2      0
   26   y    18   137  1606   24  4   4    1      3
   27   y    16  1746  1761   24  4   3    3      9
   28   y    16  1713  1743   25  6   2    4     20
   29   N   941   137  1604   17  1   6    2      0
   30   y    17   137  1553   25  6   4    1      5
   31   y    16  1657  1754   26  7   3    3     18
   32   y    16  1643  1737   26  7   2    4     24
   33   y     1  1709  1709   25  6   3   14     70
   34   y     1   256   256   25  6   3    3     15
   35   y     1   260   260   25  6   3    3     15
   36   y     1   268   268   25  6   2    1      5
   37   y     1   266   266   26  7   3    2     12
   38   y     1   269   269   26  7   3    2     12
   39   y     1   586   586   26  7   3    5     30
   40   y     1   580   580   26  7   3    4     24
   41   y     1   596   596   26  7   2    1      6
   42   y     1   594   594   27  7   3    4     24
   43   y     1   582   582   27  7   3    5     30
   44   y     2  1645  1653   27  7   3    7     42
   45   y     1   258   258   27  7   3    3     18
   46   y     1   262   262   27  7   3    3     18
   47   y     1   270   270   27  7   2    1      6
   48   y     1   264   264   27  7   3    2     12
   49   y     1   271   271   28  7   3    2     12
   50   y     1   592   592   28  7   3    5     30
   51   y     1   590   590   28  7   3    4     24
   52   y     1   597   597   28  7   2    1      6
   53   y     1   588   588   28  7   3    4     24
   54   y     1   584   584   28  7   3    5     30
   55   y     2  1649  1719   28  7   3    7     42
  highest enabled row 1777 of 2048 (usable ~2044); total selector leaves 193 across 193 constraints

  pairwise row-set relations over 1540 pairs:
    disjoint          1413  (91.8%)
    partial overlap    126  (8.2%)
    identical            1  (0.1%)
    pairs that share at least one row (compression forbids packing these):
      sel  2 &  1: 1 rows shared
      sel  3 &  1: 1 rows shared
      sel  3 &  2: 313 rows shared
      sel  4 &  2: 89 rows shared
      sel  5 &  2: 1 rows shared
      sel  5 &  4: 1 rows shared
      sel  6 &  2: 3 rows shared
      sel  6 &  4: 1 rows shared
      sel  7 &  1: 1 rows shared
      sel  7 &  2: 423 rows shared
      sel  7 &  3: 305 rows shared
      sel  7 &  4: 60 rows shared
      sel  8 &  2: 8 rows shared
      sel  8 &  4: 4 rows shared
      sel 12 &  9: 1 rows shared
      sel 13 & 10: 124 rows shared
      sel 13 & 11: 1 rows shared
      sel 15 &  8: 3 rows shared
      sel 16 &  2: 1 rows shared
      sel 16 &  3: 1 rows shared
      sel 16 &  7: 1 rows shared
      sel 17 &  8: 1 rows shared
      sel 18 &  1: 1 rows shared
      sel 18 &  2: 96 rows shared
      sel 18 &  3: 82 rows shared
      sel 18 &  4: 7 rows shared
      sel 18 &  7: 103 rows shared
      sel 18 & 16: 1 rows shared
      sel 19 &  2: 336 rows shared
      sel 19 &  3: 230 rows shared
      sel 19 &  4: 53 rows shared
      sel 19 &  7: 415 rows shared
      sel 20 &  2: 1 rows shared
      sel 20 &  4: 1 rows shared
      sel 21 &  2: 1 rows shared
      sel 21 &  3: 1 rows shared
      sel 21 &  7: 1 rows shared
      sel 21 & 18: 1 rows shared
      sel 22 &  2: 5 rows shared
      sel 22 &  4: 2 rows shared
      sel 23 &  2: 19 rows shared
      sel 23 &  4: 10 rows shared
      sel 24 &  2: 1 rows shared
      sel 24 &  3: 1 rows shared
      sel 24 &  7: 1 rows shared
      sel 24 & 18: 1 rows shared
      sel 25 &  2: 24 rows shared
      sel 25 &  4: 12 rows shared
      sel 25 & 22: 8 rows shared
      sel 25 & 23: 28 rows shared
      sel 26 &  2: 1 rows shared
      sel 26 & 22: 1 rows shared
      sel 26 & 25: 18 rows shared
      sel 27 &  2: 5 rows shared
      sel 27 &  4: 2 rows shared
      sel 29 & 25: 941 rows shared
      sel 29 & 26: 17 rows shared
      sel 30 & 25: 17 rows shared
      sel 30 & 26: 17 rows shared
      sel 30 & 29: 17 rows shared
      sel 31 & 27: 9 rows shared
      sel 31 & 28: 3 rows shared
      sel 32 & 25: 4 rows shared
      sel 32 & 28: 12 rows shared
      sel 33 &  2: 1 rows shared
      sel 34 &  2: 1 rows shared
      sel 34 &  3: 1 rows shared
      sel 34 &  7: 1 rows shared
      sel 34 & 18: 1 rows shared
      sel 35 &  2: 1 rows shared
      sel 35 &  3: 1 rows shared
      sel 35 &  7: 1 rows shared
      sel 35 & 18: 1 rows shared
      sel 36 &  2: 1 rows shared
      sel 36 &  3: 1 rows shared
      sel 36 &  7: 1 rows shared
      sel 36 & 18: 1 rows shared
      sel 37 &  2: 1 rows shared
      sel 37 &  3: 1 rows shared
      sel 37 &  7: 1 rows shared
      sel 37 & 18: 1 rows shared
      sel 38 &  2: 1 rows shared
      sel 38 &  3: 1 rows shared
      sel 38 &  7: 1 rows shared
      sel 38 & 18: 1 rows shared
      sel 39 &  7: 1 rows shared
      sel 39 & 19: 1 rows shared
      sel 40 &  7: 1 rows shared
      sel 40 & 19: 1 rows shared
      sel 41 &  7: 1 rows shared
      sel 41 & 19: 1 rows shared
      sel 42 &  7: 1 rows shared
      sel 42 & 19: 1 rows shared
      sel 43 &  7: 1 rows shared
      sel 43 & 19: 1 rows shared
      sel 44 & 25: 2 rows shared
      sel 45 &  2: 1 rows shared
      sel 45 &  3: 1 rows shared
      sel 45 &  7: 1 rows shared
      sel 45 & 18: 1 rows shared
      sel 46 &  2: 1 rows shared
      sel 46 &  3: 1 rows shared
      sel 46 &  7: 1 rows shared
      sel 46 & 18: 1 rows shared
      sel 47 &  2: 1 rows shared
      sel 47 &  3: 1 rows shared
      sel 47 &  7: 1 rows shared
      sel 47 & 18: 1 rows shared
      sel 48 &  2: 1 rows shared
      sel 48 &  3: 1 rows shared
      sel 48 &  7: 1 rows shared
      sel 48 & 18: 1 rows shared
      sel 49 &  2: 1 rows shared
      sel 49 &  3: 1 rows shared
      sel 49 &  7: 1 rows shared
      sel 49 & 18: 1 rows shared
      sel 50 &  7: 1 rows shared
      sel 50 & 19: 1 rows shared
      sel 51 &  7: 1 rows shared
      sel 51 & 19: 1 rows shared
      sel 52 &  7: 1 rows shared
      sel 52 & 19: 1 rows shared
      sel 53 & 19: 1 rows shared
      sel 54 &  7: 1 rows shared
      sel 54 & 19: 1 rows shared
      sel 55 & 25: 1 rows shared
      sel 55 & 28: 1 rows shared

  56 virtual -> 15 new fixed columns
   col  k selectors                                      maxdeg  rule  disj
    14  1 [2]                                                 0    ok   yes
    15  1 [3]                                                 0    ok   yes
    16  1 [25]                                                6    ok   yes
    17  1 [29]                                                6    ok   yes
    18  6 [0, 1, 4, 9, 10, 11]                                4    ok   yes
    19  4 [5, 6, 7, 8]                                        6    ok   yes
    20  5 [12, 13, 14, 15, 16]                                5    ok   yes
    21  4 [17, 20, 21, 22]                                    6    ok   yes
    22  1 [18]                                                9    ok   yes
    23  1 [19]                                                9    ok   yes
    24  4 [23, 24, 26, 27]                                    6    ok   yes
    25  6 [28, 30, 33, 34, 35, 36]                            4    ok   yes
    26  7 [31, 32, 37, 38, 39, 40, 41]                        3    ok   yes
    27  7 [42, 43, 44, 45, 46, 47, 48]                        3    ok   yes
    28  7 [49, 50, 51, 52, 53, 54, 55]                        3    ok   yes
  all groups disjoint and within the degree rule: YES
  every multi-selector group is degree-saturated (d + k = 9): YES -> no further packing is possible at this max degree
  complex selectors (used in a lookup -> boolean mask, own column by rule): 4 -> [2, 3, 25, 29]
  simple but alone because the degree budget is spent (deg 9): [18, 19]

  52 simple selectors repacked best-fit-decreasing: 11 columns vs 11 used by keygen (the greedy in `compress_selectors` walks indices in order, not by degree)

[3] where the cost actually is: the substitution, not the gate count
  compression adds 869 products to the quotient tree (authored 555, prover sees 1424)
  that is 61.0% of all products and 157% of the authored multiply count
  at H6's measured price (0.1861 ms per block multiply at 1P): 161.7 ms = 5.93% of the proof

   # gate                                       con  sel  k  +muls     ms
  32 CommitIvk canonicity check                  14   33  6     70   13.0
  43 y coordinate checks                          7   44  7     42    7.8
  54 y coordinate checks                          7   55  7     42    7.8
   6 complete addition                           12    8  4     36    6.7
   8 q_mul_2 == 1 checks                          6   10  6     30    5.6
  38 NoteCommit input g_d                         5   39  7     30    5.6
  42 NoteCommit input psi                         5   43  7     30    5.6
  49 NoteCommit input g_d                         5   50  7     30    5.6
  53 NoteCommit input psi                         5   54  7     30    5.6
  11 q_mul_2 == 1 checks                          6   13  5     24    4.5
  20 Canonicity checks                            8   21  4     24    4.5
  31 Decomposition check                          4   32  7     24    4.5
  39 NoteCommit input pk_d                        4   40  7     24    4.5
  41 NoteCommit input rho                         4   42  7     24    4.5
  50 NoteCommit input pk_d                        4   51  7     24    4.5
  52 NoteCommit input rho                         4   53  7     24    4.5
   0 Orchard circuit checks                       4    0  6     20    3.7
   9 q_mul_3 == 1 checks                          4   11  6     20    3.7
  14 overflow checks                              5   16  5     20    3.7
  27 Decomposition check                          4   28  6     20    3.7
  30 a' = b ⋅ swap + a ⋅ (1-swap)                 3   31  7     18    3.3
  44 NoteCommit MessagePiece b                    3   45  7     18    3.3
  45 NoteCommit MessagePiece d                    3   46  7     18    3.3
  12 q_mul_3 == 1 checks                          4   14  5     16    3.0
  33 NoteCommit MessagePiece b                    3   34  6     15    2.8
  34 NoteCommit MessagePiece d                    3   35  6     15    2.8
  19 Short fixed-base mul gate                    4   20  4     12    2.2
  22 partial rounds                               4   23  4     12    2.2
  36 NoteCommit MessagePiece g                    2   37  7     12    2.2
  37 NoteCommit MessagePiece h                    2   38  7     12    2.2
  47 NoteCommit MessagePiece g                    2   48  7     12    2.2
  48 NoteCommit MessagePiece h                    2   49  7     12    2.2
  15 LSB check                                    3   17  4      9    1.7
  21 full round                                   3   22  4      9    1.7
  23 pad-and-add                                  3   24  4      9    1.7
  26 a' = b ⋅ swap + a ⋅ (1-swap)                 3   27  4      9    1.7
  13 Decompose scalar for complete bits of vari   2   15  5      8    1.5
   3 witness point                                2    5  4      6    1.1
   5 incomplete addition                          2    7  4      6    1.1
  40 NoteCommit input value                       1   41  7      6    1.1
  46 NoteCommit MessagePiece e                    1   47  7      6    1.1
  51 NoteCommit input value                       1   52  7      6    1.1
   1 Field element addition: c = a + b            1    1  6      5    0.9
   2 Short lookup bitshift                        1    4  6      5    0.9
   7 q_mul_1 == 1 checks                          1    9  6      5    0.9
  28 Initial y_Q                                  1   30  6      5    0.9
  35 NoteCommit MessagePiece e                    1   36  6      5    0.9
  10 q_mul_1 == 1 checks                          1   12  5      4    0.7
   4 witness non-identity point                   1    6  4      3    0.6
  24 Initial y_Q                                  1   26  4      3    0.6
  16 range check                                  1   18  1      0    0.0
  17 Running sum coordinates check                3   18  1      0    0.0
  18 Full-width fixed-base scalar mul             4   19  1      0    0.0
  25 Sinsemilla gate                              2   25  1      0    0.0
  29 Sinsemilla gate                              2   29  1      0    0.0

[4] what one column is worth (measured), against H6+H7
  eval_polynomial at n=2048: 44.2 us (21.58 ns/coeff, median of 200, min 41.5 us, max 195.5 us)
  one more fixed column = 1 eval + 1 MSM point = 0.0442 ms = 0.00162% of a 1P proof
  all 29 fixed queries: 1.282 ms (0.047%)
  the 15 selector columns cost 0.663 ms (0.02432%) of prove
  and save 869 products = 161.7 ms (5.93%) of quotient

  the frontier if the packing level were a dial (uniform k):
     k  columns  +products  quotient ms  opening ms
     1       52          0          0.0       2.298
     2       26        189         35.2       1.149
     3       18        378         70.3       0.796
     4       13        567        105.5       0.575
     5       11        756        140.7       0.486
     6        9        945        175.8       0.398
     7        8       1134        211.0       0.354
     8        7       1323        246.1       0.309

[5] the prover's own totals (F2, same run, same tree)
  quotient call: poly_len 16384 chunk 147 x 112   tree vector_muls 6570  nodes 16345  max_depth 22
  of those vector muls: gate products 1424 (21.7%), of which selector recovery 869 (13.2%)
  all 13 evaluations this run: vector_muls 6698, field muls 107905024, leaf elements copied 76115968
  segments (terms per source): {'gates': 386, 'permutation': 14, 'lookup': 30}

