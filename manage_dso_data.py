# various data fetch, filter, localize, sort functions
from functools import cache
from random import random
import sqlite3
from pathlib import Path

from datetime import datetime, time, timedelta
from random import random

from typing import Optional
from zoneinfo import ZoneInfo

from astronomy_utils import calculate_pixel_scale, calculate_rise_transit_set_fast, calculate_sensor_fov_amin, find_all_twilight_times, get_sensor_coverage, ra_dec_to_altaz_airmass_multiple_times
from astropy import units as u
from astropy.coordinates import SkyCoord, EarthLocation, AltAz

from astronomy_utils import MIN_AIRMASS, MIN_ALT_FOR_COLOR

# maybe make this a class later if we need to maintain state
# for now, just functions

def load_filter_localize_data(db_path: Path, 
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
    #   Rise (str, localized rise time, e.g. '20:30')
    #   Transit (str, localized transit time, e.g. '23:15')
    #   Set (str, localized set time, e.g. '02:00')
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
        cls=filters.get('classes',[]),
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
            item['rise'] = rt.strftime("%H:%M")
            item['rise_sort'] = rt.strftime("%Y-%m-%d %H:%M")
            # item['rise'] = rt.strftime("%d %b %Y <br> %I:%M %p")
        else:
            item['rise'] = rt
            item['rise_sort'] = ""

        rt = rts[i]['transit_time']
        if isinstance(rt, datetime):
            rt = rt.astimezone(tz=ZoneInfo("America/Chicago"))
            item['transit'] = rt.strftime("%H:%M")
            item['transit_sort'] = rt.strftime("%Y-%m-%d %H:%M")
        else:
            item['transit'] = rt
            item['transit_sort'] = ""

        rt = rts[i]['set_time']
        if isinstance(rt, datetime):
            rt = rt.astimezone(tz=ZoneInfo("America/Chicago"))
            item['set'] = rt.strftime("%H:%M")
            item['set_sort'] = rt.strftime("%Y-%m-%d %H:%M")
        else:
            item['set'] = rt
            item['set_sort'] = ""

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
    if cls and len(cls) > 0:
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


