# Re-run environments

`envs/setup.sh <dir>` builds the environments used by `python -m builders.bixbench rerun`, outside the
repository:

- `<dir>/pyenv`: Python 3.11 venv with `requirements-rerun.txt`. Versions are pinned to what the
  capsule notebooks printed: Colab, Dec 2024 – Jan 2025, pandas 2.2.3, pydeseq2 0.4.12.
- `<dir>/Rlib`: R package library installed by `r-packages.R`.

Point `builders/bixbench/config.toml` (or `config.local.toml`) at them.

## Known gaps (2026-10-08, macOS arm64, R 4.3.1)

- **Bioconductor version.** BiocManager installs Bioconductor 3.18 to match R 4.3, while the notebooks
  ran 3.20 (R 4.4.2). DESeq2 1.42.1 reproduced every printed anchor of bix-7 and bix-13 exactly.
- **clusterProfiler and enrichplot do not install.**
  - `HDO.db` is missing.
  - The `igraph` and `systemfonts` source builds fail; they likely need Homebrew `glpk`, `freetype`
    and `fontconfig`.

  Capsules that use `enrichGO`/`enrichKEGG` (bix-1, 5, 26, 32) need them. Installing R 4.4 with
  binary Bioconductor 3.20 packages is the cleaner fix.
- **GO and KEGG annotations change over time.** `org.Hs.eg.db` and online KEGG differ from the
  notebooks' snapshots, so enrichment p-values drift. Record anchors and treat enrichment checkpoints
  as tolerant or non-gating.
