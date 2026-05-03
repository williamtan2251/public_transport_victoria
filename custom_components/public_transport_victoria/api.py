"""Public Transport Victoria API connector."""
from __future__ import annotations

import asyncio
import datetime
import hmac
import logging
import re
from hashlib import sha1
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

BASE_URL = "https://timetableapi.ptv.vic.gov.au"
DEPARTURES_PATH = "/v3/departures/route_type/{}/stop/{}/route/{}?direction_id={}&max_results={}"
DIRECTIONS_PATH = "/v3/directions/route/{}"
MAX_RESULTS = 10
MAX_DEPARTURES = 5
ROUTE_TYPES_PATH = "/v3/route_types"
ROUTES_PATH = "/v3/routes?route_types={}"
STOPS_PATH = "/v3/stops/route/{}/route_type/{}"
DISRUPTIONS_PATH = "/v3/disruptions?route_ids={}&route_types={}&disruption_status={}"
REQUEST_TIMEOUT = 15

_LOGGER = logging.getLogger(__name__)


class Connector:
    """Public Transport Victoria connector."""

    def __init__(
        self,
        hass: HomeAssistant,
        dev_id: str,
        api_key: str,
        route_type: int | None = None,
        route: int | None = None,
        direction: int | None = None,
        stop: int | None = None,
        route_type_name: str | None = None,
        route_name: str | None = None,
        direction_name: str | None = None,
        stop_name: str | None = None,
    ) -> None:
        """Init Public Transport Victoria connector."""
        self.hass = hass
        self.id = dev_id
        self.api_key = api_key
        self.route_type = route_type
        self.route = route
        self.direction = direction
        self.stop = stop
        self.route_type_name = route_type_name
        self.route_name = route_name
        self.direction_name = direction_name
        self.stop_name = stop_name
        self.departures: list[dict[str, Any]] = []
        self.disruptions_current: list[dict[str, Any]] = []
        self.departures_path: str | None = None
        if (
            route_type is not None
            and route is not None
            and direction is not None
            and stop is not None
        ):
            self.departures_path = DEPARTURES_PATH.format(
                route_type, stop, route, direction, MAX_RESULTS
            )

    async def _api_get(self, path: str) -> dict[str, Any] | None:
        """Make an authenticated GET request to the PTV API."""
        url = build_url(self.id, self.api_key, path)
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.json()
                    _LOGGER.warning(
                        "PTV API returned status %s for %s", response.status, path
                    )
                    return None
        except asyncio.TimeoutError:
            _LOGGER.warning("PTV API request timed out for %s", path)
            return None
        except aiohttp.ClientError as err:
            _LOGGER.warning("PTV API request failed for %s: %s", path, err)
            return None

    async def async_route_types(self) -> dict[int, str] | None:
        """Get route types from Public Transport Victoria API."""
        data = await self._api_get(ROUTE_TYPES_PATH)
        if data is None:
            return None
        _LOGGER.debug(data)
        return {r["route_type"]: r["route_type_name"] for r in data["route_types"]}

    async def async_routes(self, route_type: int) -> dict[int, str]:
        """Get routes from Public Transport Victoria API."""
        data = await self._api_get(ROUTES_PATH.format(route_type))
        if data is None:
            return {}
        _LOGGER.debug(data)

        route_list: list[tuple[int, Any, str]] = []
        for r in data["routes"]:
            route_number = r.get("route_number", "")
            try:
                sk = int(route_number) if route_number else float("inf")
            except ValueError:
                sk = (1, route_number)

            route_list.append((
                r["route_id"],
                sk,
                f"{route_number} - {r['route_name']}" if route_number else r["route_name"],
            ))

        def sort_key(x: tuple[int, Any, str]) -> tuple[int, Any]:
            sort_val = x[1]
            if isinstance(sort_val, tuple):
                return sort_val
            return (0, sort_val)

        route_list.sort(key=sort_key)
        return {route_id: display_name for route_id, _, display_name in route_list}

    async def async_directions(self, route: int) -> dict[int, str] | None:
        """Get directions from Public Transport Victoria API."""
        data = await self._api_get(DIRECTIONS_PATH.format(route))
        if data is None:
            return None
        _LOGGER.debug(data)
        return {r["direction_id"]: r["direction_name"] for r in data["directions"]}

    async def async_stops(self, route: int, route_type: int) -> dict[int, str] | None:
        """Get stops from Public Transport Victoria API."""
        data = await self._api_get(STOPS_PATH.format(route, route_type))
        if data is None:
            return None
        _LOGGER.debug(data)
        return {r["stop_id"]: r["stop_name"] for r in data["stops"]}

    async def async_run(self, run_id: int) -> dict[str, Any] | None:
        """Get run information from Public Transport Victoria API."""
        data = await self._api_get(f"/v3/runs/{run_id}")
        if data is None:
            return None
        _LOGGER.debug(data)
        runs = data.get("runs")
        if runs and len(runs) > 0:
            return runs[0]
        return None

    async def async_update(self) -> None:
        """Update the departure information."""
        data = await self._api_get(self.departures_path)
        if data is None:
            return

        _LOGGER.debug(data)
        now_utc = dt_util.utcnow()

        # Parse departures and compute local time strings
        departures_raw: list[dict[str, Any]] = []
        for r in data["departures"]:
            utc_str = r["estimated_departure_utc"] or r["scheduled_departure_utc"]
            if not utc_str:
                continue
            try:
                dep_utc = datetime.datetime.strptime(
                    utc_str, "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=datetime.timezone.utc)
            except Exception:
                continue
            r["_dep_utc"] = dep_utc
            r["departure"] = convert_utc_to_local(utc_str, self.hass)
            departures_raw.append(r)

        # Keep only future departures
        future = [d for d in departures_raw if d["_dep_utc"] > now_utc]

        # De-duplicate by minute, then sort, then cap to MAX_DEPARTURES
        seen_keys: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for d in future:
            key = d["_dep_utc"].strftime("%Y-%m-%dT%H:%M")
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(d)
        deduped.sort(key=lambda x: x["_dep_utc"])
        top = deduped[:MAX_DEPARTURES]

        # Fetch run info only for the kept departures
        run_ids = {d["run_id"] for d in top}
        run_map: dict[int, dict[str, Any]] = {}
        if run_ids:
            results = await asyncio.gather(
                *(self.async_run(rid) for rid in run_ids),
                return_exceptions=True,
            )
            for rid, result in zip(run_ids, results):
                if isinstance(result, dict):
                    run_map[rid] = result

        for d in top:
            run_info = run_map.get(d["run_id"])
            d["is_express"] = (
                run_info.get("express_stop_count", 0) > 0 if run_info else None
            )
            d.pop("_dep_utc", None)

        self.departures = top

        for departure in self.departures:
            _LOGGER.debug(departure)

    async def async_update_disruptions(self, disruption_status: int) -> list[dict[str, Any]]:
        """Update disruptions for the configured route.

        disruption_status: 0 = current, 1 = planned
        """
        if disruption_status != 0:
            return []

        disruptions_path = DISRUPTIONS_PATH.format(
            self.route, self.route_type, disruption_status
        )
        data = await self._api_get(disruptions_path)
        if data is None:
            return self.disruptions_current

        _LOGGER.debug(data)

        # Normalise disruptions list from possible response shapes
        disruptions_raw: list[dict[str, Any]] = []
        disruptions_data = data.get("disruptions")
        if isinstance(disruptions_data, list):
            disruptions_raw = disruptions_data
        elif isinstance(disruptions_data, dict):
            for value in disruptions_data.values():
                if isinstance(value, list):
                    disruptions_raw.extend(value)

        # Store a trimmed disruption object for attributes
        normalised: list[dict[str, Any]] = []
        for d in disruptions_raw:
            try:
                routes_list: list[dict[str, Any]] = []
                raw_routes = d.get("routes", [])
                for r in raw_routes if isinstance(raw_routes, list) else []:
                    if isinstance(r, dict):
                        routes_list.append({
                            "route_id": r.get("route_id"),
                            "route_type": r.get("route_type"),
                        })

                title = d.get("title")
                cleaned_title = _clean_title(title, self.route_name)

                from_src = d.get("from_date") or d.get("from_time")
                to_src = d.get("to_date") or d.get("to_time")
                from_local = _safe_local(from_src, self.hass)
                to_local = _safe_local(to_src, self.hass)
                period_relative = _relative_period(from_local, to_local, self.hass)
                state_text = _format_disruption_state(
                    title, cleaned_title, period_relative
                )

                normalised.append({
                    "disruption_id": d.get("disruption_id"),
                    "title": title,
                    "title_clean": cleaned_title,
                    "state_text": state_text,
                    "description": d.get("description"),
                    "disruption_status": d.get("disruption_status"),
                    "from_date": from_src,
                    "to_date": to_src,
                    "last_updated": d.get("last_updated"),
                    "url": d.get("url") or d.get("url_web"),
                    "routes": routes_list,
                    "severity": d.get("severity") or d.get("severity_level"),
                    "category": d.get("category") or d.get("disruption_type"),
                    "stops": [
                        s.get("stop_id")
                        for s in d.get("stops", [])
                        if isinstance(s, dict)
                    ],
                    "from_date_local": from_local,
                    "to_date_local": to_local,
                    "period_relative": period_relative,
                })
            except Exception as err:
                _LOGGER.debug("Error normalising disruption: %s", err)

        # Exclude non-service disruptions
        normalised = [n for n in normalised if not _should_exclude(n)]

        # Filter to only disruptions that explicitly reference the configured route
        route_id_str = str(self.route)
        route_type_str = str(self.route_type)
        filtered = [
            n for n in normalised
            if any(
                str(r.get("route_id")) == route_id_str
                and (r.get("route_type") is None or str(r.get("route_type")) == route_type_str)
                for r in n.get("routes", [])
            )
        ]

        # Filter out disruptions that ended more than 2 hours ago
        now_utc = dt_util.utcnow()
        cutoff = now_utc - datetime.timedelta(hours=2)
        filtered = [
            n for n in filtered
            if not n.get("to_date")
            or _parse_utc(n.get("to_date")) > cutoff
        ]

        self.disruptions_current = filtered

        for disruption in self.disruptions_current:
            _LOGGER.debug(disruption)

        return self.disruptions_current

    async def async_update_all(self) -> None:
        """Update departures and current disruptions concurrently."""
        await asyncio.gather(
            self.async_update(),
            self.async_update_disruptions(0),
        )


# --- Helper functions ---

_CARPARK_GROUPS = [
    ["temporary", "temporarily"],
    ["car park", "carpark"],
    ["closure", "closures", "closed"],
]
_PEDESTRIAN_GROUPS = [
    ["pedestrian"],
    ["access"],
    ["change", "changes", "changed"],
]
_ESCALATOR_WORDS = ["escalator", "elevator"]
_SERVICE_KEYWORDS = [
    "delay", "train", "tram", "bus", "service", "platform",
    "power", "outage", "reader", "payment", "eftpos",
    "top-up", "top up", "myki",
]


def _should_exclude(n: dict[str, Any]) -> bool:
    """Return True if disruption should be excluded (non-service related)."""
    combined_text = f"{(n.get('title') or '').lower()} {(n.get('description') or '').lower()}"
    if (_text_matches_all_groups(combined_text, _CARPARK_GROUPS)
            or _text_matches_all_groups(combined_text, _PEDESTRIAN_GROUPS)):
        return True
    if any(word in combined_text for word in _ESCALATOR_WORDS):
        if not any(keyword in combined_text for keyword in _SERVICE_KEYWORDS):
            return True
    return False


def _parse_utc(utc_str: str | None) -> datetime.datetime:
    """Parse UTC string to datetime, return epoch if parsing fails."""
    if not utc_str:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    try:
        return datetime.datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc
        )
    except Exception:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def _safe_local(
    utc: str | None, hass: HomeAssistant
) -> dict[str, str] | None:
    """Return both ISO and human local strings for a UTC time if present."""
    if not utc:
        return None
    try:
        d = datetime.datetime.strptime(utc, "%Y-%m-%dT%H:%M:%SZ")
        local_tz = dt_util.get_time_zone(hass.config.time_zone)
        d = d.replace(tzinfo=datetime.timezone.utc).astimezone(local_tz)
        return {
            "iso": d.isoformat(),
            "human": d.strftime("%Y-%m-%d %I:%M %p"),
        }
    except Exception:
        return None


