"""
fetch_seatgeek.py

SeatGeek API fallback for MLB teams not listed on Ticketmaster.
Uses the SeatGeek Events + Listings API to fetch resale inventory.

Requires SEATGEEK_CLIENT_ID in .env (free at seatgeek.com/account/develop).
"""

import os
import requests
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

SG_CLIENT_ID = os.getenv("SEATGEEK_CLIENT_ID", "")
SG_API_BASE  = "https://api.seatgeek.com/2"

# SeatGeek performer slugs for all 30 MLB teams
SG_SLUGS = {
    "diamondbacks": "arizona-diamondbacks",
    "braves":       "atlanta-braves",
    "orioles":      "baltimore-orioles",
    "redsox":       "boston-red-sox",
    "cubs":         "chicago-cubs",
    "whitesox":     "chicago-white-sox",
    "reds":         "cincinnati-reds",
    "guardians":    "cleveland-guardians",
    "rockies":      "colorado-rockies",
    "tigers":       "detroit-tigers",
    "astros":       "houston-astros",
    "royals":       "kansas-city-royals",
    "angels":       "los-angeles-angels",
    "dodgers":      "los-angeles-dodgers",
    "marlins":      "miami-marlins",
    "brewers":      "milwaukee-brewers",
    "twins":        "minnesota-twins",
    "mets":         "new-york-mets",
    "yankees":      "new-york-yankees",
    "athletics":    "athletics",
    "phillies":     "philadelphia-phillies",
    "pirates":      "pittsburgh-pirates",
    "padres":       "san-diego-padres",
    "giants":       "san-francisco-giants",
    "mariners":     "seattle-mariners",
    "cardinals":    "st-louis-cardinals",
    "rays":         "tampa-bay-rays",
    "rangers":      "texas-rangers",
    "bluejays":     "toronto-blue-jays",
    "nationals":    "washington-nationals",
}


def _params(extra: dict = {}) -> dict:
    p = {"per_page": 5000}
    if SG_CLIENT_ID:
        p["client_id"] = SG_CLIENT_ID
    p.update(extra)
    return p


def find_seatgeek_event(team_slug: str, game_date: str) -> dict:
    """
    Find the SeatGeek event for an MLB team on a specific date (YYYY-MM-DD).
    Returns the raw SeatGeek event dict.
    Raises RuntimeError if not found or client_id not configured.
    """
    if not SG_CLIENT_ID:
        raise RuntimeError("SEATGEEK_CLIENT_ID not set — skipping SeatGeek lookup.")

    sg_slug = SG_SLUGS.get(team_slug)
    if not sg_slug:
        raise RuntimeError(f"No SeatGeek slug configured for '{team_slug}'.")

    resp = requests.get(
        f"{SG_API_BASE}/events",
        params=_params({
            "performers.slug":     sg_slug,
            "datetime_local.gte":  f"{game_date}T00:00:00",
            "datetime_local.lte":  f"{game_date}T23:59:59",
            "type":                "mlb",
        }),
        timeout=15,
    )
    resp.raise_for_status()
    events = resp.json().get("events", [])

    # Prefer home games where our team is listed as home performer
    for event in events:
        performers = [p.get("slug", "") for p in event.get("performers", [])]
        if sg_slug in performers:
            return event

    if events:
        return events[0]

    raise RuntimeError(f"No SeatGeek event found for '{team_slug}' on {game_date}.")


def fetch_seatgeek_listings(event_id: int, scraped_at: str) -> list[dict]:
    """
    Fetch all resale listings for a SeatGeek event.
    Returns rows in the same format as parse_facet() — compatible with save_csv().
    """
    resp = requests.get(
        f"{SG_API_BASE}/listings",
        params=_params({"event_id": event_id}),
        timeout=30,
    )
    resp.raise_for_status()
    listings = resp.json().get("listings", [])

    rows = []
    for listing in listings:
        rows.append({
            "listing_id": str(listing.get("id", "")),
            "section":    listing.get("section", ""),
            "row":        listing.get("row", ""),
            "qty":        listing.get("quantity", 0),
            "price_usd":  listing.get("price", {}).get("amount", 0),
            "scraped_at": scraped_at,
            "source":     "seatgeek",
        })
    return rows


def scrape_seatgeek(team_slug: str, game_date: str) -> tuple[dict, list[dict]]:
    """
    High-level: find the event and fetch listings in one call.
    Returns (event_meta, rows).
    event_meta has: name, game_date, first_pitch_utc, venue, url
    """
    event    = find_seatgeek_event(team_slug, game_date)
    event_id = event["id"]

    dt_local  = event.get("datetime_local", "")
    dt_utc    = event.get("datetime_utc", "")
    name      = event.get("title", "")
    venue     = event.get("venue", {}).get("name", "")
    city      = event.get("venue", {}).get("city", "")
    url       = event.get("url", f"https://seatgeek.com/{event_id}")

    first_pitch_utc = None
    if dt_utc:
        try:
            first_pitch_utc = datetime.fromisoformat(dt_utc.replace("Z", "+00:00"))
        except ValueError:
            pass

    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = fetch_seatgeek_listings(event_id, scraped_at)

    print(f"  [SeatGeek] {name} @ {venue} — {len(rows)} listings")

    event_meta = {
        "sg_event_id":    event_id,
        "name":           name,
        "game_date":      game_date,
        "first_pitch_utc": first_pitch_utc,
        "venue":          venue,
        "city":           city,
        "url":            url,
    }
    return event_meta, rows
