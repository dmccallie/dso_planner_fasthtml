# various data fetch, filter, localize, sort functions
from curses import raw
from functools import cache
import hashlib
from random import random
import sqlite3
from pathlib import Path

from datetime import datetime, time, timedelta, timezone
from random import random

from typing import Optional
from zoneinfo import ZoneInfo

from astronomy_utils import ai_localize_dso, calculate_pixel_scale, calculate_rise_transit_set_fast, calculate_sensor_fov_amin, find_all_twilight_times, get_sensor_coverage, ra_dec_to_altaz_airmass_multiple_times
from astropy import units as u
from astropy.coordinates import SkyCoord, EarthLocation, AltAz

from astronomy_utils import MIN_AIRMASS, MIN_ALT_FOR_COLOR


def load_localize_filter_expand_sort_dso_data(session_id: str, db_path: Path, filters:dict, localization:dict, sort_key:str, order:str) -> list[dict]:
    # ai version to load raw data, apply ai or plain localization, then filter, sort, 
    #   and return DSO data with the extra fields needed for display and filtering
    # uses session_id to access db or cached localization data if needed.

    print("load_localize_filter_expand_sort_dso_data Localization parameters:", localization)
    print("load_localize_filter_expand_sort_dso_data Filter parameters:", filters)

    # make sure we have the required localization parameters or fail since defaults have already been applied
    assert 'lat' in localization, "Localization must include 'lat'"
    assert 'lon' in localization, "Localization must include 'lon'"
    assert 'elevation' in localization, "Localization must include 'elevation'"
    assert 'date' in localization, "Localization must include 'date'"
    assert 'hours_start' in localization, "Localization must include 'hours_start'"

    # assert 'timezone' in localization, "Localization must include 'timezone'"
    
    user_timezone = localization.get("timezone", "America/Chicago") # FIXME
    elevation = localization.get('elevation', 300.0) # default elevation in meters if not provided FIXME

    # localize and select subset of data based on ai_query and localization
    # will create table containing localized data, then apply SQL to fetch subset
    dso_list = get_localized_dso_data(session_id, db_path, localization['sql_query'], localization['lat'], localization['lon'],
                                      localization['elevation'], localization['date'], localization['hours_start'], user_timezone)
    
    print(f"dso list returns from get_localized_dso_data {[('name', row['name'], 'altitude', row['altitude']) for row in dso_list]}")

    #expand dso_data and apply dynamic filtering and sorting based on filters and sort_key
    dso_list = expand_dso_data_and_apply_dynamic_filters_and_sorting(dso_list, localization, filters, sort_key, order)

    return dso_list

