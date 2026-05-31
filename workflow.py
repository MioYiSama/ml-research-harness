"""
# ML Research Harness Workflow

An autonomous research loop that turns a raw idea into a written-up paper.
It is built from a few single-purpose *agents* (PascalCase) orchestrated by four
*phases* (snake_case): plan -> experiment -> implement -> finalize.

## How information flows (there is NO shared in-process state object)

Agents do not thread a mutable state object around. They communicate through the
project's filesystem, which is the only "memory":

  - Researcher reads docs/references/ and the dead-ends recorded in docs/schemes/.
  - upsert() persists every scheme version to docs/schemes/<scheme_id>.md.
  - Experimenter reads/writes experiments/ and outputs/experiments/.
  - Coder builds the <network_name>/ package; Runner runs it (uv run -m <network_name>)
    and reads outputs/<network_name>/.
  - PaperWriter renders figures via the `visualize` subcommand (logic in
    <network_name>/viz.py) into docs/paper/, next to the LaTeX.

Because failures are persisted, a fresh round can re-plan from the same idea
without repeating a path that already failed.

## Engineering contract (AGENTS.md is authoritative; this is the load-bearing subset)

Every implementing agent (Coder, Experimenter, Runner, PaperWriter) obeys AGENTS.md:

  - Toolchain: do everything through `uv`. Implemented code must pass, in order,
    lint (`uv run ruff check .`), format (`uv run ruff format .`), type check
    (`uv run ty check`), and the test suite (`uv run pytest <network_name>/tests/`).
    The suite covers both unit tests and smoke tests; a fast_dev_run pass
    (L.Trainer(fast_dev_run=True): shapes, devices, logging) counts as a smoke test.
  - Framework: PyTorch Lightning only -- a LightningModule (architecture, *_step,
    optimizers, logging) and a LightningDataModule (download/transform/split/loaders);
    no hand-written epoch/batch loops. Log metrics with self.log / self.log_dict.
  - Entrypoint reproducibility/perf: L.seed_everything(42, workers=True),
    torch.set_float32_matmul_precision("medium"), accelerator/devices="auto",
    bf16-mixed where the GPU supports it, torch.compile only if benchmarked.
  - Output layout: all run artifacts under outputs/<network_name>/ --
    checkpoints/, tensorboard/ (TensorBoardLogger), csv/ (CSVLogger).
  - Package layout: one independent root-level package per network. ALL orchestration
    (CLI parsers, Trainer, export) lives in __main__.py with train/eval/visualize
    subcommands -- never create train.py/eval.py/cli.py/runner.py or utils.py/helpers.py.
    One major class per file (model.py, dataset.py); group helpers by domain
    (losses.py, metrics.py, image_ops.py); constants in config.py; relative imports.
    Run via `uv run -m <network_name> {train,eval,visualize}`.
  - Standards: cv2 for all image I/O & ops (cv2 loads BGR -> convert before
    torch/matplotlib); pathlib.Path everywhere; strict type hints + docstrings stating
    units/ranges; wrap heavy loops in tqdm; keep flows flat with minimal abstraction.

## Scheme file: docs/schemes/<scheme_id>.md

A scheme is a living markdown document -- the single source of truth for one research
direction. It accumulates everything needed to (a) re-plan around dead-ends and
(b) write the paper, so PaperWriter can pull concrete numbers and figures straight
from it instead of re-deriving them.

YAML frontmatter (metadata, for quick scanning + lineage):
  id:              matches the filename
  parent:          id this scheme was refined from (null if it is a root)
  round:           1-indexed outer round this scheme belongs to
  title:           short human-readable name of the approach
  network:         the root-level package implementing this scheme (<network_name>/)
  status:          drafting | finish_plan | experiment_feasible | experiment_unfeasible
                   | implement_feasible | implement_unfeasible
  feasible:        true | false | null
  failure_reason:  null | scheme_fatal | exhausted_iterations
  dataset:         dataset name(s) used
  metrics:         best / most-recent key results, e.g. {f1: ..., iou: ..., auroc: ...}
  created:         ISO timestamp of first write
  updated:         ISO timestamp of last write
  tags:            optional keywords

Body sections (also double as a ready-made paper skeleton):
   1. Summary           one-line statement of the idea + headline result
   2. Motivation        the problem and why it matters
   3. Key idea          the core hypothesis / claim
   4. Related work      references (links into docs/references/) and how this differs
   5. Method            architecture, objective, training -- enough detail to implement
   6. Data & eval       datasets, splits, metrics (f1/iou/auroc/...) + units, protocol
   7. PoC plan          the small-scale validation path
   8. Results log       per run: config, seed, env, metric values, and paths to the
                        checkpoints + diagnostic plots under outputs/<network_name>/
                        (these are the raw numbers PaperWriter later renders into paper
                        figures); covers PoC, full runs, ablations
   9. Review & lessons  reviewer comments, plus dead-ends tried and rejected (with reasons)
  10. Changelog         what each refinement changed vs. the parent, and why

## Project Structure

<project>/
├── datasets/                  # Dataset directory
│   └── <dataset_name>/        # Raw + preprocessed data for a specific dataset
├── docs/
│   ├── references/            # External papers, literature, reading materials
│   ├── paper/                 # LaTeX drafts + the figures PaperWriter renders for them
│   └── schemes/               # One markdown per scheme (see "Scheme file" above)
├── experiments/               # Exploratory PoC code, draft scripts, temp analyses
├── <network_name>/            # One independent, root-level package per network (AGENTS.md sec 4)
│   ├── __init__.py
│   ├── __main__.py            # CLI + ALL orchestration: `uv run -m <network_name> {train,eval,visualize}`
│   ├── config.py              # Module-level constants (paths, hyperparameters)
│   ├── model.py               # LightningModule: architecture, *_step, optim, logging
│   ├── dataset.py             # LightningDataModule: download, transforms, splits, loaders
│   ├── losses.py              # Domain helpers, one category per file (example)
│   ├── metrics.py             # Domain helpers: f1, iou, auroc, ... (example)
│   ├── viz.py                 # Figure logic behind `visualize`; PaperWriter renders into docs/paper/
│   └── tests/
│       └── test_*.py          # unit + smoke tests; `uv run pytest <network_name>/tests/`
└── outputs/                   # Run artifacts (usually git-ignored)
    ├── <network_name>/        # All artifacts for this network
    │   ├── checkpoints/       # Best/latest model checkpoints
    │   ├── tensorboard/       # TensorBoardLogger
    │   └── csv/               # CSVLogger (metric history PaperWriter plots from)
    └── experiments/           # Outputs from experimental scripts (plots, temp logs)
"""

