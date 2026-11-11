from pathlib import Path

from agentic_cad import trace


class MockAgent:
    def __init__(self):
        pass

    def generate_cad(self, prompt: str, views: list[Path], context: Path | None, output_path: Path) -> None:
        print("thinking hard.....")

        # Each `trace.step` shows up as one row in the dashboard (ui/): replace these with the real flow.
        with trace.step("read_brief", kind="io", input={"prompt": prompt}) as step:
            for view in views[-2:]:
                step.attach_file(view)
            step.metric(views=len(views), has_context=context is not None)

        with trace.step("generate", kind="llm", input={"prompt": "<what you send to the model>"}) as step:
            mock_step = "this is mock step content"
            step.log("mock agent: no model is called yet")
            step.set_output(mock_step)

        with trace.step("export_step", kind="tool", input={"output_path": str(output_path)}):
            with open(output_path, "w") as f:
                f.write(mock_step)

        print("successful generation")
