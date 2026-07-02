from pathlib import Path

_PROMPT_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    return (_PROMPT_DIR / f"{name}.md").read_text()
