from review_agent.cli import main

if __name__ == "__main__":
    # The exit code matters: Task Scheduler shows it as the task's result
    # (0 ok / 1 some MR failed / 2 not started) when run as `pythonw -m review_agent`.
    raise SystemExit(main())
