#!/usr/bin/env python3
"""Merge classified time_horizon values back into original JSON files and update header counts."""
import json
import os

base_dir = "D:/claude_code_ana/blogger-analysis/data"
signals_dir = os.path.join(base_dir, "signals")
classified_dir = os.path.join(base_dir, "classified")

files = [
    "TL阳光.json",
    "云帆观市.json",
    "衡山佛曰论股.json"
]

for fname in files:
    print(f"\n{'='*60}")
    print(f"Processing: {fname}")
    print(f"{'='*60}")

    # Read original file
    fpath = os.path.join(signals_dir, fname)
    with open(fpath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    signals = data['signals']
    prefix = fname.replace('.json', '')
    print(f"Total signals: {len(signals)}")

    # Read all chunk classification files
    classifications = {}  # idx -> time_horizon
    chunk_num = 1
    while True:
        chunk_name = f"{prefix}_chunk{chunk_num}.json"
        chunk_path = os.path.join(classified_dir, chunk_name)
        if not os.path.exists(chunk_path):
            break

        with open(chunk_path, 'r', encoding='utf-8') as f:
            chunk_data = json.load(f)

        for item in chunk_data['classifications']:
            idx = item['idx']
            horizon = item['time_horizon']
            if horizon not in ('short', 'medium', 'long', 'unspecified'):
                print(f"  WARNING: Invalid time_horizon '{horizon}' at idx {idx}")
                continue
            classifications[idx] = horizon

        print(f"  Loaded chunk {chunk_num}: {len(chunk_data['classifications'])} items")
        chunk_num += 1

    print(f"Total classifications loaded: {len(classifications)}")

    # Apply classifications and track changes
    changes = 0
    for i, sig in enumerate(signals):
        if i not in classifications:
            print(f"  WARNING: Signal idx {i} has no classification! Keeping original.")
            continue
        new_horizon = classifications[i]
        old_horizon = sig['time_horizon']
        if new_horizon != old_horizon:
            changes += 1
            sig['time_horizon'] = new_horizon

    print(f"Signals changed: {changes} / {len(signals)}")

    # Verify all signals have a classification
    missing = [i for i in range(len(signals)) if i not in classifications]
    if missing:
        print(f"  ERROR: {len(missing)} signals missing classification! idx: {missing[:20]}...")
    else:
        print("  All signals have classifications.")

    # Recalculate header counts
    bullish_short = bullish_medium = bullish_long = bullish_unspecified = 0
    bullish_strong = bullish_moderate = 0
    bearish_short = bearish_medium = bearish_long = bearish_unspecified = 0
    bearish_strong = bearish_moderate = 0

    for sig in signals:
        d = sig['direction']
        s = sig['strength']
        h = sig['time_horizon']

        if d == 'bullish':
            if s == 'strong':
                bullish_strong += 1
            elif s == 'moderate':
                bullish_moderate += 1
            if h == 'short':
                bullish_short += 1
            elif h == 'medium':
                bullish_medium += 1
            elif h == 'long':
                bullish_long += 1
            elif h == 'unspecified':
                bullish_unspecified += 1
        elif d == 'bearish':
            if s == 'strong':
                bearish_strong += 1
            elif s == 'moderate':
                bearish_moderate += 1
            if h == 'short':
                bearish_short += 1
            elif h == 'medium':
                bearish_medium += 1
            elif h == 'long':
                bearish_long += 1
            elif h == 'unspecified':
                bearish_unspecified += 1

    # Update header
    data['scored_bullish'] = {
        "strong": bullish_strong,
        "moderate": bullish_moderate,
        "short": bullish_short,
        "medium": bullish_medium,
        "long": bullish_long,
        "unspecified": bullish_unspecified
    }
    data['scored_bearish'] = {
        "strong": bearish_strong,
        "moderate": bearish_moderate,
        "short": bearish_short,
        "medium": bearish_medium,
        "long": bearish_long,
        "unspecified": bearish_unspecified
    }

    print(f"\nNew scored_bullish: {json.dumps(data['scored_bullish'])}")
    print(f"New scored_bearish: {json.dumps(data['scored_bearish'])}")

    # Also print count by time_horizon regardless of direction
    short_total = bullish_short + bearish_short
    medium_total = bullish_medium + bearish_medium
    long_total = bullish_long + bearish_long
    unspecified_total = bullish_unspecified + bearish_unspecified
    total = short_total + medium_total + long_total + unspecified_total
    print(f"\nOverall distribution: short={short_total}, medium={medium_total}, long={long_total}, unspecified={unspecified_total} (total={total})")

    # Write back
    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nWritten updated file to: {fpath}")

print("\n\nDone! All files updated.")
