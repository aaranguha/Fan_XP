import os
import sys

# Allow tests to `import compare_snapshots`, `import teams`, etc. regardless
# of the directory pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
