"""Where a repository is hosted, read from its remote URL, and how a pull request is opened
there: GitHub (`gh pr create`), Azure DevOps (`az repos pr create`, the `az` CLI with the
azure-devops extension), any other host: the web address to open one by hand, or nothing.

Only text work: nothing here talks to a host. `framegate publish --pr` runs the command this
module builds (publish.py), and falls back to the web address when the tool is missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, unquote

GITHUB = "github"
AZURE = "azure"
OTHER = "other"

#: Azure DevOps refuses a pull request description longer than this many characters
AZURE_DESCRIPTION_MAX = 4000


@dataclass(frozen=True)
class Host:
    kind: str  # GITHUB | AZURE | OTHER
    #: GitHub: the owner; Azure DevOps: the organization
    owner: str = ""
    #: Azure DevOps only
    project: str = ""
    repo: str = ""

    @property
    def web(self) -> str:
        """The repository's page ("" for a host framegate does not know)."""
        if self.kind == GITHUB:
            return f"https://github.com/{self.owner}/{self.repo}"
        if self.kind == AZURE:
            return (
                f"https://dev.azure.com/{quote(self.owner)}/{quote(self.project)}/_git/"
                f"{quote(self.repo)}"
            )
        return ""

    @property
    def name(self) -> str:
        return {GITHUB: "GitHub", AZURE: "Azure DevOps"}.get(self.kind, "this host")


def _clean(part: str) -> str:
    part = unquote(part.strip("/"))
    return part[:-4] if part.endswith(".git") else part


def parse_remote(url: str) -> Host:
    """The host of a git remote URL, https or ssh:

    - GitHub: `https://github.com/<owner>/<repo>(.git)`, `git@github.com:<owner>/<repo>.git`,
      `ssh://git@github.com/<owner>/<repo>.git`
    - Azure DevOps: `https://(<user>@)dev.azure.com/<org>/<project>/_git/<repo>`,
      `git@ssh.dev.azure.com:v3/<org>/<project>/<repo>`,
      `https://<org>.visualstudio.com/(DefaultCollection/)<project>/_git/<repo>`,
      `<org>@vs-ssh.visualstudio.com:v3/<org>/<project>/<repo>`

    Anything else (GitLab, Bitbucket, GitHub Enterprise, a local path) is OTHER."""
    url = url.strip()
    # scheme://[user@]host[:port]/path   or   [user@]host:path (scp form)
    m = re.match(r"^[a-z][a-z0-9+.-]*://(?:[^@/]*@)?([^/:]+)(?::\d+)?/(.*)$", url, re.I)
    if not m:
        m = re.match(r"^(?:[^@/:]+@)?([^/:]+):(?!/)(.*)$", url)
    if not m or len(m.group(1)) == 1:  # no host; `C:\path` is a local path
        return Host(OTHER)
    host = m.group(1).lower()
    parts = [p for p in m.group(2).split("/") if p]
    if host == "github.com" and len(parts) == 2:
        return Host(GITHUB, _clean(parts[0]), "", _clean(parts[1]))
    if host in ("ssh.dev.azure.com", "vs-ssh.visualstudio.com"):
        if len(parts) == 4 and parts[0] == "v3":
            return Host(AZURE, _clean(parts[1]), _clean(parts[2]), _clean(parts[3]))
        return Host(OTHER)
    if host == "dev.azure.com":
        # <org>/<project>/_git/<repo>; <org>/_git/<repo>: the project is named like the repo
        if len(parts) == 4 and parts[2] == "_git":
            return Host(AZURE, _clean(parts[0]), _clean(parts[1]), _clean(parts[3]))
        if len(parts) == 3 and parts[1] == "_git":
            return Host(AZURE, _clean(parts[0]), _clean(parts[2]), _clean(parts[2]))
        return Host(OTHER)
    if host.endswith(".visualstudio.com"):
        org = host[: -len(".visualstudio.com")]
        if parts and parts[0].lower() == "defaultcollection":
            parts = parts[1:]
        if len(parts) == 3 and parts[1] == "_git":
            return Host(AZURE, org, _clean(parts[0]), _clean(parts[2]))
        if len(parts) == 2 and parts[0] == "_git":
            return Host(AZURE, org, _clean(parts[1]), _clean(parts[1]))
    return Host(OTHER)


def pr_url(host: Host, base: str, head: str) -> str:
    """The web page that opens a pull request from `head` into `base` ("" = unknown host)."""
    if host.kind == GITHUB:
        return f"{host.web}/compare/{quote(base)}...{quote(head)}?expand=1"
    if host.kind == AZURE:
        return (
            f"{host.web}/pullrequestcreate?sourceRef={quote(head, safe='')}"
            f"&targetRef={quote(base, safe='')}"
        )
    return ""


def pr_tool(host: Host) -> str:
    """The command-line tool that creates the pull request on this host ("" = none)."""
    return {GITHUB: "gh", AZURE: "az"}.get(host.kind, "")


def pr_command(host: Host, base: str, head: str, title: str, body_file: str) -> list[str]:
    """The command that creates the pull request (the tool's name first; [] = no tool for
    this host). `body_file` holds the description ("" = none); for Azure DevOps it must be
    at most AZURE_DESCRIPTION_MAX characters (azure_description), and it is passed as
    `@<file>`, the az CLI's way to read an argument's value from a file."""
    if host.kind == GITHUB:
        cmd = ["gh", "pr", "create", "--repo", f"{host.owner}/{host.repo}"]
        cmd += ["--base", base, "--head", head, "--title", title]
        return [*cmd, "--body-file", body_file] if body_file else [*cmd, "--body", ""]
    if host.kind == AZURE:
        cmd = ["az", "repos", "pr", "create", "--detect", "false"]
        cmd += ["--organization", f"https://dev.azure.com/{quote(host.owner)}"]
        cmd += ["--project", host.project, "--repository", host.repo]
        cmd += ["--source-branch", head, "--target-branch", base, "--title", title]
        if body_file:
            cmd += ["--description", f"@{body_file}"]
        return [*cmd, "--query", "pullRequestId", "--output", "tsv"]
    return []


def azure_pr_page(host: Host, pr_id: str) -> str:
    return f"{host.web}/pullrequest/{pr_id.strip()}"


def azure_description(text: str, source: str) -> str:
    """`text` as an Azure DevOps description: whole when it fits, else cut at a line end
    with a last line that says where the full text is."""
    if len(text) <= AZURE_DESCRIPTION_MAX:
        return text
    note = f"\n\n(shortened; the full text: {source})\n"
    head = text[: AZURE_DESCRIPTION_MAX - len(note)]
    cut = head.rfind("\n")
    return (head[:cut] if cut > 0 else head) + note