def _text_matches_all_groups(text: str, groups: list[list[str]]) -> bool:
    """Return True if for every group, at least one phrase appears in text."""
    if not text:
        return False
    hay = text.lower()
    for group in groups:
        if not any(phrase in hay for phrase in group):
            return False
    return True


def _clean_title(title: str | None, route_name: str | None) -> str | None:
    """Remove leading '<route_name...> lines:' prefix if present."""
    if not title:
        return title
    t = title.strip()
    rn = (route_name or "").strip()
    if not rn:
        return t
    lower = t.lower()
    rn_lower = rn.lower()
    colon = lower.find(":")
    if colon != -1:
        prefix = lower[:colon].strip()
        if (" line" in prefix or " lines" in prefix) and (rn_lower in prefix):
            return t[colon + 1 :].lstrip()
    return t


_TITLE_DATE_RANGE_RE = re.compile(
    r"from\s+\w+.*?\s+(?:to|until)\s+\w+.*?(?:\s|$)",
    re.IGNORECASE,
)


def _format_disruption_state(
    title: str | None,
    title_clean: str | None,
    period_relative: str | None,
) -> str:
    """Build a one-line summary of a disruption for sensor state.

    When the raw title already contains a "from <date> to/until <date>" range
    and we have a friendlier today/tomorrow period, replace the title's range
    with the relative one to avoid double-rendering dates.
    """
    raw = (title or "Disruption").strip()
    base = (title_clean or raw).strip()
    if not period_relative:
        return base
    if (
        period_relative.startswith("from ")
        and " until " in period_relative
        and _TITLE_DATE_RANGE_RE.search(raw)
    ):
        if " until " in base:
            base = base.split(" until ", 1)[0]
        elif " to " in base:
            base = base.split(" to ", 1)[0]
            if " from " in base:
                base = base.replace(" from ", " ", 1)
        until_part = period_relative.split(" until ", 1)[1]
        return f"{base.rstrip()} until {until_part}"
    return f"{base} — {period_relative}"


