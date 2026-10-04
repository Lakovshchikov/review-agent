import re
from pathlib import Path

import pytest

from review_agent.file_links import link_file_references, project_web_url

WT = "E:/Projects/review-agent/.review-agent/tmp/1791124380-1db76eeb/worktree"
PROJECT = "https://git.example/grp/sub/proj"
HEAD = "h" * 40
BASE = "b" * 40
HEAD_PATHS = {
    "src",
    "src/a.ts",
    "src/pages",
    "src/pages/Wishlist.tsx",
    "src/Страница группы",
    "src/Страница группы/my page.tsx",
    "package.json",
}
BASE_PATHS = {"src", "src/a.ts", "src/old.ts", "package.json"}


def link(report, *, head_paths=HEAD_PATHS, base_paths=BASE_PATHS, worktree=WT):
    return link_file_references(
        report,
        worktree_path=worktree,
        project_url=PROJECT,
        head_sha=HEAD,
        base_sha=BASE,
        head_paths=head_paths,
        base_paths=base_paths,
    )


def blob(path, anchor="", sha=HEAD):
    return f"{PROJECT}/-/blob/{sha}/{path}{anchor}"


def no_worktree(text):
    """True if no spelling of the worktree path survived."""
    flat = text.replace("\\", "/").lower()
    return "review-agent/.review-agent/tmp" not in flat and "1791124380-1db76eeb" not in flat


# -- project URL -------------------------------------------------------------


def test_project_url_comes_from_the_mr_url():
    assert (
        project_web_url("https://git.example/grp/sub/proj/-/merge_requests/13", "x", "y")
        == "https://git.example/grp/sub/proj"
    )


def test_project_url_falls_back_to_hostname_and_path():
    assert project_web_url("", "git.example", "grp/proj") == "https://git.example/grp/proj"


# -- worktree links ----------------------------------------------------------


def test_worktree_link_with_line():
    out = link(f"[`src/a.ts:13`]({WT}/src/a.ts#L13)")
    assert out == f"[`src/a.ts:13`]({blob('src/a.ts', '#L13')})"


@pytest.mark.parametrize("anchor", ["#L565-L573", "#L565-573"])
def test_worktree_link_with_range(anchor):
    assert link(f"[x]({WT}/src/a.ts{anchor})") == f"[x]({blob('src/a.ts', '#L565-573')})"


def test_worktree_link_without_line_points_to_the_file():
    assert link(f"[a]({WT}/src/a.ts)") == f"[a]({blob('src/a.ts')})"


def test_worktree_link_with_colon_line_in_target():
    assert link(f"[a]({WT}/src/a.ts:7)") == f"[a]({blob('src/a.ts', '#L7')})"


def test_worktree_link_to_a_directory_uses_tree():
    assert link(f"[pages]({WT}/src/pages)") == f"[pages]({PROJECT}/-/tree/{HEAD}/src/pages)"


def test_worktree_link_to_file_deleted_by_the_mr_points_to_base():
    assert link(f"[old]({WT}/src/old.ts#L3)") == f"[old]({blob('src/old.ts', '#L3', BASE)})"


def test_worktree_link_to_unknown_path_keeps_only_the_text():
    assert link(f"[`gone.ts`]({WT}/gone.ts#L1)") == "`gone.ts`"


def test_worktree_link_without_file_list_trusts_head():
    out = link(f"[a]({WT}/src/x.ts#L2)", head_paths=None, base_paths=None)
    assert out == f"[a]({blob('src/x.ts', '#L2')})"


# -- inline code -------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "anchor"),
    [("src/pages/Wishlist.tsx", ""), ("src/pages/Wishlist.tsx:66", "#L66"),
     ("src/pages/Wishlist.tsx:565-573", "#L565-573"), ("./src/pages/Wishlist.tsx:66", "#L66")],
)
def test_inline_code_path_becomes_a_link(code, anchor):
    out = link(f"см. `{code}` тут")
    assert out == f"см. [`{code}`]({blob('src/pages/Wishlist.tsx', anchor)}) тут"


