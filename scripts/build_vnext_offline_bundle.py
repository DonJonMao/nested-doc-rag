#!/usr/bin/env python3
"""Build a source-bound, private, fully offline deployment bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from nested_doc_rag.config import _parse_dotenv_value, load_app_config

ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES = {
    "POSTGRES_IMAGE": ("postgres:16.4", "postgres", "16.4"),
    "REDIS_IMAGE": ("redis:7.4", "redis", "7.4"),
    "MINIO_IMAGE": ("minio/minio:RELEASE.2024-10-13T13-34-11Z", "minio", "2024-10-13"),
    "QDRANT_IMAGE": ("qdrant/qdrant:v1.12.4", "qdrant", "1.12.4"),
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_models(path: Path | None) -> dict[str, str]:
    allowed = {
        "EMBEDDING_ENDPOINT", "EMBEDDING_MODEL", "RERANK_ENDPOINT", "RERANK_MODEL",
        "CHAT_ENDPOINT", "CHAT_MODEL", "CHAT_API_KEY_ENV", "DEEPSEEK_API_KEY", "QDRANT_API_KEY",
        "NDR_CHAT_ENDPOINT", "NDR_CHAT_MODEL", "NDR_CHAT_API_KEY_ENV",
        "NDR_EMBEDDING_ENDPOINT", "NDR_EMBEDDING_MODEL", "NDR_RERANK_ENDPOINT", "NDR_RERANK_MODEL",
    }
    parsed: dict[str, str] = {}
    if path:
        if not path.is_file():
            raise ValueError("The explicit private model env file does not exist")
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.removeprefix("export ").strip()
            if key in allowed:
                parsed[key] = _parse_dotenv_value(value.strip())
    return parsed


def env_value(value: str) -> str:
    if any(char in value for char in "\n\r\0"):
        raise ValueError("Multiline/NUL model configuration cannot be packaged")
    return "'" + value.replace("'", "\\'") + "'"


class Builder:
    def __init__(self, output: Path, platform: str):
        self.output = output
        self.platform = platform
        self.commands: list[dict[str, object]] = []

    def run(self, command: list[str], label: str, *, capture: bool = False) -> str:
        record: dict[str, object] = {
            "command": command, "started_at": datetime.now(UTC).isoformat(), "label": label,
        }
        if capture:
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            stdout = result.stdout
            (self.output / f"{label}.log").write_text(result.stderr)
        else:
            with (self.output / f"{label}.log").open("w") as handle:
                result = subprocess.run(command, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
            stdout = ""
        record.update(exit_code=result.returncode, finished_at=datetime.now(UTC).isoformat())
        self.commands.append(record)
        write_json(self.output / "build-commands.json", self.commands)
        if result.returncode:
            raise RuntimeError(f"{label} failed; see its credential-safe build log")
        return stdout

    def inspect(self, reference: str) -> dict[str, object]:
        raw = json.loads(self.run(
            ["docker", "image", "inspect", "--platform", self.platform, reference],
            "inspect-" + reference.split(":")[0].replace("/", "-"), capture=True,
        ))[0]
        if raw["Os"] + "/" + raw["Architecture"] != self.platform:
            raise ValueError("Image architecture does not match requested deployment platform")
        return {
            "reference": reference, "id": raw["Id"], "architecture": raw["Architecture"],
            "os": raw["Os"], "labels": raw["Config"].get("Labels", {}),
            "rootfs_sha256": hashlib.sha256(("\n".join(raw["RootFS"]["Layers"]) + "\n").encode()).hexdigest(),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=["amd64", "arm64"], required=True)
    parser.add_argument("--source-commit", default="HEAD")
    parser.add_argument("--models-env", type=Path)
    parser.add_argument("--output-root", type=Path, default=ROOT / "artifacts/release-vnext/bundles")
    args = parser.parse_args()
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
    if status:
        raise ValueError("Commit the release source before building; working tree must be clean")
    commit = subprocess.check_output(["git", "rev-parse", args.source_commit], cwd=ROOT, text=True).strip()
    name = f"nested-doc-rag-vnext-{commit[:12]}-linux-{args.architecture}"
    output = args.output_root.resolve() / name
    archive = output.with_suffix(".tar.gz")
    if output.exists() or archive.exists():
        raise ValueError("Refusing to reuse a release output identity")
    output.mkdir(parents=True, mode=0o700)
    evidence = output / "build-evidence"
    evidence.mkdir()
    platform = "linux/" + args.architecture
    builder = Builder(evidence, platform)
    export = Path(tempfile.mkdtemp(prefix="nested-doc-rag-" + commit[:12] + "-"))
    git_archive = evidence / "source.tar"
    with git_archive.open("wb") as handle:
        subprocess.run(["git", "archive", commit], cwd=ROOT, stdout=handle, check=True)
    with tarfile.open(git_archive) as handle:
        handle.extractall(export, filter="data")
    write_json(evidence / "source.json", {
        "source_commit": commit, "git_archive_sha256": digest(git_archive),
        "clean_source_export": True, "private_env_in_build_context": False,
        "export_path": str(export),
    })
    for path in (export / "go-server/deployments/offline").iterdir():
        if path.is_file():
            shutil.copy2(path, output / path.name)
    private = read_models(args.models_env)
    config = load_app_config(export / "config/docker.yaml", env=private)
    services = config.services
    models = {
        "NDR_EMBEDDING_ENDPOINT": services.embedding_endpoint,
        "NDR_EMBEDDING_MODEL": services.embedding_model,
        "NDR_RERANK_ENDPOINT": services.rerank_endpoint,
        "NDR_RERANK_MODEL": services.rerank_model,
        "NDR_CHAT_ENDPOINT": services.chat_endpoint,
        "NDR_CHAT_MODEL": services.chat_model,
        "CHAT_API_KEY_ENV": services.chat_api_key_env,
        services.chat_api_key_env: private.get(services.chat_api_key_env, ""),
        "QDRANT_API_KEY": private.get("QDRANT_API_KEY", ""),
    }
    models_file = output / "models.env"
    models_file.write_text("# Private runtime model configuration; not part of Git or image layers.\n" + "".join(
        f"{key}={env_value(str(value))}\n" for key, value in models.items()
    ))
    models_file.chmod(0o600)
    images: list[dict[str, object]] = []
    for variable, service, context, dockerfile in [
        ("API_IMAGE", "api", export / "go-server", export / "go-server/deployments/Dockerfile.api"),
        ("WORKER_IMAGE", "worker", export, export / "go-server/deployments/Dockerfile.worker"),
        ("WEB_IMAGE", "web", export, export / "go-server/deployments/Dockerfile.web"),
    ]:
        reference = f"nested-doc-rag-{service}:vnext-{commit[:12]}-{args.architecture}"
        builder.run([
            "docker", "buildx", "build", "--platform", platform, "--load", "--progress=plain",
            "--label", "org.opencontainers.image.revision=" + commit,
            "--label", "org.opencontainers.image.source=https://github.com/DonJonMao/nested-doc-rag",
            "--label", "org.opencontainers.image.description=clean-commit-offline-release",
            "-t", reference, "-f", str(dockerfile), str(context),
        ], "build-" + service)
        images.append({"variable": variable, **builder.inspect(reference)})
        print(json.dumps({"built": service, "architecture": args.architecture}), flush=True)
    for variable, (upstream, service, version) in DEPENDENCIES.items():
        raw = json.loads(builder.run(
            ["docker", "buildx", "imagetools", "inspect", upstream, "--raw"],
            "manifest-" + service, capture=True,
        ))
        if "manifests" in raw:
            selected = [item for item in raw["manifests"]
                        if item.get("platform", {}).get("os") == "linux"
                        and item.get("platform", {}).get("architecture") == args.architecture]
            if len(selected) != 1:
                raise ValueError("Could not uniquely resolve dependency architecture: " + service)
            repository = upstream.rsplit(":", 1)[0]
            pinned = repository + "@" + selected[0]["digest"]
        else:
            pinned = upstream
        builder.run(["docker", "pull", "--platform", platform, pinned], "pull-" + service)
        reference = f"nested-doc-rag-{service}:{version}-{args.architecture}"
        builder.run(["docker", "tag", pinned, reference], "tag-" + service)
        images.append({"variable": variable, "upstream": upstream, "resolved_source": pinned,
                       **builder.inspect(reference)})
    (output / "images.env").write_text(f"BUNDLE_ARCH={args.architecture}\n" + "".join(
        f"{item['variable']}={item['reference']}\n" for item in images
    ))
    images_dir = output / "images"
    images_dir.mkdir()
    image_archive = images_dir / "all-images.tar"
    builder.run(["docker", "image", "save", "--platform", platform, "-o", str(image_archive),
                 *[str(item["reference"]) for item in images]], "save-all-images")
    with tarfile.open(image_archive) as handle:
        legacy_manifest = handle.extractfile("manifest.json")
        if legacy_manifest is None:
            raise ValueError("Docker archive is missing its portable image manifest")
        archive_images = json.load(legacy_manifest)
        for image in images:
            candidates = [entry for entry in archive_images
                          if str(image["reference"]) in entry.get("RepoTags", [])
                          or "docker.io/library/" + str(image["reference"]) in entry.get("RepoTags", [])]
            if len(candidates) != 1:
                raise ValueError("Archive image reference is missing or ambiguous")
            configuration = handle.extractfile(candidates[0]["Config"])
            if configuration is None:
                raise ValueError("Archive image configuration is missing")
            image["config_digest"] = "sha256:" + hashlib.sha256(configuration.read()).hexdigest()
    write_json(evidence / "images.json", images)
    manifest = {
        "schema_version": "nested-doc-rag-offline-bundle-v1", "source_commit": commit,
        "architecture": args.architecture, "platform": platform,
        "created_at": datetime.now(UTC).isoformat(),
        "images": [{key: item[key] for key in ["variable", "reference", "id", "config_digest", "rootfs_sha256", "labels"]}
                   for item in images],
        "archives": [{"path": "images/all-images.tar", "sha256": digest(image_archive)}],
        "private_model_config_included": bool(private.get(services.chat_api_key_env)),
        "models_kind": "configured-real; availability/quality not established by packaging",
    }
    write_json(output / "bundle.json", manifest)
    # Models and user-editable .env are intentionally excluded from immutable asset checks.
    immutable = [path for path in sorted(output.rglob("*")) if path.is_file()
                 and path.name != "models.env" and "build-evidence" not in path.parts]
    (output / "SHA256SUMS").write_text("".join(
        f"{digest(path)}  {path.relative_to(output).as_posix()}\n" for path in immutable
    ))
    git_archive.unlink()
    with tarfile.open(archive, "w:gz", compresslevel=6) as handle:
        handle.add(output, arcname=name)
    archive.chmod(0o600)
    archive.with_suffix(archive.suffix + ".sha256").write_text(f"{digest(archive)}  {archive.name}\n")
    print(json.dumps({"bundle": str(archive), "sha256": digest(archive), "images": len(images),
                      "architecture": args.architecture, "source_commit": commit,
                      "private_model_config_included": manifest["private_model_config_included"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
