#!/usr/bin/env python3
"""Compute the polynomial degree of every custom gate in a halo2
`dev::CircuitGates` dump.

Why this exists
---------------
`ConstraintSystem::degree()` returns a single max over all gates, so it says
"some gate reaches 9" but not which one. `dev::CircuitGates` stringifies each
gate polynomial with parentheses preserved exactly where operator precedence
would otherwise be ambiguous, which is enough to recover per-gate degree.

Degree rules (halo2 `Expression` semantics):
  Constant(c)            -> 0   (also the right factor of `Scaled`)
  Advice/Fixed/Instance  -> 1
  Selector               -> 1
  Sum / Negated          -> max / same
  Product                -> sum of factors (constants count as 0)

Usage: python gate_degree.py <gates_raw.txt> [--top N]
"""

import re
import sys
from collections import defaultdict

NUM = re.compile(r"^(?:0x[0-9a-fA-F]+|[0-9]+)$")
IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:@-?[0-9]+)?$")


def tokenize(s):
    tokens = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
        elif c in "+-*()":
            tokens.append(c)
            i += 1
        else:
            j = i
            while j < n and (s[j].isalnum() or s[j] == "_"):
                j += 1
            # Query identifiers carry a rotation suffix: "A4@0", "F12@-1".
            if j < n and s[j] == "@":
                j += 1
                if j < n and s[j] == "-":
                    j += 1
                while j < n and s[j].isdigit():
                    j += 1
            tokens.append(s[i:j])
            i = j
    return tokens


class Parser:
    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def parse(self):
        d = self.expr()
        if self.i != len(self.t):
            raise ValueError(f"trailing tokens: {self.t[self.i:]}")
        return d

    def expr(self):
        d = self.term()
        while self.peek() in ("+", "-"):
            self.take()
            d = max(d, self.term())
        return d

    def term(self):
        d = self.factor()
        while self.peek() == "*":
            self.take()
            d += self.factor()
        return d

    def factor(self):
        tok = self.peek()
        if tok is None:
            raise ValueError("unexpected end of expression")
        if tok == "(":
            self.take()
            d = self.expr()
            if self.peek() != ")":
                raise ValueError("missing )")
            self.take()
            return d
        if tok == "-":
            self.take()
            return self.factor()
        self.take()
        if NUM.match(tok):
            return 0
        if IDENT.match(tok):
            return 1
        raise ValueError(f"unexpected token {tok!r}")


def degree(expr):
    return Parser(tokenize(expr)).parse()


def parse_dump(path):
    """Return [(gate_name, [(constraint_name, expression), ...]), ...]."""
    gates = []
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line or line.startswith(" ") or line.startswith("- "):
            i += 1
            continue
        if not line.endswith(":"):
            i += 1  # summary lines: "Total gates: 55" etc.
            continue
        if line.startswith("Total "):
            i += 1
            continue
        gate_name = line[:-1]
        constraints = []
        i += 1
        while i < len(lines) and (lines[i].startswith("- ") or lines[i].startswith("  ")):
            body = lines[i][2:] if lines[i].startswith("- ") else lines[i].strip()
            if lines[i].startswith("- ") and body.endswith(":") and not body.rstrip(":").count("("):
                cname = body[:-1]
                i += 1
                expr = lines[i].strip()
                constraints.append((cname, expr))
            elif lines[i].startswith("- ") and body.endswith(":") and "(" not in body:
                cname = body[:-1]
                i += 1
                expr = lines[i].strip()
                constraints.append((cname, expr))
            elif lines[i].startswith("- "):
                constraints.append(("", body))
            else:
                # continuation line of a multi-line expression
                if constraints:
                    cname, expr = constraints[-1]
                    constraints[-1] = (cname, expr + " " + body)
            i += 1
        gates.append((gate_name, constraints))
    return gates


def main():
    path = sys.argv[1]
    top = 10
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])

    gates = parse_dump(path)
    rows = []
    seen = defaultdict(int)
    for name, constraints in gates:
        seen[name] += 1
        label = name if seen[name] == 1 else f"{name} (instance {seen[name]})"
        degs = [(degree(e), cname) for cname, e in constraints]
        best = max(degs, key=lambda x: x[0])
        rows.append((best[0], label, len(constraints), best[1]))

    max_deg = max(r[0] for r in rows)
    print(f"gates parsed: {len(rows)}")
    print(f"max gate degree: {max_deg}")
    print()
    print(f"gates at degree {max_deg}:")
    for deg, label, ncon, cname in rows:
        if deg == max_deg:
            print(f"  [{deg}] {label}  ({ncon} constraint(s), attaining: {cname or '<unnamed>'})")
    print()
    print(f"top {top} gates by degree:")
    for deg, label, ncon, cname in sorted(rows, key=lambda r: -r[0])[:top]:
        print(f"  {deg}  {label}  ({ncon} constraint(s))")


if __name__ == "__main__":
    main()