def _relative_period(
    from_local: dict[str, str] | None,
    to_local: dict[str, str] | None,
    hass: HomeAssistant,
) -> str | None:
    """Build a human-friendly relative period string using local ISO datetimes."""
    try:
        local_tz = dt_util.get_time_zone(hass.config.time_zone)
        today = datetime.datetime.now(local_tz).date()

        def _label(local_map: dict[str, str] | None) -> str | None:
            if not local_map or not local_map.get("iso"):
                return None
            dt_obj = datetime.datetime.fromisoformat(local_map["iso"]).astimezone(local_tz)
            d = dt_obj.date()
            if d == today:
                return "today"
            if d == today + datetime.timedelta(days=1):
                return "tomorrow"
            return dt_obj.strftime("%A %d %B")

        start = _label(from_local)
        end = _label(to_local)
        if start and end and start != end:
            return f"from {start} until {end}"
        return None
    except Exception:
        return None


def build_url(dev_id: str, api_key: str, request: str) -> str:
    """Build a signed PTV API URL."""
    request = request + ("&" if ("?" in request) else "?")
    raw = request + f"devid={dev_id}"
    hashed = hmac.new(api_key.encode("utf-8"), raw.encode("utf-8"), sha1)
    signature = hashed.hexdigest()
    url = BASE_URL + raw + f"&signature={signature}"
    _LOGGER.debug(url)
    return url


def convert_utc_to_local(utc: str, hass: HomeAssistant) -> str:
    """Convert UTC to Home Assistant local time."""
    d = datetime.datetime.strptime(utc, "%Y-%m-%dT%H:%M:%SZ")
    local_tz = dt_util.get_time_zone(hass.config.time_zone)
    d = d.replace(tzinfo=datetime.timezone.utc).astimezone(local_tz)
    return d.strftime("%I:%M %p")
