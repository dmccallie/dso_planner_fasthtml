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
    
    # print(f"dso list returns from get_localized_dso_data {[('name', row['name'], 'altitude', row['altitude']) for row in dso_list]}")
    print(f"Fetched {len(dso_list)} localized DSO records for session {session_id} from database")

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
                angular_distance_deg REAL, /* this field will be populated if the SQL query includes a distance-based filter and calculation, otherwise it will be null */
                
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

    print(f"Expand dso data and apply dynamic filters and sorting to {len(dso_list)} localized DSO records for session with localization {localization} and filters {filters}")

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
        
        # since we have used normalize_loc, we can test min/max altitude directly here without worrying about missing localization values, since they will be normalized to defaults if not provided
        if 'min_altitude' in localization and dso['altitude'] is not None and \
            dso['altitude'] < localization['min_altitude']:
            continue
        if 'max_altitude' in localization and dso['altitude'] is not None and \
            dso['altitude'] > localization['max_altitude']:
            continue

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

        # add display value of ra in hours and minutes, for display only, keep ra_dd for sorting
        ra_hours = int(dso['ra_dd'] / 15)
        ra_minutes = int((dso['ra_dd'] / 15 - ra_hours) * 60)
        # dso['RA'] = f"{ra_hours}h {ra_minutes}m"
        dso['RA'] = f"{ra_hours}:{ra_minutes}"

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
            dso['coverage_sort'] = sens_cov # sortable value for coverage
        else:
            dso['coverage'] = 0
            dso['coverage_sort'] = 0

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

        # add 'AZI' for the azimuth at the first observation time, for display and filtering
        if results[0] is not None:
            dso['AZI'] = f"{results[0]['azimuth']:.0f}\u00B0"
            dso['AZI_sort'] = results[0]['azimuth'] # sortable value for azimuth    
        else:
            dso['AZI'] = "---"
            dso['AZI_sort'] = float('inf') # sort to end if no azimuth value

        # print(f"distance check: distance = {dso.get('angular_distance_deg', 'n/a')} for DSO {dso['name']} with altitudes {[dso[f'obsTime{i}_alt'] for i in range(0, NUMBER_OBS_TIMES)]} and airmasses {[results[i]['airmass'] if results[i] is not None else 'n/a' for i in range(0, NUMBER_OBS_TIMES)]} and hours_viz {dso['hours_viz']}")
        # add distance for this queries that use distance or n/a
        # format distance to 1 decimal place if it's a number
        distance = dso.get('angular_distance_deg', 'n/a')
        # try convert to float
        try:
            distance = float(distance)
            # save value for display with 1 decimal place
            dso['distance'] = f"{distance:.1f}"
            # save value for sorting
            dso['distance_sort'] = distance
        except (TypeError, ValueError):
            dso['distance'] = "n/a"
            dso['distance_sort'] = float('inf')
        
        # filter by hours_viz
        if 'min_hours_viz' in filters and dso['hours_viz'] < filters['min_hours_viz']:
            continue
        if 'max_hours_viz' in filters and dso['hours_viz'] > filters['max_hours_viz']:
            continue

        dso_results.append(dso) # our output

        # skip filter by score

    # sort
    VALID_SORTS = { "name", "catalog", "class", "constellation_abbr", "type", "hours_viz", "vis_mag", "coverage", "rise_time", "transit_time", "set_time"}

    if sort_key == 'rise_time':
        sort_key = 'rise_time_sort' # use sortable version of rise time
    elif sort_key == 'set_time':
        sort_key = 'set_time_sort' # use sortable version of set time
    elif sort_key == 'transit_time':
        sort_key = 'transit_time_sort' # use sortable version of transit time
    elif sort_key == 'distance':
        sort_key = 'distance_sort' # use sortable version of distance
    elif sort_key == 'AZI':
        sort_key = 'AZI_sort' # sort by azimuth
    elif sort_key in VALID_SORTS:
        pass # sort_key is valid as is

    else:
        sort_key = 'ra_dd' # default sort by RA

    sorted_dso_list = sorted(dso_results, key=lambda x: x[sort_key], reverse=(order=='desc'))

    return sorted_dso_list


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


