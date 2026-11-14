# Compute: where each step runs

Everything runs on free tiers. No laptop compute and no paid services. Limits below were
checked on 8 Nov 2026; providers change them, so check the linked pages if something stops.

## Where each step runs

| Step | Runs on | Why |
| --- | --- | --- |
| Ingestion, dbt build, notebooks, unit tests | GitHub Codespaces (2-core, CPU) | Data work fits on one machine with DuckDB |
| Boosted models: LightGBM, XGBoost, CatBoost, Optuna tuning | Codespaces | Zone-hour aggregates train in minutes on CPU |
| Causal study and experiment design (modules 3, 4) | Codespaces | Regressions and resampling, CPU only |
| Chronos-2 zero-shot forecasts | Codespaces for a few zones; Kaggle GPU for all zones | No training; inference over 260 zones is faster on a GPU |
| N-HiTS, TFT, spatio-temporal graph model (module 2b) | Kaggle notebook, free T4 or P100 GPU | Training across all zones is slow on 2 CPU cores |
| CI: lint, tests, dbt parse | GitHub Actions | Free on public repositories |
| Monthly ingest, retrain of boosted models, champion-challenger gate | GitHub Actions on a schedule | Free on public repositories |
| Quote API and dashboard (module 6) | Hugging Face Spaces, free CPU tier | Free hosting with a public link |

## Free limits that matter

- **Codespaces:** 120 core-hours a month on a personal account, which is about 60 hours on
  the 2-core machine, plus 15 GB-month of storage. Storage counts for as long as a codespace
  exists, stopped or not, so keep one codespace and delete old ones. Set the idle timeout to
  30 minutes and stop the codespace when you finish a session. Past the quota GitHub charges
  only if you have set a spending limit above $0; leave it at $0.
- **Kaggle:** GPU sessions stop after 12 hours. The weekly GPU quota is shown in your Kaggle
  settings (users report about 30 hours). GPU and internet access need a phone-verified
  account. Save checkpoints during long runs.
- **GitHub Actions:** free on public repositories. A single job stops after 6 hours, far
  more than the monthly retrain needs.
- **Hugging Face Spaces:** the free CPU tier sleeps when unused and wakes on the next
  visit, so the first request after a quiet period is slow.

## Installing the tools

```bash
make setup      # week 1: package, dbt, notebooks, tests (the dev container runs this)
make setup-ml   # module 2 onwards: LightGBM, XGBoost, CatBoost, Optuna, MLflow, Evidently, causal libraries
make setup-dl   # module 2b in Codespaces: CPU-only PyTorch first, then neuralforecast, Chronos, ONNX
```

`make setup-dl` installs PyTorch from the CPU-only index on purpose. The default PyPI build
bundles CUDA libraries worth several GB, which a CPU-only codespace cannot use and which
count against the storage quota.

On Kaggle, PyTorch with CUDA is already installed. In the first notebook cell:

```bash
!git clone https://github.com/Andreas142/nyc-ride-demand
!pip install -q -e "nyc-ride-demand[ml,dl]"
```

## Getting data into Kaggle

The neural models train on the marts, not on raw trips, so the data is small. Module 5
publishes the marts to a public Hugging Face dataset repo (`nycdemand publish-marts`), and
every environment starts from the same pull:

```bash
!cd nyc-ride-demand && nycdemand pull-marts
```

Each Kaggle notebook writes its predictions in the shared format and uploads them to the
dataset repo under `predictions/<model>/`, so Codespaces scores them with the same code.

## Neural model lifecycle

- Retrain on Kaggle every few months, or when the gate shows the boosted champion drifting.
- Log each run in the same MLflow format, download `mlruns` and the model files, and attach
  the model files to a GitHub release so the repository stays small.
- Export the winning neural model to ONNX (`torch.onnx.export`) so the API serves it with
  ONNX Runtime on a CPU, without PyTorch.
- Boosted models retrain monthly in GitHub Actions and serve natively.

## Sources

- [GitHub Docs: Codespaces billing](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces)
- [GitHub Docs: Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [GitHub Docs: Actions limits (6-hour job limit on GitHub-hosted runners)](https://docs.github.com/en/enterprise-server@3.17/actions/reference/limits)
- [GPU Per Hour: free cloud GPUs (Kaggle session and weekly limits)](https://gpuperhour.com/blog/free-cloud-gpus-and-credits)
- [Neuromatch: using Kaggle (phone verification for GPUs)](https://precourse.neuromatch.io/tutorials/TechnicalHelp/Tutorial_kaggle.html)
- [Kaggle notebooks documentation](https://www.kaggle.com/docs/notebooks)
- [Hugging Face Spaces overview](https://huggingface.co/docs/hub/spaces-overview)
- [PyTorch install selector](https://pytorch.org/get-started/locally/)
