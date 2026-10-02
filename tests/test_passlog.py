import sys

from review_agent.passlog import pass_log


def _read(log):
    return log.path.read_text(encoding="utf-8")


def test_log_file_name_location_and_cyrillic(tmp_path):
    shown = []
    with pass_log(tmp_path / "logs", output_fn=shown.append, error_fn=shown.append) as log:
        log.info("Найдено MR: «Исправить корзину» — 1")
        log.warning("что-то не так")
        log.error("ошибка конфигурации")
        log.file_only("только в файл")

    assert log.path.parent == tmp_path / "logs"
    assert log.path.name.startswith("poll-") and log.path.name.endswith(".log")
    text = _read(log)
    assert "INFO Найдено MR: «Исправить корзину» — 1" in text
    assert "WARNING что-то не так" in text
    assert "ERROR ошибка конфигурации" in text
    assert "только в файл" in text
    assert "только в файл" not in shown
    assert shown[0] == "Найдено MR: «Исправить корзину» — 1"


def test_missing_console_streams_do_not_break_logging(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    with pass_log(tmp_path) as log:  # default console writers: print / stderr
        log.info("в консоль некуда")
        log.error("и ошибку тоже")
    text = _read(log)
    assert "в консоль некуда" in text and "и ошибку тоже" in text


def test_console_encoding_error_is_swallowed(tmp_path):
    def cp1252_console(message):
        message.encode("cp1252")  # raises UnicodeEncodeError on Cyrillic

    with pass_log(tmp_path, output_fn=cp1252_console, error_fn=cp1252_console) as log:
        log.info("Кириллица в консоль cp1252")
        log.error("Ошибка тоже")
    assert "Кириллица в консоль cp1252" in _read(log)


def test_two_passes_get_separate_files(tmp_path):
    with pass_log(tmp_path, output_fn=lambda m: None) as first:
        first.info("первый")
    with pass_log(tmp_path, output_fn=lambda m: None) as second:
        second.info("второй")
    if first.path == second.path:  # same second, same pid - appended, not overwritten
        assert "первый" in _read(second) and "второй" in _read(second)
    else:
        assert "второй" not in _read(first)
