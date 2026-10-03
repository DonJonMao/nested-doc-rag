#!/bin/sh
set -eu

QDRANT_SEED_SOURCE=${QDRANT_SEED_SOURCE:-/seed/qdrant}
QDRANT_URL=${QDRANT_URL:-http://qdrant:6333}
QDRANT_COLLECTION=${QDRANT_COLLECTION:-datacenter_chunks_v1}
OBJECT_MANIFEST=${OBJECT_MANIFEST:-/seed/manifest/object-manifest.tsv}
KNOWLEDGE_SOURCE_DIR=${KNOWLEDGE_SOURCE_DIR:-/seed/knowledge}
FORM_ANALYSIS_SOURCE=${FORM_ANALYSIS_SOURCE:-/seed/form_analysis/12_gongkan_form_analysis}
FORMAT_PROBE_SOURCE=${FORMAT_PROBE_SOURCE:-/seed/format_probe/03_format_probe}
WORKER_ARTIFACTS_DIR=${WORKER_ARTIFACTS_DIR:-/app/python-core/artifacts}
MINIO_BUCKET=${GONGKAN_MINIO_BUCKET:-gongkan-platform}
MINIO_ENDPOINT=${GONGKAN_MINIO_ENDPOINT:-minio:9000}

is_true() {
  case "${1:-}" in
    true|TRUE|1|yes|YES|y|Y) return 0 ;;
    *) return 1 ;;
  esac
}

seed_qdrant() {
  export QDRANT_SEED_SOURCE QDRANT_URL QDRANT_COLLECTION
  python /seed/bin/seed_qdrant_http.py
}

wait_for_minio() {
  until mc alias set local "http://$MINIO_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; do
    echo "Waiting for MinIO at $MINIO_ENDPOINT ..."
    sleep 2
  done
  mc mb -p "local/$MINIO_BUCKET" >/dev/null 2>&1 || true
  mc anonymous set none "local/$MINIO_BUCKET" >/dev/null 2>&1 || true
}

seed_minio_documents() {
  count=0
  while IFS="$(printf '\t')" read -r source_path object_key content_type; do
    case "${source_path:-}" in
      ""|\#*) continue ;;
    esac
    src="$KNOWLEDGE_SOURCE_DIR/$source_path"
    if [ ! -f "$src" ]; then
      echo "Missing seed document: $src" >&2
      exit 1
    fi
    if mc stat "local/$MINIO_BUCKET/$object_key" >/dev/null 2>&1; then
      echo "MinIO object exists, skipping: $object_key"
    else
      echo "Uploading seed document: $source_path"
      mc cp "$src" "local/$MINIO_BUCKET/$object_key" >/dev/null
    fi
    count=$((count + 1))
  done < "$OBJECT_MANIFEST"
  echo "Verified $count seed documents in MinIO bucket $MINIO_BUCKET"
}

seed_form_analysis() {
  if [ ! -f "$FORM_ANALYSIS_SOURCE/form_items.jsonl" ]; then
    echo "Form analysis seed not found, skipping: $FORM_ANALYSIS_SOURCE"
    return
  fi
  target="$WORKER_ARTIFACTS_DIR/12_gongkan_form_analysis"
  mkdir -p "$target"
  cp -a "$FORM_ANALYSIS_SOURCE"/. "$target"/
  chmod -R a+rwX "$target"
  echo "Seeded base cloud form analysis into $target"
}

seed_format_probe() {
  if [ ! -f "$FORMAT_PROBE_SOURCE/probed_manifest.jsonl" ]; then
    echo "Format probe seed not found, skipping: $FORMAT_PROBE_SOURCE"
    return
  fi
  target="$WORKER_ARTIFACTS_DIR/03_format_probe"
  mkdir -p "$target"
  cp -a "$FORMAT_PROBE_SOURCE"/. "$target"/
  chmod -R a+rwX "$target"
  echo "Seeded format probe manifest into $target"
}

seed_worker_knowledge_sources() {
  marker="$WORKER_ARTIFACTS_DIR/seed_knowledge/.seeded"
  if [ -f "$marker" ] && ! is_true "${GONGKAN_KNOWLEDGE_SEED_FORCE:-false}"; then
    echo "Worker knowledge sources already seeded, skipping"
    return
  fi
  target="$WORKER_ARTIFACTS_DIR/seed_knowledge"
  mkdir -p "$target"
  cp -a "$KNOWLEDGE_SOURCE_DIR"/. "$target"/
  date -u +%Y-%m-%dT%H:%M:%SZ > "$marker"
  chmod -R a+rwX "$target"
  echo "Seeded worker knowledge sources into $target"
}

seed_qdrant
wait_for_minio
seed_minio_documents
seed_form_analysis
seed_format_probe
seed_worker_knowledge_sources
