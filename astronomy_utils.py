# astronomy routines using astropy, etc

from functools import cache
from pathlib import Path
from time import perf_counter

from astropy import units as u
from astropy.coordinates import SkyCoord, EarthLocation, AltAz
from astropy.time import Time

import astronomy as astro  # Astronomy Engine

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Union, Tuple, List
import math

# put some of our constants here to avoid circular imports
MIN_AIRMASS = 2.9 # hide targets greater than this
MIN_ALT_FOR_COLOR = 20 # min altitude to show color on map
DEFAULT_TIMEZONE = "America/Chicago"  # default timezone if none provided

stellarium_object_types = [
    ("all", "All Types"),
    # ("G", "Galaxy"),
    ("Gx", "Galaxy"),
    ("GC", "Globular Cluster"),
    ("OC", "Open Cluster"),
    # ("NB", "Nebula"),
    ("PN", "Planetary Nebula"),
    ("DN", "Dark Nebula"),
    ("RN", "Reflection Nebula"),
    ("C+N", "Cluster with Nebula"),
   # ("HA", "H-alpha Region"),
    ("HII", "H II Region"),
    ("SNR", "Supernova Remnant"),
    ("BN", "Bright Nebula"),
    ("EN", "Emission Nebula"),
    ("SA", "Stellar Association"),
    # ("SC", "Star Cloud"),
    # ("RG", "Radio Galaxy"),
    ("CL", "Galaxy Cluster"),
    ("IG", "Interacting Galaxies"),
    # ("QSO", "Quasar (QSO)"),
    ("", "Unclassified / Unknown"),
]



# routine to convert RA/Dec to Alt/Az for given lat/long and list of times
# returns list of SkyCoord objects in AltAz frame
# datetimes MUST have desired timezone info set
# this was astropy, but was too slow, so see astronomy engine code below
def altaz_from_lat_long_times(
    ra_deg: float,
    dec_deg: float,
    latitude: float,
    longitude: float,
    local_dt: list[datetime],
    elevation_m: float = 0.0,
    pressure_hPa: Optional[float] = 1013.25,  # None disables refraction
    temperature_C: float = 10.0,
    relative_humidity: float = 0.5,
    obs_wavelength_um: float = 0.55,
) -> Union[SkyCoord, list[SkyCoord]]:

    location = EarthLocation(lat=latitude * u.deg,
                             lon=longitude * u.deg,
                             height=elevation_m * u.m)

    pressure = 0 * u.hPa if pressure_hPa is None else pressure_hPa * u.hPa
    frame = AltAz(obstime=local_dt,
                  location=location,
                  pressure=pressure,
                  temperature=temperature_C * u.deg_C,
                  relative_humidity=relative_humidity,
                  obswl=obs_wavelength_um * u.um)

    target = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    skList = target.transform_to(frame)
    return skList

def datetime_to_astronomy_time(dt: datetime) -> float:
    """
    Convert a Python datetime object to Astronomy Engine time format.
    
    Astronomy Engine expects time as days since J2000.0 epoch
    (January 1, 2000, 12:00:00 UTC).
    
    Args:
        dt: Python datetime object (should be timezone-aware)
        
    Returns:
        float: Days since J2000.0 epoch
    """
    # J2000.0 epoch: January 1, 2000, 12:00:00 UTC
    j2000_epoch = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    
    # If datetime is naive, assume it's UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    
    # Calculate the difference and convert to days
    delta = dt - j2000_epoch
    days_since_j2000 = delta.total_seconds() / 86400.0  # 86400 seconds per day
    
    return days_since_j2000


def radec_to_altaz_airmass(
    ra_dec_list: List[Tuple[float, float]], 
    observer_lat: float, 
    observer_lon: float, 
    observation_time: datetime
) -> list[dict[str, float]]:
    """
    Convert J2000 RA/DEC coordinates to altitude, azimuth, and air-mass.
    
    Astronomy Engine expects J2000 coordinates and automatically handles
    precession, nutation, aberration, and other corrections to compute
    the apparent position at the observation time.
    
    Args:
        ra_dec_list: List of (RA, DEC) tuples in decimal degrees (J2000 epoch)
        observer_lat: Observer latitude in decimal degrees
        observer_lon: Observer longitude in decimal degrees
        observation_time: Timezone-aware datetime object
        
    Returns:
        List of dictionaries containing:
        - 'ra': Right ascension J2000 (degrees)
        - 'dec': Declination J2000 (degrees) 
        - 'altitude': Altitude above horizon (degrees)
        - 'azimuth': Azimuth from north (degrees)
        - 'airmass': Atmospheric air mass (dimensionless)
        - 'visible': Boolean indicating if object is above horizon
    """
    
    # Create observer location
    observer = astro.Observer(observer_lat, observer_lon, 0.0)  # 0.0 = sea level
    
    # Convert datetime to Astronomy Engine time format (days since J2000.0)
    astro_time = astro.Time(datetime_to_astronomy_time(observation_time))
    
    results = []
    
    for ra, dec in ra_dec_list:
        # Convert RA from degrees to hours for Astronomy Engine
        ra_hours = ra / 15.0
        
        # Convert RA/DEC to horizontal coordinates directly
        # Astronomy Engine handles the coordinate transformation internally
        horizontal = astro.Horizon(astro_time, observer, ra_hours, dec, astro.Refraction.Normal)
        
        # Calculate air mass using standard formula: sec(zenith_angle)
        # Air mass is undefined/infinite when altitude <= 0
        zenith_angle = 90.0 - horizontal.altitude
        zenith_radians = math.radians(zenith_angle)
        
        if horizontal.altitude > 0:
            airmass = 1.0 / math.cos(zenith_radians)
            # Apply more accurate airmass formula for low altitudes (Kasten & Young 1989)
            if horizontal.altitude < 20.0:
                airmass = 1.0 / (math.cos(zenith_radians) + 0.50572 * (horizontal.altitude + 6.07995)**(-1.6364))
            visible = True
        else:
            airmass = float('inf')  # Object below horizon
            visible = False
            
        results.append({
            'ra': ra,
            'dec': dec,
            'altitude': horizontal.altitude,
            'azimuth': horizontal.azimuth,
            'airmass': airmass,
            'visible': visible
        })
    
    return results

