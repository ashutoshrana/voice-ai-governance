"""The documented first escalation example must work through the public API."""

from pathlib import Path


def test_escalation_quickstart(capsys):
    readme = Path(__file__).resolve().parents[1] / "README.md"
    section = readme.read_text().split("### Confidence-Gated Escalation", 1)[1]
    example = section.split("```python\n", 1)[1].split("```", 1)[0]
    namespace = {}
    exec(compile(example, str(readme), "exec"), namespace)
    assert namespace["result"].triggered
    assert namespace["result"].trigger.value == "low_confidence"
    assert "Escalating:" in capsys.readouterr().out
