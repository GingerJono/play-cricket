#!/usr/bin/env python3
"""
Push data/rainham.db to AWS S3 as a versioned snapshot + a `latest.db` alias.

Required env vars:
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY     (or any boto3-supported method)
  AWS_REGION                                     defaults to us-east-1
  RCC_S3_BUCKET                                  defaults to "rcc-play-cricket-db"
  RCC_S3_PREFIX                                  defaults to "db/"

Bucket is created on first run if it doesn't exist.

Usage:
  python3 model/ops/backup_db.py
  python3 model/ops/backup_db.py --no-latest      # skip the latest.db alias
  python3 model/ops/backup_db.py --dry-run        # print what would be done
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
from botocore.exceptions import ClientError

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


def ensure_bucket(s3, bucket: str, region: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
        return
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code not in ("404", "NoSuchBucket", "NotFound"):
            raise
    print(f"creating bucket s3://{bucket} in {region} ...", flush=True)
    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket)
    else:
        s3.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": region},
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-latest", action="store_true",
                    help="skip writing the latest.db.gz alias")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB.exists():
        raise SystemExit(f"missing DB at {DB} — run build_db.py first")

    region = env("AWS_REGION", "us-east-1")
    bucket = env("RCC_S3_BUCKET", "rcc-play-cricket-db")
    prefix = env("RCC_S3_PREFIX", "db/")
    if not prefix.endswith("/"):
        prefix += "/"

    db_size = DB.stat().st_size
    db_md5 = file_md5(DB)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    snapshot_key = f"{prefix}rainham-{ts}.db.gz"
    latest_key = f"{prefix}latest.db.gz"

    print(f"DB:        {DB}  ({db_size/1e6:.1f} MB)  md5={db_md5}")
    print(f"region:    {region}")
    print(f"bucket:    s3://{bucket}/")
    print(f"snapshot:  s3://{bucket}/{snapshot_key}")
    if not args.no_latest:
        print(f"alias:     s3://{bucket}/{latest_key}")
    if args.dry_run:
        print("DRY RUN — exiting before any AWS calls")
        return 0

    # gzip into a tmp file (one-pass, deterministic)
    tmp = Path(tempfile.mkstemp(suffix=".db.gz", prefix="rainham-")[1])
    try:
        print(f"\ngzipping → {tmp} ...", flush=True)
        t0 = time.perf_counter()
        with DB.open("rb") as src, gzip.open(tmp, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, length=1 << 20)
        gz_size = tmp.stat().st_size
        print(f"gz size: {gz_size/1e6:.1f} MB  ({100*gz_size/db_size:.0f}% of original)  "
              f"({time.perf_counter() - t0:.1f}s)")

        s3 = boto3.client("s3", region_name=region)
        ensure_bucket(s3, bucket, region)

        meta = {
            "source-md5": db_md5,
            "source-size-bytes": str(db_size),
            "source-mtime": str(int(DB.stat().st_mtime)),
        }
        print(f"\nuploading snapshot ...", flush=True)
        t0 = time.perf_counter()
        s3.upload_file(
            str(tmp), bucket, snapshot_key,
            ExtraArgs={
                "Metadata": meta,
                "ContentType": "application/gzip",
            },
        )
        print(f"  {snapshot_key}  ({time.perf_counter() - t0:.1f}s)")

        if not args.no_latest:
            t0 = time.perf_counter()
            s3.upload_file(
                str(tmp), bucket, latest_key,
                ExtraArgs={
                    "Metadata": meta,
                    "ContentType": "application/gzip",
                },
            )
            print(f"  {latest_key}  ({time.perf_counter() - t0:.1f}s)")

        print(f"\ndone. recover with: python3 model/ops/restore_db.py")
        return 0
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
