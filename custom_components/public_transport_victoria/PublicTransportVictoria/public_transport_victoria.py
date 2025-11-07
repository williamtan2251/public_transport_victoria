"""Public Transport Victoria API connector."""
import aiohttp
import asyncio
import datetime
import hmac
import logging
from dataclasses import dataclass
from typing import Optional, Dict, List, Any, Set
from hashlib import sha1
from homeassistant.util.dt import get_time_zone

_LOGGER = logging.getLogger(__name__)

# Constants
BASE_URL = "https://timetableapi.ptv.vic.gov.au"
DEPARTURES_PATH = "/v3/departures/route_type/{}/stop/{}/route/{}?direction_id={}&max_results={}"
DIRECTIONS_PATH = "/v3/directions/route/{}"
ROUTE_TYPES_PATH = "/v3/route_types"
ROUTES_PATH = "/v3/routes?route_types={}"
STOPS_PATH = "/v3/stops/route/{}/route_type/{}"
DISRUPTIONS_PATH = "/v3/disruptions?route_ids={}&route_types={}&disruption_status={}"
MAX_RESULTS = 10

# Data classes
@dataclass
class Departure:
    """Represents a single departure."""
    estimated_departure_utc: Optional[str]
    scheduled_departure_utc: Optional[str]
    departure_utc: datetime.datetime
    departure_local: str
    is_express: Optional[bool]
    run_id: str
    route_id: int
    stop_id: int

@dataclass
class Disruption:
    """Represents a service disruption."""
    disruption_id: int
    title: str
    title_clean: str
    description: str
    disruption_status: int
    from_date: Optional[str]
    to_date: Optional[str]
    url: Optional[str]
    severity: Optional[str]
    category: Optional[str]
    routes: List[Dict[str, Any]]
    from_date_local: Optional[Dict[str, str]]
    to_date_local: Optional[Dict[str, str]]
    period_relative: Optional[str]

@dataclass
class RouteInfo:
    """Represents route information."""
    route_id: int
    route_name: str
    route_type: int
    route_number: Optional[str]

class PTVApiError(Exception):
    """Custom exception for PTV API errors."""
    pass

class ConfigurationError(PTVApiError):
    """Exception for configuration errors."""
    pass

