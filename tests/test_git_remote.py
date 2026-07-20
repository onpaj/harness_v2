import subprocess

from harness.drivers.git_remote import github_slug, parse_github_slug


def test_parse_ssh_form_strips_git_suffix():
    assert parse_github_slug("git@github.com:onpaj/Anela.Heblo.git") == "onpaj/Anela.Heblo"


def test_parse_https_form_strips_git_suffix():
    assert parse_github_slug("https://github.com/onpaj/Anela.Heblo.git") == "onpaj/Anela.Heblo"


def test_parse_https_without_git_suffix():
    assert parse_github_slug("https://github.com/onpaj/Anela.Heblo") == "onpaj/Anela.Heblo"


def test_parse_non_github_host_is_none():
    assert parse_github_slug("git@gitlab.com:foo/bar.git") is None


def test_parse_incomplete_path_is_none():
    assert parse_github_slug("https://github.com/onpaj") is None


def test_parse_garbage_is_none():
    assert parse_github_slug("not-a-url") is None
    assert parse_github_slug("") is None


def test_github_slug_reads_origin(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin",
         "git@github.com:onpaj/Anela.Heblo.git"],
        check=True,
    )
    assert github_slug(tmp_path) == "onpaj/Anela.Heblo"


def test_github_slug_not_a_repo_is_none(tmp_path):
    assert github_slug(tmp_path) is None
