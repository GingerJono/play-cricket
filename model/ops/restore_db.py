#!/usr/bin/env python3
"""
Restore data/rainham.db from the most recent S3 snapshot (latest.db.gz).

Same env vars as backup_db.py:
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY     (or any boto3-supported method)
  AWS_REGION                                     defaults to us-east-1
  RCC_S3_BUCKET                                  defaults to "rcc-play-cricket-db"
  RCC_S3_PREFIX                                  defaults to "db/"

Usage:
  python3 model/ops/restore_db.py                 # pulls latest.db.gz
  python3 model/ops/restore_db.py --key db/rainham-20260508T120000Z.db.gz
  python3 model/ops/restore_db.py --list          # print available snapshots
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "data" / "rainham.db"


def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        raise SystemExit(f"missing env var: {name}")
    return v


def file_md5(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=None,
                    help="S3 object key to restore (defaults to <prefix>latest.db.gz)")
    ap.add_argument("--list", action="store_true",
                    help="list available snapshots and exit")
    ap.add_argument("--out", default=str(DB), help="local path to write to")
    args = ap.parse_args()

    region = env("AWS_REGION", "us-east-1")
    bucket = env("RCC_S3_BUCKET", "rcc-play-cricket-db")
    prefix = env("RCC_S3_PREFIX", "db/")
    if not prefix.endswith("/"):
        prefix += "/"

    s3 = boto3.client("s3", region_name=region)

    if args.list:
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        rows = resp.get("Contents", [])
        rows.sort(key=lambda r: r["LastModified"])
        if not rows:
            print(f"no snapshots in s3://{bucket}/{prefix}")
            return 0
        print(f"{'last_modified':<22} {'size_mb':>8}  key")
        print("-" * 80)
        for r in rows:
            print(f"{r['LastModified'].strftime('%Y-%m-%dT%H:%M:%SZ'):<22} "
                  f"{r['Size']/1e6:>8.1f}  {r['Key']}")
        return 0

    key = args.key or (prefix + "latest.db.gz")
    out_path = Path(args.out)
    if out_path.exists():
        backup = out_path.with_suffix(out_path.suffix + ".prev")
        print(f"existing {out_path} → {backup}")
        out_path.replace(backup)

    print(f"downloading s3://{bucket}/{key} ...", flush=True)
    tmp = Path(tempfile.mkstemp(suffix=".db.gz", prefix="rainham-")[1])
    try:
        t0 = time.perf_counter()
        s3.download_file(bucket, key, str(tmp))
        gz_size = tmp.stat().st_size
        print(f"  {gz_size/1e6:.1f} MB  ({time.perf_counter() - t0:.1f}s)")

        head = s3.head_object(Bucket=bucket, Key=key)
        meta = head.get("Metadata") or {}
        expected_md5 = meta.get("source-md5")
        expected_size = int(meta.get("source-size-bytes", "0"))

        print(f"decompressing → {out_path} ...", flush=True)
        t0 = time.perf_counter()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(tmp, "rb") as src, out_path.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1 << 20)
        actual_size = out_path.stat().st_size
        print(f"  {actual_size/1e6:.1f} MB  ({time.perf_counter() - t0:.1f}s)")

        if expected_size and actual_size != expected_size:
            print(f"WARNING: size mismatch — expected {expected_size}, got {actual_size}")
        if expected_md5:
            actual_md5 = file_md5(out_path)
            if actual_md5 != expected_md5:
                print(f"WARNING: md5 mismatch — expected {expected_md5}, got {actual_md5}")
            else:
                print(f"md5 verified: {actual_md5}")

        print(f"\ndone. {out_path} is ready to use.")
        return 0
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
