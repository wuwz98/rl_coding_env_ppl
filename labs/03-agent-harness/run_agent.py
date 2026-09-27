#!/usr/bin/env python3
"""Lab 03 MVP: rollout one model, export C, and grade B+C+T."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from grader import grade_candidate


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
DEFAULT_TASK_DIR = (
    HERE.parent
    / "02-pr-task-compiler"
    / "tasks"
    / "more-itertools__more-itertools-issue-719-pr-720"
)
DEFAULT_ENDPOINT_FILE = PROJECT_ROOT / "LLM_ENDPOINT.md"


class HarnessError(RuntimeError):
    pass


@dataclass
class ModelConfig:
    api_key: str = field(repr=False)
    model: str
    base_url: str


def run_command(
    command: List[str],
    *,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            [str(part) for part in command],
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise HarnessError(
            "host command timed out after {} seconds: {}".format(
                timeout,
                " ".join(command),
            )
        ) from error
    except OSError as error:
        raise HarnessError("cannot execute {}: {}".format(command[0], error)) from error
    if check and result.returncode != 0:
        raise HarnessError(
            "command failed with status {}: {}\n{}".format(
                result.returncode,
                " ".join(command),
                result.stdout + result.stderr,
            )
        )
    return result


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def parse_assignment(text: str, name: str) -> Optional[str]:
    pattern = r"(?m)^\s*{}\s*=\s*['\"]?([^'\"\s`]+)".format(re.escape(name))
    match = re.search(pattern, text)
    return match.group(1) if match else None


def load_model_config(
    endpoint_file: Path,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> ModelConfig:
    """Load env/CLI values first, then the local untracked endpoint file."""
    key_value = api_key or os.environ.get("ARK_API_KEY")
    model_value = model or os.environ.get("ARK_MODEL")
    base_value = base_url or os.environ.get("ARK_BASE_URL")

    text = ""
    if not (key_value and model_value and base_value) and endpoint_file.is_file():
        text = endpoint_file.read_text(encoding="utf-8")
    key_value = key_value or parse_assignment(text, "API_KEY")
    model_value = model_value or parse_assignment(text, "model")
    base_value = base_value or parse_assignment(text, "base_url")

    missing = [
        name
        for name, value in (
            ("ARK_API_KEY", key_value),
            ("ARK_MODEL", model_value),
            ("ARK_BASE_URL", base_value),
        )
        if not value
    ]
    if missing:
        raise HarnessError(
            "missing model configuration: {}; set environment variables or pass "
            "--endpoint-file".format(", ".join(missing))
        )
    return ModelConfig(
        api_key=str(key_value),
        model=str(model_value),
        base_url=str(base_value).rstrip("/"),
    )


class ResponsesClient:
    def __init__(self, config: ModelConfig, timeout: int = 300):
        self.config = config
        self.timeout = timeout

    def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.config.base_url + "/responses",
            data=body,
            headers={
                "Authorization": "Bearer {}".format(self.config.api_key),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.timeout,
                ) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                response_body = error.read().decode("utf-8", errors="replace")
                if error.code not in {429, 500, 502, 503, 504}:
                    raise HarnessError(
                        "model API returned HTTP {}: {}".format(
                            error.code,
                            response_body[:2000],
                        )
                    ) from error
                last_error = HarnessError(
                    "model API returned retryable HTTP {}".format(error.code)
                )
            except (urllib.error.URLError, TimeoutError, ValueError) as error:
                last_error = error
            if attempt < 2:
                time.sleep(2**attempt)
        raise HarnessError("model API failed after 3 attempts: {}".format(last_error))


TOOLS = [
    {
        "type": "function",
        "name": "bash",
        "description": (
            "Run one Bash command in the task repository. Use this to inspect files, "
            "edit code, and run tests."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The Bash command to run.",
                }
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "finish",
        "description": (
            "Finish the task after making the best available code change and, when "
            "possible, running relevant tests."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "A concise summary of the change and tests run.",
                }
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def build_prompt(task: Dict[str, Any]) -> str:
    return """Fix the issue described below in the repository at {workspace}.

You have a Bash tool that runs inside a disposable container. Inspect the
repository, edit the implementation behavior, and run relevant existing tests.
This is a source-code bug-fix task; do not resolve it with documentation-only
changes. Do not modify tests. Put any temporary scripts outside the repository,
for example under /tmp, and do not leave scratch files in the final workspace.
The environment has no network access. Combine related shell operations when
practical because the number of turns is limited. When the implementation is
ready, call the finish tool.

