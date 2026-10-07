# Install the R packages needed to re-run BixBench capsule notebooks into a dedicated library.
# Usage: Rscript envs/r-packages.R <library-dir>
# The notebooks ran R 4.4.2 / Bioconductor 3.20; BiocManager selects the Bioconductor release that
# matches the local R (e.g. 3.18 for R 4.3), so record sessionInfo() with every re-run.
args <- commandArgs(trailingOnly = TRUE)
lib <- if (length(args)) args[[1]] else stop("usage: Rscript envs/r-packages.R <library-dir>")
dir.create(lib, recursive = TRUE, showWarnings = FALSE)
.libPaths(c(lib, .libPaths()))
options(repos = c(CRAN = "https://cloud.r-project.org"), Ncpus = 4, timeout = 600)

cran <- c("BiocManager", "tidyverse", "readxl", "jsonlite", "rstatix", "multcomp", "DescTools",
          "ggvenn", "pheatmap", "broom", "viridis", "RColorBrewer", "ggpubr")
bioc <- c("DESeq2", "apeglm", "clusterProfiler", "org.Hs.eg.db", "enrichplot")

missing <- function(pkgs) pkgs[!vapply(pkgs, requireNamespace, logical(1), quietly = TRUE)]
for (attempt in 1:3) {  # downloads occasionally fail with transient SSL errors
  todo_cran <- missing(cran)
  if (length(todo_cran)) install.packages(todo_cran, lib = lib)
  todo_bioc <- missing(bioc)
  if (length(todo_bioc)) BiocManager::install(todo_bioc, lib = lib, update = FALSE, ask = FALSE)
  if (!length(missing(c(cran, bioc)))) break
}
still <- missing(c(cran, bioc))
cat("Bioconductor", as.character(BiocManager::version()), "\n")
if (length(still)) stop("not installed: ", paste(still, collapse = ", "))
cat("all R packages installed in", lib, "\n")
