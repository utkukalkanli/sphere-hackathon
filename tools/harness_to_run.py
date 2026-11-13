"""Make a dashboard run (runs/<id>) out of an agents' work directory, so the result of every case can
be looked at in ui/: the pipeline's solid, each candidate the agent got published, and the final file.

    uv run python tools/harness_to_run.py <work_dir> <output_dir> [--id <run id>]

<work_dir> is what scripts/main.py passes to the agents (HARNESS_DIR/<time>), <output_dir> the run's
output directory. A path the agents wrote inside a container is found again by its part below the
work directory.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The dashboard shows every case along the pipeline's steps (STOPS in ui/static/app.js). The agents' run
# was made in the container without a trace, so those steps are listed as switched off, not as "not run".
PIPELINE_STEPS = ["bounding_box", "read_sources", "cut_front_back", "cut_right_left", "cut_top_bottom", "cut_iso",
                  "isometric_support", "check_views", "cut_holes", "open_mouths", "evidence", "cut_rounds", "cut_curved",
                  "level_faces", "plane_faces", "open_cavities", "open_recesses", "open_core", "model_cuts",
                  "shading_depth", "cut_pockets", "hollow_shell", "split_faces", "export_step"]
NOT_TRACED = "not recorded: the pipeline ran in the container without a trace; its result is the step pipeline_solid"


def events_of(path: Path) -> list[dict]:
    try:
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except FileNotFoundError:
        return []


def volume_cm3(case_dir: Path, ratio) -> float | None:
    try:
        true = json.loads((case_dir / "facts.json").read_text())["volume_mm3"]
        return round(ratio * true / 1000, 2)
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("work_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--id", default=None)
    parser.add_argument("--input_dir", type=Path, default=ROOT / "artifacts/inputs")
    args = parser.parse_args()
    run_id = args.id or time.strftime("%Y%m%d-%H%M%S") + "-agents"
    run_dir = ROOT / "runs" / run_id
    (run_dir / "outputs").mkdir(parents=True, exist_ok=True)
    started, ended = None, None
    for work in sorted(args.work_dir.glob("test_case_*"), key=lambda p: int(p.name.removeprefix("test_case_"))):
        name = work.name
        log = events_of(work / "log.jsonl")
        case_dir = run_dir / name
        case_dir.mkdir(exist_ok=True)
        out = []

        def emit(ts, **event):
            out.append({"ts": ts, **event})

        first = log[0]["t"] if log else time.time()
        last = log[-1]["t"] if log else first
        started = first if started is None else min(started, first)
        ended = last if ended is None else max(ended, last)
        emit(first, t="case_start", name=name)
        published = [e for e in log if e.get("event") == "published"]
        emit(first, t="plan", steps=[*({"name": step, "off": NOT_TRACED} for step in PIPELINE_STEPS),
                                     {"name": "pipeline_solid"},
                                     *({"name": f"agent_check_{e['check']:03d}"} for e in published)])

        def stage(ts, step_id, label, source: Path, measurement: dict | None):
            if not source.is_file():
                return
            target = f"{step_id}.stp"
            shutil.copyfile(source, case_dir / target)
            emit(ts, t="step_start", id=step_id, parent=None, name=label)
            emit(ts, t="artifact", step=step_id, name=target, path=target, size=source.stat().st_size, role="stage")
            metrics = {}
            if measurement:
                metrics = {"volume_cm3": volume_cm3(args.input_dir / name, measurement.get("volume")),
                           "local_score": round(measurement.get("score", 0), 3), "holes": measurement.get("holes"),
                           "volume_ratio": round(measurement.get("volume") or 0, 3),
                           "area_ratio": round(measurement.get("area") or 0, 3)}
            emit(ts, t="step_end", id=step_id, status="ok", metrics=metrics)

        base = next((e for e in log if e.get("event") == "base"), None)
        if base:
            stage(base["t"], "pipeline_solid", "pipeline_solid", work / "ws" / "base.step",
                  base["measurement"])
        for event in log:
            if event.get("event") != "published":
                continue
            folder = work / "ws" / ".cad" / f"cand_{event['check']:03d}"
            try:
                measurement = json.loads((folder / "result.json").read_text())["measurement"]
            except Exception:
                measurement = None
            stage(event["t"], f"agent_check_{event['check']:03d}", f"agent_check_{event['check']:03d}",
                  folder / "candidate.stp", measurement)
        final = args.output_dir / f"{name}.stp"
        if final.is_file():
            shutil.copyfile(final, run_dir / "outputs" / final.name)
            shutil.copyfile(final, case_dir / final.name)
            emit(last, t="artifact", step=None, name=final.name, path=final.name, size=final.stat().st_size, role="output")
        done = next((e for e in log if e.get("event") == "done"), {})
        emit(last, t="log", step=None, level="info",
             msg=f"agent: {done.get('checks')} checks in {done.get('agent_seconds')} s, local score "
                 f"{done.get('base')} -> {done.get('published') or done.get('base')}")
        emit(last, t="case_end", status="ok")
        (case_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in out))
    (run_dir / "run.json").write_text(json.dumps({
        "id": run_id, "status": "ok", "started_at": started, "ended_at": ended, "pid": None, "launched_by": "agents",
        "argv": ["scripts/main.py"], "target": "run", "dataset": "practice", "engine": "agents",
        "meta": {"input_dir": str(args.input_dir), "output_dir": str(run_dir / "outputs"), "model": "gpt-6.1-terra"}}))
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