Issue:
{problem}
""".format(
        workspace=task["workspace"],
        problem=task["problem_statement"],
    )


def function_calls(output_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [item for item in output_items if item.get("type") == "function_call"]


def response_text(output_items: List[Dict[str, Any]]) -> str:
    texts: List[str] = []
    for item in output_items:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                texts.append(str(content["text"]))
    return "\n".join(texts)


def truncate_middle(value: str, limit: int) -> str:
    if limit <= 0 or len(value) <= limit:
        return value
    marker = "\n... output truncated by Lab 03 harness ...\n"
    side = max(1, (limit - len(marker)) // 2)
    return value[:side] + marker + value[-side:]


def execute_bash(
    container: str,
    workspace: str,
    command: str,
    *,
    timeout: int,
    max_observation_chars: int,
) -> Dict[str, Any]:
    result = run_command(
        [
            "docker",
            "exec",
            "--workdir",
            workspace,
            container,
            "timeout",
            "-s",
            "KILL",
            "{}s".format(timeout),
            "bash",
            "-lc",
            command,
        ],
        timeout=timeout + 15,
        check=False,
    )
    return {
        "command": command,
        "exit_code": result.returncode,
        "stdout": truncate_middle(result.stdout, max_observation_chars),
        "stderr": truncate_middle(result.stderr, max_observation_chars),
    }


def workspace_has_changes(container: str, workspace: str) -> bool:
    result = run_command(
        [
            "docker",
            "exec",
            "--workdir",
            workspace,
            container,
            "git",
            "status",
            "--porcelain",
        ]
    )
    return bool(result.stdout.strip())


def create_container(image: str, workspace: str) -> tuple[str, str]:
    inspected = run_command(
        ["docker", "image", "inspect", image],
        check=False,
    )
    if inspected.returncode != 0:
        raise HarnessError(
            "task image is missing; run Lab 02 first: {}".format(image)
        )
    container = "rl-lab03-{}".format(uuid.uuid4().hex[:12])
    run_command(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            container,
            "--network",
            "none",
            "--cpus",
            "1",
            "--memory",
            "1g",
            image,
            "sleep",
            "infinity",
        ]
    )
    try:
        base_ref = run_command(
            [
                "docker",
                "exec",
                "--workdir",
                workspace,
                container,
                "git",
                "rev-parse",
                "HEAD",
            ]
        ).stdout.strip()
    except Exception:
        run_command(["docker", "rm", "--force", container], check=False, timeout=60)
        raise
    return container, base_ref


def export_candidate_patch(
    container: str,
    workspace: str,
    base_ref: str,
) -> Dict[str, Any]:
    # Intent-to-add makes untracked files visible to git diff without staging
    # their contents as a trusted final state.
    run_command(
        [
            "docker",
            "exec",
            "--workdir",
            workspace,
            container,
            "git",
            "add",
            "--intent-to-add",
            "--all",
        ],
        check=False,
    )
    patch = run_command(
        [
            "docker",
            "exec",
            "--workdir",
            workspace,
            container,
            "git",
            "diff",
            "--binary",
            "--no-ext-diff",
            base_ref,
        ]
    ).stdout
    changed = run_command(
        [
            "docker",
            "exec",
            "--workdir",
            workspace,
            container,
            "git",
            "diff",
            "--name-only",
            base_ref,
        ]
    ).stdout.splitlines()
    status = run_command(
        [
            "docker",
            "exec",
            "--workdir",
            workspace,
            container,
            "git",
            "status",
            "--short",
        ]
    ).stdout
    return {
        "patch": patch,
        "changed_files": [line for line in changed if line],
        "git_status": status,
    }


def usage_totals(events: List[Dict[str, Any]]) -> Dict[str, int]:
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for event in events:
        usage = event.get("usage") or {}
        for key in totals:
            totals[key] += int(usage.get(key) or 0)
    return totals


def run_episode(
    *,
    task_dir: Path,
    model_config: ModelConfig,
    output_root: Path,
    max_turns: int,
    max_output_tokens: int,
    command_timeout: int,
    grade_timeout: int,
    max_observation_chars: int,
    run_name: Optional[str] = None,
) -> Dict[str, Any]:
    task_dir = task_dir.resolve()
    task = json.loads((task_dir / "public" / "task.json").read_text(encoding="utf-8"))
    if run_name and not re.fullmatch(r"[A-Za-z0-9._-]+", run_name):
        raise HarnessError("run name may contain only letters, digits, dot, underscore, and dash")
    run_id = run_name or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    run_dir = output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    client = ResponsesClient(model_config)
    events: List[Dict[str, Any]] = []
    inputs: List[Dict[str, Any]] = [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": build_prompt(task)}],
        }
    ]
    container: Optional[str] = None
    base_ref: Optional[str] = None
    termination = "ERROR"
    final_summary = ""
    error_message: Optional[str] = None
    started_at = datetime.now(timezone.utc)
    candidate = {"patch": "", "changed_files": [], "git_status": ""}
    no_change_nudges = 0
    container_removed = True

    try:
        container, base_ref = create_container(task["image"], task["workspace"])
        container_removed = False
        for turn in range(1, max_turns + 1):
            response = client.create(
                {
                    "model": model_config.model,
                    "instructions": (
                        "You are a coding agent. Work through tools, make a focused "
                        "fix, test it, then call finish. Never claim a command ran "
                        "unless you observed its output."
                    ),
                    "input": inputs,
                    "tools": TOOLS,
                    "tool_choice": "auto",
                    "max_output_tokens": max_output_tokens,
                    "store": False,
                }
            )
            if response.get("error"):
                raise HarnessError(
                    "model API response contained an error: {}".format(
                        response["error"]
                    )
                )
            output_items = response.get("output") or []
            model_event = {
                "kind": "model",
                "turn": turn,
                "response_id": response.get("id"),
                "status": response.get("status"),
                "text": response_text(output_items),
                "output": output_items,
                "usage": response.get("usage") or {},
            }
            events.append(model_event)
            inputs.extend(output_items)
            calls = function_calls(output_items)
            if not calls:
                if response.get("status") == "incomplete":
                    notice = (
                        "Your previous response hit its output limit before making "
                        "a tool call. Continue from the current workspace. Use concise "
                        "Bash commands, implement the fix, run tests, then call finish."
                    )
                    inputs.append(
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": notice}],
                        }
                    )
                    events.append(
                        {
                            "kind": "harness",
                            "turn": turn,
                            "notice": "continued after incomplete model response",
                        }
                    )
                    continue
                if not workspace_has_changes(
                    container,
                    task["workspace"],
                ) and no_change_nudges < 2:
                    no_change_nudges += 1
                    notice = (
                        "No workspace changes exist yet. Continue working: edit the "
                        "implementation, run relevant tests, and call finish only "
                        "after producing the best candidate fix."
                    )
                    inputs.append(
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": notice}],
                        }
                    )
                    events.append(
                        {
                            "kind": "harness",
                            "turn": turn,
                            "notice": "continued after model stopped without changes",
                        }
                    )
                    continue
                termination = "MODEL_STOPPED"
                final_summary = model_event["text"]
                break

            finished = False
            for call in calls:
                call_id = call.get("call_id")
                name = call.get("name")
                arguments: Dict[str, Any] = {}
                try:
                    parsed_arguments: Any = json.loads(
                        call.get("arguments") or "{}"
                    )
                except ValueError as error:
                    outcome = {
                        "error": "invalid tool arguments: {}".format(error),
                    }
                else:
                    if not isinstance(parsed_arguments, dict):
                        outcome = {"error": "tool arguments must be a JSON object"}
                    else:
                        arguments = parsed_arguments
                    if isinstance(parsed_arguments, dict) and name == "bash":
                        command = arguments.get("command")
                        if not isinstance(command, str) or not command.strip():
                            outcome = {"error": "bash.command must be a non-empty string"}
                        else:
                            outcome = execute_bash(
                                container,
                                task["workspace"],
                                command,
                                timeout=command_timeout,
                                max_observation_chars=max_observation_chars,
                            )
                    elif isinstance(parsed_arguments, dict) and name == "finish":
                        final_summary = str(arguments.get("summary") or "")
                        outcome = {"accepted": True}
                        termination = "FINISHED"
                        finished = True
                    elif isinstance(parsed_arguments, dict):
                        outcome = {"error": "unknown tool: {}".format(name)}
                events.append(
                    {
                        "kind": "tool",
                        "turn": turn,
                        "call_id": call_id,
                        "name": name,
                        "arguments": arguments,
                        "outcome": outcome,
                    }
                )
                inputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(outcome, ensure_ascii=False),
                    }
                )
                if finished:
                    break
            if finished:
                break
            if turn == max_turns - 4:
                notice = (
                    "Only four model turns remain. Stop optional exploration. "
                    "Complete the behavioral fix, remove scratch files from the "
                    "repository, run the highest-value tests, and call finish."
                )
                inputs.append(
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": notice}],
                    }
                )
                events.append(
                    {
                        "kind": "harness",
                        "turn": turn,
                        "notice": "injected final-turn wrap-up reminder",
                    }
                )
        else:
            termination = "TURN_LIMIT"
    except Exception as error:
        error_message = "{}: {}".format(type(error).__name__, error)
    finally:
        if container and base_ref:
            try:
                candidate = export_candidate_patch(
                    container,
                    task["workspace"],
                    base_ref,
                )
            except Exception as error:
                if error_message is None:
                    error_message = "patch export failed: {}".format(error)
        if container:
            removed = run_command(
                ["docker", "rm", "--force", container],
                check=False,
                timeout=60,
            )
            container_removed = removed.returncode == 0

    candidate_path = run_dir / "candidate.patch"
    candidate_path.write_text(candidate["patch"], encoding="utf-8")
    grading = grade_candidate(
        task_dir=task_dir,
        candidate_patch=candidate_path,
        output_dir=run_dir / "grading",
        timeout=grade_timeout,
    )
    ended_at = datetime.now(timezone.utc)
    trajectory = {
        "schema_version": "lab03-trajectory-v2",
        "run_id": run_id,
        "task": {
            "task_id": task["task_id"],
            "task_dir": str(task_dir),
            "image": task["image"],
            "workspace": task["workspace"],
            "base_commit": task["base_commit"],
        },
        "model": {
            "model": model_config.model,
            "base_url": model_config.base_url,
        },
        "limits": {
            "max_turns": max_turns,
            "max_output_tokens": max_output_tokens,
            "command_timeout": command_timeout,
            "grade_timeout": grade_timeout,
            "max_observation_chars": max_observation_chars,
        },
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "elapsed_seconds": round((ended_at - started_at).total_seconds(), 3),
        "termination": termination,
        "final_summary": final_summary,
        "error": error_message,
        "events": events,
        "usage": usage_totals(events),
        "candidate": {
            "path": "candidate.patch",
            "bytes": len(candidate["patch"].encode("utf-8")),
            "sha256": hashlib.sha256(
                candidate["patch"].encode("utf-8")
            ).hexdigest(),
            "changed_files": candidate["changed_files"],
            "git_status": candidate["git_status"],
        },
        "grading": grading,
        "container_removed": container_removed,
    }
    write_json(run_dir / "trajectory.json", trajectory)
    summary = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "termination": termination,
        "error": error_message,
        "model": model_config.model,
        "turns": len([event for event in events if event["kind"] == "model"]),
        "tool_calls": len([event for event in events if event["kind"] == "tool"]),
        "usage": trajectory["usage"],
        "candidate_patch": str(candidate_path),
        "candidate_bytes": trajectory["candidate"]["bytes"],
        "changed_files": trajectory["candidate"]["changed_files"],
        "reward": grading["reward"],
        "verdict": grading["verdict"],
        "grading": {
            "path": "grading/grade.json",
            "reward": grading["reward"],
            "verdict": grading["verdict"],
            "reason_code": grading["reason_code"],
        },
        "container_removed": container_removed,
    }
    write_json(run_dir / "summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a coding-agent rollout, export C, and grade it."
    )
    parser.add_argument("task_dir", type=Path, nargs="?", default=DEFAULT_TASK_DIR)
    parser.add_argument("--output", type=Path, default=HERE / "runs")
    parser.add_argument("--endpoint-file", type=Path, default=DEFAULT_ENDPOINT_FILE)
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--command-timeout", type=int, default=120)
    parser.add_argument("--grade-timeout", type=int, default=180)
    parser.add_argument("--max-observation-chars", type=int, default=20000)
    parser.add_argument("--run-name")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_model_config(
            args.endpoint_file,
            model=args.model,
            base_url=args.base_url,
        )
        summary = run_episode(
            task_dir=args.task_dir,
            model_config=config,
            output_root=args.output,
            max_turns=args.max_turns,
            max_output_tokens=args.max_output_tokens,
            command_timeout=args.command_timeout,
            grade_timeout=args.grade_timeout,
            max_observation_chars=args.max_observation_chars,
            run_name=args.run_name,
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "ERROR",
                    "error": "{}: {}".format(type(error).__name__, error),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["error"] is None else 1


if __name__ == "__main__":
    sys.exit(main())
