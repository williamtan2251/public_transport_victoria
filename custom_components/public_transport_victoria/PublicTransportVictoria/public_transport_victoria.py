"""Public Transport Victoria API connector."""
import aiohttp
import asyncio
import datetime
import hmac
import logging
from hashlib import sha1
from homeassistant.util.dt import get_time_zone

BASE_URL = "https://timetableapi.ptv.vic.gov.au"
DEPARTURES_PATH = "/v3/departures/route_type/{}/stop/{}/route/{}?direction_id={}&max_results={}"
DIRECTIONS_PATH = "/v3/directions/route/{}"
ROUTE_TYPES_PATH = "/v3/route_types"
ROUTES_PATH = "/v3/routes?route_types={}"
STOPS_PATH = "/v3/stops/route/{}/route_type/{}"
MAX_RESULTS = 10
DISRUPTIONS_PATH = "/v3/disruptions?route_ids={}&route_types={}&disruption_status={}"

_LOGGER = logging.getLogger(__name__)

class Connector:
    """Public Transport Victoria connector."""

    manufacturer = "Demonstration Corp"

    def __init__(self, hass, id, api_key, route_type=None, route=None,
                 direction=None, stop=None, route_type_name=None,
                 route_name=None, direction_name=None, stop_name=None):
        """Init Public Transport Victoria connector."""
        self.hass = hass
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
        self.disruptions_planned = []
        self._run_cache = {}
        self._run_cache_timestamp = None

    async def _init(self):
        """Async Init Public Transport Victoria connector."""
        self.departures_path = DEPARTURES_PATH.format(
            self.route_type, self.stop, self.route, self.direction, MAX_RESULTS
        )
        await self.async_update()

    async def async_route_types(self):
        """Get route types from Public Transport Victoria API."""
        url = build_URL(self.id, self.api_key, ROUTE_TYPES_PATH)

        async with aiohttp.ClientSession() as session:
            response = await session.get(url)

        if response is not None and response.status == 200:
            response = await response.json()
            _LOGGER.debug(response)
            route_types = {}
            for r in response["route_types"]:
                route_types[r["route_type"]] = r["route_type_name"]

            return route_types

    async def async_routes(self, route_type):
        """Get routes from Public Transport Victoria API."""
        url = build_URL(self.id, self.api_key, ROUTES_PATH.format(route_type))

        async with aiohttp.ClientSession() as session:
            response = await session.get(url)

        if response is not None and response.status == 200:
            response = await response.json()
            _LOGGER.debug(response)
            routes = {}
            for r in response["routes"]:
                routes[r["route_id"]] = r["route_name"]

            self.route_type = route_type

            return routes

    async def async_directions(self, route):
        """Get directions from Public Transport Victoria API."""
        url = build_URL(self.id, self.api_key, DIRECTIONS_PATH.format(route))

        async with aiohttp.ClientSession() as session:
            response = await session.get(url)

        if response is not None and response.status == 200:
            response = await response.json()
            _LOGGER.debug(response)
            directions = {}
            for r in response["directions"]:
                directions[r["direction_id"]] = r["direction_name"]

            self.route = route

            return directions

    async def async_stops(self, route):
        """Get stops from Public Transport Victoria API."""
        url = build_URL(self.id, self.api_key, STOPS_PATH.format(route, self.route_type))

        async with aiohttp.ClientSession() as session:
            response = await session.get(url)

        if response is not None and response.status == 200:
            response = await response.json()
            _LOGGER.debug(response)
            stops = {}
            for r in response["stops"]:
                stops[r["stop_id"]] = r["stop_name"]

            self.route = route

            return stops

    async def async_update(self):
        """Update the departure information."""
        url = build_URL(self.id, self.api_key, self.departures_path)

        async with aiohttp.ClientSession() as session:
            response = await session.get(url)

        if response is not None and response.status == 200:
            response = await response.json()
            _LOGGER.debug(response)
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            
            # Clear run cache if it's from a previous update (older than 5 minutes)
            if (self._run_cache_timestamp is None or 
                (now_utc - self._run_cache_timestamp).total_seconds() > 300):
                self._run_cache.clear()
                self._run_cache_timestamp = now_utc
            
            # Build list with computed local string and keep parsed UTC for filtering
            departures_raw = []
            for r in response["departures"]:
                utc_str = r["estimated_departure_utc"] or r["scheduled_departure_utc"]
                if not utc_str:
                    continue
                try:
                    dep_utc = datetime.datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                except Exception:
                    continue
                r["_dep_utc"] = dep_utc
                r["departure"] = convert_utc_to_local(utc_str, self.hass)
                
                # Get express status with caching
                run_id = r.get("run_id")
                if run_id:
                    if run_id in self._run_cache:
                        run_info = self._run_cache[run_id]
                    else:
                        run_info = await self.async_run(run_id)
                        if run_info:
                            self._run_cache[run_id] = run_info
                    
                    if run_info:
                        r["is_express"] = run_info.get("express_stop_count", 0) > 0
                    else:
                        r["is_express"] = None
                else:
                    r["is_express"] = None
                    
                departures_raw.append(r)

            # Keep only future departures
            future = [d for d in departures_raw if d["_dep_utc"] > now_utc]

            # De-duplicate by minute to avoid identical consecutive times
            seen_keys = set()
            deduped = []
            for d in future:
                key = d["_dep_utc"].strftime("%Y-%m-%dT%H:%M")
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                deduped.append(d)

            # Sort and cap to first 5 for UI
            deduped.sort(key=lambda x: x["_dep_utc"]) 
            self.departures = deduped[:5]

        for departure in self.departures:
            _LOGGER.debug(departure)
    
    async def async_run(self, run_id):
        """Get run information from Public Transport Victoria API."""
        url = build_URL(self.id, self.api_key, f"/v3/runs/{run_id}")

        async with aiohttp.ClientSession() as session:
            response = await session.get(url)

        if response is not None and response.status == 200:
            response = await response.json()
            _LOGGER.debug(response)
            if response.get("runs") and len(response["runs"]) > 0:
                return response["runs"][0]
        return None

    async def async_update_disruptions(self, disruption_status: int):
        """Update disruptions for the configured route.

        disruption_status: 0 = current, 1 = planned
        """
        # Build disruptions query filtering to the configured route and type
        disruptions_path = DISRUPTIONS_PATH.format(self.route, self.route_type, disruption_status)
        url = build_URL(self.id, self.api_key, disruptions_path)

        async with aiohttp.ClientSession() as session:
            response = await session.get(url)

        if response is not None and response.status == 200:
            response = await response.json()
            _LOGGER.debug(response)

            # Normalise disruptions list from possible response shapes
            disruptions_raw = []
            if isinstance(response.get("disruptions"), list):
                disruptions_raw = response.get("disruptions", [])
            elif isinstance(response.get("disruptions"), dict):
                # Combine all lists under disruptions dict
                for value in response.get("disruptions", {}).values():
                    if isinstance(value, list):
                        disruptions_raw.extend(value)

            # Store a trimmed disruption object for attributes
            normalised = []
            for d in disruptions_raw:
                try:
                    # Extract routes with both id and type if available
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

                    normalised.append({
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
                    })
                except Exception as err:
                    _LOGGER.debug("Error normalising disruption: %s", err)

            # Exclude unwanted disruptions by title (case-insensitive)
            # Improved exclusion: match groups of keywords in title or description
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
            # Keywords that indicate the disruption impacts services beyond pure escalator works
            service_keywords = [
                "delay",
                "train",
                "tram",
                "bus",
                "service",
                "platform",
                "power",
                "outage",
                "reader",
                "payment",
                "eftpos",
                "top-up",
                "top up",
                "myki",
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

            # Filter to only disruptions that explicitly reference the configured route
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

            # For current disruptions (status=0), filter out those that ended more than 2 hours ago
            if disruption_status == 0:
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                cutoff = now_utc - datetime.timedelta(hours=2)
                filtered = [
                    n for n in filtered
                    if not n.get("to_date") or  # Keep if no end date (unplanned)
                    _parse_utc(n.get("to_date")) > cutoff  # Keep if ended within last 2 hours
                ]

            if disruption_status == 0:
                self.disruptions_current = filtered
            else:
                self.disruptions_planned = filtered

        if disruption_status == 0:
            for disruption in self.disruptions_current:
                _LOGGER.debug(disruption)
        else:
            for disruption in self.disruptions_planned:
                _LOGGER.debug(disruption)

        return self.disruptions_current if disruption_status == 0 else self.disruptions_planned

    async def async_update_all(self):
        """Update departures and both disruption sets together."""
        await asyncio.gather(
            self.async_update(),
            self.async_update_disruptions(0),
            self.async_update_disruptions(1),
            return_exceptions=True
        )

def _parse_utc(utc_str):
    """Parse UTC string to datetime, return epoch if parsing fails."""
    if not utc_str:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    try:
        return datetime.datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)

