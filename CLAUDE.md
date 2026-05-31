# CLAUDE.md

## Project Structure

```
<project>/
├── datasets/                  # Dataset directory
│   └── <dataset_name>/        # Raw + preprocessed data for a specific dataset
├── docs/
│   ├── references/            # External papers, literature, reading materials
│   ├── paper/                 # Self-written paper (drafts, figures, LaTeX)
│   └── schemes/               # One markdown per scheme: ideas, history, failed trials
├── experiments/               # Exploratory PoC code, draft scripts, temp analyses
├── <model_name>/              # Main model package
│   ├── tests/                 # Unit tests & smoke tests
│   ├── __main__.py            # Entry point (python -m <model_name>)
│   ├── __init__.py
│   ├── model.py               # Model architecture
│   └── train.py               # Training & evaluation pipelines
└── outputs/                   # Runtime outputs (usually git-ignored)
    ├── <model_name>/          # Stable checkpoints, training logs, eval results
    └── experiments/           # Outputs from experimental scripts (plots, temp logs)
```