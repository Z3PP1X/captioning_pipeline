#!/usr/bin/env bash
set -euo pipefail

APPDATA_DIR="${PIPELINE_APPDATA_DIR:-/var/lib/captioning_pipeline}"
INPUT_DIR="${PIPELINE_INPUT_DIR:-/data/input}"
EXPORT_DIR="${PIPELINE_EXPORT_DIR:-/data/exports}"

mkdir -p \
    "${APPDATA_DIR}/db" \
    "${APPDATA_DIR}/frames" \
    "${APPDATA_DIR}/thumbnails" \
    "${APPDATA_DIR}/rag" \
    "${EXPORT_DIR}"

if [ ! -d "${INPUT_DIR}" ]; then
    mkdir -p "${INPUT_DIR}" || true
fi

echo "[entrypoint] Native Linux AppData Volume: ${APPDATA_DIR}"
echo "[entrypoint] WSL2 Host Media Input Mount: ${INPUT_DIR}"
echo "[entrypoint] WSL2 Host Dataset Export Mount: ${EXPORT_DIR}"

exec "$@"
