"""One DeepSeek Harness agent per test case, working on that case until its time is up.

    python -m agentic_cad.harness.loop --input_dir <dir> --output_dir <dir> --work_dir <dir> --deadline <unix time>

Every case runs in its own process. It waits until the deterministic pipeline has finished the case
(main.py leaves `<work_dir>/<case>/hull_done`), then gives an agent (`dsh --profile headless`, the
model of OPENAI_MODEL) a workspace with the brief, facts.json, the views, the pipeline's solid as
`base.step` and the `./check` tool (check.py). The agent writes CadQuery scripts and checks them as
often as it likes. While it works, the best checked candidate replaces the case's output file as
soon as its local score (measure.py) beats the pipeline's solid by MARGIN. Without a key, without
an answer or with worse candidates the output stays the pipeline's.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from agentic_cad.harness.check import cq_python

MARGIN = float(os.environ.get("HARNESS_MARGIN", 0.01))         # of local score, over the pipeline's solid
MIN_AGENT_SECONDS = float(os.environ.get("HARNESS_MIN_SECONDS", 120))   # no agent is started with less left
EFFORT = os.environ.get("HARNESS_EFFORT", "medium")
PERMISSION = os.environ.get("HARNESS_PERMISSION", "workspace-write")     # the Dockerfile sets danger-full-access
API = os.environ.get("HARNESS_API", "openai-responses")           # or openai-completions
CONTEXT_WINDOW = int(os.environ.get("HARNESS_CONTEXT_WINDOW", 400000))
MAX_TOKENS = int(os.environ.get("HARNESS_MAX_TOKENS", 100000))
BASH_TIMEOUT_MS = 240000
MAX_STARTS = int(os.environ.get("HARNESS_MAX_STARTS", 30))   # of one case's agent
# An agent whose event log has not grown for this long is waiting on a model request that will not
# come back (five of nine did, for 8 to 13 minutes, in the first full run): it is stopped and started again.
GOOD_ENOUGH = float(os.environ.get("HARNESS_GOOD_ENOUGH", 0.985))   # local score at which an agent that ends is not restarted
IDLE_SECONDS = float(os.environ.get("HARNESS_IDLE_SECONDS", 240))
OPENAI_HOSTS = ("api.openai.com",)

TASK = """You model one mechanical part in CadQuery, in this directory, on your own: nobody answers questions.
You have {minutes:.0f} minutes. `./check` prints the time left on every call.

What is here:
- description.md: the design brief. facts.json: numbers measured from the true part (bounding box, volume, surface area, number of through-holes, every cylindrical hole and boss with its diameter, axis and centre).
- views/: eight renders of the true part; views.png shows all eight in one picture. Look at it first.
- base.step: the solid a silhouette-carving pipeline found for this part. base_report.txt is its check report and base_compare.png shows it beside the views. Its outlines fit the views and its bounding box is exact, but it is full wherever no outline shows a recess: cavities, pockets, blind bores, hollow interiors, walls that should be thin.
- ./check <script.py>: runs your script, measures the solid against facts.json and the eight views, prints a report, writes compare.png (every view of the part beside your solid from the same camera) and records the candidate. ./cqpy <file.py> runs any Python file with CadQuery, for experiments.
- ./sections [file.step]: writes sections.png, five cross-sections across each axis of base.step (or of the STEP file you name, such as .cad/cand_003/candidate.stp), material dark, positions in millimetres. No view shows the inside of a solid; this does. Use it to see where base.step is full and to verify a cut landed where you meant.

Work in small steps and do not plan at length: a short script checked early teaches more than a long deliberation.

The work: write a CadQuery 2 script, check it, read the report and look at compare.png, improve it, check again; repeat until the time is nearly up. Every checked candidate is recorded and the best one is handed in automatically: there is nothing to submit, and a script that was never checked counts for nothing. Make the first check within three minutes and then check after every change.

