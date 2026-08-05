#!/usr/bin/env python3
"""
Merge position snapshots from 5 batch results and resolve state dependencies.
Processes chronologically:
- Explicit snapshots are used as-is
- Inferred snapshots modify the prior state
- Partial snapshots scale from prior state proportionally
"""
import json
import os

base = r"d:\claude_code_ana\blogger-analysis\data\positions"

# Read all batch results
all_snapshots = []
for b in range(1, 6):
    result_file = os.path.join(base, f"_batch_{b}_result.json")
    if os.path.exists(result_file):
        with open(result_file, "r", encoding="utf-8") as f:
            batch = json.load(f)
            all_snapshots.extend(batch)
            print(f"Batch {b}: {len(batch)} snapshots")
    else:
        print(f"Batch {b}: FILE NOT FOUND - {result_file}")

# Sort chronologically
all_snapshots.sort(key=lambda s: s["publish_time"])
print(f"\nTotal raw snapshots: {len(all_snapshots)}")

# Process chronologically, maintaining state
current_positions = {}  # current position composition
current_unmapped = {}
current_total = 0.0
resolved = []

for snap in all_snapshots:
    conf = snap.get("confidence", "explicit")

    if conf == "explicit":
        # Use directly
        current_positions = snap.get("positions", {})
        current_unmapped = snap.get("unmapped", {})
        current_total = snap.get("total_units", 0)
        resolved.append(snap)

    elif conf == "inferred":
        # This describes a trade action applied to prior state
        # The positions in the snapshot represent the CHANGE, not the full state
        delta_positions = snap.get("positions", {})
        delta_unmapped = snap.get("unmapped", {})

        # Apply deltas to current state
        for code, units in delta_positions.items():
            current_positions[code] = current_positions.get(code, 0) + units
            if current_positions[code] <= 0:
                del current_positions[code]
        for term, units in delta_unmapped.items():
            current_unmapped[term] = current_unmapped.get(term, 0) + units
            if current_unmapped[term] <= 0:
                del current_unmapped[term]

        current_total = sum(current_positions.values()) + sum(current_unmapped.values())

        # Output full state
        resolved.append({
            "date": snap["date"],
            "publish_time": snap["publish_time"],
            "post_id": snap["post_id"],
            "description": snap["description"],
            "positions": dict(current_positions),
            "unmapped": dict(current_unmapped),
            "total_units": current_total,
            "confidence": "inferred",
        })

    elif conf == "partial":
        # Total-only or composition-unknown
        new_total = snap.get("total_units", current_total)

        if current_total > 0 and new_total != current_total:
            # Scale existing positions proportionally
            scale = new_total / current_total
            scaled_positions = {}
            for code, units in current_positions.items():
                scaled_positions[code] = round(units * scale, 4)
            scaled_unmapped = {}
            for term, units in current_unmapped.items():
                scaled_unmapped[term] = round(units * scale, 4)

            current_positions = scaled_positions
            current_unmapped = scaled_unmapped
            current_total = new_total
        elif current_total == 0:
            # No prior state, just record the total
            pass

        resolved.append({
            "date": snap["date"],
            "publish_time": snap["publish_time"],
            "post_id": snap["post_id"],
            "description": snap["description"],
            "positions": dict(current_positions) if current_total > 0 else snap.get("positions", {}),
            "unmapped": dict(current_unmapped) if current_total > 0 else snap.get("unmapped", {}),
            "total_units": new_total,
            "confidence": "partial",
        })

# Remove duplicates (same post_id)
seen = set()
unique = []
for snap in resolved:
    if snap["post_id"] not in seen:
        seen.add(snap["post_id"])
        unique.append(snap)
    else:
        print(f"  Duplicate post_id skipped: {snap['post_id']}")

# Write final output
output_file = os.path.join(base, "顺应周期_positions.json")
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(unique, f, ensure_ascii=False, indent=2)

print(f"\nFinal output: {len(unique)} snapshots written to {output_file}")
print("\nSnapshots:")
for snap in unique:
    print(f"  {snap['publish_time']} | {snap['confidence']:10s} | {snap['total_units']:5.1f}成 | {snap['positions']} | {snap.get('unmapped', {})}")
