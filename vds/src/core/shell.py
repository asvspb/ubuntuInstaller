import subprocess
import shutil
from typing import Optional, Tuple
from dataclasses import dataclass


@dataclass
class ShellResult:
    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return self.stdout.strip()


def run(
    command: str,
    check: bool = False,
    timeout: Optional[int] = None,
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
) -> ShellResult:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=cwd,
        )
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, command, result.stdout, result.stderr
            )
        return ShellResult(
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
        )
    except subprocess.TimeoutExpired:
        return ShellResult(stdout="", stderr="timeout", returncode=-1)
    except Exception as e:
        return ShellResult(stdout="", stderr=str(e), returncode=-1)


def run_list(
    args: list[str],
    check: bool = False,
    timeout: Optional[int] = None,
) -> ShellResult:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, args, result.stdout, result.stderr
            )
        return ShellResult(
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
        )
    except subprocess.TimeoutExpired:
        return ShellResult(stdout="", stderr="timeout", returncode=-1)
    except Exception as e:
        return ShellResult(stdout="", stderr=str(e), returncode=-1)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def docker_exec(container: str, command: str, timeout: int = 10) -> ShellResult:
    return run_list(["docker", "exec", container] + command.split(), timeout=timeout)


def docker_inspect(container: str, format_str: str) -> str:
    result = run_list(
        ["docker", "inspect", "--format", format_str, container],
        timeout=5,
    )
    return result.output if result.ok else ""


def systemctl(action: str, service: str, check: bool = False) -> ShellResult:
    return run_list(["systemctl", action, service], check=check, timeout=10)