from typing import Any, Optional

# Hard cap on full re-planning rounds before the harness gives up.
MAX_ROUNDS = 10


# ---------- agents ----------
def Researcher(idea: Any) -> Any:
    """
    Aggressively brainstorm a concrete scheme from a raw idea.

    Draws on the idea, related papers/code, web search, and -- importantly -- the
    failed schemes already in docs/schemes/, so it avoids known dead-ends. Proposals
    must be feasible within the engineering contract above (Lightning, uv, etc.).

    Returns a scheme (see "Scheme file" in the module docstring): a full proposal
    plus a path for small-scale validation (PoC).
    """
    ...


def Reviewer(scheme: Any, resource: Optional[Any] = None) -> Any:
    """
    A strict critic that surfaces any latent problem (e.g. the model taking
    shortcuts, data leakage, a setup that will obviously overfit).

    `resource` is the optional artifact under review -- a PoC report, a run score, or
    a paper draft; `scheme` is always the reference to judge it against.

    Returns a comments object:
      - falsy / empty -> no problems found (used by plan and finalize)
    When `resource` is a PoC report or a run score, the comments also expose a
    severity, used to pick a fix strategy:
      - .is_scheme_fatal() -> the method itself is broken; tuning cannot fix it
      - .is_minor()        -> implementation / setup / hyperparameter issue only
                              (e.g. failing lint, types, or tests)
      - otherwise          -> a method-level issue that refining the scheme can fix
    """
    ...


def Refiner(scheme: Any, comments: Any) -> Any:
    """
    Produce an improved scheme that addresses the reviewer's comments.
    The comments (including the path that failed) are folded into the scheme's
    "Review & lessons" / "Changelog" sections, so the lesson survives persistence.
    """
    ...