@pytest.mark.parametrize(
    "code", ["isFallback", "useGetGroup()", "src/missing.ts:4", "a: b", "src/a.ts:x"]
)
def test_inline_code_that_is_not_a_known_path_is_unchanged(code):
    assert link(f"x `{code}` y") == f"x `{code}` y"


def test_inline_code_of_deleted_file_points_to_base():
    assert link("`src/old.ts:9`") == f"[`src/old.ts:9`]({blob('src/old.ts', '#L9', BASE)})"


def test_inline_code_untouched_without_file_list():
    assert link("`src/a.ts:1`", head_paths=None, base_paths=None) == "`src/a.ts:1`"


def test_inline_code_with_worktree_path_becomes_relative_link():
    out = link(f"`{WT}/src/a.ts:5`")
    assert out == f"[`src/a.ts:5`]({blob('src/a.ts', '#L5')})"


def test_inline_code_inside_link_text_is_not_linked_twice():
    out = link(f"[`src/a.ts:13`]({WT}/src/a.ts#L13)")
    assert out.count("](") == 1


def test_non_ascii_and_spaces_are_url_encoded():
    out = link("`src/Страница группы/my page.tsx:3`")
    assert "(" + PROJECT + f"/-/blob/{HEAD}/src/%D0%A1" in out
    assert "my%20page.tsx#L3)" in out
    assert out.startswith("[`src/Страница группы/my page.tsx:3`]")


def test_directory_in_inline_code_uses_tree():
    assert link("`src/pages`") == f"[`src/pages`]({PROJECT}/-/tree/{HEAD}/src/pages)"


def test_http_links_are_left_alone():
    text = "[docs](https://example.com/a.ts#L3) and [rel](package.json)"
    out = link(text)
    assert "[docs](https://example.com/a.ts#L3)" in out
    assert f"[rel]({blob('package.json')})" in out


def test_other_text_is_unchanged():
    text = "### Major — заголовок\n\nТекст **жирный**, [не ссылка] и `code`.\n"
    assert link(text) == text


# -- no local paths -----------------------------------------------------------


def test_worktree_path_inside_fenced_block_becomes_relative():
    report = f"До\n\n```ts\n// {WT}/src/a.ts\nconst x = `src/a.ts:1`;\n```\nПосле `src/a.ts:1`\n"
    out = link(report)
    assert "```ts\n// src/a.ts\nconst x = `src/a.ts:1`;\n```\n" in out
    assert out.endswith(f"После [`src/a.ts:1`]({blob('src/a.ts', '#L1')})\n")
    assert no_worktree(out)


def test_tilde_fence_and_unclosed_fence():
    out = link(f"~~~\n`src/a.ts`\n~~~\n```\n{WT}/src/a.ts `src/a.ts`\n")
    assert out == f"~~~\n`src/a.ts`\n~~~\n```\nsrc/a.ts `src/a.ts`\n"


@pytest.mark.parametrize(
    "spelling",
    [
        WT,
        WT.replace("/", "\\"),
        "e" + WT[1:],
        "file:///" + WT,
        "/" + WT,
    ],
)
def test_any_spelling_of_the_worktree_is_recognised(spelling):
    out = link(f"[a]({spelling}/src/a.ts#L2) и в тексте {spelling}/src/a.ts")
    assert out == f"[a]({blob('src/a.ts', '#L2')}) и в тексте src/a.ts"


def test_url_encoded_worktree_link():
    wt = "E:/My Projects/wt"
    out = link("[a](E:/My%20Projects/wt/src/a.ts#L2)", worktree=wt)
    assert out == f"[a]({blob('src/a.ts', '#L2')})"


