"""Совместимый запуск: python run.py 'вопрос'."""
from local_agent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())