def Experimenter(scheme: Any, comments: Optional[Any] = None) -> tuple[Any, bool]:
    """
    Run the small-scale proof of concept: a short, cheap training run (small subset /
    few steps) to check whether the idea shows real signal and is worth scaling up --
    not merely that the code executes. Lightning-based; scratch code under
    experiments/, outputs under outputs/experiments/.

    It does NOT change the research idea (that is Refiner's job). Given `comments`
    (a diagnosis of a previous run), it may fix its own experimental setup -- a buggy
    harness, a wrong metric, a leaky split -- and re-run with the same scheme.

    Returns (report, feasible): a detailed report plus whether the idea is promising
    enough to scale up.
    """
    ...


def Coder(scheme: Any, comments: Optional[Any] = None) -> Any:
    """
    Implement (or, given comments, fix) the scheme's network as an independent,
    root-level package per AGENTS.md: a LightningModule + LightningDataModule, with
    ALL orchestration in __main__.py (train/eval/visualize subcommands) and no
    train.py/utils.py. Emit metrics via self.log / self.log_dict (f1, iou, auroc, ...)
    so Runner can summarise them into the scheme's Results log.

    "Done" means it passes lint (`uv run ruff check`), format (`uv run ruff format`),
    type check (`uv run ty check`), and `uv run pytest <network_name>/tests/` -- the
    unit tests and the smoke tests, the latter including a fast_dev_run pass.
    """
    ...


def Runner(scheme: Any) -> Any:
    """
    Full-scale train + eval of the scheme's network package.

    Reads the network name from the scheme and drives its CLI:
    `uv run -m <network_name> train`, then `uv run -m <network_name> eval`.
    The code has already cleared Coder's lint/type/test gate (smoke tests, incl. a
    fast_dev_run pass, included), so this just launches the full, time-consuming run.

    Artifacts land in outputs/<network_name>/{checkpoints,tensorboard,csv}. Returns a
    score whose .good() reflects the eval metrics.
    """
    ...


def PaperWriter(scheme: Any, comments: Optional[Any] = None) -> Any:
    """
    Write the paper in LaTeX (under docs/paper/), or revise the current draft given
    reviewer comments. Pulls motivation, method, concrete metric values and lessons
    straight from the scheme's body sections.

    It produces the paper's figures itself, from already-logged metrics (no
    re-training): the plotting logic lives in <network_name>/viz.py and is run through
    the package's `visualize` subcommand (uv run -m <network_name> visualize ...),
    reading outputs/<network_name>/csv; the rendered figures are written into
    docs/paper/, next to the LaTeX that references them. Use cv2 for image ops.

    Leave a single placeholder each for author, affiliation, and email.
    Returns the paper draft.
    """
    ...


# ---------- phases ----------
def plan(idea: Any, MAX_ITER_COUNT: int = 3) -> Any:
    """
    Turn a raw idea into a review-hardened scheme: propose, then critique-and-refine
    until the reviewer is satisfied or the iteration budget is exhausted.
    """
    scheme = Researcher(idea)

    for _ in range(MAX_ITER_COUNT):
        comments = Reviewer(scheme)
        if not comments:  # reviewer is satisfied
            break
        scheme = Refiner(scheme, comments)

    upsert(scheme)  # status -> finish_plan
    return scheme


def experiment(scheme: Any, MAX_ITER_COUNT: int = 5) -> tuple[Any, bool]:
    """
    Validate the scheme cheaply via PoC, choosing a fix strategy by severity
    (mirrors implement). Returns (scheme, feasible). On failure the (refined) scheme
    is still persisted, so the next planning round can learn from it.
    """
    report, feasible = Experimenter(scheme)  # first PoC

    # One initial PoC plus up to MAX_ITER_COUNT fix-and-retry attempts. Every run's
    # feasibility is checked, and we never apply a fix after the final PoC.
    for attempt in range(MAX_ITER_COUNT + 1):
        if feasible:
            upsert(scheme)  # log PoC results; status -> experiment_feasible
            return scheme, True

        if attempt == MAX_ITER_COUNT:
            break  # out of fix attempts

        comments = Reviewer(scheme, report)
        if comments.is_scheme_fatal():
            # The idea does not hold up even at PoC scale -> hand back to re-plan.
            upsert(
                scheme
            )  # record the fatal finding; status -> experiment_unfeasible (scheme_fatal)
            return scheme, False
        elif comments.is_minor():
            # PoC setup/harness bug, the idea is fine: re-run with the setup fixed.
            report, feasible = Experimenter(scheme, (report, comments))
        else:
            # Method-level but fixable: refine the scheme, then re-run the PoC.
            scheme = Refiner(scheme, comments)
            upsert(scheme)  # record the failed PoC + lesson + refined scheme
            report, feasible = Experimenter(scheme)

    upsert(scheme)  # status -> experiment_unfeasible (exhausted_iterations)
    return scheme, False