The script:
- `import cadquery as cq`; assign the finished part to `result`, a cq.Workplane holding exactly one solid. In the script `base` is a cq.Workplane with the solid of base.step.
- Either cut and add to it (`result = base.cut(tool)`) or model the part from the brief. Model from the brief when the part can be described exactly with a few features (plates, cylinders, revolved profiles, patterns of holes); work on `base` when its shape is too complex to rebuild. You can try both and keep what scores higher.
- Frame: the bounding box goes from (0, 0, 0) to the X, Y, Z of facts.json; all coordinates in the brief and facts.json are in this frame, in millimetres. "front" is seen from -Y (image right = +X, up = +Z), "back" from +Y, "left" from -X, "right" from +X, "top" from +Z (image up = +Y), "bottom" from -Z; the isometric views from (+X, -Y, +Z) and (-X, +Y, -Z).
- A fillet or chamfer that can fail goes into try/except. A script has 90 seconds.

The score of the true competition, after the best rigid alignment with the true part: 30 % volume overlap, 30 % surface match, 20 % bounding-box dimensions, 20 % number of through-holes (the genus of the solid, counted from its topology; it must be exact). So:
- Keep the bounding box exact and the through-hole count equal to facts.json. A through-hole adds one to the count, a blind hole or a pocket adds none; do not break through a wall by accident and do not close a listed through-hole.
- Then bring volume and surface area to the true values by putting material and recesses where the part has them: where the views show an opening, a pocket, a cavity or a thin wall. Removing material in the wrong place to reach the volume does not help.
- The check's score is an estimate from these numbers and the outlines; it cannot see inside a cavity, so use the views and the brief to decide where material goes.
"""


def provider_patch() -> list[dict]:
    """The profile patch: which endpoint and model the agent uses, from the README's three variables."""
    model = os.environ["OPENAI_MODEL"]
    base = (os.environ.get("OPENAI_BASE_URL") or "").rstrip("/")
    # the model is declared by hand: the harness's own catalog is older than the newest models
    entry = {"id": model, "input": ["text", "image"], "contextWindow": CONTEXT_WINDOW, "maxTokens": MAX_TOKENS,
             "reasoningEfforts": {"off": None, "low": "low", "medium": "medium", "high": "high"}}
    if not base or any(host in base for host in OPENAI_HOSTS):
        provider = "openai"
        route = {"apiKeyEnv": "OPENAI_API_KEY", "api": API, "reasoning": EFFORT, "models": [entry]}
    else:   # any other OpenAI-compatible endpoint
        provider = "team"
        route = {"apiKeyEnv": "OPENAI_API_KEY", "api": "openai-completions", "baseURL": base, "reasoning": EFFORT,
                 "compat": {"supportsDeveloperRole": False, "maxTokensField": "max_tokens"}, "models": [entry]}
    return [{"id": "agent-default-model", "config": {"provider": provider, "model": model}},
            {"id": "llm-pi-ai", "config": {"providers": {provider: route}}},
            {"id": "bash-sandbox", "config": {"timeoutMs": BASH_TIMEOUT_MS}}]


def dsh_command() -> str | None:
    python = cq_python()
    command = Path(python).with_name("dsh") if python else None
    return str(command) if command and command.is_file() else None


def enabled() -> bool:
    return bool(os.environ.get("HARNESS", "1") != "0" and os.environ.get("OPENAI_API_KEY")
                and os.environ.get("OPENAI_MODEL") and dsh_command())


