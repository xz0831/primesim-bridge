from __future__ import annotations

import importlib
import importlib.metadata
import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import FrozenSet, List, Optional, Tuple

from pydantic import BaseModel


VERIFIED_COMPANION_VERSIONS = ("0.8.0",)
_CAPABILITIES = frozenset({"transport", "psf_ascii", "env"})


class CompanionInfo(BaseModel):
    available: bool
    version: str
    verified: bool
    capabilities: FrozenSet[str]


class CompanionUnavailable(RuntimeError):
    pass


def _callable_attribute(module_name: str, attribute: str) -> bool:
    try:
        module = importlib.import_module(module_name)
        return callable(getattr(module, attribute, None))
    except Exception:
        return False


@lru_cache(maxsize=1)
def companion_info() -> CompanionInfo:
    if os.environ.get("PSB_NO_COMPANION") == "1":
        return CompanionInfo(
            available=False,
            version="unknown",
            verified=False,
            capabilities=frozenset(),
        )

    try:
        importlib.import_module("virtuoso_bridge")
    except Exception:
        return CompanionInfo(
            available=False,
            version="unknown",
            verified=False,
            capabilities=frozenset(),
        )

    try:
        version = importlib.metadata.version("virtuoso-bridge")
    except Exception:
        version = "unknown"
    verified = version in VERIFIED_COMPANION_VERSIONS
    allow_capabilities = verified or os.environ.get("PSB_COMPANION_FORCE") == "1"
    capabilities = set()
    if allow_capabilities:
        checks = {
            "transport": ("virtuoso_bridge.transport.ssh", "SSHRunner"),
            "psf_ascii": (
                "virtuoso_bridge.spectre.parsers",
                "parse_psf_ascii_directory",
            ),
            "env": ("virtuoso_bridge.env", "resolve_env_path"),
        }
        for capability, (module_name, attribute) in checks.items():
            if _callable_attribute(module_name, attribute):
                capabilities.add(capability)
    return CompanionInfo(
        available=True,
        version=version,
        verified=verified,
        capabilities=frozenset(capabilities) & _CAPABILITIES,
    )


def reset_cache() -> None:
    companion_info.cache_clear()


def _require(capability: str) -> None:
    if capability not in companion_info().capabilities:
        raise CompanionUnavailable(f"companion capability unavailable: {capability}")


def _merge_directory(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir() and destination.is_dir():
            _merge_directory(item, destination)
            item.rmdir()
            continue
        if destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        shutil.move(str(item), str(destination))


class CompanionTransport:
    def __init__(self, host: str, user: Optional[str], timeout: int) -> None:
        _require("transport")
        module = importlib.import_module("virtuoso_bridge.transport.ssh")
        self._runner = module.SSHRunner(host, user=user, timeout=timeout)

    def check(self) -> bool:
        return bool(self._runner.test_connection())

    def run(self, command: str, timeout: int) -> Tuple[int, str, str]:
        result = self._runner.run_command(command, timeout=timeout)
        return int(result.returncode), str(result.stdout), str(result.stderr)

    def put_batch(self, files: List[Tuple[Path, str]], timeout: int) -> int:
        result = self._runner.upload_batch(files, timeout=timeout)
        return int(result.returncode)

    def get_dir(self, remote_path: str, local_path: Path, timeout: int) -> int:
        staging = local_path.parent / (local_path.name + ".dl")
        if staging.exists():
            if staging.is_dir():
                shutil.rmtree(staging)
            else:
                staging.unlink()
        try:
            result = self._runner.download(
                remote_path,
                staging,
                recursive=True,
                timeout=timeout,
            )
            returncode = int(result.returncode)
            if returncode == 0:
                _merge_directory(staging, local_path)
            return returncode
        finally:
            if staging.exists():
                if staging.is_dir():
                    shutil.rmtree(staging)
                else:
                    staging.unlink()


def parse_psf_dir(output_dir: Path) -> dict:
    _require("psf_ascii")
    module = importlib.import_module("virtuoso_bridge.spectre.parsers")
    data = module.parse_psf_ascii_directory(Path(output_dir))
    return {
        "parser": "virtuoso-bridge-psfascii",
        "dialect_verified": False,
        "empty": not bool(data),
        "data": data,
    }


def env_file() -> Optional[Path]:
    if "env" not in companion_info().capabilities:
        return None
    try:
        module = importlib.import_module("virtuoso_bridge.env")
        resolved = module.resolve_env_path()
        return Path(resolved) if resolved is not None else None
    except Exception:
        return None
