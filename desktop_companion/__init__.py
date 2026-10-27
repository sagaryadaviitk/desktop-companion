"""Desktop Companion runtime package."""

from pathlib import Path

# Keep machine configuration, private state and assets at the checkout root.
ROOT = Path(__file__).resolve().parent.parent