class Case:
    def __init__(self, case_dir: Path, output_path: Path, work: Path, deadline: float):
        self.case_dir, self.output_path, self.work, self.deadline = case_dir, output_path, work, deadline
        self.workspace = work / "ws"
        self.state = self.workspace / ".cad"
        self.base_score: float | None = None
        self.base_terms: dict = {}
        self.published: float | None = None

    def log(self, **event) -> None:
        event["t"] = round(time.time(), 1)
        try:
            self.work.mkdir(parents=True, exist_ok=True)
            with (self.work / "log.jsonl").open("a") as handle:
                handle.write(json.dumps(event, default=str) + "\n")
        except Exception:
            pass

    def prepare(self) -> bool:
        """The agent's workspace. False when the pipeline's solid cannot be read."""
        from agentic_cad import verify
        from agentic_cad.harness import measure as ms

        (self.state / "tmp").mkdir(parents=True, exist_ok=True)
        for name in ("description.md", "facts.json"):
            shutil.copyfile(self.case_dir / name, self.workspace / name)
        shutil.copytree(self.case_dir / "images", self.workspace / "views", dirs_exist_ok=True)
        base = self.workspace / "base.step"
        shutil.copyfile(self.output_path, base)
        shape, why = ms.load(base)
        if shape is None:
            self.log(event="base_unreadable", why=why)
            return False
        observed = verify.observe(self.case_dir)
        measurement = ms.measure(shape, self.case_dir, observed)
        self.base_score = measurement["score"]
        self.base_terms = measurement["terms"]
        (self.workspace / "base_report.txt").write_text(ms.feedback(measurement) + "\n")
        if observed:
            (self.workspace / "views.png").write_bytes(ms.views_sheet(observed))
            (self.workspace / "base_compare.png").write_bytes(ms.compare_sheet(measurement, observed))
        (self.state / "meta.json").write_text(json.dumps({
            "case_dir": str(self.case_dir), "deadline": self.deadline - 20, "base_score": self.base_score}))
        python = sys.executable
        for name, line in (("check", f'TMPDIR="$PWD/.cad/tmp" exec "{python}" -m agentic_cad.harness.check "$@"'),
                           ("sections", f'exec "{python}" -m agentic_cad.harness.sections "$@"'),
                           ("cqpy", f'exec "{cq_python()}" "$@"')):
            tool = self.workspace / name
            tool.write_text(f'#!/bin/sh\ncd "$(dirname "$0")" || exit 1\n{line}\n')
            tool.chmod(0o755)
        self.log(event="base", measurement=ms.summary(measurement))
        return True

    def publish(self) -> None:
        """Put the agent's best checked candidate in the output file when it beats the pipeline's solid."""
        from agentic_cad import export

        try:
            best = json.loads((self.state / "best.json").read_text())
        except Exception:
            return
        floor = self.published if self.published is not None else (self.base_score or 0.0) + MARGIN
        if best["score"] <= floor or not export.file_valid(Path(best["path"])):
            return
        # the two terms that are measured exactly must not get worse: a wrong hole count or size costs
        # for certain, what the candidate gains in volume and surface is an estimate
        terms = best.get("terms") or {}
        if any(terms.get(name, 0.0) < self.base_terms.get(name, 0.0) - 0.005 for name in ("holes", "dimensions")):
            return
        export.publish(Path(best["path"]), self.output_path)
        self.published = best["score"]
        self.log(event="published", check=best["index"], score=best["score"], base=self.base_score)

    def start_agent(self, again: int = 0) -> subprocess.Popen:
        """Start the agent; `again` > 0 when an earlier one ended before the time was up."""
        home = self.work / "dsh-home"
        profile = home / "profiles" / "headless"
        env = {**os.environ, "DSH_HOME": str(home), "DSH_TELEMETRY_MODE": "DISABLED", "HOME": str(self.work)}
        command = dsh_command()
        if not again:
            # the first call makes the profile, then the patch (JSON is YAML) says which model it talks to
            subprocess.run([command, "--profile", "headless", "--dump-default-config"], env=env, cwd=str(self.workspace),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
            profile.mkdir(parents=True, exist_ok=True)
            (profile / "cordis.patch.yml").write_text(json.dumps(provider_patch(), indent=1))
        task = TASK.format(minutes=max(self.deadline - 20 - time.time(), 60) / 60)
        if again:
            task += ("\nYou worked on this before and were interrupted. Your scripts are still here; best_part.py is the "
                     "best checked one so far (if any). Go on from there.\n")
        log = (self.work / f"agent_{again}.jsonl").open("w")
        errors = (self.work / f"agent_{again}.err").open("w")
        process = subprocess.Popen(
            [command, "--profile", "headless", "--json", "--permission-mode", PERMISSION, "--approval-policy", "never", "-"],
            stdin=subprocess.PIPE, stdout=log, stderr=errors, cwd=str(self.workspace), env=env, start_new_session=True)
        process.stdin.write(task.encode())
        process.stdin.close()
        return process

    def best_score(self) -> float:
        try:
            return float(json.loads((self.state / "best.json").read_text())["score"])
        except Exception:
            return 0.0

    def idle(self, index: int) -> float:
        """Seconds since the agent last wrote an event."""
        try:
            return time.time() - (self.work / f"agent_{index}.jsonl").stat().st_mtime
        except OSError:
            return 0.0

    @staticmethod
    def stop(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except Exception:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            except Exception:
                pass

    def run(self) -> dict:
        while not (self.work / "hull_done").exists():
            if self.deadline - time.time() < MIN_AGENT_SECONDS:
                return {"case": self.case_dir.name, "skipped": "the pipeline left no time"}
            time.sleep(1.0)
        if self.deadline - time.time() < MIN_AGENT_SECONDS or not self.output_path.is_file() or not self.prepare():
            return {"case": self.case_dir.name, "skipped": "no time or no solid to start from"}
        process = self.start_agent()
        started, starts = time.time(), 1
        self.log(event="agent_started", pid=process.pid)
        try:
            while time.time() < self.deadline - 10:
                time.sleep(2.0)
                self.publish()
                if process.poll() is None:
                    if self.idle(starts - 1) < IDLE_SECONDS:
                        continue
                    self.log(event="agent_hung", starts=starts)
                    self.stop(process)
                # the agent ended on its own (it thinks it is done, or an error): the time left is not wasted
                self.log(event="agent_ended", exit=process.returncode, starts=starts)
                if self.deadline - time.time() < MIN_AGENT_SECONDS or starts >= MAX_STARTS:
                    break
                if process.returncode == 0 and max(self.best_score(), self.base_score or 0.0) >= GOOD_ENOUGH:
                    break   # it stopped by itself on a part that leaves next to nothing to gain
                time.sleep(min(5.0 * starts, 30.0))
                process = self.start_agent(starts)
                starts += 1
        finally:
            self.stop(process)
        self.publish()
        checks = len(list(self.state.glob("cand_*")))
        result = {"case": self.case_dir.name, "agent_seconds": round(time.time() - started), "exit": process.poll(),
                  "checks": checks, "starts": starts, "base": self.base_score and round(self.base_score, 4),
                  "published": self.published and round(self.published, 4)}
        self.log(event="done", **result)
        return result


def run_case(job: tuple) -> dict:
    case_dir, output_path, work, deadline = job
    try:
        return Case(Path(case_dir), Path(output_path), Path(work), deadline).run()
    except Exception:
        error = traceback.format_exc()
        try:
            Path(work).mkdir(parents=True, exist_ok=True)
            (Path(work) / "error.txt").write_text(error)
        except Exception:
            pass
        return {"case": Path(case_dir).name, "error": error.strip().splitlines()[-1]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_dir", type=Path, required=True)
    parser.add_argument("--output_dir", type=Path, required=True)
    parser.add_argument("--work_dir", type=Path, required=True)
    parser.add_argument("--deadline", type=float, required=True, help="unix time at which every case must have stopped")
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args()
    if not enabled():
        print("harness: off (needs OPENAI_API_KEY, OPENAI_MODEL and the dsh command)", flush=True)
        return 0
    cases = sorted((p for p in args.input_dir.glob("test_case_*") if (p / "facts.json").is_file()
                    and (p / "description.md").is_file()), key=lambda p: int(p.name.removeprefix("test_case_")))
    if args.only:
        cases = [case for case in cases if case.name in args.only]
    if not cases:
        return 0
    jobs = [(str(case.resolve()), str((args.output_dir / f"{case.name}.stp").resolve()),
             str((args.work_dir / case.name).resolve()), args.deadline) for case in cases]
    workers = int(os.environ.get("HARNESS_WORKERS", len(jobs)))
    print(f"harness: {len(jobs)} cases with {os.environ['OPENAI_MODEL']} (effort {EFFORT}), {workers} at a time, "
          f"{args.deadline - time.time():.0f} s", flush=True)
    with ProcessPoolExecutor(max(1, workers), mp_context=multiprocessing.get_context("spawn")) as pool:
        for result in pool.map(run_case, jobs):
            print(f"harness: {json.dumps(result)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
