import os
import sys

# Allow tests to `import fanxp_api` regardless of the directory pytest is
# invoked from, matching the pattern in scraping/tests/conftest.py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
