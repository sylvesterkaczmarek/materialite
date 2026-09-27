import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

meta = json.loads((Path(__file__).parent / "case.json").read_text())
work = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
evidence.mkdir(parents=True, exist_ok=True)
python = sys.executable
is_material = meta["repo"].endswith("materialite")
source = work / meta["source"]
fixed = source.read_bytes()
summary = {}


def run(label, command, expected=0, cwd=work, timeout=480):
    with (evidence / (label + ".log")).open("w") as log:
        proc = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                              timeout=timeout, env=os.environ.copy())
    output = (evidence / (label + ".log")).read_text(errors="replace")
    print(label, "exit", proc.returncode, output[-350:], flush=True)
    assert proc.returncode == expected, (label, proc.returncode, output[-8000:])
    return output


def results(label):
    doc = ET.parse(evidence / (label + ".xml"))
    suites = list(doc.getroot().iter("testsuite"))
    counts = [sum(int(s.attrib.get(key, 0)) for s in suites)
              for key in ("tests", "failures", "errors", "skipped")]
    failures = sorted({t.attrib.get("classname", "") + "::" + t.attrib["name"]
                       for t in doc.getroot().iter("testcase")
                       if t.find("failure") is not None or t.find("error") is not None})
    summary[label] = {"counts": counts, "failed_nodes": failures}
    print(label, "counts", counts, "failed nodes", failures, flush=True)
    return counts, failures


def test(label, files, expected=0, coverage=False, cwd=work):
    args = [python, "-m", "pytest", *files, "-q", "--tb=short",
            "--junitxml=" + str(evidence / (label + ".xml"))]
    if coverage:
        module = "materialite" if is_material else "heatsspy.HE_files.LMTD"
        args += ["--cov=" + module, "--cov-branch",
                 "--cov-report=json:" + str(evidence / "coverage.json")]
    run(label, args, expected=expected, cwd=cwd)
    return results(label)


assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=work, text=True).strip() == meta["head"]
print("TESTED SOURCE", meta["head"], flush=True)
run("dependencies", [python, "-m", "pip", "freeze"])
run("dependency-check", [python, "-m", "pip", "check"])
focused = [meta["test"]]
if not is_material:
    focused.append("heatsspy/HE_files/test/test_LMTD.py")
full = ["tests"] if is_material else ["heatsspy", "--continue-on-collection-errors"]
original = subprocess.check_output(["git", "show", meta["base"] + ":" + meta["source"]], cwd=work)
try:
    counts, failures = test("fixed-focused", focused, coverage=True)
    assert not failures and counts[1:] == [0, 0, 0]
    assert counts[0] >= (16 if is_material else 8)
    fixed_full_counts, fixed_full_failures = test("fixed-full", full, expected=1)
    source.write_bytes(original)
    original_counts, original_failures = test("original-focused", focused, expected=1)
    if is_material:
        assert original_counts[:3] == [16, 10, 0]
    else:
        assert len(original_failures) == 5
    # Compare the complete pre-existing suite in the same process environment.
    _, baseline_failures = test("original-existing-full", full + ["--ignore=" + meta["test"]], expected=1)
    assert fixed_full_failures == baseline_failures, (fixed_full_failures, baseline_failures)
finally:
    source.write_bytes(fixed)
assert source.read_bytes() == fixed
counts, failures = test("restored-focused", focused)
assert not failures
if is_material:
    selected = ["tests", "--ignore=tests/test_convolution_gpu.py",
                "--ignore=tests/test_convolution_microstructure.py"]
    counts, failures = test("cpu-suite", selected)
    assert counts == [261, 0, 0, 0]
run("new-test-lint", [python, "-m", "ruff", "check", "--select", "E9,F63,F7,F82", meta["source"], meta["test"]])
run("new-test-format", [python, "-m", "black", "--target-version", "py310", "--check", meta["test"]])
if is_material:
    run("new-test-imports", [python, "-m", "isort", "--profile", "black", "--check-only", meta["test"]])
run("source-restoration", ["git", "diff", "--exit-code"])
run("patch-check", ["git", "diff", "--check", meta["base"]])
run("package-build", [python, "-m", "build", "--outdir", str(evidence / "dist")])
wheel, = (evidence / "dist").glob("*.whl")
run("install-wheel", [python, "-m", "pip", "install", "--force-reinstall", "--no-deps", str(wheel)])
with tempfile.TemporaryDirectory() as directory:
    outside = Path(directory)
    copied = outside / Path(meta["test"]).name
    shutil.copyfile(work / meta["test"], copied)
    module = "materialite" if is_material else "heatsspy"
    code = f"import {module}; from pathlib import Path; p=Path({module}.__file__).resolve(); print(p); assert not p.is_relative_to(Path({str(work)!r}))"
    run("installed-import", [python, "-c", code], cwd=outside)
    counts, failures = test("installed-focused", [str(copied)], cwd=outside)
    assert not failures and counts[1:] == [0, 0, 0]
(evidence / "summary.json").write_text(json.dumps(summary, indent=2))
print("VALIDATION COMPLETE", meta["head"], flush=True)
