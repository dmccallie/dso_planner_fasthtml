from fasthtml.common import *
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from pathlib import Path
import json

from astronomy_utils import ra_dec_to_altaz_airmass_multiple_times
from manage_dso_data import load_dso_by_id

app, rt = fast_app()

# Set your default timezone
DEFAULT_TIMEZONE = 'America/Chicago'

def calculate_dso_positions(dso_id, obs_lat, obs_long, obs_date, hours_before=2, hours_after=10):
    """Calculate positions for a DSO over a night"""
    # dso = {
    #     'id': dso_id,
    #     'name': 'M42 (Orion Nebula)',
    #     'ra': 83.82,
    #     'dec': -5.39
    # }
    print(f"Loading DSO {dso_id} from DB")
    dso = load_dso_by_id(dso_id, db_path=Path("./dso_data.db"))
    if not dso:
        raise ValueError("DSO not found")
    
    data_points = []
    # start_time = obs_date.replace(hour=19, minute=0, second=0, microsecond=0)
    # start_obs_time = datetime.combine(obs_date, time(19,0,0)).replace(tzinfo=ZoneInfo("America/Chicago"))
    print(f"calculate-dso-positions: obs_date before timezone check: {obs_date} (tzinfo={obs_date.tzinfo})")
    # if obs_date.tzinfo is None:
    
    obs_date = obs_date.replace(hour=19, minute=0, second=0, microsecond=0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

    
    obs_times = [obs_date + timedelta(hours=i) for i in range(14)]

    results = ra_dec_to_altaz_airmass_multiple_times(
        ra=dso['ra_dd'],
        dec=dso['dec_dd'],
        observer_lat=obs_lat,
        observer_lon=obs_long,
        datetime_list=obs_times
    )

    for i in range(14):
        # current_time = start_time - timedelta(hours=hours_before) + timedelta(hours=i)
        # hour_decimal = current_time.hour + current_time.minute / 60
        # altitude = 40 + 20 * abs((hour_decimal - 1) / 12)
        # azimuth = 180 + 30 * ((hour_decimal - 20) / 12)
        
        res = results[i]
        data_points.append({
            'time': obs_times[i].isoformat(), # will include timezone info
            'hour': i,
            'alt': res['altitude'],
            'azi': res['azimuth']
        })
    print(f"data points calculated: {data_points}")
    return dso, data_points

# Simple API endpoint for chart data
@rt('/api/dso/{dso_id}/positions')
def get_dso_positions(dso_id: int, 
                      lat: float = 38.9, 
                      lon: float = -94.6,
                      date: str = None,
                      tz: str = DEFAULT_TIMEZONE):
    """
    API endpoint that returns JSON data
    date: YYYY-MM-DD format (interpreted as local date in the given timezone)
    tz: IANA timezone string (e.g., 'America/Chicago', 'America/New_York')
    """
    # Parse date string as a date in the specified timezone
    if date:
        # Parse as naive date, then make it timezone-aware at midnight
        naive_date = datetime.strptime(date, '%Y-%m-%d')
        obs_date = naive_date.replace(tzinfo=ZoneInfo(tz))
    else:
        # Get current date/time in the specified timezone
        obs_date = datetime.now(ZoneInfo(tz))
    
    dso, data_points = calculate_dso_positions(dso_id, lat, lon, obs_date)
    
    observer_hours = {
        'start': '19:00',
        'end': '05:00'
    }
    
    return {
        'data': data_points,
        'dso_name': dso['name'],
        'dso_id': dso['dso_id'],
        'obs_lat': lat,
        'obs_long': lon,
        'obs_date': obs_date.isoformat(),  # Includes timezone
        'timezone': tz,
        'observer_hours': observer_hours,
        'safe_alt': 20
    }


# SIMPLIFIED: HTMX endpoint returns chart container with embedded config
# note that query params are passed in the URL as qparams, not form data
# GET /dso/254/chart-fragment?date=2025-10-08&lat=38.9&lon=-94.6
@rt('/dso/{dso_id}/chart-fragment')
def get_dso_chart_fragment(dso_id: int, 
                           lat: float = 38.9, 
                           lon: float = -94.6,
                           date: str = None,
                           tz: str = DEFAULT_TIMEZONE):
    """
    Returns just the chart container HTML fragment for HTMX to swap in.
    Includes inline script to initialize the chart.
    """
    if date:
        obs_date_str = date
    else:
        local_now = datetime.now(ZoneInfo(tz))
        obs_date_str = local_now.strftime('%Y-%m-%d')
    
    # Create the config object
    config = {
        'dso_id': dso_id,
        'lat': lat,
        'lon': lon,
        'date': obs_date_str,
        'tz': tz
    }
    
    # Return container with initialization script
    return Div(
        Div("Loading chart...", cls="loading", id="chart-container"),
        # Inline script that runs after HTMX swaps this in
        Script(f"""
            (async function() {{
                const {{ initChartFromAPI }} = await import('/static/dso_chart_fetch.js');
                initChartFromAPI('chart-container', {dso_id}, {json.dumps(config)});
            }})();
        """),
        id="chart-wrapper"
    )


@rt('/dso/{dso_id}')
def get_dso_detail(dso_id: int, 
                   lat: float = 38.9, 
                   lon: float = -94.6,
                   date: str = None,
                   tz: str = DEFAULT_TIMEZONE):
    """
    Main detail page - HTMX loads chart, updates on form submit
    """
    # Get current date in the specified timezone
    if date:
        obs_date_str = date
    else:
        local_now = datetime.now(ZoneInfo(tz))
        obs_date_str = local_now.strftime('%Y-%m-%d')
    
    return Html(
        Head(
            Title(f"DSO {dso_id} - Observation Chart"),
            Script(src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"),
            Script(src="https://cdnjs.cloudflare.com/ajax/libs/suncalc/1.9.0/suncalc.min.js"),
            Script(src="https://unpkg.com/htmx.org@1.9.10"),
            Script(src="/static/dso_chart_fetch.js", type="module"),
            Style("""
                body { 
                    margin: 0; 
                    padding: 20px; 
                    font-family: system-ui, -apple-system, sans-serif;
                    background-color: #1a1a1a;
                    color: #e0e0e0;
                }
                #chart-wrapper {
                    max-width: 100%;
                    margin: 0 auto;
                }
                #chart-container {
                    min-height: 600px;
                }
                .loading {
                    text-align: center;
                    padding: 100px;
                    font-size: 18px;
                    color: #888;
                }
                .error {
                    text-align: center;
                    padding: 50px;
                    color: #ff4444;
                }
                .back-link {
                    display: inline-block;
                    margin-bottom: 20px;
                    color: #4a9eff;
                    text-decoration: none;
                }
                .controls {
                    margin-bottom: 20px;
                    padding: 15px;
                    background: #2a2a2a;
                    border-radius: 5px;
                }
                .controls label {
                    margin-right: 10px;
                }
                .controls input, .controls button {
                    margin-right: 15px;
                    padding: 5px 10px;
                }
                /* Smooth transitions when HTMX swaps content */
                .htmx-swapping {
                    opacity: 0;
                    transition: opacity 0.2s ease;
                }
                .htmx-settling {
                    opacity: 1;
                }
            """)
        ),
        Body(
            A("← Back to DSO List", href="/", cls="back-link"),
            
            # Form with HTMX to update chart without page reload
            Form(
                Label("Date:", For="date-input"),
                Input(
                    type="date", 
                    id="date-input", 
                    name="date",
                    value=obs_date_str
                ),
                Label("Latitude:", For="lat-input"),
                Input(
                    type="number", 
                    id="lat-input", 
                    name="lat",
                    value=str(lat), 
                    step="0.1"
                ),
                Label("Longitude:", For="lon-input"),
                Input(
                    type="number", 
                    id="lon-input", 
                    name="lon",
                    value=str(lon), 
                    step="0.1"
                ),
                Button("Update Chart", type="submit"),
                cls="controls",
                hx_get=f'/dso/{dso_id}/chart-fragment',  # Use underscore instead of hyphen
                hx_target='#chart-wrapper',
                hx_swap='innerHTML',
                hx_include='[name="date"],[name="lat"],[name="lon"]'
            ),
            
            # Wrapper that HTMX swaps content into
            # this is what goes into my main detail page
            Div(
                # Initial chart load
                Div("Loading chart...", cls="loading", id="chart-container"),
                Script(f"""
                    (async function() {{
                        const {{ initChartFromAPI }} = await import('/static/dso_chart_fetch.js');
                        initChartFromAPI('chart-container', {dso_id}, {{
                            lat: {lat},
                            lon: {lon},
                            date: '{obs_date_str}',
                            tz: '{tz}'
                        }});
                    }})();
                """),
                id="chart-wrapper"
            )
        )
    )


@rt('/')
def get_dso_list():
    """Homepage with list of DSOs"""
    dsos = [
        {'id': 1878, 'name': 'M42 (Orion Nebula)'},
        {'id': 254, 'name': 'M31 (Andromeda Galaxy)'},
        {'id': 4830, 'name': 'M51 (Whirlpool Galaxy)'},
    ]
    
    return Html(
        Head(
            Title("Deep Sky Objects"),
            Script(src="https://unpkg.com/htmx.org@1.9.10"),
            Style("""
                body { 
                    padding: 20px; 
                    font-family: system-ui, -apple-system, sans-serif;
                    background-color: #1a1a1a;
                    color: #e0e0e0;
                }
                .dso-list { list-style: none; padding: 0; }
                .dso-item { 
                    margin: 10px 0; 
                    padding: 15px;
                    background: #2a2a2a;
                    border-radius: 5px;
                    transition: background 0.2s;
                }
                .dso-item:hover {
                    background: #3a3a3a;
                }
                .dso-item a { 
                    color: #4a9eff; 
                    text-decoration: none;
                    font-size: 18px;
                    display: block;
                }
            """)
        ),
        Body(
            H1("Deep Sky Objects"),
            Ul(
                *[Li(
                    # pass query params for the initial chart view
                    # date must be 'YYYY-MM-DD' and USE TZ for proper timezone handling
                    A(dso['name'], href=f"/dso/{dso['id']}?date=2025-07-04&tz=America/Chicago&lat=38.9&lon=-94.6"), 
                    cls="dso-item"
                ) for dso in dsos],
                cls="dso-list"
            )
        )
    )

serve()