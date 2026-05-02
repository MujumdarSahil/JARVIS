"""
GitHub REST API client using PyGithub library.

Authentication: Personal Access Token (PAT).
  - Set github.token in config.yaml (never hardcode).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


class GitHubClient:
    """GitHub REST API client backed by a Personal Access Token."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._gh: Any = None
        self._user: Any = None
        self._connect()

    def _connect(self) -> None:
        try:
            from github import Github
            self._gh = Github(self._token)
            self._user = self._gh.get_user()
            logger.info("GitHub authenticated as %s", self._user.login)
        except Exception as e:
            logger.error("GitHub connection failed: %s", e)
            self._gh = None
            self._user = None

    def _ok(self) -> bool:
        return self._gh is not None and self._user is not None

    def get_user(self) -> dict:
        try:
            if not self._ok():
                return {"success": False, "error": "Not connected"}
            u = self._user
            return {
                "success": True,
                "login": u.login,
                "name": u.name or "",
                "email": u.email or "",
                "public_repos": u.public_repos,
                "followers": u.followers,
                "following": u.following,
                "url": u.html_url,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_repos(self, owned_only: bool = True) -> list:
        try:
            if not self._ok():
                return []
            repos = self._user.get_repos(type="owner" if owned_only else "all")
            result = []
            for r in repos:
                result.append({
                    "name": r.full_name,
                    "description": r.description or "",
                    "language": r.language or "",
                    "stars": r.stargazers_count,
                    "updated_at": str(r.updated_at),
                    "is_private": r.private,
                    "url": r.html_url,
                })
            return result
        except Exception as e:
            logger.error("list_repos failed: %s", e)
            return []

    def get_repo(self, repo_name: str) -> dict:
        try:
            if not self._ok():
                return {"success": False, "error": "Not connected"}
            r = self._gh.get_repo(repo_name)
            return {
                "success": True,
                "name": r.full_name,
                "description": r.description or "",
                "language": r.language or "",
                "stars": r.stargazers_count,
                "forks": r.forks_count,
                "open_issues": r.open_issues_count,
                "default_branch": r.default_branch,
                "url": r.html_url,
                "topics": list(r.get_topics()),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_issues(self, repo_name: str, state: str = "open") -> list:
        try:
            if not self._ok():
                return []
            repo = self._gh.get_repo(repo_name)
            issues = repo.get_issues(state=state)
            return [
                {
                    "number": i.number,
                    "title": i.title,
                    "body": (i.body or "")[:500],
                    "labels": [l.name for l in i.labels],
                    "assignee": i.assignee.login if i.assignee else None,
                    "url": i.html_url,
                    "created_at": str(i.created_at),
                }
                for i in issues
            ]
        except Exception as e:
            logger.error("list_issues failed: %s", e)
            return []

    def create_issue(self, repo_name: str, title: str, body: str, labels: list | None = None) -> dict:
        try:
            if not self._ok():
                return {"success": False, "error": "Not connected"}
            repo = self._gh.get_repo(repo_name)
            label_objs = []
            for lname in (labels or []):
                try:
                    label_objs.append(repo.get_label(lname))
                except Exception:
                    pass
            issue = repo.create_issue(title=title, body=body, labels=label_objs)
            return {"success": True, "number": issue.number, "url": issue.html_url}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_pull_requests(self, repo_name: str, state: str = "open") -> list:
        try:
            if not self._ok():
                return []
            repo = self._gh.get_repo(repo_name)
            prs = repo.get_pulls(state=state)
            return [
                {
                    "number": pr.number,
                    "title": pr.title,
                    "body": (pr.body or "")[:500],
                    "user": pr.user.login,
                    "head": pr.head.ref,
                    "base": pr.base.ref,
                    "url": pr.html_url,
                    "created_at": str(pr.created_at),
                }
                for pr in prs
            ]
        except Exception as e:
            logger.error("list_pull_requests failed: %s", e)
            return []

    def get_pr_diff(self, repo_name: str, pr_number: int) -> str:
        try:
            if not self._ok():
                return ""
            repo = self._gh.get_repo(repo_name)
            pr = repo.get_pull(pr_number)
            files = pr.get_files()
            diff_parts = []
            for f in files:
                diff_parts.append(f"=== {f.filename} (+{f.additions} -{f.deletions}) ===")
                if f.patch:
                    diff_parts.append(f.patch[:5000])
            return "\n".join(diff_parts)[:20000]
        except Exception as e:
            logger.error("get_pr_diff failed: %s", e)
            return ""

    def review_pr(
        self,
        repo_name: str,
        pr_number: int,
        review_body: str,
        event: str = "COMMENT",
    ) -> dict:
        try:
            if not self._ok():
                return {"success": False, "error": "Not connected"}
            repo = self._gh.get_repo(repo_name)
            pr = repo.get_pull(pr_number)
            review = pr.create_review(body=review_body, event=event)
            return {"success": True, "review_id": review.id, "url": pr.html_url}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_file_content(self, repo_name: str, file_path: str, branch: str = "main") -> dict:
        try:
            if not self._ok():
                return {"success": False, "error": "Not connected"}
            repo = self._gh.get_repo(repo_name)
            content = repo.get_contents(file_path, ref=branch)
            return {
                "success": True,
                "path": file_path,
                "content": content.decoded_content.decode("utf-8", errors="replace"),
                "sha": content.sha,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def commit_file(
        self,
        repo_name: str,
        file_path: str,
        content: str,
        commit_message: str,
        branch: str = "main",
    ) -> dict:
        try:
            if not self._ok():
                return {"success": False, "error": "Not connected"}
            repo = self._gh.get_repo(repo_name)
            try:
                existing = repo.get_contents(file_path, ref=branch)
                result = repo.update_file(file_path, commit_message, content, existing.sha, branch=branch)
            except Exception:
                result = repo.create_file(file_path, commit_message, content, branch=branch)
            return {
                "success": True,
                "commit_sha": result["commit"].sha,
                "url": result["commit"].html_url,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_workflows(self, repo_name: str) -> list:
        try:
            if not self._ok():
                return []
            repo = self._gh.get_repo(repo_name)
            workflows = repo.get_workflows()
            return [{"id": w.id, "name": w.name, "state": w.state, "url": w.html_url} for w in workflows]
        except Exception as e:
            logger.error("list_workflows failed: %s", e)
            return []

    def trigger_workflow(self, repo_name: str, workflow_id: str, branch: str = "main") -> dict:
        try:
            if not self._ok():
                return {"success": False, "error": "Not connected"}
            repo = self._gh.get_repo(repo_name)
            wf = repo.get_workflow(workflow_id)
            wf.create_dispatch(branch)
            return {"success": True, "workflow": workflow_id, "branch": branch}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_workflow_runs(self, repo_name: str, limit: int = 5) -> list:
        try:
            if not self._ok():
                return []
            repo = self._gh.get_repo(repo_name)
            runs = repo.get_workflow_runs()
            result = []
            for run in runs:
                result.append({
                    "id": run.id,
                    "name": run.name,
                    "status": run.status,
                    "conclusion": run.conclusion,
                    "branch": run.head_branch,
                    "created_at": str(run.created_at),
                    "url": run.html_url,
                })
                if len(result) >= limit:
                    break
            return result
        except Exception as e:
            logger.error("get_workflow_runs failed: %s", e)
            return []

    def clone_repo_locally(self, repo_name: str, local_path: str) -> dict:
        try:
            if not self._ok():
                return {"success": False, "error": "Not connected"}
            repo = self._gh.get_repo(repo_name)
            clone_url = repo.clone_url
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["git", "clone", clone_url, local_path],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                return {"success": True, "path": local_path}
            return {"success": False, "error": result.stderr}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def search_code(self, query: str, language: str | None = None) -> list:
        try:
            if not self._ok():
                return []
            q = query
            if language:
                q += f" language:{language}"
            results = self._gh.search_code(q)
            return [
                {
                    "repo": r.repository.full_name,
                    "path": r.path,
                    "url": r.html_url,
                    "sha": r.sha,
                }
                for r in results[:20]
            ]
        except Exception as e:
            logger.error("search_code failed: %s", e)
            return []


if __name__ == "__main__":
    print("GitHubClient module OK — set github.token in config.yaml")
