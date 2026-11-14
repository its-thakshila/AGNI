"""Where the dataset lives: a local folder (development) or a Cloudflare R2 prefix (production).

R2 is reached through its S3-compatible API, using credentials from the environment:
R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY and R2_BUCKET.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class LocalStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def __repr__(self):
        return f"LocalStore({self.root})"

    def read(self, key: str) -> bytes | None:
        path = self.root / key
        return path.read_bytes() if path.is_file() else None

    def write(self, key: str, data: bytes) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def delete(self, key: str) -> None:
        (self.root / key).unlink(missing_ok=True)

    def list(self, prefix: str) -> list[str]:
        base = self.root / prefix
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)).replace("\\", "/") for p in base.rglob("*") if p.is_file())


class R2Store:
    def __init__(self, prefix: str):
        import boto3  # only needed in production

        account = os.environ["R2_ACCOUNT_ID"]
        self.bucket = os.environ["R2_BUCKET"]
        self.prefix = prefix.strip("/") + "/"
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
        )

    def __repr__(self):
        return f"R2Store({self.bucket}/{self.prefix})"

    def read(self, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self.prefix + key)["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None

    def write(self, key: str, data: bytes) -> None:
        content_type = "application/json" if key.endswith(".json") else "application/octet-stream"
        self.client.put_object(Bucket=self.bucket, Key=self.prefix + key, Body=data, ContentType=content_type)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self.prefix + key)

    def list(self, prefix: str) -> list[str]:
        keys, token = [], None
        while True:
            args = {"Bucket": self.bucket, "Prefix": self.prefix + prefix}
            if token:
                args["ContinuationToken"] = token
            page = self.client.list_objects_v2(**args)
            keys += [item["Key"][len(self.prefix):] for item in page.get("Contents", [])]
            if not page.get("IsTruncated"):
                return sorted(keys)
            token = page["NextContinuationToken"]


def open_store(target: str):
    """'local:<folder>' or 'r2:<prefix>' (for example r2:mock or r2:real)."""
    kind, _, where = target.partition(":")
    if kind == "local":
        return LocalStore(Path(where))
    if kind == "r2":
        return R2Store(where)
    raise ValueError(f"Unknown store {target!r}; use local:<folder> or r2:<prefix>.")


def read_json(store, key: str, default):
    data = store.read(key)
    return json.loads(data) if data else default


def write_json(store, key: str, value) -> None:
    store.write(key, (json.dumps(value, indent=1, sort_keys=True, default=str) + "\n").encode())
