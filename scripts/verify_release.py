"""Validate built release metadata without importing source code (Python 3.11+)."""
import ast
from email.parser import Parser
import os
from pathlib import Path
import sys
import tarfile
import tomllib
import zipfile

root = Path(__file__).resolve().parents[1]
project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
name, expected = project["name"], project["version"]
module = name.replace("-", "_")
source = ast.parse((root / "src" / module / "__init__.py").read_text())
runtime = next(ast.literal_eval(node.value) for node in source.body
               if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets))
if runtime != expected:
    raise SystemExit(f"Runtime version {runtime} differs from project version {expected}")
if os.environ.get("GITHUB_REF", "").startswith("refs/tags/"):
    if os.environ["GITHUB_REF"] != f"refs/tags/v{expected}":
        raise SystemExit("Release tag must equal v plus the package version")
dist = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "dist"
wheels, sdists = list(dist.glob("*.whl")), list(dist.glob("*.tar.gz"))
if len(wheels) != 1 or len(sdists) != 1:
    raise SystemExit("Expected exactly one wheel and one source distribution")
with zipfile.ZipFile(wheels[0]) as wheel:
    entries = [n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")]
    if len(entries) != 1 or module + "/py.typed" not in wheel.namelist():
        raise SystemExit("Wheel metadata or typing marker missing")
    wheel_metadata = wheel.read(entries[0]).decode()
with tarfile.open(sdists[0]) as sdist:
    entries = [n for n in sdist.getnames() if n.count("/") == 1 and n.endswith("/PKG-INFO")]
    if len(entries) != 1:
        raise SystemExit("Source distribution metadata missing")
    sdist_metadata = sdist.extractfile(entries[0]).read().decode()
for label, content in [("wheel", wheel_metadata), ("sdist", sdist_metadata)]:
    metadata = Parser().parsestr(content)
    if metadata["Name"] != name or metadata["Version"] != expected:
        raise SystemExit(f"{label} name/version differs from source")
print(f"Verified source, runtime, wheel and sdist metadata: {name} {expected}")
