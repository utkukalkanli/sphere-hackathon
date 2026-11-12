"""`./check part.py`: the one tool the agent of a case has. It runs a CadQuery script, measures its
solid against facts.json and the views (measure.py), prints the report and keeps the candidate.

It runs inside the agent's workspace and writes only there: `compare.png` for the agent to look at,
and under `.cad/` every candidate with its score and `best.json`, which the orchestrator (loop.py)
reads to decide what goes into the output file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_TIMEOUT = float(os.environ.get("HARNESS_SCRIPT_TIMEOUT", 90))


def cq_python() -> str | None:
    """The interpreter of the CadQuery environment, which also holds the `dsh` command."""
    for candidate in (os.environ.get("HARNESS_CQ_PYTHON"), "/opt/cq/bin/python", str(REPO_ROOT / ".venv-cq/bin/python")):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def run_script(script: Path, out: Path, base: Path | None, timeout: float) -> str:
    """Run a script in the CadQuery environment. Returns "" when `out` was written, else the error."""
    out.unlink(missing_ok=True)
    command = [cq_python(), str(Path(__file__).with_name("runner.py")), str(script), str(out)]
    if base is not None:
        command.append(str(base))
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=str(script.parent))
    except subprocess.TimeoutExpired:
        return f"the script ran longer than {timeout:.0f} s and was stopped; make it simpler"
    if done.returncode != 0 or not out.is_file():
        return (done.stderr or done.stdout or "the script failed").strip()[-2000:]
    return ""


class _Quiet:
    """OpenCascade prints to the process's own stdout while it reads and writes STEP files; keep that
    out of the report the agent reads."""

    def __enter__(self):
        sys.stdout.flush()
        self.saved = os.dup(1)
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, 1)
        os.close(null)

    def __exit__(self, *_):
        sys.stdout.flush()
        try:   # what the C++ side still holds in its buffer goes to the null device too
            import ctypes

            ctypes.CDLL(None).fflush(None)
        except Exception:
            pass
        os.dup2(self.saved, 1)
        os.close(self.saved)


def _write_json(path: Path, value) -> None:
    part = path.with_suffix(".part")
    part.write_text(json.dumps(value, default=str))
    os.replace(part, path)


def main() -> int:
    workspace = Path.cwd()
    state = workspace / ".cad"
    meta = json.loads((state / "meta.json").read_text())
    left = meta["deadline"] - time.time()
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_file():
        print("usage: ./check <script.py>   (a CadQuery script that assigns `result`)")
        return 2
    from agentic_cad import export
    from agentic_cad.harness import measure as ms

    index = len(list(state.glob("cand_*")))
    folder = state / f"cand_{index:03d}"
    folder.mkdir(parents=True)
    script = folder / "part.py"
    shutil.copyfile(sys.argv[1], script)
    base = workspace / "base.step"
    tail = f"\n[check {index}] time left {max(left, 0) / 60:.1f} min."
    best = json.loads((state / "best.json").read_text()) if (state / "best.json").is_file() else None
    if best:
        tail += f" Best so far: check {best['index']} with {best['score']:.3f}."
    tail += f" The pipeline's solid (base) scores {meta['base_score']:.3f}." if meta.get("base_score") else ""

    def fail(message: str) -> int:
        _write_json(folder / "result.json", {"index": index, "error": message[:600]})
        print("NOT COUNTED: " + message + tail)
        return 1

    error = run_script(script, folder / "raw.step", base if base.is_file() else None,
                       min(SCRIPT_TIMEOUT, max(left, 10)))
    if error:
        return fail("the script failed:\n" + error)
    final = folder / "candidate.stp"
    with _Quiet():
        shape, why = ms.load(folder / "raw.step")
        written = shape is not None and export.checkpoint(ms.to_origin(shape), final, keep_faces=True)
        if written:   # measure what the file holds, not what was in memory
            shape, why = ms.load(final)
    if shape is None:
        return fail(why)
    if not written:
        return fail("the solid does not survive being written as a STEP file (invalid geometry)")
    case_dir = Path(meta["case_dir"])
    from agentic_cad import verify

    observed = verify.observe(case_dir)
    measurement = ms.measure(shape, case_dir, observed)
    try:
        (workspace / "compare.png").write_bytes(ms.compare_sheet(measurement, observed))
    except Exception:
        pass
    record = {"index": index, "score": measurement["score"], "path": str(final), "measurement": ms.summary(measurement)}
    _write_json(folder / "result.json", record)
    if best is None or measurement["score"] > best["score"]:
        _write_json(state / "best.json", {"index": index, "score": measurement["score"], "path": str(final),
                                          "terms": measurement["terms"]})
        shutil.copyfile(script, workspace / "best_part.py")
        verdict = "This is your best so far (kept as best_part.py)."
    else:
        verdict = f"Not better than check {best['index']} ({best['score']:.3f}); that one stays your best (best_part.py)."
    print(ms.feedback(measurement))
    print(verdict + " compare.png shows every view of the part beside your solid from the same camera "
          "(red: your material outside the part's outline, blue: outline you do not fill)." + tail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
