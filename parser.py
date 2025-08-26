#!/usr/bin/env python3
"""
LLM Comments Parser (local version)
-----------------------------------
- Scans an input folder for *_COMMENTS.txt files
- Cleans & validates JSON arrays of opinion objects
- Appends to a master CSV with stable schema and de-dupe
- Moves bad JSON files to a debug folder for inspection

Usage:
  python parser.py --in ./comments_out --out ./analysis --master comments_master_table.csv

Requirements:
  - Python 3.9+
  - pandas
"""

import argparse
import json
import logging
import re
import shutil
import sys
from pathlib import Path

import pandas as pd

# ---------------- Logging ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("parser")

# ---------------- Cleaning helpers ----------------
def clean_json_text(raw_text: str) -> str:
    """
    Harden typical LLM JSON quirks:
      - Strip ```json ... ``` fences
      - Remove trailing commas before } or ]
      - Drop control chars
      - Decode HTML entities
      - Normalise odd encodings
    """
    import html

    raw_text = re.sub(r"^```json\s*", "", raw_text, flags=re.IGNORECASE | re.MULTILINE)
    raw_text = re.sub(r"```$", "", raw_text, flags=re.MULTILINE).strip()
    raw_text = re.sub(r",\s*([}\]])", r"\1", raw_text)  # trailing commas
    raw_text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", raw_text)  # control chars
    raw_text = html.unescape(raw_text)
    raw_text = raw_text.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
    return raw_text


def parse_comments_json_text(raw_text: str, filename: str) -> list[dict] | None:
    """
    Return list of valid dict rows or:
      - [] if structure is wrong (non-fatal)
      - None if completely unparseable (fatal)
    """
    try:
        cleaned = clean_json_text(raw_text)
        obj = json.loads(cleaned)
        if not isinstance(obj, list):
            log.warning(f"⚠️ Unexpected JSON format in {filename} — not a list")
            return []

        valid = []
        for i, row in enumerate(obj):
            if not isinstance(row, dict):
                log.warning(f"⚠️ Skip non-dict entry in {filename} [index {i}]")
                continue
            # Minimal required fields for traceability
            if "video_id" not in row or "quote" not in row:
                log.warning(f"⚠️ Skip malformed dict missing required fields in {filename} [index {i}]")
                continue
            valid.append(row)

        log.info(f"✅ Parsed {len(valid)} rows from {filename}")
        return valid

    except json.JSONDecodeError as e:
        log.error(f"❌ JSON decoding error in {filename}: {e}")
        return None
    except Exception as e:
        log.error(f"❌ Failed to parse {filename}: {e}")
        return None


# ---------------- Core pipeline ----------------
# Basic schema only — extend with extra fields if needed
COLUMN_ORDER = [
    "video_id",
    "video_title",
    "channel_title",
    "publish_date",
    "quote",
    # e.g., "topic", "sentiment_score" etc. can be added here
]


def ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = df.copy()
    for col in columns:
        if col not in out.columns:
            out[col] = ""  # fill missing
    return out.reindex(columns=columns)


def load_master(master_path: Path) -> pd.DataFrame:
    if master_path.exists():
        log.info(f"📄 Loading existing master: {master_path.name}")
        return pd.read_csv(master_path)
    log.info("📄 No existing master found — starting fresh.")
    return pd.DataFrame(columns=COLUMN_ORDER)


def save_master(df: pd.DataFrame, out_dir: Path, master_name: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / master_name
    df.to_csv(target, index=False)
    log.info(f"💾 Saved master: {target} | total rows: {len(df)}")


def process_folder(in_dir: Path, out_dir: Path, master_name: str) -> None:
    debug_dir = in_dir / "_debug_bad_json"
    debug_dir.mkdir(parents=True, exist_ok=True)

    files = [p for p in in_dir.glob("*_COMMENTS.txt") if p.is_file()]
    log.info(f"🔍 Found {len(files)} files to parse in {in_dir}")

    all_rows: list[dict] = []

    for fp in files:
        raw = fp.read_text(encoding="utf-8", errors="ignore")
        rows = parse_comments_json_text(raw, fp.name)

        if rows is None:
            log.warning(f"⚠️ Moving bad JSON to debug: {fp.name}")
            shutil.move(str(fp), str(debug_dir / fp.name))
            continue

        all_rows.extend(rows)
        fp.unlink(missing_ok=True)
        log.info(f"🧹 Deleted parsed file: {fp.name}")

    if not all_rows:
        log.warning("⚠️ No valid rows parsed — nothing to append.")
        return

    new_df = pd.DataFrame(all_rows)
    new_df = ensure_columns(new_df, COLUMN_ORDER)

    if "publish_date" in new_df.columns:
        new_df["publish_date"] = pd.to_datetime(new_df["publish_date"], errors="coerce", utc=True)
        new_df = new_df.sort_values("publish_date", ascending=False)

    master_path = out_dir / master_name
    master_df = load_master(master_path)
    combined = pd.concat([master_df, new_df], ignore_index=True)

    combined["quote"] = combined["quote"].astype(str)
    combined = combined.drop_duplicates(subset=["video_id", "quote"]).reset_index(drop=True)

    save_master(combined, out_dir, master_name)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Parse LLM *_COMMENTS.txt into a clean master CSV.")
    p.add_argument("--in", dest="in_dir", required=True, help="Input folder with *_COMMENTS.txt files")
    p.add_argument("--out", dest="out_dir", required=True, help="Output folder for master CSV")
    p.add_argument(
        "--master",
        dest="master",
        default="comments_master_table.csv",
        help="Master CSV filename (default: comments_master_table.csv)",
    )
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)

    if not in_dir.exists():
        log.error(f"Input folder not found: {in_dir}")
        sys.exit(1)

    process_folder(in_dir, out_dir, args.master)


if __name__ == "__main__":
    main()
