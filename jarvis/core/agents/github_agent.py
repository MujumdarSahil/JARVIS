"""
GitHub Agent — lists repos, manages issues, AI-reviews PRs, checks CI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.agents.base_agent import BaseAgent
from utils.logger import get_logger

if TYPE_CHECKING:
    from core.brain import Brain
    from core.db import Database
    from skills.github.client import GitHubClient

logger = get_logger(__name__)


class GithubAgent(BaseAgent):
    """Agent that handles GitHub API tasks."""

    def __init__(
        self,
        brain: "Brain",
        db: "Database",
        config: dict,
        github_client: "GitHubClient | None" = None,
    ) -> None:
        super().__init__("github", brain, db, config)
        self._client = github_client

    def _not_configured(self) -> dict:
        return {
            "success": False,
            "result": "GitHub is not enabled. Set github.enabled: true and github.token in config.yaml.",
        }

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        def _run():
            action = str(task.get("action") or task.get("type") or "").lower().strip()
            params = task.get("params") or {}

            if self._client is None:
                return self._not_configured()

            if action == "list_repos":
                return self._list_repos(params)
            if action == "list_issues":
                return self._list_issues(params)
            if action == "create_issue":
                return self._create_issue(params)
            if action == "review_pr":
                return self._review_pr(params)
            if action == "check_ci":
                return self._check_ci(params)
            if action == "commit_file":
                return self._commit_file(params)
            if action == "search_code":
                return self._search_code(params)
            if action == "repo_summary":
                return self._repo_summary(params)
            return {"success": False, "result": f"Unknown github action: {action}"}

        return self._run_wrapped(task, _run)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _list_repos(self, params: dict) -> dict:
        repos = self._client.list_repos(owned_only=params.get("owned_only", True))
        if not repos:
            return {"success": True, "result": "No repositories found.", "repos": []}
        lines = [f"- {r['name']} ({r.get('language', '?')}) ⭐{r.get('stars', 0)}" for r in repos[:20]]
        return {
            "success": True,
            "result": f"Found {len(repos)} repo(s):\n" + "\n".join(lines),
            "repos": repos,
        }

    def _list_issues(self, params: dict) -> dict:
        repo = params.get("repo") or params.get("repo_name") or self.config.get("default_repo", "")
        if not repo:
            return {"success": False, "result": "No repo specified."}
        state = params.get("state", "open")
        issues = self._client.list_issues(repo, state=state)
        if not issues:
            return {"success": True, "result": f"No {state} issues in {repo}.", "issues": []}
        lines = [f"#{i['number']}: {i['title']}" for i in issues[:20]]
        return {
            "success": True,
            "result": f"{len(issues)} {state} issue(s) in {repo}:\n" + "\n".join(lines),
            "issues": issues,
        }

    def _create_issue(self, params: dict) -> dict:
        repo = params.get("repo") or params.get("repo_name") or self.config.get("default_repo", "")
        title = params.get("title", "")
        body = params.get("body", "")
        if not repo or not title:
            return {"success": False, "result": "Missing repo or title."}
        result = self._client.create_issue(repo, title, body, labels=params.get("labels", []))
        msg = (
            f"Issue #{result.get('number')} created: {title}"
            if result.get("success")
            else f"Create issue failed: {result.get('error')}"
        )
        return {"success": result.get("success", False), "result": msg, "issue": result}

    def _review_pr(self, params: dict) -> dict:
        repo = params.get("repo") or params.get("repo_name") or self.config.get("default_repo", "")
        pr_number = int(params.get("pr_number") or 0)
        if not repo or not pr_number:
            return {"success": False, "result": "Missing repo or pr_number."}
        review = self.ai_review_pr(repo, pr_number)
        return review

    def _check_ci(self, params: dict) -> dict:
        repo = params.get("repo") or params.get("repo_name") or self.config.get("default_repo", "")
        if not repo:
            return {"success": False, "result": "No repo specified."}
        runs = self._client.get_workflow_runs(repo, limit=5)
        if not runs:
            return {"success": True, "result": f"No CI runs found for {repo}.", "runs": []}
        lines = [
            f"- {r.get('name', '?')} [{r.get('status')}] {r.get('conclusion', '')} @ {r.get('branch', '?')}"
            for r in runs
        ]
        return {
            "success": True,
            "result": f"Recent CI runs for {repo}:\n" + "\n".join(lines),
            "runs": runs,
        }

    def _commit_file(self, params: dict) -> dict:
        repo = params.get("repo") or params.get("repo_name") or self.config.get("default_repo", "")
        file_path = params.get("file_path", "")
        content = params.get("content", "")
        message = params.get("commit_message", "Update via Jarvis")
        if not all([repo, file_path, content]):
            return {"success": False, "result": "Missing repo, file_path, or content."}
        result = self._client.commit_file(repo, file_path, content, message,
                                          branch=params.get("branch", "main"))
        msg = (
            f"Committed {file_path} to {repo}"
            if result.get("success")
            else f"Commit failed: {result.get('error')}"
        )
        return {"success": result.get("success", False), "result": msg, "commit": result}

    def _search_code(self, params: dict) -> dict:
        query = params.get("query", "")
        if not query:
            return {"success": False, "result": "No search query provided."}
        results = self._client.search_code(query, language=params.get("language"))
        if not results:
            return {"success": True, "result": "No code results found.", "results": []}
        lines = [f"- {r['repo']}/{r['path']}" for r in results[:10]]
        return {
            "success": True,
            "result": f"Found {len(results)} code result(s):\n" + "\n".join(lines),
            "results": results,
        }

    def _repo_summary(self, params: dict) -> dict:
        repo = params.get("repo") or params.get("repo_name") or self.config.get("default_repo", "")
        if not repo:
            return {"success": False, "result": "No repo specified."}

        repo_info = self._client.get_repo(repo)
        issues = self._client.list_issues(repo, state="open")
        prs = self._client.list_pull_requests(repo, state="open")
        ci_runs = self._client.get_workflow_runs(repo, limit=3)

        lines = [
            f"Repo: {repo_info.get('name', repo)}",
            f"Description: {repo_info.get('description', 'N/A')}",
            f"Language: {repo_info.get('language', 'N/A')}",
            f"Stars: {repo_info.get('stars', 0)} | Forks: {repo_info.get('forks', 0)}",
            f"Open Issues: {len(issues)} | Open PRs: {len(prs)}",
        ]
        if ci_runs:
            last = ci_runs[0]
            lines.append(f"Last CI: {last.get('name', '?')} [{last.get('conclusion', last.get('status', '?'))}]")

        return {
            "success": True,
            "result": "\n".join(lines),
            "repo": repo_info,
            "issues": issues[:5],
            "prs": prs[:5],
            "ci": ci_runs,
        }

    def ai_review_pr(self, repo_name: str, pr_number: int) -> dict:
        """Full AI review pipeline: get diff → LLM review → post comment."""
        try:
            diff = self._client.get_pr_diff(repo_name, pr_number)
            if not diff:
                return {"success": False, "result": "Could not fetch PR diff.", "review_posted": False}

            review_body = self.think(
                f"Review this pull request diff and provide constructive feedback:\n\n{diff[:8000]}",
                system=(
                    "You are a senior software engineer doing a code review. "
                    "Be specific, constructive, and concise. "
                    "Point out bugs, style issues, and suggest improvements."
                ),
            )

            post_result = self._client.review_pr(repo_name, pr_number, review_body, event="COMMENT")
            return {
                "success": post_result.get("success", False),
                "review_posted": post_result.get("success", False),
                "review_body": review_body,
                "pr_url": post_result.get("url", ""),
                "result": f"AI review posted to PR #{pr_number} in {repo_name}",
            }
        except Exception as e:
            return {"success": False, "result": str(e), "review_posted": False}


if __name__ == "__main__":
    print("GithubAgent module OK")
