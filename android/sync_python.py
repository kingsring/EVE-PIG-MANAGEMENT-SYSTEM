"""Copy desktop Python sources and item index into the Android project."""
from pathlib import Path
import shutil

android_dir = Path(__file__).resolve().parent
repo_dir = android_dir.parent
target_python = android_dir / "app" / "src" / "main" / "python" / "app"
target_index = android_dir / "app" / "src" / "main" / "assets" / "data" / "item_index.db"

if target_python.exists():
    shutil.rmtree(target_python)
shutil.copytree(
    repo_dir / "app",
    target_python,
    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
)

target_index.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(repo_dir / "data" / "item_index.db", target_index)
print(f"Synced Python sources to {target_python}")
print(f"Synced item index to {target_index}")