def _safe_local(utc, hass):
    """Return both ISO and human local strings for a UTC time if present."""
    if not utc:
        return None
    try:
        d = datetime.datetime.strptime(utc, "%Y-%m-%dT%H:%M:%SZ")
        local_tz = get_time_zone(hass.config.time_zone)
        d = d.replace(tzinfo=datetime.timezone.utc).astimezone(local_tz)
        return {
            "iso": d.isoformat(),
            "human": d.strftime("%Y-%m-%d %I:%M %p"),
        }
    except Exception:
        return {"iso": utc, "human": utc}

def _text_matches_all_groups(text, groups):
    """Return True if for every group, at least one phrase appears in text."""
    if not text:
        return False
    hay = text.lower()
    for group in groups:
        if not any(phrase in hay for phrase in group):
            return False
    return True

def _clean_title(title, route_name):
    """Remove leading '<route_name...> lines:' prefix if present."""
    if not title:
        return title
    t = title.strip()
    rn = (route_name or "").strip()
    if not rn:
        return t
    # Case-insensitive: if the text starts with the route name and the pre-colon
    # segment mentions 'line' or 'lines', strip everything up to and including ':'
    lower = t.lower()
    rn_lower = rn.lower()
    colon = lower.find(":")
    if colon != -1:
        prefix = lower[:colon].strip()
        # If the pre-colon prefix mentions lines and contains the route name, strip it
        if (" line" in prefix or " lines" in prefix) and (rn_lower in prefix):
            return t[colon+1:].lstrip()
    return t