class Connector:
    """Public Transport Victoria connector."""

    manufacturer = "Public Transport Victoria"

    def __init__(
        self,
        hass,
        dev_id: str,
        api_key: str,
        route_type: Optional[int] = None,
        route: Optional[int] = None,
        direction: Optional[int] = None,
        stop: Optional[int] = None,
        route_type_name: Optional[str] = None,
        route_name: Optional[str] = None,
        direction_name: Optional[str] = None,
        stop_name: Optional[str] = None
    ):
        """Initialize PTV connector."""
        self.hass = hass
        self.dev_id = dev_id
        self.api_key = api_key
        self.route_type = route_type
        self.route = route
        self.direction = direction
        self.stop = stop
        self.route_type_name = route_type_name
        self.route_name = route_name
        self.direction_name = direction_name
        self.stop_name = stop_name
        
        # Session management
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        
        # State attributes using data classes
        self.departures: List[Departure] = []
        self.disruptions_current: List[Disruption] = []
        self.disruptions_planned: List[Disruption] = []
        
        # Cache for validation and performance
        self._valid_routes: Set[int] = set()
        self._valid_stops: Set[int] = set()
        self._route_info_cache: Dict[int, RouteInfo] = {}
        self._run_cache: Dict[str, Dict[str, Any]] = {}
        self._run_cache_timestamp: Optional[datetime.datetime] = None

    async def ensure_session(self) -> aiohttp.ClientSession:
        """Ensure we have a session, create one if needed."""
        async with self._session_lock:
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=30)
                self._session = aiohttp.ClientSession(timeout=timeout)
                _LOGGER.debug("Created new aiohttp session")
            return self._session

    async def close(self):
        """Close the session."""
        async with self._session_lock:
            if self._session and not self._session.closed:
                await self._session.close()
                self._session = None
                _LOGGER.debug("Closed aiohttp session")

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - ensure session is closed."""
        await self.close()

    async def _make_api_request(self, path: str) -> Dict[str, Any]:
        """Make API request with error handling using shared session."""
        session = await self.ensure_session()
        url = self._build_url(path)
        
        try:
            async with session.get(url) as response:
                if response.status != 200:
                    raise PTVApiError(f"API request failed with status {response.status}")
                
                return await response.json()
                
        except aiohttp.ClientError as err:
            _LOGGER.error("Network error during API request to %s: %s", path, err)
            raise PTVApiError(f"Network error: {err}") from err
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout during API request to %s", path)
            raise PTVApiError("Request timeout") from None

    def _build_url(self, path: str) -> str:
        """Build signed PTV API URL."""
        separator = '&' if '?' in path else '?'
        unsigned_url = f"{path}{separator}devid={self.dev_id}"
        
        # Create signature
        signature = hmac.new(
            self.api_key.encode('utf-8'),
            unsigned_url.encode('utf-8'),
            sha1
        ).hexdigest()
        
        url = f"{BASE_URL}{unsigned_url}&signature={signature}"
        _LOGGER.debug("Built URL: %s", url)
        return url

    # Validation methods
    async def validate_route(self, route_id: int, route_type: Optional[int] = None) -> bool:
        """Validate that a route exists and is accessible."""
        if route_id in self._valid_routes:
            return True
            
        try:
            route_type_to_use = route_type or self.route_type
            if not route_type_to_use:
                return False
                
            routes = await self.async_routes(route_type_to_use)
            is_valid = route_id in routes
            if is_valid:
                self._valid_routes.add(route_id)
            return is_valid
        except PTVApiError:
            _LOGGER.warning("Failed to validate route %s", route_id)
            return False

    async def validate_stop(self, stop_id: int, route_id: Optional[int] = None) -> bool:
        """Validate that a stop exists and is accessible for the route."""
        if stop_id in self._valid_stops:
            return True
            
        try:
            route_to_use = route_id or self.route
            route_type_to_use = self.route_type
            
            if not all([route_to_use, route_type_to_use]):
                return False
                
            stops = await self.async_stops(route_to_use, route_type_to_use)
            is_valid = stop_id in stops
            if is_valid:
                self._valid_stops.add(stop_id)
            return is_valid
        except PTVApiError:
            _LOGGER.warning("Failed to validate stop %s for route %s", stop_id, route_id)
            return False

    async def get_route_info(self, route_id: int) -> Optional[RouteInfo]:
        """Get detailed route information with caching."""
        if route_id in self._route_info_cache:
            return self._route_info_cache[route_id]
            
        try:
            # Use routes endpoint to get detailed info
            if not self.route_type:
                return None
                
            routes_data = await self._make_api_request(ROUTES_PATH.format(self.route_type))
            for route in routes_data.get("routes", []):
                if route["route_id"] == route_id:
                    route_info = RouteInfo(
                        route_id=route_id,
                        route_name=route["route_name"],
                        route_type=route["route_type"],
                        route_number=route.get("route_number")
                    )
                    self._route_info_cache[route_id] = route_info
                    return route_info
        except PTVApiError:
            _LOGGER.debug("Failed to fetch detailed info for route %s", route_id)
            
        return None

    async def async_route_types(self) -> Dict[int, str]:
        """Get available route types."""
        data = await self._make_api_request(ROUTE_TYPES_PATH)
        return {item["route_type"]: item["route_type_name"] for item in data["route_types"]}

    async def async_routes(self, route_type: int) -> Dict[int, str]:
        """Get routes for a route type without modifying instance state."""
        data = await self._make_api_request(ROUTES_PATH.format(route_type))
        return {item["route_id"]: item["route_name"] for item in data["routes"]}

    async def async_directions(self, route_id: int) -> Dict[int, str]:
        """Get directions for a route without modifying instance state."""
        data = await self._make_api_request(DIRECTIONS_PATH.format(route_id))
        return {item["direction_id"]: item["direction_name"] for item in data["directions"]}

    async def async_stops(self, route_id: int, route_type: int) -> Dict[int, str]:
        """Get stops for a route without modifying instance state."""
        data = await self._make_api_request(STOPS_PATH.format(route_id, route_type))
        return {item["stop_id"]: item["stop_name"] for item in data["stops"]}

    def _validate_departure_configuration(self) -> None:
        """Validate that all required departure parameters are set."""
        missing_params = []
        if self.route_type is None:
            missing_params.append("route_type")
        if self.stop is None:
            missing_params.append("stop")
        if self.route is None:
            missing_params.append("route")
        if self.direction is None:
            missing_params.append("direction")
            
        if missing_params:
            raise ConfigurationError(
                f"Missing required parameters for departures update: {', '.join(missing_params)}"
            )

    async def async_update_departures(self) -> None:
        """Update departure information with validation."""
        # Validate required parameters
        self._validate_departure_configuration()

        # Validate route and stop
        if not await self.validate_route(self.route):
            raise ConfigurationError(f"Invalid route ID: {self.route}")
            
        if not await self.validate_stop(self.stop, self.route):
            raise ConfigurationError(f"Invalid stop ID: {self.stop} for route: {self.route}")

        path = DEPARTURES_PATH.format(
            self.route_type, self.stop, self.route, self.direction, MAX_RESULTS
        )
        
        data = await self._make_api_request(path)
        await self._process_departures(data)

    async def _process_departures(self, data: Dict[str, Any]) -> None:
        """Process and filter departure data into Departure objects."""
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        
        # Clear run cache if it's from a previous update (older than 5 minutes)
        if (self._run_cache_timestamp is None or 
            (now_utc - self._run_cache_timestamp).total_seconds() > 300):
            self._run_cache.clear()
            self._run_cache_timestamp = now_utc
        
        # Process departures with run caching
        departures_processed = []
        for departure_data in data.get("departures", []):
            departure = await self._process_departure(departure_data)
            if departure and departure.departure_utc > now_utc:
                departures_processed.append(departure)

        # Deduplicate and limit results
        unique_departures = self._deduplicate_departures(departures_processed)[:5]
        self.departures = unique_departures
        
        for departure in self.departures:
            _LOGGER.debug("Processed departure: %s", departure)

    async def _process_departure(self, departure_data: Dict[str, Any]) -> Optional[Departure]:
        """Process a single departure record into a Departure object without mutating input."""
        utc_str = (departure_data.get("estimated_departure_utc") or 
                  departure_data.get("scheduled_departure_utc"))
        
        if not utc_str:
            return None

        try:
            departure_utc = datetime.datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=datetime.timezone.utc
            )
        except ValueError:
            _LOGGER.warning("Invalid UTC time format: %s", utc_str)
            return None

        departure_local = self._convert_utc_to_local(utc_str)
        
        # Get express status with caching
        run_id = departure_data.get("run_id")
        is_express = None
        if run_id:
            if run_id in self._run_cache:
                run_info = self._run_cache[run_id]
            else:
                run_info = await self.async_run(run_id)
                if run_info:
                    self._run_cache[run_id] = run_info
            
            if run_info:
                is_express = run_info.get("express_stop_count", 0) > 0

        return Departure(
            estimated_departure_utc=departure_data.get("estimated_departure_utc"),
            scheduled_departure_utc=departure_data.get("scheduled_departure_utc"),
            departure_utc=departure_utc,
            departure_local=departure_local,
            is_express=is_express,
            run_id=run_id or "",
            route_id=self.route,
            stop_id=self.stop
        )

    def _deduplicate_departures(self, departures: List[Departure]) -> List[Departure]:
        """Remove duplicate departures within the same minute."""
        seen_times = set()
        unique_departures = []
        
        for departure in sorted(departures, key=lambda x: x.departure_utc):
            time_key = departure.departure_utc.strftime("%Y-%m-%dT%H:%M")
            if time_key not in seen_times:
                seen_times.add(time_key)
                unique_departures.append(departure)
                
        return unique_departures

    async def async_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Get run information."""
        if not run_id:
            return None
            
        try:
            data = await self._make_api_request(f"/v3/runs/{run_id}")
            return data.get("runs", [{}])[0] if data.get("runs") else None
        except PTVApiError:
            _LOGGER.debug("Failed to fetch run info for %s", run_id)
            return None

    async def async_update_disruptions(self, disruption_status: int) -> List[Disruption]:
        """Update disruptions for the configured route with comprehensive filtering."""
        if not all([self.route, self.route_type is not None]):
            raise ConfigurationError("Missing route information for disruptions update")

        # Validate route exists
        if not await self.validate_route(self.route):
            raise ConfigurationError(f"Cannot fetch disruptions for invalid route: {self.route}")

        path = DISRUPTIONS_PATH.format(self.route, self.route_type, disruption_status)
        
        try:
            data = await self._make_api_request(path)
            disruptions = await self._process_disruptions(data, disruption_status)
            
            if disruption_status == 0:
                self.disruptions_current = disruptions
            else:
                self.disruptions_planned = disruptions
                
            return disruptions
            
        except PTVApiError as err:
            _LOGGER.error("Failed to update disruptions: %s", err)
            return []

    async def _process_disruptions(self, data: Dict[str, Any], disruption_status: int) -> List[Disruption]:
        """Process and normalize disruptions data with comprehensive filtering."""
        disruptions_raw = []
        
        # Handle different response formats
        disruptions_data = data.get("disruptions", [])
        if isinstance(disruptions_data, dict):
            for disruptions_list in disruptions_data.values():
                if isinstance(disruptions_list, list):
                    disruptions_raw.extend(disruptions_list)
        elif isinstance(disruptions_data, list):
            disruptions_raw = disruptions_data

        # Normalize disruptions
        normalized = []
        for disruption in disruptions_raw:
            normalized_disruption = await self._normalize_disruption(disruption)
            if normalized_disruption:
                normalized.append(normalized_disruption)

        # Apply domain-specific filtering
        filtered = [d for d in normalized if not self._should_filter_disruption(d)]
        
        # Filter to relevant disruptions for this route
        route_filtered = [d for d in filtered if self._is_relevant_disruption(d)]

        # For current disruptions, filter out old ones
        if disruption_status == 0:
            route_filtered = self._filter_old_disruptions(route_filtered)

        return route_filtered

    async def _normalize_disruption(self, disruption: Dict[str, Any]) -> Optional[Disruption]:
        """Normalize disruption data into Disruption object."""
        try:
            # Extract routes
            routes_list = []
            for route in disruption.get("routes", []):
                if isinstance(route, dict):
                    routes_list.append({
                        "route_id": route.get("route_id"),
                        "route_type": route.get("route_type"),
                        "route_name": route.get("route_name")
                    })

            title = disruption.get("title", "")
            cleaned_title = self._clean_title(title)

            # Handle dates
            from_src = disruption.get("from_date") or disruption.get("from_time")
            to_src = disruption.get("to_date") or disruption.get("to_time")
            from_local = self._safe_local(from_src)
            to_local = self._safe_local(to_src)
            period_relative = self._relative_period(from_local, to_local)

            return Disruption(
                disruption_id=disruption.get("disruption_id", 0),
                title=title,
                title_clean=cleaned_title,
                description=disruption.get("description", ""),
                disruption_status=disruption.get("disruption_status", 1),
                from_date=from_src,
                to_date=to_src,
                url=disruption.get("url") or disruption.get("url_web"),
                severity=disruption.get("severity") or disruption.get("severity_level"),
                category=disruption.get("category") or disruption.get("disruption_type"),
                routes=routes_list,
                from_date_local=from_local,
                to_date_local=to_local,
                period_relative=period_relative
            )
        except Exception as err:
            _LOGGER.debug("Error normalizing disruption: %s", err)
            return None

    def _should_filter_disruption(self, disruption: Disruption) -> bool:
        """Apply domain-specific filtering rules."""
        combined_text = f"{(disruption.title or '').lower()} {(disruption.description or '').lower()}"
        
        # Define filtering rules
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
        escalator_words = ["escalator", "elevator", "lift"]
        
        # Service keywords that indicate real service impact
        service_keywords = [
            "delay", "train", "tram", "bus", "service", "platform",
            "power", "outage", "reader", "payment", "eftpos", "top-up",
            "top up", "myki", "replacement", "shuttle", "cancel", "cancelled"
        ]

        # Filter car park closures
        if self._text_matches_all_groups(combined_text, carpark_groups):
            return True

        # Filter pedestrian access changes
        if self._text_matches_all_groups(combined_text, pedestrian_groups):
            return True

        # Filter escalator/elevator issues unless they mention service impact
        if any(word in combined_text for word in escalator_words):
            if not any(keyword in combined_text for keyword in service_keywords):
                return True

        return False

    def _is_relevant_disruption(self, disruption: Disruption) -> bool:
        """Check if disruption is relevant to current route."""
        route_id_str = str(self.route)
        route_type_str = str(self.route_type)
        
        return any(
            str(route.get("route_id")) == route_id_str and
            str(route.get("route_type", "")) == route_type_str
            for route in disruption.routes
        )

    def _filter_old_disruptions(self, disruptions: List[Disruption]) -> List[Disruption]:
        """Filter out disruptions that ended more than 2 hours ago."""
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        cutoff = now_utc - datetime.timedelta(hours=2)
        
        filtered = []
        for disruption in disruptions:
            if not disruption.to_date:  # Keep if no end date
                filtered.append(disruption)
            else:
                end_time = self._parse_utc(disruption.to_date)
                if end_time > cutoff:
                    filtered.append(disruption)
                    
        return filtered

    # Helper methods for disruption processing
    def _parse_utc(self, utc_str: Optional[str]) -> datetime.datetime:
        """Parse UTC string to datetime."""
        if not utc_str:
            return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
        try:
            return datetime.datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=datetime.timezone.utc
            )
        except ValueError:
            return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)

    def _safe_local(self, utc: Optional[str]) -> Optional[Dict[str, str]]:
        """Convert UTC to local time strings."""
        if not utc:
            return None
        try:
            d = datetime.datetime.strptime(utc, "%Y-%m-%dT%H:%M:%SZ")
            local_tz = get_time_zone(self.hass.config.time_zone)
            d = d.replace(tzinfo=datetime.timezone.utc).astimezone(local_tz)
            return {
                "iso": d.isoformat(),
                "human": d.strftime("%Y-%m-%d %I:%M %p"),
            }
        except Exception:
            return {"iso": utc, "human": utc}

    def _clean_title(self, title: str) -> str:
        """Clean disruption title by removing route prefixes."""
        if not title:
            return title
            
        t = title.strip()
        if not self.route_name:
            return t
            
        rn = self.route_name.strip()
        lower = t.lower()
        rn_lower = rn.lower()
        colon = lower.find(":")
        
        if colon != -1:
            prefix = lower[:colon].strip()
            if (" line" in prefix or " lines" in prefix) and (rn_lower in prefix):
                return t[colon+1:].lstrip()
                
        return t

    def _relative_period(self, from_local: Optional[Dict[str, str]], 
                        to_local: Optional[Dict[str, str]]) -> Optional[str]:
        """Generate human-friendly relative period string."""
        try:
            today = datetime.datetime.now(get_time_zone(self.hass.config.time_zone)).date()
            
            def _label(local_map: Optional[Dict[str, str]]) -> Optional[str]:
                if not local_map or not local_map.get("iso"):
                    return None
                dt = datetime.datetime.fromisoformat(local_map["iso"]).astimezone(
                    get_time_zone(self.hass.config.time_zone)
                )
                d = dt.date()
                if d == today:
                    return "today"
                if d == today + datetime.timedelta(days=1):
                    return "tomorrow"
                return dt.strftime("%A %d %B")

            start = _label(from_local)
            end = _label(to_local)
            
            if start and end and start != end:
                return f"from {start} until {end}"
                
            return None
        except Exception:
            return None

    def _text_matches_all_groups(self, text: str, groups: List[List[str]]) -> bool:
        """Check if text contains at least one phrase from each group."""
        if not text:
            return False
            
        hay = text.lower()
        for group in groups:
            if not any(phrase in hay for phrase in group):
                return False
        return True

    async def async_update_all(self) -> None:
        """Update all data (departures and disruptions) with proper error handling."""
        try:
            # Use context manager to ensure session cleanup on errors
            async with self:
                await asyncio.gather(
                    self.async_update_departures(),
                    self.async_update_disruptions(0),
                    self.async_update_disruptions(1),
                    return_exceptions=True
                )
        except Exception as err:
            _LOGGER.error("Error during update_all: %s", err)
            # Ensure session is closed even on errors
            await self.close()
            raise

    def _convert_utc_to_local(self, utc_string: str) -> str:
        """Convert UTC string to local time string."""
        try:
            utc_time = datetime.datetime.strptime(utc_string, "%Y-%m-%dT%H:%M:%SZ")
            utc_time = utc_time.replace(tzinfo=datetime.timezone.utc)
            
            local_tz = get_time_zone(self.hass.config.time_zone)
            local_time = utc_time.astimezone(local_tz)
            
            return local_time.strftime("%I:%M %p")
            
        except ValueError as err:
            _LOGGER.warning("Time conversion error: %s", err)
            return utc_string
