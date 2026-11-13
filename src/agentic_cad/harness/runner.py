"""Run one model-written CadQuery script and write its `result` as a STEP file.

Started as its own process by loop.py, with the interpreter of the CadQuery environment (which has
another OpenCascade than the pipeline's): it must import nothing of agentic_cad.

    python runner.py <script.py> <out.step> [<base.step>]

Exit code 0 and the file on success; otherwise the last lines of the error on stderr.
"""

import sys
import traceback


def main() -> int:
    script, out = sys.argv[1], sys.argv[2]
    base_path = sys.argv[3] if len(sys.argv) > 3 else None
    import math

    import cadquery as cq

    # the workspace's helpers (outlines.py): a check runs the script in .cad/cand_NNN, a preview in the workspace
    import os
    folder = os.path.dirname(os.path.abspath(script))
    for _ in range(4):
        if os.path.isfile(os.path.join(folder, "outlines.py")):
            sys.path.insert(0, folder)
            break
        folder = os.path.dirname(folder)
    scope = {"cq": cq, "math": math, "__name__": "__main__", "base": None}
    if base_path:
        scope["base"] = cq.importers.importStep(base_path)
    scope["show_object"] = lambda *args, **kwargs: None
    try:
        exec(compile(open(script).read(), "script.py", "exec"), scope)
    except BaseException:
        lines = traceback.format_exc().strip().splitlines()
        print("\n".join(lines[-12:]), file=sys.stderr)
        return 1
    result = scope.get("result")
    if result is None:
        print("the script did not assign `result`", file=sys.stderr)
        return 1
    try:
        if isinstance(result, cq.Assembly):
            result = result.toCompound()
        if isinstance(result, cq.Workplane):
            solids = result.solids().vals() or [v for v in result.vals() if isinstance(v, cq.Shape)]
            if not solids:
                print("`result` holds no solid", file=sys.stderr)
                return 1
            shape = solids[0] if len(solids) == 1 else cq.Compound.makeCompound(solids)
        else:
            shape = result
        shape = shape.clean() if hasattr(shape, "clean") else shape
        cq.exporters.export(cq.Workplane(obj=shape), out, exportType="STEP")
    except BaseException:
        lines = traceback.format_exc().strip().splitlines()
        print("could not export `result`:\n" + "\n".join(lines[-6:]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