def _relative_period(from_local, to_local, hass):
    """Build a human-friendly relative period string using local ISO datetimes."""
    try:
        today = datetime.datetime.now(get_time_zone(hass.config.time_zone)).date()
        def _label(local_map):
            if not local_map or not local_map.get("iso"):
                return None
            dt = datetime.datetime.fromisoformat(local_map["iso"]).astimezone(get_time_zone(hass.config.time_zone))
            d = dt.date()
            if d == today:
                return "today"
            if d == today + datetime.timedelta(days=1):
                return "tomorrow"
            return dt.strftime("%A %d %B")

        start = _label(from_local)
        end = _label(to_local)
        # Only generate relative period if we have both start and end and they're different
        if start and end and start != end:
            return f"from {start} until {end}"
        # Don't generate partial periods for disruptions without clear date ranges
        return None
    except Exception:
        return None
    return None

def build_URL(id, api_key, request):
    request = request + ('&' if ('?' in request) else '?')
    raw = request + 'devid={}'.format(id)
    hashed = hmac.new(api_key.encode('utf-8'), raw.encode('utf-8'), sha1)
    signature = hashed.hexdigest()
    url = BASE_URL + raw + '&signature={}'.format(signature)
    _LOGGER.debug(url)
    return url

def convert_utc_to_local(utc, hass):
    """Convert UTC to Home Assistant local time."""
    d = datetime.datetime.strptime(utc, "%Y-%m-%dT%H:%M:%SZ")
    # Get the Home Assistant configured time zone
    local_tz = get_time_zone(hass.config.time_zone)
    # Convert the time to the Home Assistant time zone
    d = d.replace(tzinfo=datetime.timezone.utc).astimezone(local_tz)
    return d.strftime("%I:%M %p")
