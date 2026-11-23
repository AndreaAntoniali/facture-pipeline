"""Télécharge les factures (PDF) d'un bucket S3 Hyperfluid vers data/bronze/factures/.

Variables lues dans l'environnement (projet/.env) :
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY   -> identifiants du service account
    AWS_ENDPOINT_URL                            -> https://dams-5.s3-gateway.console.hyperfluid.cloud
    AWS_DEFAULT_REGION                          -> default
    S3_BUCKET                                   -> nom du bucket (à ajouter)
    S3_PREFIX                                   -> optionnel, ex. "factures/" (le "scope")

Lancement :
    uv run --env-file .env --with boto3 python download_factures.py
    uv run --env-file .env --with boto3 python download_factures.py --list   # liste seulement
"""
import os
import sys
from pathlib import Path

import boto3
from botocore.config import Config

BUCKET = os.environ["S3_BUCKET"]
PREFIX = os.environ.get("S3_PREFIX", "")
OUT_DIR = Path(os.environ.get("OUT_DIR", "data/bronze/factures"))
EXTENSIONS = (".pdf",)

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["AWS_ENDPOINT_URL"],
    region_name=os.environ.get("AWS_DEFAULT_REGION", "default"),
    # Les gateways S3 hors AWS demandent souvent le style "path" (endpoint/bucket/key)
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)


def iter_objects():
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=PREFIX):
        for obj in page.get("Contents", []):
            if obj["Key"].lower().endswith(EXTENSIONS):
                yield obj


def main():
    list_only = "--list" in sys.argv
    n_dl = n_skip = 0
    for obj in iter_objects():
        key, size = obj["Key"], obj["Size"]
        if list_only:
            print(f"{size:>10}  {key}")
            continue
        dest = OUT_DIR / key  # conserve l'arborescence du bucket
        if dest.exists() and dest.stat().st_size == size:
            n_skip += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(BUCKET, key, str(dest))
        n_dl += 1
        print(f"↓ {key}")
    if not list_only:
        print(f"{n_dl} téléchargé(s), {n_skip} déjà présent(s) -> {OUT_DIR}/")


if __name__ == "__main__":
    main()
