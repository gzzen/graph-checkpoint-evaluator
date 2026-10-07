"""Builder configuration: config.toml with optional config.local.toml overrides."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Config:
    questions: Path
    capsules: Path
    python: Path
    r_library: Path
    rscript: str
    work: Path

    def notebook_dir(self, capsule_uuid: str) -> Path:
        return self.capsules / f"CapsuleNotebook-{capsule_uuid}"

    def data_dir(self, capsule_uuid: str) -> Path:
        return self.capsules / f"CapsuleData-{capsule_uuid}"


def load_config() -> Config:
    raw: dict = {}
    for name in ("config.toml", "config.local.toml"):
        path = HERE / name
        if path.is_file():
            raw.update(tomllib.loads(path.read_text(encoding="utf-8")))

    def resolve(key: str) -> Path:
        p = Path(raw[key]).expanduser()
        return p if p.is_absolute() else (HERE / p).resolve()

    return Config(resolve("questions"), resolve("capsules"), resolve("python"), resolve("r_library"), raw.get("rscript", "Rscript"), resolve("work"))
