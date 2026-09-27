#!/usr/bin/env python3
"""Lab 04: discover GitHub issue/PR pairs that can enter Lab 02."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time as datetime_time, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
DEFAULT_ENDPOINT_FILE = PROJECT_ROOT / "LLM_ENDPOINT.md"
TEST_DIR_NAMES = {"test", "tests", "testing", "spec", "specs"}
SCHEMA_VERSION = 1
SEMANTIC_PROMPT_VERSION = 2


class DiscoveryError(RuntimeError):
    pass


class GitHubError(DiscoveryError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class ModelConfig:
    api_key: str = field(repr=False)
    model: str
    base_url: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def parse_repo(value: str) -> str:
    value = value.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    match = re.fullmatch(
        r"(?:https?://github\.com/|git@github\.com:)?"
        r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
        value,
    )
    if not match:
        raise DiscoveryError("expected a GitHub owner/repo or repository URL")
    return "{}/{}".format(*match.groups())


def is_test_path(path: str) -> bool:
    """Keep this P0 policy identical to Lab 02."""
    pure = PurePosixPath(path)
    directories = {part.lower() for part in pure.parts[:-1]}
    filename = pure.name.lower()
    return (
        bool(directories & TEST_DIR_NAMES)
        or filename.startswith("test_")
        or filename.endswith(("_test.py", "_tests.py"))
    )


CLOSING_REFERENCE_RE = re.compile(
    r"""(?ix)
    \b(?P<keyword>close[sd]?|fix(?:e[sd])?|resolve[sd]?)
    \s*:?\s+
    (?:
        https?://github\.com/
        (?P<url_owner>[A-Za-z0-9_.-]+)/
        (?P<url_repo>[A-Za-z0-9_.-]+)/
        issues/(?P<url_number>\d+)
      |
        (?:(?P<short_owner>[A-Za-z0-9_.-]+)/
           (?P<short_repo>[A-Za-z0-9_.-]+))?
        \#(?P<short_number>\d+)
    )
    """
)


def extract_closing_issue_references(
    body: str,
    current_repo: str,
) -> List[Dict[str, Any]]:
    """Extract GitHub closing-keyword references without treating mentions as links."""
    references: List[Dict[str, Any]] = []
    seen = set()
    for match in CLOSING_REFERENCE_RE.finditer(body or ""):
        if match.group("url_number"):
            repo = "{}/{}".format(
                match.group("url_owner"),
                match.group("url_repo"),
            )
            number = int(match.group("url_number"))
        else:
            if match.group("short_owner"):
                repo = "{}/{}".format(
                    match.group("short_owner"),
                    match.group("short_repo"),
                )
            else:
                repo = current_repo
            number = int(match.group("short_number"))
        key = (repo.lower(), number)
        if key in seen:
            continue
        seen.add(key)
        references.append(
            {
                "repo": repo,
                "number": number,
                "keyword": match.group("keyword").lower(),
                "reference": match.group(0),
                "is_local": repo.lower() == current_repo.lower(),
            }
        )
    return references


def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_date_boundary(value: Optional[str], *, end: bool) -> Optional[datetime]:
    if not value:
        return None
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as error:
        raise DiscoveryError("expected date in YYYY-MM-DD format: {}".format(value)) from error
    clock = datetime_time.max if end else datetime_time.min
    return datetime.combine(day, clock, tzinfo=timezone.utc)


def in_merged_range(
    merged_at: Optional[str],
    since: Optional[datetime],
    until: Optional[datetime],
) -> bool:
    merged = parse_timestamp(merged_at)
    if merged is None:
        return False
    return not ((since and merged < since) or (until and merged > until))


class GitHubClient:
    """Small REST client with durable response caching and rate-limit evidence."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        token: Optional[str] = None,
        refresh: bool = False,
        max_requests: int = 0,
        base_url: str = "https://api.github.com",
    ):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.refresh = refresh
        self.max_requests = max_requests
        self.base_url = base_url.rstrip("/")
        self.network_requests = 0
        self.cache_hits = 0
        self.rate_limit: Dict[str, Any] = {}

    def _url(self, path: str, params: Optional[Dict[str, Any]]) -> str:
        query = urllib.parse.urlencode(
            sorted((params or {}).items()),
            doseq=True,
        )
        return self.base_url + path + (("?" + query) if query else "")

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / "{}.json".format(digest)

    def get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = self._url(path, params)
        cache_path = self._cache_path(url)
        if cache_path.is_file() and not self.refresh:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            self.cache_hits += 1
            return cached["response"]["body"]

        if self.max_requests and self.network_requests >= self.max_requests:
            raise DiscoveryError(
                "GitHub network request budget exhausted ({}); rerun with a "
                "larger --max-requests or reuse the cache".format(self.max_requests)
            )
        if self.rate_limit.get("remaining") == 0:
            reset = self.rate_limit.get("reset_at") or "unknown"
            raise DiscoveryError(
                "GitHub API rate limit exhausted; reset at {} or set GITHUB_TOKEN".format(
                    reset
                )
            )

        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "rl-coding-env-lab04",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = "Bearer {}".format(self.token)
        request = urllib.request.Request(url, headers=headers)
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                self.network_requests += 1
                with urllib.request.urlopen(request, timeout=45) as response:
                    body = json.loads(response.read().decode("utf-8"))
                    response_headers = dict(response.headers.items())
                    self._record_rate_limit(response_headers)
                    write_json(
                        cache_path,
                        {
                            "request": {"method": "GET", "url": url},
                            "response": {
                                "body": body,
                                "etag": response_headers.get("etag"),
                                "fetched_at": utc_now(),
                                "status": response.status,
                            },
                        },
                    )
                    return body
            except urllib.error.HTTPError as error:
                response_body = error.read().decode("utf-8", errors="replace")
                self._record_rate_limit(dict(error.headers.items()))
                if error.code not in {429, 500, 502, 503, 504}:
                    raise GitHubError(
                        error.code,
                        "GitHub API returned HTTP {} for {}: {}".format(
                            error.code,
                            url,
                            response_body[:500],
                        ),
                    ) from error
                last_error = error
            except (urllib.error.URLError, TimeoutError, ValueError) as error:
                last_error = error
            if attempt < 2:
                time.sleep(2**attempt)
        raise DiscoveryError(
            "GitHub API failed after 3 attempts for {}: {}".format(url, last_error)
        )

    def _record_rate_limit(self, headers: Dict[str, str]) -> None:
        normalized = {key.lower(): value for key, value in headers.items()}
        reset = normalized.get("x-ratelimit-reset")
        reset_at = None
        if reset and reset.isdigit():
            reset_at = datetime.fromtimestamp(
                int(reset),
                tz=timezone.utc,
            ).isoformat().replace("+00:00", "Z")
        self.rate_limit = {
            "limit": _optional_int(normalized.get("x-ratelimit-limit")),
            "remaining": _optional_int(normalized.get("x-ratelimit-remaining")),
            "used": _optional_int(normalized.get("x-ratelimit-used")),
            "resource": normalized.get("x-ratelimit-resource"),
            "reset_at": reset_at,
        }

    def get_all(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        max_pages: int = 0,
    ) -> List[Any]:
        values: List[Any] = []
        page = 1
        while True:
            page_params = dict(params or {})
            page_params.update({"per_page": 100, "page": page})
            batch = self.get(path, page_params)
            if not isinstance(batch, list):
                raise DiscoveryError("expected a JSON list from {}".format(path))
            values.extend(batch)
            if len(batch) < 100 or (max_pages and page >= max_pages):
                return values
            page += 1

    def evidence(self) -> Dict[str, Any]:
        return {
            "network_requests": self.network_requests,
            "cache_hits": self.cache_hits,
            "rate_limit_after_last_network_request": self.rate_limit or None,
        }