# use timezone aware datetimes in the list!
# e.g. [datetime.now(tz=ZoneInfo("America/Chicago")) + timedelta(hours=i) for i in range(5)]
def ra_dec_to_altaz_airmass_multiple_times(
    ra: float,
    dec: float,
    observer_lat: float, 
    observer_lon: float, 
    observer_elevation: float,
    datetime_list: List[datetime]
) -> list[dict[str, Union[float, datetime]]]:
    """
    Calculate altitude, azimuth, and air-mass for a single object at multiple times.
    
    Args:
        ra: Right ascension in decimal degrees (J2000 epoch)
        dec: Declination in decimal degrees (J2000 epoch)
        observer_lat: Observer latitude in decimal degrees
        observer_lon: Observer longitude in decimal degrees
        datetime_list: List of timezone-aware datetime objects
        
    Returns:
        List of dictionaries containing:
        - 'datetime': Original datetime object
        - 'ra': Right ascension J2000 (degrees)
        - 'dec': Declination J2000 (degrees) 
        - 'altitude': Altitude above horizon (degrees)
        - 'azimuth': Azimuth from north (degrees)
        - 'airmass': Atmospheric air mass (dimensionless)
        - 'visible': Boolean indicating if object is above horizon
        - 'j2000_days': Days since J2000.0 for reference
    """
    
    # Create observer location
    observer = astro.Observer(observer_lat, observer_lon, observer_elevation) 
    # Convert RA from degrees to hours for Astronomy Engine
    ra_hours = ra / 15.0
    
    results = []
    
    for obs_time in datetime_list:
        # Convert datetime to Astronomy Engine time format (days since J2000.0)
        astro_time = astro.Time(datetime_to_astronomy_time(obs_time))
        
        # Convert RA/DEC to horizontal coordinates directly
        horizontal = astro.Horizon(astro_time, observer, ra_hours, dec, astro.Refraction.Normal)
        
        # Calculate air mass using standard formula: sec(zenith_angle)
        zenith_angle = 90.0 - horizontal.altitude
        zenith_radians = math.radians(zenith_angle)
        
        if horizontal.altitude > 0:
            airmass = 1.0 / math.cos(zenith_radians)
            # Apply more accurate airmass formula for low altitudes (Kasten & Young 1989)
            if horizontal.altitude < 20.0:
                airmass = 1.0 / (math.cos(zenith_radians) + 0.50572 * (horizontal.altitude + 6.07995)**(-1.6364))
            visible = True
        else:
            airmass = float('inf')  # Object below horizon
            visible = False
            
        results.append({
            'datetime': obs_time,
            'ra': ra,
            'dec': dec,
            'altitude': horizontal.altitude,
            'azimuth': horizontal.azimuth,
            'airmass': airmass,
            'visible': visible,
            'j2000_days': datetime_to_astronomy_time(obs_time)
        })
    
    return results

