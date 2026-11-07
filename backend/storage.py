from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

from .config import get_settings


class Storage:
    def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream") -> None: ...
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def download(self, key: str, path: Path) -> None: ...
    def exists(self, key: str) -> bool: ...


class LocalStorage(Storage):
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("非法对象键")
        return p

    def put_file(self, key, path, content_type="application/octet-stream"):
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, p)

    def put_bytes(self, key, data, content_type="application/octet-stream"):
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get_bytes(self, key):
        return self._p(key).read_bytes()

    def download(self, key, path):
        shutil.copyfile(self._p(key), path)

    def exists(self, key):
        return self._p(key).exists()


class CosStorage(Storage):
    def __init__(self):
        from qcloud_cos import CosConfig, CosS3Client

        s = get_settings()
        cfg = CosConfig(Region=s.cos_region, SecretId=s.cos_secret_id, SecretKey=s.cos_secret_key,
                        Token=s.cos_session_token or None, Scheme="https")
        self.client = CosS3Client(cfg)
        self.bucket = s.cos_bucket
        self.prefix = "/".join(p for p in s.cos_prefix.split("/") if p) + "/"

    def _k(self, key: str) -> str:
        if ".." in key.split("/") or "\\" in key:
            raise ValueError("非法对象键")
        return self.prefix + key

    def put_file(self, key, path, content_type="application/octet-stream"):
        self.client.upload_file(Bucket=self.bucket, Key=self._k(key), LocalFilePath=str(path),
                                ContentType=content_type, ACL="private", PartSize=8, MAXThread=4)

    def put_bytes(self, key, data, content_type="application/octet-stream"):
        self.client.put_object(Bucket=self.bucket, Key=self._k(key), Body=data, ContentType=content_type,
                               ACL="private")

    def get_bytes(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=self._k(key))["Body"].get_raw_stream().read()

    def download(self, key, path):
        self.client.download_file(Bucket=self.bucket, Key=self._k(key), DestFilePath=str(path))

    def exists(self, key):
        return self.client.object_exists(Bucket=self.bucket, Key=self._k(key))


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        s = get_settings()
        _storage = CosStorage() if s.storage_provider == "cos" else LocalStorage(s.data_dir / "objects")
    return _storage


SIGNED_URL_TTL = timedelta(minutes=5)
