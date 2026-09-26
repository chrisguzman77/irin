import sys
from pathlib import Path

# backend/ on sys.path so `import app` works from any cwd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
