"""Create the checkout's local configuration without replacing an existing .env."""
from pathlib import Path


def initialize(root: Path) -> bool:
    template = (root / ".env.example").read_bytes()
    try:
        with (root / ".env").open("xb") as target:
            target.write(template)
    except FileExistsError:
        return False
    return True


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    if initialize(root):
        print("Created .env. Set GEMINI_API_KEY locally, then start the backend.")
    else:
        print("Existing .env preserved unchanged. No secret values were read or printed.")
