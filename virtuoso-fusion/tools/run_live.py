#!/usr/bin/env python3
"""Run installed-wheel acceptance in a disposable authenticated RAMIC session."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET


def main():
    args = arguments()
    root = args.root.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="live-", dir=root))
    runtime = run / "runtime"
    runtime.mkdir(mode=0o700)
    private = run / "private"
    private.mkdir(mode=0o700)
    shutil.copyfile(args.cds_lib, runtime / "cds.lib")
    from virtuoso_bridge.daemon_setup import install
    from virtuoso_bridge import VirtuosoClient

    setup = install(run / "daemon", python=sys.executable, scratch_root=str(private))
    init = runtime / "start.il"
    startup = ''
    if args.init_il is not None:
        extra_init = runtime / 'extra_init.il'
        shutil.copyfile(args.init_il, extra_init)
        startup = 'load(' + json.dumps(str(extra_init)) + ')\n'
    init.write_text(startup + 'load(' + json.dumps(str(setup)) + ')\n')
    token_path = private / "token"
    env = dict(os.environ, RB_TOKEN_PATH=str(token_path), RB_SCRATCH_ROOT=str(private),
               RB_PORT="0", RB_AUTO_PORT="1", RB_LISTEN_ALL="0", RB_LOG_ENABLED="1",
               RB_LOG_PATH=str(run / "ramic.log"), RB_ALLOW_UNAUTHENTICATED="0",
               VB_ALLOW_UNAUTHENTICATED="0")
    for key in ("PYTHONPATH", "PYTHONHOME", "RB_IDENTITY_PATH"):
        env.pop(key, None)
    result = {"status": "FAIL", "root": str(run), "python": sys.executable,
              "scope": "authenticated isolated session; acceptance limited to executed test assertions",
              "gui": args.gui}
    process = None
    daemon_pid = None
    xvfb = None
    try:
        if args.gui:
            xvfb, display = start_display(run)
            env['DISPLAY'] = display
            result.update(display=display, xvfb_pid=xvfb.pid)
        command = [args.virtuoso] + ([] if args.gui else ['-nograph']) + ["-nocdsinit", "-restore", str(init)]
        result["virtuoso_command"] = command
        with (run / "virtuoso.log").open("w") as output:
            process = subprocess.Popen(command, cwd=runtime, env=env, stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        record = wait_for_record(private, process, args.startup_timeout)
        daemon_pid = record["daemon_pid"]
        client = VirtuosoClient(port=record["port"], daemon_token=token_path.read_text().strip(),
                                timeout=10, log_to_ciw=False)
        signed_pid = client.handshake_virtuoso_pid(timeout=10)
        if signed_pid != record["pid"] or record["work_dir"] != str(runtime):
            raise RuntimeError("Signed daemon identity does not match this isolated session")
        probe = client.execute_skill("getWorkingDir()", timeout=10, retry_connect=False)
        if not probe.ok or str(runtime) not in probe.output:
            raise RuntimeError("Authenticated read-only working-directory probe failed")
        result.update(endpoint=record, authenticated=True, probe=probe.output)
        if args.gui:
            windows = client.execute_skill('length(hiGetWindowList())', timeout=10, retry_connect=False)
            if not windows.ok or int(windows.output.strip()) < 1:
                raise RuntimeError('Owned graphical Virtuoso has no observable window')
            result['gui_windows'] = int(windows.output.strip())
        env.update(VF_LIVE_PORT=str(record["port"]), VF_LIVE_TOKEN_FILE=str(token_path),
                   VF_LIVE_ROOT=str(run), VF_LIVE_PID=str(signed_pid))
        if args.probe_only:
            result.update(status="PASS", scope="probe_only: authenticated session readiness; no acceptance tests executed")
        else:
            command = [sys.executable, "-I", "-m", "pytest", "-c", "/dev/null",
                       str(args.tests.resolve()), "--import-mode=importlib", "-v",
                       "--junitxml=" + str(run / "live.xml")]
            result["test_command"] = command
            with (run / "pytest.log").open("w") as output:
                test = subprocess.run(command, cwd=runtime, env=env, stdout=output,
                                      stderr=subprocess.STDOUT, timeout=args.test_timeout)
            result.update(summarize_test_run(run / "live.xml", test.returncode))
    except Exception as error:
        result["error"] = repr(error)
    finally:
        stop_process(process)
        # Cadence IPC children can detach from their parent's process group.
        if daemon_pid and Path(f"/proc/{daemon_pid}/cmdline").exists():
            command_line = Path(f"/proc/{daemon_pid}/cmdline").read_bytes()
            if str(run / "daemon").encode() in command_line:
                os.kill(daemon_pid, signal.SIGTERM)
                deadline = time.monotonic() + 5
                while Path(f"/proc/{daemon_pid}").exists() and time.monotonic() < deadline:
                    time.sleep(0.1)
                if Path(f"/proc/{daemon_pid}/cmdline").exists():
                    if str(run / "daemon").encode() in Path(f"/proc/{daemon_pid}/cmdline").read_bytes():
                        os.kill(daemon_pid, signal.SIGKILL)
        result["process_stopped"] = process is None or process.poll() is not None
        daemon_cmdline = Path(f"/proc/{daemon_pid}/cmdline") if daemon_pid else None
        result["daemon_stopped"] = not daemon_cmdline or not daemon_cmdline.exists() or (
            str(run / "daemon").encode() not in daemon_cmdline.read_bytes())
        stop_process(xvfb)
        result['xvfb_stopped'] = xvfb is None or xvfb.poll() is not None
        if not result["process_stopped"] or not result["daemon_stopped"] or not result['xvfb_stopped']:
            result.update(status="FAIL", cleanup_error="Owned session process remains alive")
        result["artifact_sha256"] = {
            str(path.relative_to(run)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (runtime / "cds.lib", init, runtime / 'extra_init.il', run / "virtuoso.log", run / "pytest.log")
            if path.is_file()
        }
        (run / "result.json").write_text(json.dumps(result, indent=2))
        (root / "latest-live.txt").write_text(str(run))
    print(json.dumps(result, indent=2))
    if (run / "pytest.log").exists():
        print((run / "pytest.log").read_text())
    return 0 if result["status"] == "PASS" else 1


def summarize_test_run(report, returncode):
    """A successful pytest exit alone also permits every test to be skipped."""
    suites = list(ET.parse(report).getroot().iter("testsuite"))
    counts = {key: sum(int(suite.attrib[key]) for suite in suites)
              for key in ("tests", "skipped", "errors", "failures")}
    counts["passed"] = counts["tests"] - counts["skipped"] - counts["errors"] - counts["failures"]
    if counts["errors"] or counts["failures"] or returncode not in (0, 5):
        status = "FAIL"
    elif returncode == 0 and counts["passed"] > 0:
        status = "PASS"
    else:
        status = "INCOMPLETE"
    return {"exit_code": returncode, "status": status, "test_counts": counts,
            "scope": "authenticated isolated session; acceptance limited to passed test assertions; skipped tests are not validated"}


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--cds-lib", type=Path, required=True)
    parser.add_argument("--virtuoso", default="virtuoso")
    parser.add_argument("--tests", type=Path, default=Path(__file__).resolve().parents[1] / "tests/live")
    parser.add_argument("--startup-timeout", type=int, default=90)
    parser.add_argument("--test-timeout", type=int, default=240)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--gui", action="store_true", help="Start an owned Xvfb and graphical Virtuoso")
    parser.add_argument("--init-il", type=Path, help="Copy and load an attempt-specific startup script")
    return parser.parse_args()


def wait_for_record(private, process, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Virtuoso exited during startup: {process.returncode}")
        for path in (private / "runs").glob("*.json"):
            record = json.loads(path.read_text())
            if "daemon_pid" in record:
                return record
        time.sleep(0.25)
    raise TimeoutError(f"RAMIC startup exceeded {timeout}s")


def stop_process(process):
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)


def start_display(run):
    read_fd, write_fd = os.pipe()
    process = None
    try:
        with (run / 'xvfb.log').open('w') as output:
            process = subprocess.Popen(
                ['Xvfb', '-displayfd', str(write_fd), '-screen', '0', '1600x1200x24', '-nolisten', 'tcp'],
                pass_fds=(write_fd,), stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        os.close(write_fd)
        write_fd = None
        ready, _, _ = select.select([read_fd], [], [], 10)
        number = os.read(read_fd, 64).decode().strip() if ready else ''
        if not number.isdecimal():
            raise RuntimeError('Xvfb did not publish a display number within 10 seconds')
        return process, ':' + number
    except BaseException:
        stop_process(process)
        raise
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)


if __name__ == "__main__":
    raise SystemExit(main())
