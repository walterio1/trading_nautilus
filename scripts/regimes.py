"""
Regime enumeration for the three crossover pairings.

Three cumulative-profit series are tracked, one per pairing:

    A = fast + slow
    B = fast + superSlow
    C = slow + superSlow

The regime is the sign pattern of the INCREMENT of A, B and C measured from a
reset point (the slow-superSlow crossover) up to the bar being classified.
Three signs give 2^3 = 8 regimes, grouped by how many pairings are in profit:

    3 winners : ALL
    2 winners : AB, AC, BC        <- these were missing from the first pass
    1 winner  : A, B, C
    0 winners : NONE

Measurement scheme
------------------
The regime for a segment is read off the COMPLETED increments of the PREVIOUS
segment - the stretch running from one slow-superSlow crossover to the next.
This is causal by construction (the previous segment is closed before the
current one starts) and is defined from the very first bar of the segment, so
there is no blind warm-up window after a reset.

Zeros
-----
An increment of exactly zero carries no directional information, so it does not
classify: `classify` returns None and nothing is traded for that segment. The
state space therefore stays at the 8 sign triples plus one "do not trade" case,
rather than the 27 a fully three-valued sign would produce.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Regime:
    code: str        # Sign triple, e.g. "+-+"
    name: str        # Short label
    winners: tuple   # Which pairings are in profit
    description: str


# All 2^3 sign combinations of (A, B, C), ordered from three winners to none.
REGIMES: dict[str, Regime] = {
    "+++": Regime("+++", "ALL", ("A", "B", "C"), "las tres operativas en beneficio"),
    "++-": Regime("++-", "AB", ("A", "B"), "A y B en beneficio, C no"),
    "+-+": Regime("+-+", "AC", ("A", "C"), "A y C en beneficio, B no"),
    "-++": Regime("-++", "BC", ("B", "C"), "B y C en beneficio, A no"),
    "+--": Regime("+--", "A", ("A",), "solo A en beneficio"),
    "-+-": Regime("-+-", "B", ("B",), "solo B en beneficio"),
    "--+": Regime("--+", "C", ("C",), "solo C en beneficio"),
    "---": Regime("---", "NONE", (), "ninguna operativa en beneficio"),
}


def classify(delta_a, delta_b, delta_c) -> Regime | None:
    """
    Map the previous segment's three increments to a regime.

    Returns None when any increment is exactly zero: that segment carries no
    directional information, so nothing is traded in the segment it would have
    classified.
    """
    deltas = (delta_a, delta_b, delta_c)
    if any(d == 0 for d in deltas):
        return None
    return REGIMES["".join("+" if d > 0 else "-" for d in deltas)]


def count_zeros(delta_a, delta_b, delta_c) -> int:
    """How many of the three increments are exactly zero, for diagnostics."""
    return sum(1 for d in (delta_a, delta_b, delta_c) if d == 0)


if __name__ == "__main__":
    from itertools import product

    seen = set()
    print(f"{'code':<7}{'name':<7}{'winners':<12}description")
    print("-" * 62)
    for signs in product((1.0, -1.0), repeat=3):
        regime = classify(*signs)
        seen.add(regime.code)
        print(f"{regime.code:<7}{regime.name:<7}{','.join(regime.winners) or '-':<12}"
              f"{regime.description}")

    assert seen == set(REGIMES), "classify does not reach every declared regime"
    assert len(REGIMES) == 8, f"expected 8 regimes, declared {len(REGIMES)}"
    assert classify(0, 1, -1) is None, "a zero increment must not classify"
    assert classify(1, 0, 0) is None, "a zero increment must not classify"
    assert classify(1, -1, 1) is not None, "non-zero increments must classify"
    by_count: dict[int, int] = {}
    for regime in REGIMES.values():
        by_count[len(regime.winners)] = by_count.get(len(regime.winners), 0) + 1
    print()
    print("regimenes por numero de operativas en beneficio:",
          {k: by_count[k] for k in sorted(by_count, reverse=True)})
    print("clasificacion exhaustiva y biyectiva: OK")