@cache
def calculate_rise_transit_set_fast(
    ra_dec_list: Tuple[Tuple[float, float]], 
    observer_lat: float, 
    observer_lon: float, 
    reference_date: datetime,
    elevation_meters: float = 0.0
) -> list[dict[str, Union[float, datetime, str]]]:
    """
    Fast calculation of rise, transit, and set times using Astronomy Engine's built-in functions.
    
    This method finds rise/transit/set times for the specific date requested, not the next
    occurrence. It searches from the beginning of the day to ensure we get today's events.
    FIXME - set time could be tomorrow!
    FIXME - make this faster (check old code for ideas)
    
    Args:
        ra_dec_list: List of (RA, DEC) tuples in decimal degrees (J2000 epoch)
        observer_lat: Observer latitude in decimal degrees
        observer_lon: Observer longitude in decimal degrees
        reference_date: Reference date for calculations
        elevation_meters: Observer elevation above sea level in meters
        
    Returns:
        List of dictionaries containing:
        - 'ra': Right ascension J2000 (degrees)
        - 'dec': Declination J2000 (degrees)
        - 'rise_time': Rise datetime for the reference date (or None if never rises)
        - 'transit_time': Transit datetime for the reference date
        - 'set_time': Set datetime for the reference date (or None if never sets)
        - 'circumpolar': True if object never sets
        - 'never_visible': True if object never rises
    """
    
    print("Calculating rise/transit/set times for", len(ra_dec_list), "objects on", reference_date.date())
    # Create observer location
    observer = astro.Observer(observer_lat, observer_lon, elevation_meters)
    
    # Start search from the beginning of the reference day (00:00 local time)
    day_start = reference_date.replace(hour=0, minute=0, second=0, microsecond=0)
    if day_start.tzinfo is None:
        day_start = day_start.replace(tzinfo=timezone.utc)
    
    # Also get the end of the day for validation
    day_end = day_start + timedelta(days=1)
    
    # Convert to Astronomy Engine time
    start_astro_time = astro.Time(datetime_to_astronomy_time(day_start))
    
    results = []
    
    for ra, dec in ra_dec_list:
        # Convert RA from degrees to hours for DefineStar
        ra_hours = ra / 15.0
        
        try:
            # Define/redefine the "dso" star with the current RA/DEC coordinates
            # DefineStar expects: name, ra_hours, dec_degrees, distance_parsecs
            astro.DefineStar(astro.Body.Star1, ra_hours, dec, 1000.0)
            
            # Get the dso star body object
            star_body = astro.Body.Star1  # or however the dso body is accessed
            
            # Search for rise time starting from beginning of day
            rise_event = astro.SearchRiseSet(star_body, observer, astro.Direction.Rise, start_astro_time, 1, 0.0)
            rise_time = None
            if rise_event is not None:
                # Convert back to Python datetime
                rise_days = rise_event.ut
                rise_j2000_epoch = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
                candidate_rise_time = rise_j2000_epoch + timedelta(days=rise_days)
                
                # Only accept if it's within our target day
                if day_start <= candidate_rise_time < day_end:
                    rise_time = candidate_rise_time
            
            # Search for transit time (hour angle = 0) starting from beginning of day
            try:
                transit_event = astro.SearchHourAngle(star_body, observer, 0.0, start_astro_time, 1)
                transit_days = transit_event.time.ut
                transit_j2000_epoch = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
                candidate_transit_time = transit_j2000_epoch + timedelta(days=transit_days)
                
                # Only accept if it's within our target day
                if day_start <= candidate_transit_time < day_end:
                    transit_time = candidate_transit_time
                else:
                    transit_time = None
            except:
                transit_time = None
            
            # Search for set time starting from beginning of day
            set_event = astro.SearchRiseSet(star_body, observer, astro.Direction.Set, start_astro_time, 1, 0.0)
            set_time = None
            if set_event is not None:
                # Convert back to Python datetime
                set_days = set_event.ut
                set_j2000_epoch = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
                candidate_set_time = set_j2000_epoch + timedelta(days=set_days)
                
                # Only accept if it's within our target day
                if day_start <= candidate_set_time < day_end:
                    set_time = candidate_set_time
            
            # Alternative approach: if no rise found, search backwards from current time
            if rise_time is None and rise_event is not None:
                # The rise we found was tomorrow, so search backwards from current time
                current_astro_time = astro.Time(datetime_to_astronomy_time(reference_date))
                
                # Search backwards by using a negative time step (search in reverse)
                # We'll search from current time backwards for up to 1 day
                try:
                    # Create a time 24 hours before current time as search start
                    yesterday = reference_date - timedelta(days=1)
                    yesterday_astro_time = astro.Time(datetime_to_astronomy_time(yesterday))
                    
                    # Search for rise from yesterday to now
                    past_rise_event = astro.SearchRiseSet(star_body, observer, astro.Direction.Rise, yesterday_astro_time, 2, 0.0)
                    
                    if past_rise_event is not None:
                        past_rise_days = past_rise_event.ut
                        past_rise_time = rise_j2000_epoch + timedelta(days=past_rise_days)
                        
                        # Accept this rise time if it's within today
                        if day_start <= past_rise_time < day_end:
                            rise_time = past_rise_time
                except:
                    pass
            
            # Apply same logic for set time if needed
            if set_time is None and set_event is not None:
                try:
                    yesterday = reference_date - timedelta(days=1)
                    yesterday_astro_time = astro.Time(datetime_to_astronomy_time(yesterday))
                    
                    past_set_event = astro.SearchRiseSet(star_body, observer, astro.Direction.Set, yesterday_astro_time, 2, 0.0)
                    
                    if past_set_event is not None:
                        past_set_days = past_set_event.ut
                        past_set_time = rise_j2000_epoch + timedelta(days=past_set_days)
                        
                        if day_start <= past_set_time < day_end:
                            set_time = past_set_time
                except:
                    pass
            
            # Determine object status
            circumpolar = (rise_event is None and set_event is None and transit_time is not None)
            never_visible = (rise_event is None and set_event is None and transit_time is None)
            
            # If we have a transit but no rise/set, check altitude to determine status
            if transit_time and (rise_time is None or set_time is None):
                transit_astro_time = astro.Time(datetime_to_astronomy_time(transit_time))
                try:
                    horizontal = astro.Horizon(transit_astro_time, observer, ra_hours, dec, astro.Refraction.Normal)
                    if horizontal.altitude > 0:
                        circumpolar = True
                        never_visible = False
                    else:
                        circumpolar = False
                        never_visible = True
                except:
                    circumpolar = False
                    never_visible = True
            
        except Exception as e:
            # If anything fails, fall back to None values
            print(f"Exception in rise-set-transit-fast processing RA={ra}, DEC={dec}: {e}")
            rise_time = None
            transit_time = None
            set_time = None
            circumpolar = False
            never_visible = True
        
        results.append({
            'ra': ra,
            'dec': dec,
            'rise_time': rise_time,
            'transit_time': transit_time,
            'set_time': set_time,
            'circumpolar': circumpolar,
            'never_visible': never_visible
        })
    
    return results

