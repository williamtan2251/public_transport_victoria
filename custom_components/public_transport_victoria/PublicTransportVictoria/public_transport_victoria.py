"""Public Transport Victoria API connector."""
import aiohttp
import asyncio
import datetime
import hmac
import logging
from hashlib import sha1

from homeassistant.util.dt import get_time_zone
from homeassistant.helpers.aiohttp_client import async_get_clientsession

BASE_URL = "https://timetableapi.ptv.vic.gov.au"
DEPARTURES_PATH = "/v3/departures/route_type/{}/stop/{}/route/{}?direction_id={}&max_results={}"
DIRECTIONS_PATH = "/v3/directions/route/{}"
MAX_RESULTS = 10
ROUTE_TYPES_PATH = "/v3/route_types"
ROUTES_PATH = "/v3/routes?route_types={}"
STOPS_PATH = "/v3/stops/route/{}/route_type/{}"
DISRUPTIONS_PATH = "/v3/disruptions?route_ids={}&route_types={}&disruption_status={}"

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=30, sock_read=60, sock_connect=30)
DEDUP_LIMIT = 5  # Number of departures to keep for UI
ATTR_ATTRIBUTION = "Data provided by Public Transport Victoria"

_LOGGER = logging.getLogger(__name__)

class Connector:
    """Public Transport Victoria connector."""

    manufacturer = "Demonstration Corp"

    def __init__(self, hass, id, api_key, route_type=None, route=None,
                 direction=None, stop=None, route_type_name=None,
                 route_name=None, direction_name=None, stop_name=None):
        """Init Public Transport Victoria connector."""
        self.hass = hass
        self.session = async_get_clientsession(hass)
        self.id = id
        self.api_key = api_key
        self.route_type = route_type
        self.route = route
        self.direction = direction
        self.stop = stop
        self.route_type_name = route_type_name
        self.route_name = route_name
        self.direction_name = direction_name
        self.stop_name = stop_name
        self.disruptions_current = []
        self.departures = []

    async def _init(self) -> None:
        """Async Init Public Transport Victoria connector."""
        self.departures_path = DEPARTURES_PATH.format(
            self.route_type, self.stop, self.route, self.direction, MAX_RESULTS
        )
        await self.async_update()

    async def async_route_types(self) -> dict:
        """Get route types from Public Transport Victoria API."""
        url = build_URL(self.id, self.api_key, ROUTE_TYPES_PATH)
        async with self.session.get(url, timeout=DEFAULT_TIMEOUT) as response:
            if response.status == 200:
                data = await response.json()
                _LOGGER.debug(data)
                route_types = {r["route_type"]: r["route_type_name"] for r in data.get("route_types", [])}
                route_types["attribution"] = ATTR_ATTRIBUTION
                return route_types
        return {"attribution": ATTR_ATTRIBUTION}

    async def async_routes(self, route_type: int) -> dict:
        """Get routes from Public Transport Victoria API."""
        url = build_URL(self.id, self.api_key, ROUTES_PATH.format(route_type))
        headers = {
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive'
        }
        async with self.session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
            if response.status == 200:
                data = await response.json()
                route_list = []
                for r in data.get("routes", []):
                    route_number = r.get("route_number", "")
                    try:
                        sort_key = int(route_number) if route_number else float('inf')
                    except ValueError:
                        sort_key = (1, route_number)
                    route_list.append((
                        r["route_id"],
                        sort_key,
                        f"{route_number} - {r['route_name']}" if route_number else r["route_name"]
                    ))
                # Improved deduplication and sorting
                route_list.sort(key=lambda x: (isinstance(x[1], tuple), x[1]))
                routes = {route_id: display_name for route_id, _, display_name in route_list}
                routes["attribution"] = ATTR_ATTRIBUTION
                self.route_type = route_type
                return routes
        return {"attribution": ATTR_ATTRIBUTION}

    async def async_directions(self, route: int) -> dict:
        """Get directions from Public Transport Victoria API."""
        url = build_URL(self.id, self.api_key, DIRECTIONS_PATH.format(route))
        async with self.session.get(url, timeout=DEFAULT_TIMEOUT) as response:
            if response.status == 200:
                data = await response.json()
                _LOGGER.debug(data)
                directions = {r["direction_id"]: r["direction_name"] for r in data.get("directions", [])}
                directions["attribution"] = ATTR_ATTRIBUTION
                self.route = route
                return directions
        return {"attribution": ATTR_ATTRIBUTION}

    async def async_stops(self, route: int) -> dict:
        """Get stops from Public Transport Victoria API."""
        url = build_URL(self.id, self.api_key, STOPS_PATH.format(route, self.route_type))
        async with self.session.get(url, timeout=DEFAULT_TIMEOUT) as response:
            if response.status == 200:
                data = await response.json()
                _LOGGER.debug(data)
                stops = {r["stop_id"]: r["stop_name"] for r in data.get("stops", [])}
                stops["attribution"] = ATTR_ATTRIBUTION
                self.route = route
                return stops
        return {"attribution": ATTR_ATTRIBUTION}

    async def async_update(self) -> None:
        """Update the departure information."""
        url = build_URL(self.id, self.api_key, self.departures_path)
        async with self.session.get(url, timeout=DEFAULT_TIMEOUT) as response:
            if response.status == 200:
                data = await response.json()
                _LOGGER.debug(data)
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                departures_raw = []
                # Parallel fetch run info for all departures
                run_ids = [r["run_id"] for r in data.get("departures", []) if r.get("run_id")]
                run_infos = await self._fetch_runs_parallel(run_ids)
                run_info_map = {info["run_id"]: info for info in run_infos if info}
                for r in data.get("departures", []):
                    utc_str = r.get("estimated_departure_utc") or r.get("scheduled_departure_utc")
                    if not utc_str:
                        continue
                    try:
                        dep_utc = datetime.datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                    except Exception:
                        continue
                    r["_dep_utc"] = dep_utc
                    r["departure"] = convert_utc_to_local(utc_str, self.hass)
                    run_info = run_info_map.get(r["run_id"])
                    r["is_express"] = run_info.get("express_stop_count", 0) > 0 if run_info else None
                    departures_raw.append(r)
                # Deduplicate by minute using dict
                deduped = {d["_dep_utc"].strftime("%Y-%m-%dT%H:%M"): d for d in departures_raw if d["_dep_utc"] > now_utc}
                # Sort and cap to DEDUP_LIMIT for UI
                sorted_deps = sorted(deduped.values(), key=lambda x: x["_dep_utc"])
                self.departures = sorted_deps[:DEDUP_LIMIT]
                for departure in self.departures:
                    departure["attribution"] = ATTR_ATTRIBUTION
                    _LOGGER.debug(departure)

    async def _fetch_runs_parallel(self, run_ids) -> list:
        """Fetch run info for all run_ids in parallel."""
        tasks = [self.async_run(run_id) for run_id in run_ids]
        return await asyncio.gather(*tasks)

    async def async_run(self, run_id: int) -> dict:
        """Get run information from Public Transport Victoria API."""
        url = build_URL(self.id, self.api_key, f"/v3/runs/{run_id}")
        async with self.session.get(url, timeout=DEFAULT_TIMEOUT) as response:
            if response.status == 200:
                data = await response.json()
                _LOGGER.debug(data)
                if data.get("runs") and len(data["runs"]) > 0:
                    run = data["runs"][0]
                    run["attribution"] = ATTR_ATTRIBUTION
                    return run
        return {}

    async def async_update_disruptions(self, disruption_status: int) -> list:
        """Update disruptions for the configured route.
        disruption_status: 0 = current, 1 = planned
        Returns a list of disruptions with attribution.
        """
        if disruption_status != 0:
            return []
        disruptions_path = DISRUPTIONS_PATH.format(self.route, self.route_type, disruption_status)
        url = build_URL(self.id, self.api_key, disruptions_path)
        async with self.session.get(url, timeout=DEFAULT_TIMEOUT) as response:
            if response.status == 200:
                data = await response.json()
                _LOGGER.debug(data)
                disruptions_raw = []
                if isinstance(data.get("disruptions"), list):
                    disruptions_raw = data.get("disruptions", [])
                elif isinstance(data.get("disruptions"), dict):
                    for value in data.get("disruptions", {}).values():
                        if isinstance(value, list):
                            disruptions_raw.extend(value)
                normalised = []
                # Parallel run info fetch for disruptions if needed (example, not used here)
                for d in disruptions_raw:
                    try:
                        routes_list = []
                        for r in d.get("routes", []) if isinstance(d.get("routes"), list) else []:
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
                        disruption_obj = {
                            "disruption_id": d.get("disruption_id"),
                            "title": title,
                            "title_clean": cleaned_title,
                            "description": d.get("description"),
                            "disruption_status": d.get("disruption_status"),
                            "from_date": from_src,
                            "to_date": to_src,
                            "last_updated": d.get("last_updated"),
                            "url": d.get("url") or d.get("url_web"),
                            "routes": routes_list,
                            "severity": d.get("severity") or d.get("severity_level"),
                            "category": d.get("category") or d.get("disruption_type"),
                            "stops": [s.get("stop_id") for s in d.get("stops", []) if isinstance(s, dict)],
                            "from_date_local": from_local,
                            "to_date_local": to_local,
                            "period_relative": period_relative,
                            "attribution": ATTR_ATTRIBUTION,
                        }
                        normalised.append(disruption_obj)
                    except Exception as err:
                        _LOGGER.debug("Error normalising disruption: %s", err)
                # Filtering logic unchanged
                carpark_groups = [
                    ["temporary", "temporarily"],
                    ["car park", "carpark"],
                    ["closure", "closures", "closed"],
                ]
                pedestrian_groups = [
                    ["pedestrian"],
                    ["access"],
                    ["change", "changes", "changed"],
                ]
                escalator_words = ["escalator", "elevator"]
                service_keywords = [
                    "delay", "train", "tram", "bus", "service", "platform", "power", "outage",
                    "reader", "payment", "eftpos", "top-up", "top up", "myki",
                ]
                def _should_exclude(n):
                    combined_text = f"{(n.get('title') or '').lower()} {(n.get('description') or '').lower()}"
                    if (_text_matches_all_groups(combined_text, carpark_groups) or
                            _text_matches_all_groups(combined_text, pedestrian_groups)):
                        return True
                    if any(word in combined_text for word in escalator_words):
                        if not any(keyword in combined_text for keyword in service_keywords):
                            return True
                    return False
                normalised = [n for n in normalised if not _should_exclude(n)]
                route_id_str = str(self.route)
                route_type_str = str(self.route_type)
                filtered = [
                    n for n in normalised
                    if any(
                        str(r.get("route_id")) == route_id_str and
                        (r.get("route_type") is None or str(r.get("route_type")) == route_type_str)
                        for r in n.get("routes", [])
                    )
                ]
                if disruption_status == 0:
                    now_utc = datetime.datetime.now(datetime.timezone.utc)
                    cutoff = now_utc - datetime.timedelta(hours=2)
                    filtered = [
                        n for n in filtered
                        if not n.get("to_date") or
                        _parse_utc(n.get("to_date")) > cutoff
                    ]
                    self.disruptions_current = filtered
                    for disruption in self.disruptions_current:
                        _LOGGER.debug(disruption)
                return filtered
        return []

    async def async_update_all(self) -> None:
        """Update departures and current disruptions only."""
        await asyncio.gather(
            self.async_update(),
            self.async_update_disruptions(0)
        )

# ...existing utility functions unchanged...
