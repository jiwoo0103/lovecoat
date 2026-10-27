"""Reproducible garment-group split for category annotation drafts, not truth promotion."""
from collections import Counter, defaultdict
import random


def group_candidates(records, near_pairs=()):
    rows = {r["code"]: r for r in records}
    if len(rows) != len(records):
        raise ValueError("Duplicate garment code")
    parent = {code: code for code in rows}

    def root(code):
        while parent[code] != code:
            parent[code] = parent[parent[code]]
            code = parent[code]
        return code

    def join(a, b):
        a, b = root(a), root(b)
        parent[max(a, b)] = min(a, b)

    for key in ("source_group", "source_sha256"):
        first = {}
        for row in records:
            if not row.get(key):
                raise ValueError(f"Missing {key}")
            if row[key] in first:
                join(row["code"], first[row[key]])
            else:
                first[row[key]] = row["code"]
    for a, b in near_pairs:
        if a not in rows or b not in rows:
            raise ValueError("Unknown code in a similarity pair")
        join(a, b)
    return {code: "category-group-" + root(code) for code in rows}


def split_groups(records, groups, ratios=None, seed=2026102705, train_only=()):
    """Stratify label and age approximately; keep mixed-label similarity groups intact."""
    ratios = ratios or {"train": .65, "dev": .15, "test": .20}
    if set(ratios) != {"train", "dev", "test"} or abs(sum(ratios.values()) - 1) > 1e-8 or min(ratios.values()) <= 0:
        raise ValueError("Three positive split fractions must sum to one")
    if {r["code"] for r in records} != set(groups):
        raise ValueError("Missing or unknown grouping")
    grouped = defaultdict(list)
    for row in records:
        grouped[groups[row["code"]]].append(row)
    strata = Counter((r["draft_category"], r["age_group"]) for r in records)
    rng = random.Random(seed)
    order = list(grouped)
    rng.shuffle(order)
    order.sort(key=lambda g: -sum(1 / strata[(r["draft_category"], r["age_group"])] for r in grouped[g]))
    allocated = {split: Counter() for split in ratios}
    total = Counter()
    pinned = set(train_only)
    if not pinned <= set(groups):
        raise ValueError("Unknown train-only code")
    assignments = {}
    for group in order:
        rows = grouped[group]
        counts = Counter((r["draft_category"], r["age_group"]) for r in rows)
        candidates = list(ratios)
        rng.shuffle(candidates)
        if any(r["code"] in pinned for r in rows):
            selected = "train"
        else:
            selected = max(candidates, key=lambda split: (
                sum(n * (ratios[split] - allocated[split][key] / strata[key]) for key, n in counts.items()),
                ratios[split] - total[split] / len(records)))
        allocated[selected].update(counts)
        total[selected] += len(rows)
        for row in rows:
            assignments[row["code"]] = selected
    for group, rows in grouped.items():
        if len({assignments[r["code"]] for r in rows}) != 1:
            raise AssertionError("Garment group crossed splits")
    return assignments
