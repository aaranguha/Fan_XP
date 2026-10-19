"""Throwaway, read-only helper: prints the next real on-sale home-game event
URL for a given team keyword/city, using the same Discovery API lookup as
batch_capture_stadium_geometry.py. Used once to source a live validation URL
for smoke-test.yml; not meant to be merged."""
import sys
from batch_capture_stadium_geometry import find_home_event_url

if __name__ == "__main__":
    keyword, city = sys.argv[1], sys.argv[2]
    url = find_home_event_url(keyword, city)
    print(f"RESULT_URL={url}")