@cache
def find_all_twilight_times(
    observer_lat: float,
    observer_lon: float,
    reference_date: datetime,
    elevation_meters: float = 0.0
) -> dict[str, Union[datetime, None]]:
    """
    Find all twilight transition times (civil, nautical, and astronomical).
    
    Args:
        observer_lat: Observer latitude in decimal degrees
        observer_lon: Observer longitude in decimal degrees
        reference_date: Reference date for calculations (timezone will be preserved in results)
        elevation_meters: Observer elevation above sea level in meters
    
    Returns times for (all in reference_date's timezone):
    - Civil twilight: Sun 6° below horizon
    - Nautical twilight: Sun 12° below horizon  
    - Astronomical twilight: Sun 18° below horizon
    """
    
    observer = astro.Observer(observer_lat, observer_lon, elevation_meters)
    
    # Store the original timezone for result conversion
    original_tz = reference_date.tzinfo or timezone.utc
    
    # Convert reference date to start of day in UTC for calculations
    start_of_day = reference_date.replace(hour=12, minute=0, second=0, microsecond=0)
    if start_of_day.tzinfo is None:
        start_of_day = start_of_day.replace(tzinfo=timezone.utc)
    else:
        # Convert to UTC for Astronomy Engine calculations
        start_of_day = start_of_day.astimezone(timezone.utc)
    
    astro_time = astro.Time(datetime_to_astronomy_time(start_of_day))
    
    # Define twilight altitudes
    twilight_altitudes = {
        'civil': -6.0,
        'nautical': -12.0, 
        'astronomical': -18.0
    }
    
    def convert_to_original_tz(astro_event):
        """Helper function to convert Astronomy Engine time back to original timezone"""
        if astro_event is None:
            return None
        event_days = astro_event.ut
        j2000_epoch_utc = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        utc_time = j2000_epoch_utc + timedelta(days=event_days)
        return utc_time.astimezone(original_tz)
    
    results = {}
    
    for twilight_type, altitude in twilight_altitudes.items():
        try:
            # Evening twilight end (Sun setting to threshold altitude)
            evening_event = astro.SearchAltitude(
                astro.Body.Sun, observer, astro.Direction.Set,
                astro_time, 1, altitude
            )
            
            # Morning twilight start (Sun rising from threshold altitude)
            # Start search from evening event if available
            morning_search_time = astro_time
            if evening_event:
                morning_search_time = evening_event
                
            morning_event = astro.SearchAltitude(
                astro.Body.Sun, observer, astro.Direction.Rise,
                morning_search_time, 1, altitude
            )
            
            # Convert to original timezone
            evening_time = convert_to_original_tz(evening_event)
            morning_time = convert_to_original_tz(morning_event)
            
            results[f'{twilight_type}_evening'] = evening_time
            results[f'{twilight_type}_morning'] = morning_time
            
        except:
            results[f'{twilight_type}_evening'] = None
            results[f'{twilight_type}_morning'] = None
    
    return results

# sensor coverage stuff
@cache
def calculate_pixel_scale(focal_length_mm: float, pixel_size_um: float) -> float:
    # pixel scale = size of photosite (microns) / focal length (mm) * 206.265
    # assume square pixels for now
    # 071 has 4.78um pixels, zwo 2600 = 3.76 (assume bin=1)
    # returned value is in arcSECONDS per pixel

    pixel_scale = (pixel_size_um / focal_length_mm) * 206.265
    return pixel_scale

def _calculate_sensor_fov_amin(pixel_scale: float, sensor_width_px: int, sensor_height_px: int) -> Tuple[float, float]:
    # sensor size in arcminutes = pixel scale (arcsec/pixel) * number of pixels / 60
    sensor_width_amin = (pixel_scale * sensor_width_px) / 60.0
    sensor_height_amin = (pixel_scale * sensor_height_px) / 60.0
    return (sensor_width_amin, sensor_height_amin)

@cache
def calculate_sensor_fov_amin(focal_length_mm: float, pixel_size_um: float,
    sensor_width_px: int, sensor_height_px: int) -> Tuple[float, float]:
    if focal_length_mm <= 0 or pixel_size_um <= 0 or sensor_width_px <= 0 or sensor_height_px <= 0:
        return (0.0, 0.0)
    pixel_scale = calculate_pixel_scale(focal_length_mm, pixel_size_um)
    return _calculate_sensor_fov_amin(pixel_scale, sensor_width_px, sensor_height_px)


def calculate_fov(focal_length_mm: float, sensor_width_mm: float, sensor_height_mm: float) -> Tuple[float, float]:
    # fov = 2 * arctan(sensor dimension / (2 * focal length))
    # returned value is in degrees
    fov_width = 2 * math.degrees(math.atan((sensor_width_mm / 2) / focal_length_mm))
    fov_height = 2 * math.degrees(math.atan((sensor_height_mm / 2) / focal_length_mm))
    return (fov_width, fov_height)

def calculate_fov_pixels(sensor_width_mm: float, sensor_height_mm: float, pixel_size_um: float) -> Tuple[int, int]:
    # calculate number of pixels in width and height based on sensor size and pixel size
    # pixel size is in microns, sensor size is in mm
    width_pixels = int((sensor_width_mm * 1000) / pixel_size_um)
    height_pixels = int((sensor_height_mm * 1000) / pixel_size_um)
    return (width_pixels, height_pixels)

# sensor coverage is an approx percentage that DSO takes up on the sensor
@cache  #FIXME does this help?
def get_sensor_coverage(dso_min_axis: float, dso_maj_axis: float,
    sensor_width_amin:float, sensor_height_amin:float) -> float:

    # need at least the major axis, and sensor size
    # if not dso_min_axis or dso_min_axis <= 0:
    #     dso_min_axis = 1.0 # assume 1 arcmin if not given
    # if dso_maj_axis and sensor_width_amin and sensor_height_amin:
    #     sensor_diag = math.sqrt(sensor_height_amin**2 + sensor_width_amin**2)
    #     dso_diag = math.sqrt(dso_min_axis**2 + dso_maj_axis**2)
    #     return round(100 * (dso_diag / sensor_diag))

    # lets try comparing largest dimension of DSO to largest dimension of sensor
    # assuming sensor can be rotated to fit
    # if dso_maj_axis and sensor_width_amin and sensor_height_amin:
    #     sensor_max_dim = max(sensor_width_amin, sensor_height_amin)
    #     return round(100 * (dso_maj_axis / sensor_max_dim))
    
    # try comparing largest dim of dso to average dim of sensor
    if dso_maj_axis and sensor_width_amin and sensor_height_amin:
        sensor_avg_dim = (sensor_width_amin + sensor_height_amin) / 2.0
        return round(100 * (dso_maj_axis / sensor_avg_dim))
    else:
        return 0