def _optional_int(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def repository_record(repo: Dict[str, Any]) -> Dict[str, Any]:
    license_data = repo.get("license") or {}
    return {
        "full_name": repo.get("full_name"),
        "url": repo.get("html_url"),
        "description": repo.get("description"),
        "default_branch": repo.get("default_branch"),
        "language": repo.get("language"),
        "license_spdx_id": license_data.get("spdx_id"),
        "stars": repo.get("stargazers_count"),
        "fork": bool(repo.get("fork")),
        "archived": bool(repo.get("archived")),
        "size_kb": repo.get("size"),
    }


def actor_login(value: Dict[str, Any]) -> Optional[str]:
    actor = value.get("user") or {}
    return actor.get("login")


def label_names(value: Dict[str, Any]) -> List[str]:
    return [
        str(label.get("name"))
        for label in value.get("labels") or []
        if label.get("name")
    ]


def pull_record(pull: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "number": pull.get("number"),
        "url": pull.get("html_url"),
        "title": pull.get("title") or "",
        "body": pull.get("body") or "",
        "author": actor_login(pull),
        "author_association": pull.get("author_association"),
        "labels": label_names(pull),
        "created_at": pull.get("created_at"),
        "updated_at": pull.get("updated_at"),
        "closed_at": pull.get("closed_at"),
        "merged_at": pull.get("merged_at"),
        "base_commit": (pull.get("base") or {}).get("sha"),
        "head_commit": (pull.get("head") or {}).get("sha"),
        "merge_commit": pull.get("merge_commit_sha"),
        "commit_count": pull.get("commits"),
        "conversation_comment_count": pull.get("comments"),
        "review_comment_count": pull.get("review_comments"),
        "changed_file_count": pull.get("changed_files"),
        "additions": pull.get("additions"),
        "deletions": pull.get("deletions"),
    }


def issue_record(issue: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "number": issue.get("number"),
        "url": issue.get("html_url"),
        "title": issue.get("title") or "",
        "body": issue.get("body") or "",
        "author": actor_login(issue),
        "author_association": issue.get("author_association"),
        "state": issue.get("state"),
        "state_reason": issue.get("state_reason"),
        "labels": label_names(issue),
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "closed_at": issue.get("closed_at"),
        "comment_count": issue.get("comments"),
    }


def file_features(files: Sequence[Dict[str, Any]], expected_count: int) -> Dict[str, Any]:
    records = []
    for item in files:
        path = item.get("filename") or ""
        records.append(
            {
                "path": path,
                "previous_path": item.get("previous_filename"),
                "status": item.get("status"),
                "is_test": is_test_path(path),
                "additions": int(item.get("additions") or 0),
                "deletions": int(item.get("deletions") or 0),
                "changes": int(item.get("changes") or 0),
            }
        )
    source = [item for item in records if not item["is_test"]]
    tests = [item for item in records if item["is_test"]]

    def totals(items: Sequence[Dict[str, Any]]) -> Dict[str, int]:
        additions = sum(item["additions"] for item in items)
        deletions = sum(item["deletions"] for item in items)
        return {
            "file_count": len(items),
            "additions": additions,
            "deletions": deletions,
            "changed_lines": additions + deletions,
        }

    return {
        "files": records,
        "all": totals(records),
        "source": totals(source),
        "tests": totals(tests),
        "source_paths": [item["path"] for item in source],
        "test_paths": [item["path"] for item in tests],
        "separable_by_lab02_policy": bool(source and tests),
        "complete": len(records) == expected_count,
    }


def issue_text_signals(title: str, body: str) -> Dict[str, Any]:
    combined = "{}\n{}".format(title, body).lower()
    ambiguity_terms = (
        "unclear",
        "ambiguous",
        "or wrong",
        "maybe",
        "perhaps",
        "if that is intentional",
        "if that's intentional",
        "not sure",
    )
    expected_terms = (
        "expected",
        "should ",
        "shouldn't",
        "should not",
        "instead",
        "want ",
    )
    observed_terms = (
        "actual",
        "currently",
        "but ",
        "error",
        "fails",
        "wrong",
    )
    return {
        "title_chars": len(title),
        "body_chars": len(body),
        "has_body": bool(body.strip()),
        "has_code_fence": "```" in body,
        "has_expected_behavior_language": any(term in combined for term in expected_terms),
        "has_observed_behavior_language": any(term in combined for term in observed_terms),
        "mentions_ambiguity": any(term in combined for term in ambiguity_terms),
        "question_mark_count": body.count("?"),
    }


def comment_record(comment: Dict[str, Any], kind: str) -> Dict[str, Any]:
    return {
        "id": comment.get("id"),
        "kind": kind,
        "url": comment.get("html_url"),
        "author": actor_login(comment),
        "author_association": comment.get("author_association"),
        "created_at": comment.get("created_at") or comment.get("submitted_at"),
        "updated_at": comment.get("updated_at") or comment.get("submitted_at"),
        "state": comment.get("state"),
        "body": comment.get("body") or "",
        "path": comment.get("path"),
        "line": comment.get("line") or comment.get("original_line"),
    }


def collect_comments(
    client: GitHubClient,
    repo: str,
    issue_number: int,
    pr_number: int,
    mode: str,
) -> Dict[str, Any]:
    if mode == "counts":
        return {
            "mode": "counts",
            "issue": [],
            "pr_conversation": [],
            "pr_reviews": [],
            "pr_review_inline": [],
        }
    groups = {
        "issue": [
            comment_record(item, "issue")
            for item in client.get_all(
                "/repos/{}/issues/{}/comments".format(repo, issue_number)
            )
        ],
        "pr_conversation": [
            comment_record(item, "pr_conversation")
            for item in client.get_all(
                "/repos/{}/issues/{}/comments".format(repo, pr_number)
            )
        ],
        "pr_reviews": [
            comment_record(item, "pr_review")
            for item in client.get_all(
                "/repos/{}/pulls/{}/reviews".format(repo, pr_number)
            )
        ],
        "pr_review_inline": [
            comment_record(item, "pr_review_inline")
            for item in client.get_all(
                "/repos/{}/pulls/{}/comments".format(repo, pr_number)
            )
        ],
    }
    groups["mode"] = "full"
    return groups


def comments_before(
    comments: Sequence[Dict[str, Any]],
    timestamp: Optional[str],
) -> List[Dict[str, Any]]:
    boundary = parse_timestamp(timestamp)
    if boundary is None:
        return []
    result = []
    for comment in comments:
        created = parse_timestamp(comment.get("created_at"))
        if created and created <= boundary:
            result.append(comment)
    return result


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
        raise DiscoveryError(
            "missing model configuration: {}".format(", ".join(missing))
        )
    return ModelConfig(
        api_key=str(key_value),
        model=str(model_value),
        base_url=str(base_value).rstrip("/"),
    )


SEMANTIC_SCHEMA = {
    "type": "object",
    "properties": {
        "original_issue": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "enum": ["CLEAR", "AMBIGUOUS", "INSUFFICIENT"],
                },
                "confidence": {"type": "number"},
                "rationale": {"type": "string"},
                "competing_interpretations": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "missing_information": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "evidence_quotes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "label",
                "confidence",
                "rationale",
                "competing_interpretations",
                "missing_information",
                "evidence_quotes",
            ],
            "additionalProperties": False,
        },
        "with_pre_pr_comments": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "enum": ["CLEAR", "AMBIGUOUS", "INSUFFICIENT"],
                },
                "confidence": {"type": "number"},
                "rationale": {"type": "string"},
                "clarifying_comment_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
                "selected_interpretation": {"type": ["string", "null"]},
                "evidence_quotes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "label",
                "confidence",
                "rationale",
                "clarifying_comment_ids",
                "selected_interpretation",
                "evidence_quotes",
            ],
            "additionalProperties": False,
        },
        "pr_issue_alignment": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "enum": ["ALIGNED", "PARTIAL", "MISALIGNED", "UNKNOWN"],
                },
                "confidence": {"type": "number"},
                "rationale": {"type": "string"},
                "evidence_quotes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["label", "confidence", "rationale", "evidence_quotes"],
            "additionalProperties": False,
        },
        "behavioral_spec": {"type": ["string", "null"]},
        "behavioral_spec_evidence": {
            "type": "array",
            "items": {"type": "string"},
        },
        "recommended_action": {
            "type": "string",
            "enum": ["ACCEPT", "NEEDS_SPEC", "REJECT"],
        },
    },
    "required": [
        "original_issue",
        "with_pre_pr_comments",
        "pr_issue_alignment",
        "behavioral_spec",
        "behavioral_spec_evidence",
        "recommended_action",
    ],
    "additionalProperties": False,
}


