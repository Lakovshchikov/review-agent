"""Interactive subscription quota readings around one review (design.md decision 6).

Only for a single MR or commit range reviewed from a console: the user
reads the current "percent used" of each limit window off the
provider's UI before the review and again right after the harness
finishes. There is no automatic source for these numbers yet (AGENTS.md
backlog), so asking is the honest way to tie a share to one review.
"""

from __future__ import annotations

from typing import Callable

from review_agent.usage_ledger import QuotaPrompt

_STAGES = {"before": "до ревью", "after": "после ревью"}


def make_quota_prompt(
    windows: list[str],
    provider_name: str,
    *,
    input_fn: Callable[[str], str] | None = None,
    output_fn: Callable[[str], object] | None = None,
) -> QuotaPrompt:
    def ask(stage: str) -> dict[str, float]:
        # Resolved at call time, so a console swapped in later is honored.
        read = input_fn or input
        say = output_fn or print
        say(
            f"Замер квоты {provider_name} ({_STAGES.get(stage, stage)}): сколько % лимита "
            "использовано сейчас (см. UI подписки). Enter — пропустить."
        )
        readings: dict[str, float] = {}
        for window in windows:
            while True:
                try:
                    answer = read(f"  окно {window}, %: ").strip()
                except EOFError:
                    return readings
                if not answer:
                    break
                try:
                    value = float(answer.replace(",", ".").rstrip("%").strip())
                except ValueError:
                    value = -1.0
                if not 0 <= value <= 100:
                    say("  Нужно число от 0 до 100 (или Enter, чтобы пропустить).")
                    continue
                readings[window] = int(value) if value.is_integer() else value
                break
        return readings

    return ask