# stuff to support D3 charting in detail page
def calculate_dso_positions(dso:dict, obs_lat, obs_long, obs_elevation: float, obs_date, hours_before=2, hours_after=10):
    """Calculate positions for a DSO over a night"""

    data_points = []
    # start_time = obs_date.replace(hour=19, minute=0, second=0, microsecond=0)
    # start_obs_time = datetime.combine(obs_date, time(19,0,0)).replace(tzinfo=ZoneInfo("America/Chicago"))
    print(f"calculate-dso-positions: obs_date before timezone check: {obs_date} (tzinfo={obs_date.tzinfo})")
    # if obs_date.tzinfo is None:
    
    # starting at 7PM local time - FIXME
    #FIXME - decide on time limit - maybe 14 hours total?

    HOURS_TO_SHOW = 14
    obs_date = obs_date.replace(hour=19, minute=0, second=0, microsecond=0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    obs_times = [obs_date + timedelta(hours=i) for i in range(HOURS_TO_SHOW)]

    results = ra_dec_to_altaz_airmass_multiple_times(
        ra=dso['ra_dd'],
        dec=dso['dec_dd'],
        observer_lat=obs_lat,
        observer_lon=obs_long,
        observer_elevation=obs_elevation,
        datetime_list=obs_times
    )
    for i in range(HOURS_TO_SHOW):        
        res = results[i]
        data_points.append({
            'time': obs_times[i].isoformat(), # will include timezone info
            'hour': i,
            'alt': res['altitude'],
            'azi': res['azimuth']
        })
    # print(f"data points calculated: {data_points}")
    return dso, data_points

# timezone stuff from GPT to help create sample times that ignore DST

@cache
def standard_utc_offset(tzname: str, year: int) -> timedelta:
    """
    Find the zone's standard (non-DST) UTC offset for the given year.
    We sample the 15th of each month at noon local time and take the
    smallest offset (standard time; DST is typically +1h from standard).
    """
    tz = ZoneInfo(tzname)
    offsets = set()
    for month in range(1, 13):
        dt = datetime(year, month, 15, 12, tzinfo=tz)
        offsets.add(dt.utcoffset())
    # Standard time is the smallest (most negative / least positive) offset
    return min(offsets, key=lambda td: td.total_seconds())

def datetime_with_standard_offset(
    year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0,
    tzname: str = "America/Chicago"
) -> datetime:
    """
    Create a timezone-aware datetime for the given local wall time,
    but with the zone's *standard-time* offset (no DST adjustment).
    """
    std_offset = standard_utc_offset(tzname, year)
    # Optional: try to label with the zone's standard abbreviation for that offset
    # by probing a non-DST moment that matches the chosen offset.
    tz = ZoneInfo(tzname)
    std_label = None
    for probe_month in (1, 2, 11, 12):  # months most likely in standard time
        try:
            probe = datetime(year, probe_month, 15, 12, tzinfo=tz)
        except ValueError:
            continue
        if probe.dst() == timedelta(0) and probe.utcoffset() == std_offset:
            std_label = probe.tzname()
            break

    fixed = timezone(std_offset, name=std_label or f"{tzname} (STD)")
    return datetime(year, month, day, hour, minute, second, tzinfo=fixed)

# ----------------------------
# Examples (America/Chicago)
# ----------------------------
# d1 = datetime_with_standard_offset(2025, 1, 15, 21, tzname="America/Chicago")
# d2 = datetime_with_standard_offset(2025, 7, 15, 21, tzname="America/Chicago")

# print(d1, d1.utcoffset())  # 2025-01-15 21:00:00-06:00, offset -06:00
# print(d2, d2.utcoffset())  # 2025-07-15 21:00:00-06:00, offset -06:00 (still standard time)

    
def moon_illumination_percent(dt_aware: datetime) -> float:
    """Percent of the lunar disk illuminated at the given timezone-aware datetime."""
    t = astro.Time(datetime_to_astronomy_time(dt_aware))  # create Astronomy Engine time
    info = astro.Illumination(astro.Body.Moon, t)  # illumination info for the Moon

    # Prefer the library’s fraction field if available; else compute from phase angle.
    frac = getattr(info, "phase_fraction", None)
    if frac is None:
        # phase_angle is in degrees; illuminated fraction k = (1 + cos(alpha)) / 2
        alpha_rad = math.radians(info.phase_angle)
        frac = 0.5 * (1.0 + math.cos(alpha_rad))

    return frac * 100.0

def get_data_for_dso_moon_chart(dso:dict, obs_lat, obs_long, obs_date, sample_hour = 21,
                 tz=DEFAULT_TIMEZONE) -> dict[str, list[dict[str, Union[str, float]]]]:
    # return one year's worth of alt/az for the DSO and in parallel, the degree of 
    # moon illumination for the same sample dates.
    # return dict with two lists of dicts
    
    # we want to sample at the same local time each day, ignoring DST
    # so we use our datetime_with_standard_offset function to get a fixed offset time
    # e.g. 9PM CST (UTC-6) every day of the year
    # leave the tzinfo in place, since the Javascript Date() will handle it correctly
    # and we want to show local time on the chart

    sample_points = [  {'month': 1, 'day': 1, 'hour': sample_hour}
                     , {'month': 2, 'day': 1, 'hour': sample_hour}
                     , {'month': 3, 'day': 1, 'hour': sample_hour}
                     , {'month': 4, 'day': 1, 'hour': sample_hour}
                     , {'month': 5, 'day': 1, 'hour': sample_hour}
                     , {'month': 6, 'day': 1, 'hour': sample_hour}
                     , {'month': 7, 'day': 1, 'hour': sample_hour}
                     , {'month': 8, 'day': 1, 'hour': sample_hour}
                     , {'month': 9, 'day': 1, 'hour': sample_hour}
                     , {'month': 10, 'day': 1, 'hour': sample_hour}
                     , {'month': 11, 'day': 1, 'hour': sample_hour}
                     , {'month': 12, 'day': 1, 'hour': sample_hour}
                     , {'month': 12, 'day': 31, 'hour': sample_hour}
                     ]
    sample_datetimes = [datetime_with_standard_offset(
                            year=obs_date.year,
                            month=pt['month'],
                            day=pt['day'],
                            hour=pt['hour'],
                            tzname=tz
                        ) for pt in sample_points]
    print(f"Sample datetimes for DSO/Moon chart: {sample_datetimes}")

    # now get alt/azi for the DSO at those times
    dso_positions = ra_dec_to_altaz_airmass_multiple_times(
        ra=dso['ra_dd'],
        dec=dso['dec_dd'],
        observer_lat=obs_lat,
        observer_lon=obs_long,
        datetime_list=sample_datetimes
    )
    
    dso_data = []
    moon_data = []
    for res in dso_positions:
        dso_data.append({
            'time': res['datetime'].isoformat(), # will include timezone info
            'alt': res['altitude'],
            'azi': res['azimuth'],
            # 'airmass': res['airmass'],
            # 'visible': res['visible']
        })
    
    # create lots of moon illumination data for the same range of dates
    first_date = sample_datetimes[0]
    for i in range(0, 366, 4): # one year
        dt = first_date + timedelta(days=i)
        illum = moon_illumination_percent(dt)
        moon_data.append({
            'time': dt.isoformat(), # will include timezone info
            'illum': illum
        })

    return {
        "dso_name": dso['name'],
        "dso_data": dso_data,
        "moon_data": moon_data
    }

##########################################
##########################################
# code below here was added for AI tools to be merged into original astronomy_utils.py


def ai_localize_dso(ra:float, dec:float, observer_lat: float, observer_lon: float, obs_dt: datetime, tzname: str) \
                -> tuple[float, float, float, bool, Optional[str], Optional[str], Optional[str]]:
    """
    Localize a dso at ra,dec for a given observer location and time.
    This does one DSO at a time, returning altitude, azimuth, air-mass, and visibility.
    TODO consider batching all DSO at once for efficiency.
    """


    obs_date = obs_dt
    if obs_date.tzinfo is None:
        print(f"[ai_localize_dso] surprise! obs_date is naive, assuming {tzname} timezone")
        obs_date = obs_date.replace(tzinfo=ZoneInfo(tzname))  # Default timezone

    # print(f"[ai_localize_dso] using obs_date: {obs_date} (tzinfo={obs_date.tzinfo}) passed in: {obs_dt}, tzname: {tzname})")

    if observer_lat is None or observer_lon is None:
        print("Surprise! Observer latitude or longitude is None, cannot localize DSO.")
        return dso
    
    # Calculate altitude, azimuth, and air-mass for the DSO
    # extracted from radec_to_altaz_airmass_multiple_times
    
    # Create astro.Observer location
    observer = astro.Observer(observer_lat, observer_lon, 300)  # 300 meters above sea level
    
    # Convert datetime to Astronomy Engine time format (days since J2000.0)
    astro_time = astro.Time(datetime_to_astronomy_time(obs_date))
    
    # Convert RA from degrees to hours for Astronomy Engine
    ra_hours = ra / 15.0
        
    # Convert RA/DEC to horizontal coordinates directly
    # Astronomy Engine handles the coordinate transformation internally
    horizontal = astro.Horizon(astro_time, observer, ra_hours, dec, astro.Refraction.Normal)
        
    # Calculate air mass using standard formula: sec(zenith_angle)
    # Air mass is undefined/infinite when altitude <= 0
    zenith_angle = 90.0 - horizontal.altitude
    zenith_radians = math.radians(zenith_angle)
        
    if horizontal.altitude > 0:
        airmass = 1.0 / math.cos(zenith_radians)
        # Apply more accurate airmass formula for low altitudes (Kasten & Young 1989)
        if horizontal.altitude < 20.0:
            airmass = 1.0 / (math.cos(zenith_radians) + 0.50572 * (horizontal.altitude + 6.07995)**(-1.6364))
        visible = True
    else:
        airmass = float(10000000) # float('inf')  # Object below horizon
        visible = False

    
    #  calculate rise, transit, set times using code extracted from calculate_rise_transit_set_fast    

    # Start search from the beginning of the reference day (00:00 local time)
    day_start = obs_date.replace(hour=0, minute=0, second=0, microsecond=0)
    if day_start.tzinfo is None:
        day_start = day_start.replace(tzinfo=timezone.utc)
    
    # Also get the end of the day for validation
    # day_end = day_start + timedelta(days=1)
    day_end = day_start + timedelta(days=2)  # to help with set time possibly being tomorrow
    # print(f"[ai_localize_dso] searching for RTS using day_start: {day_start}, day_end: {day_end}")
    
    # Convert to Astronomy Engine time
    start_astro_time = astro.Time(datetime_to_astronomy_time(day_start))

    try:
        # Define/redefine the "dso" star with the current RA/DEC coordinates
        # DefineStar expects: name, ra_hours, dec_degrees, distance_parsecs
        astro.DefineStar(astro.Body.Star1, ra_hours, dec, 1000.0)
        
        # Get the dso star body object
        star_body = astro.Body.Star1  # or however the dso body is accessed
        
        # Search for rise time starting from beginning of day
        rise_event = astro.SearchRiseSet(star_body, observer, astro.Direction.Rise, start_astro_time, 1, 0.0)
        rise_time = None
        if rise_event is not None:
            # Convert back to Python datetime
            rise_days = rise_event.ut
            rise_j2000_epoch = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
            candidate_rise_time = rise_j2000_epoch + timedelta(days=rise_days)
            
            # Only accept if it's within our target day
            if day_start <= candidate_rise_time < day_end:
                rise_time = candidate_rise_time
        
        # Search for transit time (hour angle = 0) starting from beginning of day
        # no, if there is a riuse time, start after that (might be tomorrow!)
        if rise_event is not None:
            transit_search_time = rise_event # astro.Time(datetime_to_astronomy_time(rise_time))
        else:
            transit_search_time = start_astro_time

        try:
            assert isinstance(transit_search_time, astro.Time)
            transit_event = astro.SearchHourAngle(star_body, observer, 0.0, transit_search_time, 1)
            transit_days = transit_event.time.ut
            transit_j2000_epoch = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
            candidate_transit_time = transit_j2000_epoch + timedelta(days=transit_days)
            
            # Only accept if it's within our target day
            if day_start <= candidate_transit_time < day_end:
                transit_time = candidate_transit_time
            else:
                transit_time = None
        except:
            transit_time = None
        
        # Search for set time starting from beginning of day
        # no, search for set time after rise time if we have it
        if rise_event is not None:
            rise_astro_time = rise_event # astro.Time(datetime_to_astronomy_time(rise_time))
        else:
            rise_astro_time = start_astro_time    
        
        assert isinstance(rise_astro_time, astro.Time)
        set_event = astro.SearchRiseSet(star_body, observer, astro.Direction.Set, rise_astro_time, 1, 0.0)
        set_time = None
        if set_event is not None:
            # Convert back to Python datetime
            set_days = set_event.ut
            set_j2000_epoch = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
            candidate_set_time = set_j2000_epoch + timedelta(days=set_days)
            
            # Only accept if it's within our target day
            if day_start <= candidate_set_time < day_end:
                set_time = candidate_set_time
        
        # Alternative approach: if no rise found, search backwards from current time
        if rise_time is None and rise_event is not None:
            # The rise we found was tomorrow, so search backwards from current time
            current_astro_time = astro.Time(datetime_to_astronomy_time(obs_date))
            
            # Search backwards by using a negative time step (search in reverse)
            # We'll search from current time backwards for up to 1 day
            try:
                # Create a time 24 hours before current time as search start
                yesterday = obs_date - timedelta(days=1)
                yesterday_astro_time = astro.Time(datetime_to_astronomy_time(yesterday))
                
                # Search for rise from yesterday to now
                past_rise_event = astro.SearchRiseSet(star_body, observer, astro.Direction.Rise, yesterday_astro_time, 2, 0.0)
                
                if past_rise_event is not None:
                    past_rise_days = past_rise_event.ut
                    past_rise_time = rise_j2000_epoch + timedelta(days=past_rise_days)
                    
                    # Accept this rise time if it's within today
                    if day_start <= past_rise_time < day_end:
                        rise_time = past_rise_time
            except:
                pass
        
        # Apply same logic for set time if needed
        if set_time is None and set_event is not None:
            try:
                yesterday = obs_date - timedelta(days=1)
                yesterday_astro_time = astro.Time(datetime_to_astronomy_time(yesterday))
                
                past_set_event = astro.SearchRiseSet(star_body, observer, astro.Direction.Set, yesterday_astro_time, 2, 0.0)
                
                if past_set_event is not None:
                    past_set_days = past_set_event.ut
                    past_set_time = rise_j2000_epoch + timedelta(days=past_set_days)
                    
                    if day_start <= past_set_time < day_end:
                        set_time = past_set_time
            except:
                pass
        
        # Determine object status
        circumpolar = (rise_event is None and set_event is None and transit_time is not None)
        never_visible = (rise_event is None and set_event is None and transit_time is None)
        
        # If we have a transit but no rise/set, check altitude to determine status
        if transit_time and (rise_time is None or set_time is None):
            transit_astro_time = astro.Time(datetime_to_astronomy_time(transit_time))
            try:
                horizontal2 = astro.Horizon(transit_astro_time, observer, ra_hours, dec, astro.Refraction.Normal)
                if horizontal2.altitude > 0:
                    circumpolar = True
                    never_visible = False
                else:
                    circumpolar = False
                    never_visible = True
            except:
                circumpolar = False
                never_visible = True
        
    except Exception as e:
        # If anything fails, fall back to None values
        print(f"Exception in rise-set-transit-fast processing RA={ra}, DEC={dec}: {e}")
        rise_time = None
        transit_time = None
        set_time = None
        circumpolar = False
        never_visible = True

    # convert rise, transit, set times to UTC in format strftime('%Y-%m-%dT%H:%M:%SZ)
    # this allows lexical string comparison in the SQL generated by the AI
    if rise_time is not None and isinstance((rise_time), datetime):
        # rise_time_str = rise_time.astimezone(ZoneInfo(tzname)).strftime("%H:%M")
        rise_time_iso = rise_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    else:
        rise_time_iso = None
        
    if transit_time is not None and isinstance((transit_time), datetime):
        # transit_time_str = transit_time.astimezone(ZoneInfo(tzname)).strftime("%H:%M")
        transit_time_iso = transit_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    else:
        transit_time_iso = None
    if set_time is not None and isinstance((set_time), datetime):
        # set_time_str = set_time.astimezone(ZoneInfo(tzname)).strftime("%H:%M")
        set_time_iso = set_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    else:
        set_time_iso = None
    
    return horizontal.altitude, horizontal.azimuth, airmass, visible, \
            rise_time_iso, transit_time_iso, set_time_iso

import sqlite3
from pathlib import Path

# THis is OLD code saved for reference
def ai_localize_and_fetch_dsos(ai_query:str, db_path:Path, observer_lat: float, observer_lon: float,
                            observe_date:str, observe_time: str, timezone:str) -> list[dict[str, str]]:
    """ Create a temporary table with localized data for all DSOs
            then run the ai-generated sql query against the temp table
            then return the list of DSO dicts matching the query with localization data included.  
        Note that in web context we have to start from scratch each time, since no caching between calls
        Do temp table creation in single transaction to keep it speedy.
    """

    # verify localization data (assumes caller has set up defaults if needed)

    assert observer_lat is not None and observer_lon is not None, "Observer latitude and longitude must be provided."
    assert observe_date is not None, "Observer date must be provided."
    assert observe_time is not None, "Observer time must be provided."
    assert timezone is not None, "Observer timezone must be provided."

    # ensure we have no seconds in time string (AI puts them there sometimes)
    if len(observe_time.split(":")) == 3:
        observe_time = ":".join(observe_time.split(":")[0:2])

    date_iso = f"{observe_date}T{observe_time}"
    dt = datetime.strptime(f"{observe_date} {observe_time}", "%Y-%m-%d %H:%M")

    # attach timezone
    dt = dt.replace(tzinfo=ZoneInfo(timezone))
    print(f"[ai_localize_and_fetch_dsos] recreate full datetime = {dt.isoformat()} for ({timezone})")

    # create temp table
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # create a temporary in-memory table we can map localizations into
        # make it match the DeepSpaceObject fields we want to fill in
        start = perf_counter()
        cursor.execute('''
            DROP TABLE IF EXISTS dso_localized;
        ''')
        
        cursor.execute("""
            CREATE TEMP TABLE dso_localized (    
                dso_id TEXT PRIMARY KEY,
                catalog TEXT,
                name TEXT,
                ra_dd REAL,
                dec_dd REAL,
                type TEXT,
                class TEXT,
                vis_mag REAL,
                maj_axis REAL,
                min_axis REAL,
                size TEXT,
                constellation TEXT,
                constellation_abbr TEXT,
                altitude REAL,
                azimuth REAL,
                air_mass REAL,
                rise_time TEXT, /* iso format full datetime string for utc */
                set_time TEXT, /* iso format full datetime string for utc */
                transit_time TEXT /* iso format full datetime string for utc */
            );
        """)
        cursor.execute("""
                        
            INSERT INTO dso_localized (dso_id, catalog, name, ra_dd, dec_dd, type, class, vis_mag, maj_axis, min_axis, size,
                constellation, constellation_abbr, altitude, azimuth, air_mass, rise_time, set_time, transit_time)
            SELECT dso_id, catalog, name, ra_dd, dec_dd, type, class, vis_mag, maj_axis, min_axis, size,
                constellation, constellation_abbr, NULL, NULL, NULL, NULL, NULL, NULL
            FROM dso;
        """)
    except Exception as e:
        print(f"Error creating temporary localized DSO table: {e}")
        return [{}]
    
    print(f"[ai_localize_and_fetch_dsos] Created temporary dso_localized table in {perf_counter() - start:.2f} seconds")

    # fetch all the raw dso data from the temp table we just created
    try:
        cursor.execute("""
            SELECT * from dso_localized;
        """)
        all_dsos = cursor.fetchall()

    except Exception as e:
        print(f"Error fetching raw DSOs from temp localized table: {e}")
        return [{}]

    conn.commit()
    print(f"[ai_localize_and_fetch_dsos] Fetched {len(all_dsos)} DSOs for localization")

    # now loop through all DSOs and compute localization data
    try:
        start = perf_counter()        
        # open transaction - for now, insert row by row, try batching later if needed
        conn.execute("BEGIN;")
        #     
        # for each DSO, compute localization based on observer_context if present
        for dso_row in all_dsos:
            # default values
            altitude = None
            azimuth = None
            air_mass = None
            rise_time = None
            set_time = None
            transit_time = None

            # if observer_context has location compute localization
            # we already created a datetime object 'dt' above
            if observer_lat is not None and observer_lon is not None:

                altitude, azimuth, air_mass, visible, rise_time, transit_time, set_time = \
                  ai_localize_dso(dso_row['ra_dd'], dso_row['dec_dd'],
                     observer_lat, observer_lon,
                     dt.isoformat(), timezone)
            
            # insert into temp table using DSO id as key
            cursor.execute("""
                UPDATE dso_localized
                SET altitude = ?, azimuth = ?, air_mass = ?, rise_time = ?, set_time = ?, transit_time = ?
                WHERE dso_id = ?;
                """, (
                altitude,
                azimuth,
                air_mass,
                rise_time,
                set_time,
                transit_time,
                dso_row['dso_id']
            ))
            # print(f"[ai_localize_and_fetch_dsos] Localized DSO {dso_row['name']} (ID: {dso_row['dso_id']})")

        # commit transaction
        conn.commit()
        print(f"[ai_localize_and_fetch_dsos] Localized all DSOs in {perf_counter() - start:.2f} seconds")
    
    except Exception as e:
        conn.rollback()
        print(f"Error localizing DSOs and updating temp table: {e}")
        return [{}] 

    # now finally run the AI-generated query against the temp table
    try:
        start = perf_counter()
        print(f"[ai_localize_and_fetch_dsos] Running AI-generated query: {ai_query}")
        cursor.execute(ai_query)
        
        # convert to real dicts since I think Sqlite.Row goes away when conneciton closed?
        results = [dict(row) for row in cursor.fetchall()]

        return results

    except Exception as e:
        print(f"Error running AI-generated query: {e}")
        return [{}]

def ai_convert_utc_iso_to_local(iso_str: str, tzname: str) -> Optional[str]:
    """Convert an ISO time string in UTC to local time string in given timezone."""
    if iso_str is None:
        return None
    try:
        dt_utc = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        dt_local = dt_utc.astimezone(ZoneInfo(tzname))
        return dt_local.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        print(f"Error converting ISO time to local: {e}")
        return None