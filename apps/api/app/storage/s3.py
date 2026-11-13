"""S3-compatible object store, works against MinIO unchanged.

boto3 is imported lazily so the default install has no AWS dependency.
"""

from __future__ import annotations


class S3ObjectStore:
    backend = "s3"

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: str = "",
        secret_key: str = "",
        region: str = "us-east-1",
    ) -> None:
        import boto3

        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint or None,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            region_name=region,
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self._bucket)

    def put(self, key: str, data: bytes, content_type: str = "") -> str:
        extra = {"ContentType": content_type} if content_type else {}
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, **extra)
        return key

    def get(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=self._bucket, Key=key)
        return obj["Body"].read()

    def delete_prefix(self, prefix: str) -> int:
        paginator = self._client.get_paginator("list_objects_v2")
        removed = 0
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if not keys:
                continue
            self._client.delete_objects(Bucket=self._bucket, Delete={"Objects": keys})
            removed += len(keys)
        return removed

    def health(self) -> bool:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.head_bucket(Bucket=self._bucket)
            return True
        except (BotoCoreError, ClientError):
            return False