def implement(scheme: Any, MAX_ITER_COUNT: int = 5) -> tuple[Any, bool]:
    """
    Scale the validated scheme up to a full implementation + full training run,
    iterating on failures with a fix strategy chosen by the reviewer's severity.

    Returns (scheme, feasible).
    """
    Coder(scheme)  # first full implementation of this scheme

    # One initial run plus up to MAX_ITER_COUNT fix-and-retry attempts. Every run's
    # score is checked, and we never apply a fix after the final evaluation.
    for attempt in range(MAX_ITER_COUNT + 1):
        score = Runner(scheme)  # full-scale train + eval
        if score.good():
            upsert(
                scheme
            )  # log metrics + checkpoint/plot paths; status -> implement_feasible
            return scheme, True

        if attempt == MAX_ITER_COUNT:
            break  # out of fix attempts

        comments = Reviewer(scheme, score)
        if comments.is_scheme_fatal():
            # Method-level dead-end: tuning/refining cannot save it -> hand back to re-plan.
            upsert(
                scheme
            )  # record the fatal finding; status -> implement_unfeasible (scheme_fatal)
            return scheme, False
        elif comments.is_minor():
            # Pure implementation / hyperparameter issue (incl. a failed lint/type/test
            # check): feed the diagnosis back to the coder; the scheme is unchanged.
            Coder(scheme, (score, comments))
        else:
            # Method-level but fixable: refine the scheme first, then re-implement.
            scheme = Refiner(scheme, comments)
            upsert(scheme)  # record the failed run + lesson + refined scheme
            Coder(scheme)

    upsert(scheme)  # status -> implement_unfeasible (exhausted_iterations)
    return scheme, False


def finalize(scheme: Any, MAX_REVIEW_COUNT: int = 2) -> Any:
    """
    Write up the successful scheme, then polish the draft through up to
    MAX_REVIEW_COUNT review-and-revise rounds. Returns the final paper.
    """
    paper = PaperWriter(scheme)  # first draft (+ figures rendered into docs/paper/)

    for _ in range(MAX_REVIEW_COUNT):
        comments = Reviewer(scheme, paper)
        if not comments:  # reviewer is happy with the writeup
            break
        paper = PaperWriter(scheme, comments)  # revise the draft

    return paper


# ---------- utility ----------
def upsert(scheme: Any) -> None:
    """
    Persist the scheme to docs/schemes/<scheme_id>.md, keyed by its id: insert on the
    first write, update in place on later refinements within the same round. Writes
    both the frontmatter (status, feasibility, metrics, lineage) and the body
    (results log, review history, changelog) -- the format defined in the module
    docstring -- which is what lets later rounds avoid dead-ends and lets PaperWriter
    pull concrete numbers later.
    """
    ...


# ---------- workflow ----------
if __name__ == "__main__":
    idea = input()

    scheme, feasible = None, False
    for _ in range(MAX_ROUNDS):
        # Each round re-plans from the original idea. Researcher consults the
        # dead-ends persisted in docs/schemes/, so rounds explore new directions.
        scheme = plan(idea)

        scheme, feasible = experiment(scheme)
        if not feasible:
            continue  # PoC failed -> start a fresh round

        scheme, feasible = implement(scheme)
        if feasible:
            break  # full-scale run succeeded -> stop searching

        # implement() failed (e.g. the method did not scale up) -> start a fresh round

    if feasible:
        finalize(scheme)  # write up + review the successful scheme
    else:
        # Exhausted MAX_ROUNDS with no feasible scheme; nothing trustworthy to write up.
        # The full search history remains in docs/schemes/ for a human to review.
        pass
