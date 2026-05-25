#!/usr/bin/env bash
# Build .env on EC2 from SSM Parameter Store (SecureString) under /ploy-agent/prod/
#
# Prereqs: IAM instance profile with ssm:GetParametersByPath on /ploy-agent/*
# Usage:   ./scripts/aws-ssm-pull-env.sh [--prefix /ploy-agent/prod]
set -euo pipefail

PREFIX="${AWS_SSM_PREFIX:-/ploy-agent/prod}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)
      PREFIX="${2:?missing value after --prefix}"
      shift 2
      ;;
    *)
      echo "Usage: $0 [--prefix /ploy-agent/prod]"
      exit 1
      ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/.env"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-eu-west-1}}"

if ! command -v aws >/dev/null 2>&1; then
  echo "aws CLI not found — install AWS CLI v2"
  exit 1
fi

echo "==> Fetching parameters from ${PREFIX} (region ${REGION})..."
TMP="$(mktemp)"
aws ssm get-parameters-by-path \
  --region "$REGION" \
  --path "$PREFIX" \
  --recursive \
  --with-decryption \
  --query 'Parameters[*].[Name,Value]' \
  --output text >"$TMP"

if [[ ! -s "$TMP" ]]; then
  echo "No parameters found under ${PREFIX}"
  echo "Create them first — see docs/aws-hosting.md (SSM secrets)"
  rm -f "$TMP"
  exit 1
fi

: >"$OUT"
while IFS=$'\t' read -r name value; do
  key="${name#${PREFIX}/}"
  key="${key#/}"
  if [[ -z "$key" ]]; then
    continue
  fi
  printf '%s=%s\n' "$key" "$value" >>"$OUT"
done <"$TMP"
rm -f "$TMP"

chmod 600 "$OUT"
echo "Wrote ${OUT} ($(wc -l <"$OUT" | tr -d ' ') keys)"
echo "Run: ./scripts/aws-deploy.sh"
