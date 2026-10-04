from conftest import _git

from review_agent.review_stats import ChangeSize, count_findings, measure_change


def test_measure_change_counts_files_lines_and_commits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "keep.txt").write_text("a\nb\nc\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # commit 1: +2 -1 in keep.txt
    (repo / "keep.txt").write_text("a\nB\nc\nd\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "edit")
    # commit 2: new text file (+3) and a binary file
    (repo / "new.ts").write_text("x\ny\nz\n", encoding="utf-8")
    (repo / "logo.png").write_bytes(bytes(range(256)) * 4)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add")
    # commit 3: delete nothing but touch new.ts (-1)
    (repo / "new.ts").write_text("x\ny\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "trim")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    assert measure_change(repo, base, head) == ChangeSize(files=3, lines_added=4, lines_deleted=1, commits=3)


def test_measure_change_unknown_commit_gives_none(git_repo_with_base_and_head):
    fixture = git_repo_with_base_and_head
    assert measure_change(fixture["repo"], "0" * 40, fixture["head_sha"]) is None


REPORT = """# Код-ревью MR !544

## Итог
Найдено 5 замечаний: 3 Major, 2 Minor. Major проблемы связаны с формой.

## Замечания

### 1. [Major] Повторная отправка формы через «Вперёд»
Файл `src/form.ts`. Это major риск для пользователя.

### 2. **SEV: Major** — дублирующий POST
...

3. Major: гонка при параллельной отправке
   - обсуждение: minor детали ниже не являются отдельной находкой

- (Minor) утечка старого ключа кэша
- SEV Minor — лишний ререндер

| Файл | SEV |
|------|-----|
"""


def test_count_findings_counts_labels_not_prose():
    assert count_findings(REPORT) == {"blocker": 0, "major": 3, "minor": 2}


def test_count_findings_blocker_and_bold_variants():
    report = "**Blocker** падение на пустой корзине\n__minor__ опечатка\nSEV=blocker: XSS"
    assert count_findings(report) == {"blocker": 2, "major": 0, "minor": 1}


def test_count_findings_empty_and_absent_report():
    assert count_findings("") == {"blocker": 0, "major": 0, "minor": 0}
    assert count_findings(None) is None