def response_text(response: Dict[str, Any]) -> str:
    texts = []
    for item in response.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                texts.append(str(content["text"]))
    return "\n".join(texts)


def parse_json_object(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("semantic response must be a JSON object")
    return value


class SemanticAssessor:
    def __init__(
        self,
        config: ModelConfig,
        cache_dir: Path,
        *,
        refresh: bool = False,
        timeout: int = 180,
    ):
        self.config = config
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.refresh = refresh
        self.timeout = timeout
        self.network_requests = 0
        self.cache_hits = 0

    def assess(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        issue_comments = comments_before(
            candidate["comments"]["issue"],
            candidate["pr"]["created_at"],
        )
        source = {
            "issue": {
                "title": candidate["issue"]["title"],
                "body": candidate["issue"]["body"],
            },
            "pre_pr_issue_comments": [
                {
                    "id": item["id"],
                    "author": item["author"],
                    "body": item["body"],
                }
                for item in issue_comments
            ],
            "pull_request": {
                "title": candidate["pr"]["title"],
                "body": candidate["pr"]["body"],
            },
            "patch_shape": {
                "source_paths": candidate["patch"]["source_paths"],
                "test_paths": candidate["patch"]["test_paths"],
                "source_changed_lines": candidate["patch"]["source"]["changed_lines"],
                "test_changed_lines": candidate["patch"]["tests"]["changed_lines"],
            },
        }
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "model": self.config.model,
                    "prompt_version": SEMANTIC_PROMPT_VERSION,
                    "source": source,
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        cache_path = self.cache_dir / "{}.json".format(cache_key)
        if cache_path.is_file() and not self.refresh:
            self.cache_hits += 1
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            return cached["assessment"]

        payload = {
            "model": self.config.model,
            "instructions": (
                "You audit GitHub issue/PR pairs for coding-task data quality. "
                "Judge whether the original issue title and body alone specify one "
                "unambiguous externally observable behavior. An issue is AMBIGUOUS "
                "when multiple valid resolutions remain, including code change versus "
                "documentation change. Separately judge whether comments posted before "
                "the PR was opened resolve that ambiguity, and whether the PR description "
                "aligns with the clarified request. Resolve pronouns and phrases such as "
                "'your interpretation' against the exact alternatives introduced by the "
                "issue author; do not confuse the reported current behavior with the "
                "author's proposed behavior. Evidence quotes must be short verbatim "
                "snippets from the supplied text. Do not infer details from hidden tests "
                "or invent implementation steps. behavioral_spec must be null unless the "
                "chosen behavior is explicit after considering pre-PR comments; otherwise "
                "it must state only what behavior is expected, never how to implement it."
            ),
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(source, ensure_ascii=False),
                        }
                    ],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "issue_pair_quality",
                    "strict": True,
                    "schema": SEMANTIC_SCHEMA,
                }
            },
            "max_output_tokens": 2000,
            "store": False,
        }
        request = urllib.request.Request(
            self.config.base_url + "/responses",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer {}".format(self.config.api_key),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                self.network_requests += 1
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                assessment = parse_json_object(response_text(raw))
                result = {
                    "status": "ASSESSED",
                    "method": "llm",
                    "model": self.config.model,
                    "response_id": raw.get("id"),
                    "usage": raw.get("usage") or {},
                    "assessment": assessment,
                }
                write_json(
                    cache_path,
                    {
                        "source_sha256": cache_key,
                        "created_at": utc_now(),
                        "assessment": result,
                        "raw_response": raw,
                    },
                )
                return result
            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")
                if error.code not in {429, 500, 502, 503, 504}:
                    raise DiscoveryError(
                        "model API returned HTTP {}: {}".format(error.code, body[:1000])
                    ) from error
                last_error = error
            except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
                last_error = error
            if attempt < 2:
                time.sleep(2**attempt)
        raise DiscoveryError(
            "semantic assessment failed after 3 attempts: {}".format(last_error)
        )

    def evidence(self) -> Dict[str, Any]:
        return {
            "model": self.config.model,
            "network_requests": self.network_requests,
            "cache_hits": self.cache_hits,
        }


def not_assessed() -> Dict[str, Any]:
    return {
        "status": "NOT_ASSESSED",
        "method": "none",
        "model": None,
        "assessment": None,
    }


def rejected_record(
    repo: str,
    pr_number: int,
    code: str,
    message: str,
    *,
    issue_number: Optional[int] = None,
    observed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "REJECTED",
        "repo": repo,
        "pr_number": pr_number,
        "issue_number": issue_number,
        "reason": {"code": code, "message": message},
        "observed": observed or {},
    }


def iter_pulls(
    client: GitHubClient,
    repo: str,
    *,
    pr_numbers: Sequence[int],
    merged_since: Optional[datetime],
    merged_until: Optional[datetime],
    max_prs: int,
    max_pages: int,
    counters: Counter,
) -> Iterable[Dict[str, Any]]:
    selected = 0
    if pr_numbers:
        for number in pr_numbers:
            pull = client.get("/repos/{}/pulls/{}".format(repo, number))
            counters["pulls_seen"] += 1
            if not pull.get("merged_at"):
                counters["not_merged"] += 1
                continue
            if not in_merged_range(pull.get("merged_at"), merged_since, merged_until):
                counters["outside_date_range"] += 1
                continue
            counters["merged_prs_scanned"] += 1
            yield pull
        return

    page = 1
    while True:
        batch = client.get(
            "/repos/{}/pulls".format(repo),
            {
                "state": "closed",
                "sort": "created",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            },
        )
        if not isinstance(batch, list):
            raise DiscoveryError("GitHub pulls endpoint did not return a list")
        for pull in batch:
            counters["pulls_seen"] += 1
            if not pull.get("merged_at"):
                counters["not_merged"] += 1
                continue
            if not in_merged_range(pull.get("merged_at"), merged_since, merged_until):
                counters["outside_date_range"] += 1
                continue
            counters["merged_prs_scanned"] += 1
            selected += 1
            yield pull
            if max_prs and selected >= max_prs:
                return
        if len(batch) < 100 or (max_pages and page >= max_pages):
            return
        page += 1


def discover(
    client: GitHubClient,
    repo: str,
    *,
    pr_numbers: Sequence[int] = (),
    merged_since: Optional[datetime] = None,
    merged_until: Optional[datetime] = None,
    max_prs: int = 0,
    max_pages: int = 0,
    comment_mode: str = "full",
    semantic: Optional[SemanticAssessor] = None,
    max_source_changed_lines: int = 300,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    repo_data = client.get("/repos/{}".format(repo))
    repository = repository_record(repo_data)
    candidates: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    counters: Counter = Counter()

    pulls = iter_pulls(
        client,
        repo,
        pr_numbers=pr_numbers,
        merged_since=merged_since,
        merged_until=merged_until,
        max_prs=max_prs,
        max_pages=max_pages,
        counters=counters,
    )
    for listed_pull in pulls:
        pr_number = int(listed_pull["number"])
        references = extract_closing_issue_references(
            listed_pull.get("body") or "",
            repo,
        )
        local_references = [item for item in references if item["is_local"]]
        if not local_references:
            counters["prs_without_local_closing_issue"] += 1
            rejected.append(
                rejected_record(
                    repo,
                    pr_number,
                    "NO_LOCAL_CLOSING_ISSUE",
                    "Merged PR body has no explicit local closes/fixes/resolves reference.",
                    observed={"closing_references": references},
                )
            )
            continue

        pull = client.get("/repos/{}/pulls/{}".format(repo, pr_number))
        files = client.get_all(
            "/repos/{}/pulls/{}/files".format(repo, pr_number),
            max_pages=30,
        )
        patch = file_features(files, int(pull.get("changed_files") or len(files)))
        if not patch["complete"]:
            for reference in local_references:
                counters["rejection_FILES_TRUNCATED"] += 1
                rejected.append(
                    rejected_record(
                        repo,
                        pr_number,
                        "FILES_TRUNCATED",
                        "GitHub file listing did not contain every changed file.",
                        issue_number=reference["number"],
                        observed={"patch": patch},
                    )
                )
            continue
        if not patch["separable_by_lab02_policy"]:
            for reference in local_references:
                counters["rejection_PATCH_NOT_SEPARABLE"] += 1
                rejected.append(
                    rejected_record(
                        repo,
                        pr_number,
                        "PATCH_NOT_SEPARABLE",
                        "PR does not contain both source and test paths under Lab 02 policy.",
                        issue_number=reference["number"],
                        observed={"patch": patch},
                    )
                )
            continue

        for reference in local_references:
            issue_number = int(reference["number"])
            counters["linked_pairs_found"] += 1
            try:
                issue_data = client.get(
                    "/repos/{}/issues/{}".format(repo, issue_number)
                )
            except GitHubError as error:
                counters["rejection_ISSUE_FETCH_FAILED"] += 1
                rejected.append(
                    rejected_record(
                        repo,
                        pr_number,
                        "ISSUE_FETCH_FAILED",
                        str(error),
                        issue_number=issue_number,
                    )
                )
                continue
            if issue_data.get("pull_request"):
                counters["rejection_LINKED_TARGET_IS_PR"] += 1
                rejected.append(
                    rejected_record(
                        repo,
                        pr_number,
                        "LINKED_TARGET_IS_PR",
                        "Closing reference points to another pull request, not an issue.",
                        issue_number=issue_number,
                    )
                )
                continue

            comments = collect_comments(
                client,
                repo,
                issue_number,
                pr_number,
                comment_mode,
            )
            issue = issue_record(issue_data)
            pr = pull_record(pull)
            pre_pr_issue_comments = comments_before(
                comments["issue"],
                pr["created_at"],
            )
            candidate_id = "{}__{}-issue-{}-pr-{}".format(
                repo.split("/", 1)[0],
                repo.split("/", 1)[1],
                issue_number,
                pr_number,
            )
            candidate = {
                "schema_version": SCHEMA_VERSION,
                "status": "DISCOVERED",
                "verification_status": "NOT_RUN",
                "candidate_id": candidate_id,
                "collected_at": utc_now(),
                "repository": repository,
                "link": reference,
                "issue": issue,
                "pr": pr,
                "patch": patch,
                "comments": comments,
                "features": {
                    "issue_text": issue_text_signals(
                        issue["title"],
                        issue["body"],
                    ),
                    "issue_comments_before_pr_count": len(pre_pr_issue_comments),
                    "issue_comments_before_merge_count": len(
                        comments_before(comments["issue"], pr["merged_at"])
                    ),
                    "within_recommended_source_change_limit": (
                        patch["source"]["changed_lines"] <= max_source_changed_lines
                    ),
                    "recommended_source_change_limit": max_source_changed_lines,
                },
                "semantic_review": not_assessed(),
                "next_stage": {
                    "lab": "02-pr-task-compiler",
                    "command": (
                        "python3 ../02-pr-task-compiler/compile_task.py "
                        "--repo {} --pr {} --issue {}"
                    ).format(repo, pr_number, issue_number),
                },
            }
            if semantic is not None:
                try:
                    candidate["semantic_review"] = semantic.assess(candidate)
                    counters["semantic_assessed"] += 1
                except DiscoveryError as error:
                    candidate["semantic_review"] = {
                        "status": "ERROR",
                        "method": "llm",
                        "model": semantic.config.model,
                        "error": str(error),
                        "assessment": None,
                    }
                    counters["semantic_errors"] += 1
            candidates.append(candidate)
            counters["discovered_pairs"] += 1

    rejection_reasons = Counter(
        item["reason"]["code"] for item in rejected
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "DISCOVERY_COMPLETE",
        "scan_scope": (
            "TARGETED"
            if pr_numbers
            else ("BOUNDED" if max_prs or max_pages else "FULL_REPOSITORY")
        ),
        "repo": repo,
        "collected_at": utc_now(),
        "result_contract": (
            "DISCOVERED means statically eligible for Lab 02; only Lab 02 execution "
            "may produce VERIFIED."
        ),
        "repository": repository,
        "filters": {
            "pr_numbers": list(pr_numbers),
            "merged_since": (
                merged_since.isoformat().replace("+00:00", "Z")
                if merged_since
                else None
            ),
            "merged_until": (
                merged_until.isoformat().replace("+00:00", "Z")
                if merged_until
                else None
            ),
            "max_prs": max_prs or None,
            "max_pages": max_pages or None,
            "comment_mode": comment_mode,
            "semantic_mode": "llm" if semantic else "off",
            "max_source_changed_lines": max_source_changed_lines,
        },
        "funnel": dict(sorted(counters.items())),
        "candidate_count": len(candidates),
        "rejected_record_count": len(rejected),
        "rejection_reasons": dict(sorted(rejection_reasons.items())),
        "github_api": client.evidence(),
        "semantic_api": semantic.evidence() if semantic else None,
    }
    return candidates, rejected, summary


CSV_FIELDS = [
    "candidate_id",
    "status",
    "repo",
    "pr_number",
    "issue_number",
    "pr_url",
    "issue_url",
    "source_file_count",
    "test_file_count",
    "source_additions",
    "source_deletions",
    "source_changed_lines",
    "test_additions",
    "test_deletions",
    "test_changed_lines",
    "issue_comment_count",
    "pre_pr_issue_comment_count",
    "pr_conversation_comment_count",
    "pr_review_comment_count",
    "within_recommended_scope",
    "original_issue_clarity",
    "clarified_issue_clarity",
    "pr_issue_alignment",
    "semantic_recommended_action",
]


def csv_row(candidate: Dict[str, Any]) -> Dict[str, Any]:
    assessment = candidate["semantic_review"].get("assessment") or {}
    return {
        "candidate_id": candidate["candidate_id"],
        "status": candidate["status"],
        "repo": candidate["repository"]["full_name"],
        "pr_number": candidate["pr"]["number"],
        "issue_number": candidate["issue"]["number"],
        "pr_url": candidate["pr"]["url"],
        "issue_url": candidate["issue"]["url"],
        "source_file_count": candidate["patch"]["source"]["file_count"],
        "test_file_count": candidate["patch"]["tests"]["file_count"],
        "source_additions": candidate["patch"]["source"]["additions"],
        "source_deletions": candidate["patch"]["source"]["deletions"],
        "source_changed_lines": candidate["patch"]["source"]["changed_lines"],
        "test_additions": candidate["patch"]["tests"]["additions"],
        "test_deletions": candidate["patch"]["tests"]["deletions"],
        "test_changed_lines": candidate["patch"]["tests"]["changed_lines"],
        "issue_comment_count": candidate["issue"]["comment_count"],
        "pre_pr_issue_comment_count": candidate["features"][
            "issue_comments_before_pr_count"
        ],
        "pr_conversation_comment_count": candidate["pr"][
            "conversation_comment_count"
        ],
        "pr_review_comment_count": candidate["pr"]["review_comment_count"],
        "within_recommended_scope": candidate["features"][
            "within_recommended_source_change_limit"
        ],
        "original_issue_clarity": (assessment.get("original_issue") or {}).get(
            "label",
            "NOT_ASSESSED",
        ),
        "clarified_issue_clarity": (
            assessment.get("with_pre_pr_comments") or {}
        ).get("label", "NOT_ASSESSED"),
        "pr_issue_alignment": (assessment.get("pr_issue_alignment") or {}).get(
            "label",
            "NOT_ASSESSED",
        ),
        "semantic_recommended_action": assessment.get(
            "recommended_action",
            "NOT_ASSESSED",
        ),
    }


def write_outputs(
    run_dir: Path,
    candidates: Sequence[Dict[str, Any]],
    rejected: Sequence[Dict[str, Any]],
    summary: Dict[str, Any],
) -> None:
    write_jsonl(run_dir / "candidates.jsonl", candidates)
    write_jsonl(run_dir / "rejected.jsonl", rejected)
    with (run_dir / "candidates.csv").open(
        "w",
        encoding="utf-8",
        newline="",
    ) as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(csv_row(candidate))
    summary["outputs"] = {
        "candidates_jsonl": "candidates.jsonl",
        "candidates_csv": "candidates.csv",
        "rejected_jsonl": "rejected.jsonl",
        "summary": "summary.json",
    }
    write_json(run_dir / "summary.json", summary)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Discover explicitly linked merged GitHub issue/PR pairs for Lab 02."
        )
    )
    parser.add_argument("--repo", required=True, help="GitHub owner/repo or URL")
    parser.add_argument(
        "--pr",
        dest="pr_numbers",
        type=int,
        action="append",
        default=[],
        help="Inspect one PR directly; repeat to inspect multiple PRs.",
    )
    parser.add_argument("--merged-since", help="Inclusive YYYY-MM-DD")
    parser.add_argument("--merged-until", help="Inclusive YYYY-MM-DD")
    parser.add_argument(
        "--max-prs",
        type=int,
        default=0,
        help="Maximum merged PRs to inspect after date filtering; 0 means unlimited.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Maximum closed-PR listing pages; 0 means unlimited.",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=0,
        help="Maximum uncached GitHub requests; 0 means unlimited.",
    )
    parser.add_argument(
        "--comment-mode",
        choices=("counts", "full"),
        default="full",
        help="Fetch full comment bodies or retain API counts only.",
    )
    parser.add_argument(
        "--semantic-mode",
        choices=("off", "llm"),
        default="off",
        help="Optionally assess issue clarity and PR alignment with an LLM.",
    )
    parser.add_argument(
        "--max-source-changed-lines",
        type=int,
        default=300,
        help="Recommendation threshold only; it does not reject a pair.",
    )
    parser.add_argument(
        "--run-name",
        help="Output directory name; defaults to timestamp plus a random suffix.",
    )
    parser.add_argument("--output-root", type=Path, default=HERE / "runs")
    parser.add_argument("--cache-dir", type=Path, default=HERE / ".cache")
    parser.add_argument("--refresh", action="store_true", help="Bypass both caches")
    parser.add_argument("--github-token", help="Defaults to GITHUB_TOKEN")
    parser.add_argument("--endpoint-file", type=Path, default=DEFAULT_ENDPOINT_FILE)
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        repo = parse_repo(args.repo)
        since = parse_date_boundary(args.merged_since, end=False)
        until = parse_date_boundary(args.merged_until, end=True)
        if since and until and since > until:
            raise DiscoveryError("--merged-since must not be after --merged-until")
        if args.semantic_mode == "llm" and args.comment_mode != "full":
            raise DiscoveryError(
                "--semantic-mode llm requires --comment-mode full so pre-PR "
                "clarifications are available"
            )
        if args.run_name and not re.fullmatch(r"[A-Za-z0-9._-]+", args.run_name):
            raise DiscoveryError(
                "run name may contain only letters, digits, dot, underscore, and dash"
            )
        run_name = args.run_name or (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        run_dir = args.output_root.resolve() / run_name
        run_dir.mkdir(parents=True, exist_ok=False)
        github = GitHubClient(
            args.cache_dir.resolve() / "github",
            token=args.github_token or os.environ.get("GITHUB_TOKEN"),
            refresh=args.refresh,
            max_requests=args.max_requests,
        )
        semantic = None
        if args.semantic_mode == "llm":
            config = load_model_config(
                args.endpoint_file,
                api_key=args.api_key,
                model=args.model,
                base_url=args.base_url,
            )
            semantic = SemanticAssessor(
                config,
                args.cache_dir.resolve() / "semantic",
                refresh=args.refresh,
            )
        candidates, rejected, summary = discover(
            github,
            repo,
            pr_numbers=args.pr_numbers,
            merged_since=since,
            merged_until=until,
            max_prs=args.max_prs,
            max_pages=args.max_pages,
            comment_mode=args.comment_mode,
            semantic=semantic,
            max_source_changed_lines=args.max_source_changed_lines,
        )
        write_outputs(run_dir, candidates, rejected, summary)
    except (DiscoveryError, OSError, ValueError) as error:
        if "run_dir" in locals() and run_dir.is_dir():
            write_json(
                run_dir / "_FAILED.json",
                {
                    "status": "FAILED",
                    "error": str(error),
                    "failed_at": utc_now(),
                },
            )
        print("ERROR: {}".format(error), file=sys.stderr)
        return 1

    print("status: {}".format(summary["status"]))
    print("repo: {}".format(repo))
    print("candidates: {}".format(len(candidates)))
    print("rejected records: {}".format(len(rejected)))
    print("output: {}".format(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