def get_localized_dso_data(session_id: str, db_path: Path, sql_query: str, observer_lat: float, observer_lon: float,
                            elevation: float, observe_date_str:str, observe_time_str: str, timezone:str ) -> list[dict]:
    # uses a session-keyed db table to cache localized data accros calls where localization is the same
    # observe date and time are strings, assumed to be in local timezone

    # will create a CTE that joins the raw dso data with the localized data for the current session and localization,
    #  then apply the passed in sql_query to that CTE to fetch the localized subset of data needed for display and filtering
    # that way we don't need a temp table (which doesn't support parameterized queries) 

    # here's the CTE wrapper function (essentially CTE is a dynamic view)
    def wrap_ai_sql_in_cte(ai_sql: str) -> str:
        ai_sql = ai_sql.strip().rstrip(";")
        return f"""
            WITH dso_localized AS (
                SELECT dso.*, ld.altitude, ld.azimuth, ld.air_mass,
                    ld.rise_time, ld.set_time, ld.transit_time
                FROM dso
                LEFT JOIN dso_localization_values ld ON dso.dso_id = ld.dso_id
                WHERE ld.session_id = ? AND ld.loc_hash = ?
            )
            {ai_sql}
        """
    
    # hash for localization parameters to use as key in db to cache localized data for each unique localization/session
    def create_hash(observer_lat: float, observer_lon: float, elevation: float, observe_date_iso:str) -> str:
        raw = f"lat:{observer_lat}_lon:{observer_lon}_elev:{elevation}_date:{observe_date_iso}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
    
    
    assert sql_query is not None and sql_query.strip() != "", "SQL query was missing from localization"
    assert session_id is not None and session_id.strip() != "", "Session ID is required to fetch localized DSO data"
    assert observer_lat is not None, "Observer latitude is required to fetch localized DSO data"
    assert observer_lon is not None, "Observer longitude is required to fetch localized DSO data"
    assert elevation is not None, "Observer elevation is required to fetch localized DSO data"
    assert observe_date_str is not None and observe_date_str.strip() != "", "Observe date is required to fetch localized DSO data"
    assert observe_time_str is not None and observe_time_str.strip() != "", "Observe time is required to fetch localized DSO data"
    assert timezone is not None and timezone.strip() != "", "Timezone is required to fetch localized DSO data"

    # first time, make sure the localized dso table and indexes exist
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS dso_localization_values (
                session_id TEXT NOT NULL,
                loc_hash   TEXT NOT NULL,
                dso_id TEXT NOT NULL,

                altitude REAL,
                azimuth REAL,
                air_mass REAL,
                rise_time TEXT, /* iso format full datetime string for utc */
                set_time TEXT, /* iso format full datetime string for utc */
                transit_time TEXT, /* iso format full datetime string for utc */

                created_at TEXT NOT NULL,
            PRIMARY KEY (session_id, loc_hash, dso_id),
            FOREIGN KEY (dso_id) REFERENCES dso(dso_id)
            );
            ''')
        
        # create index on session_id and loc_hash for faster lookups
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS dso_localization_values_session_idx
            ON dso_localization_values(session_id, loc_hash);
                       ''')
        # may need to create keys on dso_id for the join 
        print("Ensured dso_localization_values table and indexes exist in database")
    
    # clean up date and time
    # ensure we have no seconds in time string (AI puts them there sometimes)
    if len(observe_time_str.split(":")) == 3:
        observe_time_str = ":".join(observe_time_str.split(":")[0:2])
    
    observe_date = datetime.strptime(observe_date_str, '%Y-%m-%d') # user's string
    observe_date = observe_date.replace(tzinfo=ZoneInfo(timezone)) # user's tz
        
    observe_dt = datetime.combine(observe_date, 
                   datetime.strptime(observe_time_str, "%H:%M").time()).replace(tzinfo=ZoneInfo(timezone)) 

    print(f"[ai_localize_and_fetch_dsos] recreate full datetime = {observe_dt.isoformat()} for ({timezone})")

    loc_hash = create_hash(observer_lat, observer_lon, elevation, observe_dt.isoformat())

    # wrap the ai sql inside the CTE that joins with the localized data for this session and localization
    final_sql_query = wrap_ai_sql_in_cte(sql_query)

    # see if we already have data for this session and localization
    print(f"Final SQL Query: {final_sql_query} with params session_id={session_id}, loc_hash={loc_hash}")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        # first see if there are any localized records for this session and localization
        cursor.execute('SELECT COUNT(*) as count FROM dso_localization_values WHERE session_id = ? AND loc_hash = ?', (session_id, loc_hash))
        count_row = cursor.fetchone()
        count = count_row['count'] if count_row is not None else 0
        if count > 0:
            cursor.execute(final_sql_query, (session_id, loc_hash))
            rows = cursor.fetchall()
            if rows and len(rows) > 0:
                print(f"FOUND {len(rows)} localized DSO records for session {session_id} and loc_hash {loc_hash}")
                return [dict(row) for row in rows]
            else:
                # our query found no qualified records, but we do have localized data for this session and localization, so return empty result
                print(f"NO matching DSO records found for session {session_id} and loc_hash {loc_hash}")
                return []        
            
        # we have no data for this session and localization, so we need to create it
        print(f"No existing localized DSO records found for session {session_id} and loc_hash {loc_hash}, SO CREATING NEW localized data")

        # spin through the raw DSO data and calculate the localization values for each, then insert into the localized values table
        conn.commit()

        cursor.execute('SELECT * FROM dso')
        raw_dsos = cursor.fetchall()
        conn.commit()

        # for each DSO, calculate the localization values 
        # do this all in memory because there are only a few hundred dsos.
        localization_records = [] # for a batch insert after the loop

        for dso in raw_dsos:
            dso_id = dso['dso_id']
            ra_dd = dso['ra_dd']
            dec_dd = dso['dec_dd']

            altitude, azimuth, air_mass, visible, rise_time, transit_time, set_time = \
                  ai_localize_dso(ra_dd, dec_dd, observer_lat, observer_lon, observe_dt, timezone)
            
            localization_records.append((session_id, loc_hash, dso_id, altitude, azimuth, air_mass, rise_time, set_time, transit_time, datetime.now().isoformat()))

        # batch insert all the localization records for this session and localization
        cursor.executemany('''
            INSERT INTO dso_localization_values (session_id, loc_hash, dso_id,
                altitude, azimuth, air_mass, rise_time, set_time, transit_time, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', localization_records)
        
        conn.commit()

        # now fetch the data using the final_sql_query with the CTE that joins with the localized data for this session and localization
        cursor.execute(final_sql_query, (session_id, loc_hash))
        rows = cursor.fetchall()
        if rows and len(rows) > 0:
            print(f"After creating localized data, found {len(rows)} localized DSO records for session {session_id} and loc_hash {loc_hash}")
            return [dict(row) for row in rows]

    return []


def expand_dso_data_and_apply_dynamic_filters_and_sorting(dso_list: list[dict], localization:dict, filters:dict,
                                                           sort_key:str, order:str) -> list[dict]:
    # input is raw list of dso dicts with localization values (altitude, azimuth, air_mass, rise_time, set_time, transit_time)
    # output is list of dso dicts with extra fields needed for display and filtering

    # try to filter first to reduce computation of expansion fields.
 
    user_timezone = localization.get("timezone", "America/Chicago") #FIXME

    # we may skip rows to save time, so build a new list of results rather than modifying in place
    dso_results = []

    # precompute sensor size in arcmin if we have the needed localization params, since it's used in the loop
    if localization.get('fl_mm', None) is not None and \
        localization.get('px_um', 0.0) != 0.0 and \
        localization.get('rows', 0) != 0 and \
        localization.get('cols', 0) != 0:
        # compute pixel scale in arcsec/pixel
        fl_mm = localization['fl_mm']
        px_um = localization['px_um']
        fov_width_px = localization['cols']
        fov_height_px = localization['rows']
        width_amin, height_amin = calculate_sensor_fov_amin(fl_mm, px_um,fov_width_px, fov_height_px)
    else:
        width_amin, height_amin = None, None

    for dso in dso_list:
        
        # filter by name match (field 'q' in filters) if provided
        # FIXME make this more robust like a fuzzy match
        if 'q' in filters and filters['q'].strip() != "":
            q = filters['q'].strip().lower()
            if q not in dso['name'].lower() and q not in dso['catalog'].lower():
                continue
        
        # filter out by class if needed
        # for 'constellation': ['all'], or list of constellations to include, e.g. ['Ori', 'And']
        # for 'classes': either ['all'] for all, or list ['Neb']
        # same for 'object_types': ['all'' for all, or ['SNR', 'xxx'],
        if 'classes' in filters and filters['classes'] != ['all']:
            if dso['class'] not in filters['classes']:
                continue
        
        # filter out by constellation if needed
        if 'constellation' in filters and filters['constellation'] != ['all']:
            if dso['constellation_abbr'] not in filters['constellation']:
                continue
        
        # filter out by object type if needed
        if 'object_types' in filters and filters['object_types'] != ['all']:
            if dso['type'] not in filters['object_types']:
                continue
        
        # add score field for sorting and filtering (random for now)
        dso['score'] = int(100 * random()) # random int from 0 to 100 for now

        # fix up rise set transit times to be localized strings for display, and sortable strings for sorting
        # FIXME consider move to Td display logic
        for key in ['rise_time', 'set_time', 'transit_time']:
            rt = dso.get(key, None)
            if rt is not None:
                try:
                    rt_dt = datetime.strptime(rt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    rt_dt = rt_dt.astimezone(ZoneInfo(user_timezone))
                    dso[key] = rt_dt.strftime("%H:%M")
                    dso[f"{key}_sort"] = rt_dt.strftime("%Y-%m-%d %H:%M")
                except Exception as e:
                    print(f"Error parsing {key} for DSO {dso['name']} with value {rt}: {e}")
                    dso[key] = "---"
                    dso[f"{key}_sort"] = ""
            else:
                dso[key] = "---"
                dso[f"{key}_sort"] = ""


        # extract localization params to fill out the extra hours_viz and coverage fields needed for filtering and display
        # FIXME make this a common routine
        lat = localization.get('lat', 38.76918)
        lon = localization.get('lon', -94.65635)
        elevation = localization.get('elevation', 300.0)
        start_date = localization.get('date', None)
        start_time = localization.get('hours_start', "20:00") # default to 8PM local time if not provided
        
        if start_date is None:
            start_date = datetime.now(tz=ZoneInfo(user_timezone))
        else:
            start_date = datetime.strptime(start_date, '%Y-%m-%d')
            start_date = start_date.replace(tzinfo=ZoneInfo(user_timezone))

        if width_amin is not None and height_amin is not None and \
           dso.get('maj_axis', None) is not None:  # only must have major axis

            dso_maj_axis = dso['maj_axis'] # arcmin
            dso_min_axis = dso['min_axis']

            sens_cov = get_sensor_coverage(dso_min_axis, dso_maj_axis, width_amin, height_amin)

            dso['coverage'] = int(sens_cov) # already in percent (0.0-100.0)
        else:
            dso['coverage'] = 0

        # filter out records by coverage
        if 'min_coverage' in filters and dso['coverage'] < filters['min_coverage']:
            continue
        if 'max_coverage' in filters and dso['coverage'] > filters['max_coverage']:
            continue

        # for now, first time will be what user queried, then add 5 more obs times at 1 hour intervals
        # consider rounding to nearest hour? but that would mess up Agent's logic?
        NUMBER_OBS_TIMES = 6
        start_obs_time = datetime.combine(start_date, datetime.strptime(start_time, "%H:%M").time()).replace(tzinfo=ZoneInfo(user_timezone)) 
        obs_times = [start_obs_time + timedelta(hours=i) for i in range(0, NUMBER_OBS_TIMES)]
        
        # debugging - this one is correct
        results = ra_dec_to_altaz_airmass_multiple_times(
            ra=dso['ra_dd'],
            dec=dso['dec_dd'],
            observer_lat=lat,
            observer_lon=lon,
            observer_elevation=elevation,
            datetime_list=obs_times
        )

        number_hours_viz = 0 # how many times is airmass < 3.0
        for i in range(0, NUMBER_OBS_TIMES):
            res = results[i]
            # add the actual time for each obs, for column header
            # FIXME should be normalized into a dict of these observations
            dso[f'obsTime{i}_dt'] = obs_times[i]
            if res is None:
                dso[f'obsTime{i}'] = "---/---<br>---"
                dso[f'obsTime{i}_alt'] = -90.0
            else:
                alt, az, airmass = res['altitude'], res['azimuth'], res['airmass']
                dso[f'obsTime{i}'] = f"{alt:.0f}\u00B0/{az:.0f}\u00B0<br>{airmass:.1f}"
                dso[f'obsTime{i}_alt'] = alt # used to drive color scale
                if airmass is not None and isinstance(airmass, float) and airmass <= MIN_AIRMASS:
                    number_hours_viz += 1        
    
        # fake hours_viz for now - revisit FIXME
        dso['hours_viz'] = number_hours_viz

        # filter by hours_viz
        if 'min_hours_viz' in filters and dso['hours_viz'] < filters['min_hours_viz']:
            continue
        if 'max_hours_viz' in filters and dso['hours_viz'] > filters['max_hours_viz']:
            continue

        dso_results.append(dso) # our output

        # skip filter by score

    # sort
    VALID_SORTS = { "name", "catalog", "class", "constellation_abbr", "score", "type", "hours_viz", "vis_mag", "coverage", "rise_time", "transit_time", "set_time"}

    if sort_key not in VALID_SORTS:
        print(f"Invalid sort key {sort_key}, defaulting to 'ra_dd'")
        sort_key = 'ra_dd' # default sort by RA
    if sort_key == 'rise_time':
        sort_key = 'rise_time_sort' # use sortable version of rise time
    if sort_key == 'set_time':
        sort_key = 'set_time_sort' # use sortable version of set time
    if sort_key == 'transit_time':
        sort_key = 'transit_time_sort' # use sortable version of transit time

    sorted_dso_list = sorted(dso_results, key=lambda x: x[sort_key], reverse=(order=='desc'))

    return sorted_dso_list


# This is the OLD pre-ai code - save for reference
def OLDload_filter_localize_data(db_path: Path, 
    filters:dict, localization:dict,
    sort_key:str, order:str) -> list[dict]:
    # since we have no state between calls, just load all data each time
    # with larger db we could cache some of this in redis or similar
    
    # filters is dict with optional keys:
    #   name (str, substring match, case insensitive)
    #   class (list of str, e.g. ['Galaxy', 'Nebula'])
    #   constellation_abbr (list of str, e.g. ['Ori', 'And'])
    #   min_mag (float)
    #   max_mag (float)
    #   min_size (float, arcmin)
    #   max_size (float, arcmin)
    #   min_score (float)
    #   max_score (float)
    #   min_hours_viz (float)
    #   max_hours_viz (float)
    # localization is dict with keys:
    #   lat (float, degrees)
    #   lon (float, degrees)
    #   elevation (float, meters)
    #   timezone (str, e.g. 'US/Eastern')
    #   date (str, 'YYYY-MM-DD')
    # sort_key is one of the sortable fields in the final data dict
    # order is 'asc' or 'desc'
    # returns list of dicts with all fields from dso table plus:
    #   Coverage (float, percent of sensor's field of view covered by object)
    #   Rise_time (str, localized rise time, e.g. '20:30')
    #   Transit_time (str, localized transit time, e.g. '23:15')
    #   Set_time (str, localized set time, e.g. '02:00')
    #   Score (float, computed score based on visibility, mag, size, etc.)
    #   HoursViz (float, hours usefully visible)
    #   ObsTime1 (datetime, date/time for observation 1)
    #   ObsTime2 (datetime, date/time for observation 2)
    #   ObsTime3 (datetime, date/time for observation 3)
    #   ObsTime4 (datetime, date/time for observation 4)
    #   ObsTime5 (datetime, date/time for observation 5)

    # db_path is path to sqlite db created by load_stellarium_data_to_sqlite

    # first get all raw data that can be filtered without localization
    print("load_dso_subset Localization parameters:", localization)
    print("load_dso_subset Filter parameters:", filters)

    raw_data = load_dso_subset(
        db_path,
        name=filters.get('q',''),
        cls=filters.get('classes',['all']),
        constellation_abbrev=filters.get('constellation',["all"]),
        object_types=filters.get('object_types',["all"])
    )

    # then apply localization to the raw data
    # for testing, let's just add the new fields to each dict


    lat = localization.get('lat', 38.76918)
    lon = localization.get('lon', -94.65635)
    elev = localization.get('elevation', 330.0)
    start_date_str = localization.get('date', None)
    
    if start_date_str is None:
        start_date = datetime.now(tz=ZoneInfo("America/Chicago"))
    else:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        start_date = start_date.replace(tzinfo=ZoneInfo("America/Chicago"))
    
    # for now, assume tz=ZoneInfo("America/Chicago")
    # FIXME - use localization timezone
    
    # next calc rise/transit/set, for each object
    # more efficient to do all at once

    # get rise/transit/set times for the given date and location
    # use beginning of the day
    # cache the data if date is same
    # day_start = datetime.now(tz=ZoneInfo("America/Chicago")). \
    #     replace(hour=0, minute=0, second=0, microsecond=0)
    
    # we @cache the function, so convert list to tuple for hashability
    radec_items = tuple( (item['ra_dd'], item['dec_dd']) for item in raw_data )
    rts = calculate_rise_transit_set_fast(
        ra_dec_list=radec_items,
        observer_lat=lat,
        observer_lon=lon,
        elevation_meters=elev,
        reference_date=start_date,
    )

    # and assign to each item
    # fix me move this to Td function
    for i, item in enumerate(raw_data):
        rt = rts[i]['rise_time']
        if isinstance(rt, datetime):
            # convert to localized string
            # should use localization timezone
            # for now, assume tz=ZoneInfo("America/Chicago")
            rt = rt.astimezone(tz=ZoneInfo("America/Chicago"))
            # format as 24hr time, no date
            item['rise_time'] = rt.strftime("%H:%M")
            item['rise_time_sort'] = rt.strftime("%Y-%m-%d %H:%M")
            # item['rise'] = rt.strftime("%d %b %Y <br> %I:%M %p")
        else:
            item['rise_time'] = rt
            item['rise_time_sort'] = ""

        rt = rts[i]['transit_time']
        if isinstance(rt, datetime):
            rt = rt.astimezone(tz=ZoneInfo("America/Chicago"))
            item['transit_time'] = rt.strftime("%H:%M")
            item['transit_time_sort'] = rt.strftime("%Y-%m-%d %H:%M")
        else:
            item['transit_time'] = rt
            item['transit_time_sort'] = ""

        rt = rts[i]['set_time']
        if isinstance(rt, datetime):
            rt = rt.astimezone(tz=ZoneInfo("America/Chicago"))
            item['set_time'] = rt.strftime("%H:%M")
            item['set_time_sort'] = rt.strftime("%Y-%m-%d %H:%M")
        else:
            item['set_time'] = rt
            item['set_time_sort'] = ""

    # find astro twilight time for the date - our first obs time
    # this is @cache so only computed once per date / location
    dark_times = find_all_twilight_times(
        observer_lat=lat,
        observer_lon=lon,
        reference_date=start_date,
        elevation_meters=elev
    )

    print("Dark times found:", dark_times)

    # lets use astro dark end time as first obs time
    # but round "back" to the prior hour
    if dark_times['astronomical_evening'] is None:
        # no astro twilight, use 21:00 local time
        print("No astro twilight end time found, using 21:00")
        start_obs_time = datetime.combine(start_date, time(21,0,0)).replace(tzinfo=ZoneInfo("America/Chicago"))
    else:
        start_obs_time = dark_times['astronomical_evening']
        start_obs_time = start_obs_time.replace(minute=0, second=0, microsecond=0)    

    for item in raw_data:
        item['score'] = int(100 * random()) # random int from 0 to 100 for now

        # add 5 observations with alt/az/airmass for each object
        # make sure start_date is using local timezone

        # start_obs_time = datetime.combine(start_date, time(21,0,0)).replace(tzinfo=ZoneInfo("America/Chicago"))
        # start_obs_time = start_date
        # start_obs_time = datetime.now(tz=ZoneInfo("America/Chicago")) # should use localization timezone
        obs_times = [start_obs_time + timedelta(hours=i) for i in range(0, 6)]

        results = ra_dec_to_altaz_airmass_multiple_times(
            ra=item['ra_dd'],
            dec=item['dec_dd'],
            observer_lat=lat,
            observer_lon=lon,
            datetime_list=obs_times
        )

        number_hours_viz = 0 # how many times is airmass < 3.0
        for i in range(0, 6):
            res = results[i]
            # add the actual time for each obs, for column header
            # FIXME should be normalized into a dict of these observations
            item[f'obsTime{i}_dt'] = obs_times[i]
            if res is None:
                item[f'obsTime{i}'] = "---/---<br>---"
                item[f'obsTime{i}_alt'] = -90.0
            else:
                alt, az, airmass = res['altitude'], res['azimuth'], res['airmass']
                item[f'obsTime{i}'] = f"{alt:.0f}\u00B0/{az:.0f}\u00B0<br>{airmass:.1f}"
                item[f'obsTime{i}_alt'] = alt # used to drive color scale
                if airmass is not None and isinstance(airmass, float) and airmass <= MIN_AIRMASS:
                    number_hours_viz += 1
    
        item['hours_viz'] = number_hours_viz

        # now compute coverage based on size
        if localization.get('fl_mm', None) is not None and \
           localization.get('px_um', 0.0) != 0.0 and \
           localization.get('rows', 0) != 0 and \
           localization.get('cols', 0) != 0 and \
           item.get('maj_axis', None) is not None:  # only must have major axis

            # compute pixel scale in arcsec/pixel
            fl_mm = localization['fl_mm']
            px_um = localization['px_um']
            fov_width_px = localization['cols']
            fov_height_px = localization['rows']
            width_amin, height_amin = calculate_sensor_fov_amin(fl_mm, px_um,fov_width_px, fov_height_px)

            dso_maj_axis = item['maj_axis'] # arcmin
            dso_min_axis = item['min_axis']

            sens_cov = get_sensor_coverage(dso_min_axis, dso_maj_axis, width_amin, height_amin)

            item['coverage'] = int(sens_cov) # already in percent (0.0-100.0)
        else:
            item['coverage'] = 0

    # finally apply subseleting filters
    # if 'min_mag' in filters:
    #     raw_data = [item for item in raw_data if item.get('vis_mag') is not None and item['vis_mag'] <= filters['min_mag']]
    # if 'max_mag' in filters:
    #     raw_data = [item for item in raw_data if item.get('vis_mag') is not None and item['vis_mag'] >= filters['max_mag']]
    if 'min_coverage' in filters:
        raw_data = [item for item in raw_data if item.get('coverage') is not None and item['coverage'] >= filters['min_coverage']]
    if 'max_coverage' in filters:
        raw_data = [item for item in raw_data if item.get('coverage') is not None and item['coverage'] <= filters['max_coverage']]
    # hack test using hours_viz as score
    # if 'min_hours_viz' in filters:
    #     raw_data = [item for item in raw_data if item.get('hours_viz') is not None and item['hours_viz'] >= filters['min_score']]
    if 'max_score' in filters:
        raw_data = [item for item in raw_data if item.get('score') is not None and item['score'] <= filters['max_score']]
    if 'min_hours_viz' in filters:
        raw_data = [item for item in raw_data if item.get('hours_viz') is not None and item['hours_viz'] >= filters['min_hours_viz']]
    # if 'max_hours_viz' in filters:
    #     raw_data = [item for item in raw_data if item.get('hours_viz') is not None and item['hours_viz'] <= filters['max_hours_viz']]
    if 'max_hours_viz' in filters:
        raw_data = [item for item in raw_data if item.get('hours_viz') is not None and item['hours_viz'] <= filters['max_hours_viz']]

    # sorting
    VALID_SORTS = { "name", "catalog", "class", "constellation_abbr", "score", "type", "hours_viz", "vis_mag", "coverage", "rise", "transit", "set"}

    if sort_key not in VALID_SORTS:
        sort_key = 'ra_dd' # default sort by RA
    if sort_key == 'rise':
        sort_key = 'rise_sort' # use sortable version of rise time
    if sort_key == 'set':
        sort_key = 'set_sort' # use sortable version of set time
    if sort_key == 'transit':
        sort_key = 'transit_sort' # use sortable version of transit time

    raw_data = sorted(raw_data, key=lambda x: x[sort_key], reverse=(order=='desc'))

    return raw_data

def load_dso_by_id(dso_id: str, db_path: Path) -> Optional[dict]:
    # using db from load_stellarium_data_to_sqlite.py
    # fetch all fields from dso table for given dso_id
    # return as dict
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM dso WHERE dso_id = ?', (dso_id,))
    row = cursor.fetchone() # returns a tuple, not a dict!
    conn.close()
    if row is None:
        return None
    return dict(zip([column[0] for column in cursor.description], row))

# not currently used
def load_dso_subset(db_path: Path, name: str, cls: list[str], 
                    constellation_abbrev: list[str], object_types:list[str]) -> list[dict]:
    # using db from load_stellarium_data_to_sqlite.py
    # optional parameters: name (str), cls (list of str), constellation_abbrev (list of str)
    # if parameter is None or empty, ignore it
    # return list of dicts with all fields from dso table
    # fields: dso_id, catalog, name, ra_dd, dec_dd, type, class, vis_mag,
    #  maj_axis, min_axis, size, constellation, constellation_abbr,

    # we will load from scratch each time, as db is small
    # maybe add caching later if needed

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    # build query using the optional parameters
    # uses parameterized queries to prevent SQL injection
    query = 'SELECT * FROM dso WHERE 1=1'
    params = []
    
    if name and name.strip() != "":
        # add wildcards for LIKE search
        # search catalog and name fields
        query += ' AND (LOWER(name) LIKE ? OR LOWER(catalog) LIKE ?)'
        params.append(f'%{name.lower()}%')
        params.append(f'%{name.lower()}%')
    if cls == ["all"]:
        pass
    elif cls and len(cls) > 0:
        query += ' AND class IN ({})'.format(','.join('?' * len(cls)))
        params.extend(cls)
    # hack list with "all" means no filtering
    if constellation_abbrev == ["all"]:
        pass
    elif constellation_abbrev:
        query += ' AND constellation_abbr IN ({})'.format(','.join('?' * len(constellation_abbrev)))
        params.extend(constellation_abbrev)
    # filter by list of object types, unless ["all"] is selected
    if object_types == ["all"]:
        pass
    elif object_types and len(object_types) > 0:
        query += ' AND type IN ({})'.format(','.join('?' * len(object_types)))
        params.extend(object_types)
    # query += ' ORDER BY ra_dd ASC' # default sort ascending by RA
    cursor.execute(query, params)
    rows = cursor.fetchall() # returns list of tuples, not dicts!
    conn.close()

    # convert rows to list of dicts
    return [dict(zip([column[0] for column in cursor.description], row)) for row in rows]

def get_unique_classes(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT class FROM dso ORDER BY class')
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows if row[0] is not None]

def get_unique_constellation_abbrs(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT constellation_abbr FROM dso ORDER BY constellation_abbr')
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows if row[0] is not None]

# get distinct pairs of constgellation_abbr and constellation
def get_unique_constellations(db_path: Path) -> list[tuple[str,str]]:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT constellation_abbr, constellation FROM dso ORDER BY constellation')
    rows = cursor.fetchall()
    conn.close()
    return [(row[0], row[1]) for row in rows if row[0] is not None and row[1] is not None]  


