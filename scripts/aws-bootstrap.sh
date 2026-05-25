#!/usr/bin/env bash
# EC2 first-boot bootstrap (Ubuntu 24.04). Run as root via cloud-init or manually once.
#
# Env (optional):
#   PLOY_REPO_URL   default https://github.com/uri-source/PloyAgent.git
#   PLOY_BRANCH     default main
#   AWS_SSM_PREFIX  default /ploy-agent/prod
#   AWS_REGION      default eu-west-1
#   SKIP_DEPLOY=1   only install Docker + clone (no compose up)
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
REPO_URL="${PLOY_REPO_URL:-https://github.com/uri-source/PloyAgent.git}"
BRANCH="${PLOY_BRANCH:-main}"
INSTALL_DIR="${PLOY_INSTALL_DIR:-/opt/PloyAgent}"
SSM_PREFIX="${AWS_SSM_PREFIX:-/ploy-agent/prod}"
REGION="${AWS_REGION:-eu-west-1}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (or via cloud-init)"
  exit 1
fi

echo "==> System packages..."
apt-get update -qq
apt-get install -y -qq git curl ca-certificates awscli

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker..."
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "${VERSION_CODENAME:-$VERSION}") stable" \
    >/etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

echo "==> Clone PloyAgent..."
if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" fetch origin
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" || true
else
  git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
chmod +x scripts/*.sh 2>/dev/null || true

if [[ -n "${SKIP_DEPLOY:-}" ]]; then
  echo "SKIP_DEPLOY set — clone only. Next: aws-ssm-pull-env.sh && aws-deploy.sh"
  exit 0
fi

echo "==> Pull secrets from SSM (${SSM_PREFIX})..."
export AWS_REGION="$REGION"
export AWS_DEFAULT_REGION="$REGION"
if ./scripts/aws-ssm-pull-env.sh --prefix "$SSM_PREFIX"; then
  ./scripts/aws-deploy.sh
else
  echo "SSM pull failed — copy .env manually then run ./scripts/aws-deploy.sh"
  exit 1
fi

echo "==> Bootstrap done. Run ./scripts/aws-verify.sh after ALB/Cognito are configured."