def test_worktree_in_link_text_and_plain_text_is_removed():
    out = link(f"[{WT}/src/a.ts]({WT}/src/a.ts) см. {WT}\\src\\a.ts и корень {WT}.")
    assert out.startswith(f"[src/a.ts]({blob('src/a.ts')})")
    assert "корень .." in out
    assert no_worktree(out)


def test_worktree_link_to_path_in_no_commit_leaves_no_local_path():
    out = link(f"[{WT}/nowhere.ts]({WT}/nowhere.ts)")
    assert out == "nowhere.ts"


def test_relative_part_keeps_its_case():
    out = link(f"[a]({WT.upper()}/src/pages/Wishlist.tsx)")
    assert out == f"[a]({blob('src/pages/Wishlist.tsx')})"


def test_posix_worktree_path():
    wt = "/home/ci/.review-agent/tmp/r1/worktree"
    out = link(f"[a]({wt}/src/a.ts#L1) {wt}/src/a.ts", worktree=wt)
    assert out == f"[a]({blob('src/a.ts', '#L1')}) src/a.ts"


def test_no_worktree_known_still_links_inline_code():
    assert link("`src/a.ts:1`", worktree=None) == f"[`src/a.ts:1`]({blob('src/a.ts', '#L1')})"


def test_line_range_end_not_after_start_keeps_start_only():
    assert link("`src/a.ts:9-9`") == f"[`src/a.ts:9-9`]({blob('src/a.ts', '#L9')})"
    assert re.search(r"#L9\)$", link("`src/a.ts:9-3`"))


# -- real reports --------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures" / "file_links"
FAKE_WT = "C:/Users/dev/.review-agent/tmp/1700000000-abcdef12/worktree"


def _unlink(text, url_prefix):
    """The text with every link whose target starts with `url_prefix` replaced by its text."""
    return re.sub(r"\[([^\]\n]*)\]\(" + re.escape(url_prefix) + r"[^)\n]*\)", r"\1", text)


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        (
            "worktree-links.in.md",
            [
                ("`src/pages/seller-group/ui/seller-group-page.tsx:13`",
                 "src/pages/seller-group/ui/seller-group-page.tsx#L13"),
                ("`src/pages/seller-group/ui/seller-group-page.tsx:19`",
                 "src/pages/seller-group/ui/seller-group-page.tsx#L19"),
                ("`src/entities/revision/model/types.ts:40`",
                 "src/entities/revision/model/types.ts#L40"),
            ],
        ),
        (
            "inline-paths.in.md",
            [
                ("`src/app/provider/WidgetRenderer/WidgetRendererProvider.tsx:34`",
                 "src/app/provider/WidgetRenderer/WidgetRendererProvider.tsx#L34"),
                ("`src/pages/WishlistPage/WishlistPage.tsx:66`",
                 "src/pages/WishlistPage/WishlistPage.tsx#L66"),
                ("`src/app/provider/WidgetRenderer/legacy/LegacyWidgetRenderer.tsx:565-573`",
                 "src/app/provider/WidgetRenderer/legacy/LegacyWidgetRenderer.tsx#L565-573"),
                ("`src/app/provider/WidgetRenderer/v2/WidgetRendererV2.tsx:54-57`",
                 "src/app/provider/WidgetRenderer/v2/WidgetRendererV2.tsx#L54-57"),
            ],
        ),
    ],
)
def test_real_report_gets_links_and_nothing_else_changes(fixture, expected):
    report = (FIXTURES / fixture).read_text(encoding="utf-8")
    head_paths = set(re.findall(r"src/[\w./-]+\.tsx?", report))

    out = link(report, head_paths=head_paths, base_paths=set(), worktree=FAKE_WT)

    for text, target in expected:
        assert f"[{text}]({PROJECT}/-/blob/{HEAD}/{target})" in out
    assert no_worktree(out) and FAKE_WT.lower() not in out.lower()
    # Findings, SEV, code blocks: byte for byte the same once links are unwrapped.
    assert _unlink(out, PROJECT) == _unlink(report, FAKE_WT)
