#!/usr/bin/env python3
"""Extract signals from JSON files and split into chunks for classification."""
import json
import os

files = [
    "TL阳光.json",
    "云帆观市.json",
    "衡山佛曰论股.json"
]

base_dir = "D:/claude_code_ana/blogger-analysis/data/signals"
chunk_dir = "D:/claude_code_ana/blogger-analysis/data/chunks"
os.makedirs(chunk_dir, exist_ok=True)

for fname in files:
    fpath = os.path.join(base_dir, fname)
    with open(fpath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    signals = data['signals']
    print(f"{fname}: {len(signals)} signals")

    # Create chunks of ~250 signals each
    chunk_size = 250
    prefix = fname.replace('.json', '')

    for i in range(0, len(signals), chunk_size):
        chunk = signals[i:i+chunk_size]
        chunk_idx = i // chunk_size + 1
        chunk_name = f"{prefix}_chunk{chunk_idx}.json"
        chunk_path = os.path.join(chunk_dir, chunk_name)

        # Write simplified format: list of {idx, direction, strength, current_horizon, evidence}
        simplified = []
        for j, sig in enumerate(chunk):
            simplified.append({
                "idx": i + j,
                "direction": sig["direction"],
                "strength": sig["strength"],
                "current_horizon": sig["time_horizon"],
                "evidence": sig["evidence"]
            })

        with open(chunk_path, 'w', encoding='utf-8') as f:
            json.dump(simplified, f, ensure_ascii=False, indent=2)

        print(f"  Created {chunk_name}: signals {i}-{i+len(chunk)-1} ({len(chunk)} signals)")

    # Also save the full header for later
    header = {k: v for k, v in data.items() if k != 'signals'}
    header_path = os.path.join(chunk_dir, f"{prefix}_header.json")
    with open(header_path, 'w', encoding='utf-8') as f:
        json.dump(header, f, ensure_ascii=False, indent=2)

    print(f"  Total chunks: {(len(signals) + chunk_size - 1) // chunk_size}")

print("\nDone extracting chunks!")
