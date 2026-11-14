"""Host detection from a remote URL and the pull request command / page per host. Nothing
here talks to a host."""

from __future__ import annotations

import pytest

from framegate import hosts
from framegate.hosts import AZURE, GITHUB, OTHER, Host


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/widgets.git",
        "https://github.com/acme/widgets",
        "https://user:token@github.com/acme/widgets.git",
        "git@github.com:acme/widgets.git",
        "ssh://git@github.com/acme/widgets.git",
        "ssh://git@github.com:22/acme/widgets",
        "  https://GitHub.com/acme/widgets/  ",
    ],
)
def test_github_urls(url: str) -> None:
    assert hosts.parse_remote(url) == Host(GITHUB, "acme", "", "widgets")


@pytest.mark.parametrize(
    "url",
    [
        "https://dev.azure.com/contoso/Data%20Platform/_git/da-argos",
        "https://contoso@dev.azure.com/contoso/Data%20Platform/_git/da-argos",
        "git@ssh.dev.azure.com:v3/contoso/Data%20Platform/da-argos",
        "ssh://git@ssh.dev.azure.com/v3/contoso/Data%20Platform/da-argos",
        "https://contoso.visualstudio.com/Data%20Platform/_git/da-argos",
        "https://contoso.visualstudio.com/DefaultCollection/Data%20Platform/_git/da-argos",
        "contoso@vs-ssh.visualstudio.com:v3/contoso/Data%20Platform/da-argos",
    ],
)
def test_azure_devops_urls(url: str) -> None:
    assert hosts.parse_remote(url) == Host(AZURE, "contoso", "Data Platform", "da-argos")


def test_azure_devops_project_named_like_the_repository() -> None:
    want = Host(AZURE, "contoso", "argos", "argos")
    assert hosts.parse_remote("https://dev.azure.com/contoso/_git/argos") == want
    assert hosts.parse_remote("https://contoso.visualstudio.com/_git/argos") == want


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/acme/widgets.git",
        "git@bitbucket.org:acme/widgets.git",
        "https://github.example.com/acme/widgets.git",  # GitHub Enterprise: not known
        "https://github.com/acme",  # no repository
        "https://dev.azure.com/contoso/project",  # no _git part
        "git@ssh.dev.azure.com:v2/contoso/p/r",
        "C:\\repos\\origin.git",
        "C:/repos/origin.git",
        "/srv/git/origin.git",
        "../origin.git",
        "file:///srv/git/origin.git",
        "",
    ],
)
def test_other_hosts(url: str) -> None:
    assert hosts.parse_remote(url).kind == OTHER


def test_pull_request_pages() -> None:
    gh = Host(GITHUB, "acme", "", "widgets")
    assert hosts.pr_url(gh, "main", "feat/x") == (
        "https://github.com/acme/widgets/compare/main...feat/x?expand=1"
    )
    az = Host(AZURE, "contoso", "Data Platform", "da-argos")
    assert hosts.pr_url(az, "dev", "feature/runner-skeleton") == (
        "https://dev.azure.com/contoso/Data%20Platform/_git/da-argos/pullrequestcreate"
        "?sourceRef=feature%2Frunner-skeleton&targetRef=dev"
    )
    assert hosts.azure_pr_page(az, "412\n") == (
        "https://dev.azure.com/contoso/Data%20Platform/_git/da-argos/pullrequest/412"
    )
    assert hosts.pr_url(Host(OTHER), "main", "feat/x") == ""


def test_pull_request_commands() -> None:
    gh = Host(GITHUB, "acme", "", "widgets")
    assert hosts.pr_tool(gh) == "gh"
    assert hosts.pr_command(gh, "main", "feat/x", "x: clamp", "out/x/final_summary.md") == [
        *("gh", "pr", "create", "--repo", "acme/widgets", "--base", "main", "--head", "feat/x"),
        *("--title", "x: clamp", "--body-file", "out/x/final_summary.md"),
    ]
    assert hosts.pr_command(gh, "main", "feat/x", "x", "")[-2:] == ["--body", ""]
    az = Host(AZURE, "contoso", "Data Platform", "da-argos")
    assert hosts.pr_tool(az) == "az"
    assert hosts.pr_command(az, "dev", "feature/x", "x: clamp", "out/x/pr_description.md") == [
        *("az", "repos", "pr", "create", "--detect", "false"),
        *("--organization", "https://dev.azure.com/contoso"),
        *("--project", "Data Platform", "--repository", "da-argos"),
        *("--source-branch", "feature/x", "--target-branch", "dev", "--title", "x: clamp"),
        *("--description", "@out/x/pr_description.md"),
        *("--query", "pullRequestId", "--output", "tsv"),
    ]
    assert "--description" not in hosts.pr_command(az, "dev", "feature/x", "x", "")
    assert hosts.pr_tool(Host(OTHER)) == ""
    assert hosts.pr_command(Host(OTHER), "a", "b", "c", "") == []


def test_azure_description_is_cut_at_a_line_end() -> None:
    short = "# Summary\n\nAll good.\n"
    assert hosts.azure_description(short, "final_summary.md") == short
    long = "".join(f"line {i} of the summary\n" for i in range(400))
    cut = hosts.azure_description(long, "final_summary.md")
    assert len(cut) <= hosts.AZURE_DESCRIPTION_MAX
    assert cut.endswith("(shortened; the full text: final_summary.md)\n")
    body = cut[: cut.index("\n\n(shortened")]
    assert long.startswith(body + "\n") and body.endswith("of the summary")
