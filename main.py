# app.py
"""
FastHTML + HTMX starter: filters + sortable table + **row-based infinite scroll** (single page scroll)

"""
from __future__ import annotations
import datetime
from datetime import datetime, timedelta, time
import sqlite3
import secrets
from zoneinfo import ZoneInfo
import random
from datetime import date, timedelta
from time import perf_counter
from typing import Callable, Any
import json
from faker import Faker
from fasthtml.common import *

import asyncio

from starlette.staticfiles import StaticFiles
from starlette.responses import Response


from ai_data_models import AstroDependencies, SA_Plan
from color_utils import ColorScale, HSL_Green_Scale, best_text_color, MapPlotLibColorScale

from astronomy_utils import MIN_AIRMASS, MIN_ALT_FOR_COLOR, calculate_dso_positions, get_data_for_dso_moon_chart, \
    stellarium_object_types, DEFAULT_TIMEZONE

# our data access layer
from manage_dso_data import get_unique_constellations, load_dso_by_id, load_dso_subset, get_unique_classes, \
        get_localized_dso_data, load_localize_filter_expand_sort_dso_data

from agents import single_agent_astro_plan

# set up some stuff for pydantic-ai and logfire
# fetch openai api key from env file
import dotenv
dotenv.load_dotenv()

# logire notes:
# Your Logfire credentials are stored in /home/david/.logfire/default.toml
# setup using logfire-cli via uv
# uv add logfire
# uv run logfire auth
# uv run logfire projects use astro-planner-project
#  https://logfire-us.pydantic.dev/dmccallie/astro-planner-project
import logfire

logfire.configure()
logfire.instrument_pydantic_ai()
# logfire.instrument_httpx(capture_all=True)

# ----------------------------- App setup -------------------------------------
app, rt = fast_app()
# add a static files mount for CSS, JS, images, etc
app.mount("/static", StaticFiles(directory="static"), name="static")

# ensure that every request has a session ID cookie, and that the session state is loaded/saved around the request
# does this have performance implications? should we only do it for certain routes? for now just do it for everything except static files
@app.middleware("http")
async def ensure_session_middleware(req, call_next):
    response = await call_next(req)
    if req.url.path.startswith("/static/"):
        return response
    ensure_session_id(req, response)
    return response

# # -------------------------- Fake dataset -------------------------------------
# SEED          = 42
# N_ROWS_TOTAL  = 1000
PAGE_SIZE     = 50
CLASSES    =  [('Cls', 'Cluster'), ('DS', 'Double Star'), ('Gal', 'Galaxy'), ('Neb', 'Nebula'), ('Oth', 'Other')] #FIXME ddx name from abbreviation in DB
REGIONS       = ["All", "North", "South", "East", "West"]
MAX_HOURS_VISIBLE = 6  # max for hours visible filter FIXME for whole night?

db_path = Path("./dso_data.db")

SESSION_COOKIE_NAME = "astro_session_id"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30

def _session_db():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_session_table() -> None:
    with _session_db() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_state (
                session_id TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

def load_session_state(session_id: str | None) -> dict:
    if not session_id:
        return {}
    with _session_db() as conn:
        row = conn.execute(
            "SELECT data_json FROM session_state WHERE session_id = ?",
            (session_id,)
        ).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row["data_json"])
    except json.JSONDecodeError:
        return {}

def save_session_state(session_id: str | None, data: dict) -> None:
    if not session_id:
        return
    payload = json.dumps(data)
    updated_at = datetime.utcnow().isoformat()
    with _session_db() as conn:
        # the ON CONFLICT clause allows us to do an upsert: insert a new row if the session_id doesn't exist, or update the existing row if it does
        conn.execute(
            """
            INSERT INTO session_state (session_id, data_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                data_json = excluded.data_json,
                updated_at = excluded.updated_at
            """,
            (session_id, payload, updated_at)
        )

def get_session_id(req) -> str | None:
    return req.cookies.get(SESSION_COOKIE_NAME)

def ensure_session_id(req, response: Response | None = None) -> str | None:
    session_id = get_session_id(req)
    if session_id:
        return session_id

    # If a session was already created earlier in this request, reuse it.
    existing = getattr(req.state, "session_id", None)
    if existing:
        session_id = existing
    else:
        session_id = secrets.token_urlsafe(24)
        req.state.session_id = session_id
        save_session_state(session_id, {})

    if response is not None:
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session_id,
            max_age=SESSION_TTL_SECONDS,
            path="/",
            samesite="lax"
        )
    return session_id

def default_loc() -> dict:
    return dict(
        lat=38.76918,
        lon=-94.65635,
        date=date.today().isoformat(),
        hours_start="20:00",  # 8PM local time default start
        hours_end="",
        scope_name="",
        fl_mm=0,
        camera_name="",
        px_um=0.0,
        rows=0,
        cols=0,
        site_name="Powell Observatory",
        elevation=300.0,
        timezone= DEFAULT_TIMEZONE,
        min_altitude=20.0,
        max_altitude=90.0,
        ai_text="",
        sql_query="SELECT * FROM dso_localized WHERE 1=1" # first time we start with everything.
    )

def read_loc_session(req, response: Response | None = None) -> dict:
    if response:
        session_id = ensure_session_id(req, response)
    else:
        session_id = get_session_id(req) or getattr(req.state, "session_id", None)
    state = load_session_state(session_id) # returns a dict with all session data, including "loc" if it exists
    loc = default_loc()
    # dict.update merges the default loc with any values from the session, with session values taking precedence
    loc.update(state.get("loc", {}))
    return loc

def get_sensor_coverage(dso_min_axis: float, dso_maj_axis: float, 
                        sensor_width_amin: float = 21.5, sensor_height_amin: float = 14.3) -> int:
    # updated to return in percent, not fraction
    # approximate how relevant the size of the object will be in the view of the telescope
    # all units are arcMINUTES
    # use nova.astrometry.net to get the actual size of the camera + sensor in arcminutes
    # for default, use 071 camera's 14.3amin x 21.5amin
    # try: compare diagonal of view to the diagonal of dso

    # need at least the major axis, and sensor size
    if (dso_maj_axis and sensor_width_amin and sensor_height_amin):
        sensor_diag = math.sqrt(sensor_height_amin**2 + sensor_width_amin**2)
        dso_diag    = math.sqrt(dso_min_axis**2 + dso_maj_axis**2)
        return round(100 * (dso_diag / sensor_diag))
    else:
        return 0

dso_classes = get_unique_classes(db_path=db_path)
print(f"Unique DSO classes: {dso_classes}")
# gets pairs of (abbr, full name)
dso_constellation_name_pairs = [("all", "All Constellations")] + get_unique_constellations(db_path=db_path)
print(f"Unique DSO constellation abbreviations: {dso_constellation_name_pairs}")
ensure_session_table()

# ------------------------ Helpers: filtering/sorting -------------------------

LOC_PARAM_MAP = {
    "site_name": "site_name",
    "lat": "lat",
    "lon": "lon",
    "date": "date",
    "elevation": "elevation",
    "timezone": "timezone",
    "hstart": "hours_start",
    "hend": "hours_end",
    "scope_name": "scope_name",
    "camera_name": "camera_name",
    "fl_mm": "fl_mm",
    "px_um": "px_um",
    "rows": "rows",
    "cols": "cols",
    "min_altitude": "min_altitude",
    "max_altitude": "max_altitude",
    "ai_text": "ai_text",
    "sql_query": "sql_query"
}

def _parse_bool(val: str|None, *, default=False) -> bool:
    if val is None:
        return default
    return val in {"1", "true", "True", "on", "yes"}

def _merge_loc(base: dict, mapping) -> dict:
    loc = dict(base)
    for param, key in LOC_PARAM_MAP.items():
        if param in mapping:
            loc[key] = mapping.get(param)
    return loc

def normalize_loc(loc: dict) -> dict:
    # make sure lat and long are floats
    try:
        loc['lat'] = float(loc.get('lat'))
    except (TypeError, ValueError):
        loc['lat'] = 38.76918  # powell
    try:
        loc['lon'] = float(loc.get('lon'))
    except (TypeError, ValueError):
        loc['lon'] = -94.65635  # powell

    # date is ISO format YYYY-MM-DD
    try:
        date.fromisoformat(loc.get('date'))
    except (TypeError, ValueError):
        loc['date'] = date.today().isoformat()

    # also fl_mm, rows, cols as int
    try:
        loc['fl_mm'] = int(loc.get('fl_mm'))
    except (TypeError, ValueError):
        loc['fl_mm'] = 0

    try:
        loc['px_um'] = float(loc.get('px_um'))
    except (TypeError, ValueError):
        loc['px_um'] = 0.0  # default pixel size

    try:
        loc['rows'] = int(loc.get('rows'))
    except (TypeError, ValueError):
        loc['rows'] = 0

    try:
        loc['cols'] = int(loc.get('cols'))
    except (TypeError, ValueError):
        loc['cols'] = 0

    # make sure min/max altitude are floats and within reasonable bounds (0 to 90)
    try:
        min_alt = float(loc.get('min_altitude'))
        if 0 <= min_alt <= 90:
            loc['min_altitude'] = min_alt
        else:
            loc['min_altitude'] = 20.0  # default min altitude
    except (TypeError, ValueError):
        loc['min_altitude'] = 20.0
    try:        
        max_alt = float(loc.get('max_altitude'))
        if 0 <= max_alt <= 90:
            loc['max_altitude'] = max_alt
        else:
            loc['max_altitude'] = 90.0  # default max altitude
    except (TypeError, ValueError):
        loc['max_altitude'] = 90.0  
    # print(f"Normalized loc resulted in: {loc}")
    return loc

def get_loc(req, response: Response | None = None) -> dict:
    qp = req.query_params
    print(f"get_loc query params: {qp}")
    loc = _merge_loc(read_loc_session(req, response), qp)
    loc = normalize_loc(loc)
    if response is not None and any(param in qp for param in LOC_PARAM_MAP):
        session_id = ensure_session_id(req, response)
        if session_id:
            state = load_session_state(session_id)
            state["loc"] = loc
            save_session_state(session_id, state)
    return loc

def get_filters_from_mapping(qp) -> dict:
    # print(f"\nGet Filters Request URL: {req.url}")
    # e.g. http://localhost:5001/table?q=lisa&region=All&active=any&cat_Gamma=on&min_score=0&max_score=100
    q = (qp.get("q") or "").strip()
    a_constellation = qp.get("constellation") or "all"
    constellation = [a_constellation] # convert item to list, with "all" meaning no filtering
    
    active_sel = qp.get("active") or "any"
    min_hours_viz = int(qp.get("min_hours_viz") or 0)
    min_coverage = int(qp.get("min_coverage") or 0)
    max_coverage = int(qp.get("max_coverage") or 1000)
    # max_hours_viz = int(qp.get("max_hours_viz") or 24)
    
    classes = [c[0] for c in CLASSES if _parse_bool(qp.get(f"class_{c[0]}"))]
    # if no classes are selected, treat it as if all are selected (i.e. no filtering)
    if not classes or len(classes) == 0:
        classes = ["all"]

    # object_types = [t[0] for t in stellarium_object_types if _parse_bool(qp.get(f"object_type_{t[0]}"))]
    # could handle lists but just one at a time for now
    object_types = [t[0] for t in stellarium_object_types if t[0] == qp.get("object_type")]
    if not object_types or len(object_types) == 0:
        object_types = ["all"]

    sortname = qp.get("sortname") or "dso_id"
    order = qp.get("order") or "asc"
    d = dict(q=q, constellation=constellation, active_sel=active_sel,
                min_hours_viz=min_hours_viz, min_coverage=min_coverage, max_coverage=max_coverage,
                classes=classes, object_types=object_types, sortname=sortname, order=order)
    print(f"get_filters returning {d}")
    return d

def get_filters(req) -> dict:
    return get_filters_from_mapping(req.query_params)

# ----------------------- UI builders (FastTags) ------------------------------

# UI localization bar to show observer location, telescope, sensor, etc
# opens a modal dialog to change settings

def localization_bar(loc: dict, oob=False) -> FT:
    # compute any derived values here (pixel scale, FOV, darkness window…)
    lat = loc.get("lat")
    lon = loc.get("lon")
    elevation = loc.get("elevation")
    date_value = loc.get("date")
    # format date as "20 Dec 2025" for display in the loc bar
    if date_value:
        try:
            date_obj = date.fromisoformat(date_value)
            date_value = date_obj.strftime("%d %b %Y")
        except ValueError:
            date_value = "????"

    hours_start = loc.get("hours_start")
    timezone = loc.get("timezone")
    fl_mm = loc.get("fl_mm")
    rows = int(loc.get("rows")) or 0
    cols = int(loc.get("cols")) or 0
    px_um = float(loc.get("px_um")) or 0.0
    min_altitude = loc.get("min_altitude") or 20.0
    max_altitude = loc.get("max_altitude") or 90.0

    lat_lon_elev = f"{lat}, {lon}, {elevation}m"
    date_time_details = f"{hours_start}, {timezone}"
    scope_details = f"FL {fl_mm} mm"
    camera_details = f"{rows} rows, {cols} cols, {px_um}\u00b5m"
    # degree symbol is \u00b0, micro symbol is \u00b5
    altitude_details = f"Min: {min_altitude}\u00b0, Max: {max_altitude}\u00b0"
    ai_text = loc.get("ai_text") or ""
    ai_query = loc.get("sql_query") or ""

    return Div(id="locbar", cls="locbar",
               hx_swap_oob="true" if oob else "false",
               hx_get=loc_dialog,
               hx_target="#loc-dialog-body",
               hx_swap="innerHTML")(
        Div(cls="locbar-top")(
            Div(cls="locbar-grid")(
                Div(cls="locbar-item")(
                    Strong(loc.get("site_name") or "Site", cls="locbar-title"),
                    Span(lat_lon_elev, cls="locbar-detail")
                ),
                Div(cls="locbar-item")(
                    Strong(date_value or "Date", cls="locbar-title"),
                    Span(date_time_details, cls="locbar-detail")
                ),
                Div(cls="locbar-item")(
                    Strong(loc.get("scope_name") or "Scope", cls="locbar-title"),
                    Span(scope_details, cls="locbar-detail")
                ),
                Div(cls="locbar-item")(
                    Strong("DSO Altitude", cls="locbar-title"),
                    Span(altitude_details, cls="locbar-detail")
                ),
                Div(cls="locbar-item")(
                    Strong(loc.get("camera_name") or "Camera", cls="locbar-title"),
                    Span(camera_details, cls="locbar-detail")
                ),
            ),
        ),
        Div(cls="locbar-ai")(
            Strong("AI Text:"),
            Span(ai_text + " | SQL Query: " + ai_query, cls="locbar-ai-text")
        ),
    )

def filter_form(filters: dict, loc: dict, oob=False) -> FT:
    def classes_box(cat: tuple[str,str]) -> FT:
        # cat is a tuple (abbreviation, full name)
        # the abbreviation is used for actual filtering and sorting
        checked = (cat[0] in filters["classes"])
        return Label(
            Input(type="checkbox", name=f"class_{cat[0]}", checked=checked, cls="filter-ctl"), f" {cat[1]}", cls="chk"
        )

    # Re-render table on filter submit; resets paging/sentinel
    # note that Form(key-word-params)(children) works because FT is a builder.
    #  params first, then children FT
    # print(f"filter_form current sort/order: {filters.get('sort')} / {filters.get('order')}")
    return Form(
            id="filters-form",
            hx_swap_oob="true" if oob else "false", # update with new Table other than full index page
            # hx_get=table.to(frump="trump",sort=filters.get("sort", "ra_dd"), order=filters.get("order", "asc")),  # was index,  # was table
            hx_get=table, # was index,  # was table
            # have target include the current values of sort and order- doesnt work
            # hx_params="*", 
            hx_target="#table", # was "#content",  # was #table now includes filter and table
            hx_swap="outerHTML",
            hx_push_url="true",
            # hx_trigger="change from:input, select, checkbox, radio, textarea",

        )(
        Fieldset(
            Legend("Filters"),
            Div(
                Div(Label("Search", Input(name="q", value=filters["q"], placeholder="name contains…", cls="filter-ctl"))),
                
                # combo select only one at a time but db expects a list, with ['all'] meaning no filtering
                Div(Label("Constellation", Select(name="constellation", cls="filter-ctl")( *[Option(c[1], value=c[0],
                                selected=(filters["constellation"]==c[0])) for c in dso_constellation_name_pairs] ))),

                Div(Label("Object Type", Select(name="object_type", cls="filter-ctl")( *[Option(c[1], value=c[0],
                                selected=(filters["object_types"]==c[0])) for c in stellarium_object_types] ))),

                # Div(Label("Active", Select(name="active", cls="filter-ctl")( 
                #     Option("Any", value="any", selected=(filters["active_sel"]=="any")),
                #     Option("Active", value="true", selected=(filters["active_sel"]=="true")),
                #     Option("Inactive", value="false", selected=(filters["active_sel"]=="false")),
                # ))),
            cls="grid")
        ),
        Fieldset(
            Legend("Classes"), Div(*[classes_box(c) for c in CLASSES], cls="cats"),
        ),
        Fieldset(cls="actions")(
            Legend("Filters"),
            Div(
                Label(Safe("Min Hours Visible"), Input(type="number", name="min_hours_viz", value=str(filters["min_hours_viz"]), style="width:fit-content",
                                     min="0", max=MAX_HOURS_VISIBLE, step=1, cls="filter-ctl")),
                # Label("Max"), Input(type="number", name="max_hours_viz", value=str(filters["max_hours_viz"]), min=0, max=24, step=1, cls="filter-ctl"),
                cls="range"
            ),
            Div(
                Label("Min FOV %", Input(type="number", name="min_coverage", value=str(filters["min_coverage"]), style="width:fit-content",
                                     min="0", max="1000", step=10, cls="filter-ctl")),
                # Label("Max"), Input(type="number", name="max_coverage", value=str(filters["max_coverage"]), min=0, max=24, step=1, cls="filter-ctl"),
                cls="range"
            ),
            Div(
                Label("Max FOV %", Input(type="number", name="max_coverage", value=str(filters["max_coverage"]), style="width:fit-content",
                                     min="0", max="1000", step=10, cls="filter-ctl")),
                # Label("Max"), Input(type="number", name="max_coverage", value=str(filters["max_coverage"]), min=0, max=24, step=1, cls="filter-ctl"),
                cls="range"
            )
        ),

        # add fields to save sort (now "sortname") and order (get persisted into localStorage by JS)
        Input(type="hidden", name="sortname", value=filters.get("sortname") or "ra_dd"),
        Input(type="hidden", name="order",    value=filters.get("order") or "asc"),

        # Div(id="button-container")(
        #     Button("Apply", id="apply-filters", type="submit", hx_scroll="closest #button-container"),
        #     A("Reset", href=index, cls="secondary"),
        #     cls="actions",
        # )
        Div(id="button-container")(
            Button("Apply", id="apply-filters", type="submit", hx_scroll="this"),
            Button("Reset", cls="secondary", type="button", onclick="window.location.href='/'"),
            cls="actions",
        )
    )

def loc_form(loc: dict) -> FT:
    return Form(
        id="loc-form",
        method="post",
        hx_post=save_loc,
        hx_target="#table",
        hx_swap="outerHTML",
        hx_include="#filters-form"
    )(
        Div(cls="loc-grid")(
            Fieldset(
                Legend("Site"),
                Div(cls="loc-fields")(
                    Label("Site name",   Input(name="site_name", value=loc.get("site_name") or "")),
                    Label("Latitude",    Input(name="lat", value=loc.get("lat") or "")),
                    Label("Longitude",   Input(name="lon", value=loc.get("lon") or "")),
                    Label("Elevation (m)", Input(name="elevation", value=loc.get("elevation") or "")),
                    Label("Time zone",   Select(name="timezone")(
                        Option("UTC", value="UTC", selected=(loc.get("timezone")=="UTC")),
                        # Option("Local (auto-detect)", value="local", selected=(loc.get("timezone")=="local")),
                        Option("America/New_York", value="America/New_York", selected=(loc.get("timezone")=="America/New_York")),
                        Option("America/Chicago", value="America/Chicago", selected=(loc.get("timezone")=="America/Chicago")),
                        Option("America/Denver", value="America/Denver", selected=(loc.get("timezone")=="America/Denver")),
                        Option("America/Los_Angeles", value="America/Los_Angeles", selected=(loc.get("timezone")=="America/Los_Angeles")),
                    )),
                ),
                cls="loc-section"
            ),
            Fieldset(
                Legend("Date"),
                Div(cls="loc-fields")(
                    Label("Date",        Input(type="date", name="date", value=loc.get("date") or "")),
                    Label("Start hour",  Input(name="hstart", value=loc.get("hours_start") or "")),
                   # Label("End hour",    Input(name="hend", value=loc.get("hours_end") or "")),
                ),
                cls="loc-section"
            ),
            Fieldset(
                Legend("Telescope"),
                Div(cls="loc-fields")(
                    Label("Telescope",    Input(name="scope_name", value=loc.get("scope_name") or "")),
                    Label("Focal length (mm)", Input(name="fl_mm", value=loc.get("fl_mm") or "")),
                ),
                cls="loc-section"
            ),
            Fieldset(
                Legend("DSO Altitude Range"),
                Div(cls="loc-fields")(
                    Label("Min Altitude (°)",    Input(name="min_altitude", value=loc.get("min_altitude") or "")),
                    Label("Max Altitude (°)", Input(name="max_altitude", value=loc.get("max_altitude") or "")),
                ),
                cls="loc-section"
            ),
            Fieldset(
                Legend("Camera"),
                Div(cls="loc-fields")(
                    Label("Camera",       Input(name="camera_name", value=loc.get("camera_name") or "")),
                    Label("Pixel size (µm)",   Input(name="px_um", value=loc.get("px_um") or "")),
                    Label("Sensor rows", Input(name="rows", value=loc.get("rows") or "")),
                    Label("Sensor cols", Input(name="cols", value=loc.get("cols") or "")),
                ),
                cls="loc-section"
            ),
        ),
        Fieldset(
            Legend("AI Context"),
            Label("AI Text", Textarea(loc.get("ai_text") or "", name="ai_text", rows=4)),
            cls="loc-ai"
        )
    )

# Simple no dependents
SCORE_SCALE = ColorScale(
    vmin=15, vmax=50,
    colors=["#a50026", "#ffff00", "#1a9850"],
    gamma=0.5, # bias toward the upper end a bit
    space='srgb' # interpolate in linear light for smoother blends
)


# using Matplotlib's extensive color mapping
CS_ALT = MapPlotLibColorScale(
    model = "Greens",
    vmin=0, vmax=9, 
)

HSL_GREEN = HSL_Green_Scale(
    vmin=20, vmax=80,
    lmin=5, lmax=35
)

# configuration stuff - column names, etc
RenderTdFn   = Callable[["ColumnConfig", dict], FT]
GetHeaderFn = Callable[["ColumnConfig",dict], str] # doesnt gen the TH at this point, just the content

# helper functions

def extract_dt(dt) -> str:
    # format like "20 Dec 2025 <br> 9:45 PM"
    if isinstance(dt, datetime):
        return dt.strftime("%d %b %Y<br>%I:%M %p")
    else:
        return "????<br>????"

def merge_styles(*parts: Optional[str]) -> str:
    # join non-empty bits with semicolons, keep order, avoid duplicates
    bits = [p.strip().rstrip(';') for p in parts if p and p.strip()]
    return '; '.join(bits) + (';' if bits else '')

def default_Td(col: ColumnConfig, row: dict) -> FT:
    """Default TD renderer that honors col.width / col.style / col.cls and col.color_scale."""
    attrs = {}
    style_accum = []

    if col.width:
        style_accum.append(f"width:{col.width}")
    if col.style:
        style_accum.append(col.style)

    val = row.get(col.name)
    if col.color_scale and val is not None:
        rgb = col.color_scale.as_rgb_tuple(val)
        bg  = col.color_scale.as_css(val)
        fg  = best_text_color(rgb)
        style_accum.append(f"background:{bg}")
        style_accum.append(f"color:{fg}")

    style_str = merge_styles(*style_accum)
    if style_str:
        attrs['style'] = style_str
    if col.cls:
        attrs['class'] = col.cls

    return Td(Safe(val), **attrs)

def altAzi_Td(col: ColumnConfig, row: dict) -> FT:
    """Custom TD for alt/azitude that honors col.width / col.style / col.cls and col.color_scale.
       Uses colname_azi as a numeric value for color scaling, but colname for display.
    """
    attrs = {}
    style_accum = []

    if col.width:
        style_accum.append(f"width:{col.width}")
    if col.style:
        style_accum.append(col.style)

    val = row.get(col.name)
    color_val = row.get(col.name + "_alt")  # numeric value for altitude color scale
    if col.color_scale and color_val is not None and color_val >= MIN_ALT_FOR_COLOR:
        rgb = col.color_scale.as_rgb_tuple(color_val)
        bg  = col.color_scale.as_css(color_val)
        fg  = best_text_color(rgb)
        style_accum.append(f"background:{bg}")
        style_accum.append(f"color:{fg}")

    style_str = merge_styles(*style_accum)
    if style_str:
        attrs['style'] = style_str
    if col.cls:
        attrs['class'] = col.cls

    return Td(Safe(val), **attrs)

def nameCatTd(col: ColumnConfig, row: dict) -> FT:
    """Custom TD for name/catalog that puts the catalog in smaller text below the name."""
    name = row.get("name") or "Unknown"
    cat  = row.get("catalog") or ""
    content = [name]
    if cat:
        content.append(Br())
        content.append(Small(cat, cls="catalog"))
    return Td(*content, style=col.style, cls=col.cls)

@dataclass
class ColumnConfig:
    name: str  # the index/key in the data dict
    width: Optional[str] = None  # style string like "8%"' or '"clamp(100px, 10%, 200px)"'
    style: Optional[str] = None  # other style string like 'text-align:right;'
    sortable: bool = True
    hdr_cls: Optional[str] = "nowrap"  # optional class for the header TH
    cls: Optional[str] = None  # optional class for the column
    color_scale: Optional["HSL_Green_Scale"] = None
    header_fn: Optional[GetHeaderFn] = None # custom header generator
    renderTd_fn: Optional[RenderTdFn] = None # custom cell renderer

    # these are the endpoints to use when generating the table
    # get the header content (not the TH itself)
    def get_header(self, row: dict) -> str:
        if self.header_fn:
            return self.header_fn(self, row) # if we have a custom header function, use it
        # default is just the name capitalized
        return self.name.capitalize()

    # function to render the TD cell for this column
    def render_Td(self, row: dict) -> FT:
        if self.renderTd_fn:
            return self.renderTd_fn(self, row) # if we have a custom renderer, use it
        # default is just the value as a string
        # use the default TD renderer that honors width/style/cls/color_scale
        return default_Td(self, row)


COL_FIGS: list[ColumnConfig]= [

    # ColumnConfig(name = "dso_id", width = "4%", style=None, cls=None, sortable=True, color_scale=None,
    #     header_fn = lambda col, row: "Id",
    #     renderTd_fn = lambda col, row: default_Td(col, row)
    # ),

    ColumnConfig(name = "name", width = "16%", style=None, hdr_cls="wrap", cls="wrap", sortable=True, color_scale=None,
        header_fn = lambda col, row: "Name",
        renderTd_fn = lambda col, row: nameCatTd(col, row)
    ),

    # ColumnConfig(name = "catalog", width = "5%", style="text-align:center;", cls=None, sortable=True, color_scale=None,
    #     header_fn = lambda col, row: "Cat",
    #     renderTd_fn = lambda col, row: default_Td(col, row)
    # ),

    ColumnConfig(name = "RA", width = "6%", style="text-align:center;", cls=None, sortable=True, color_scale=None,
        header_fn = lambda col, row: "RA",
        renderTd_fn = lambda col, row: default_Td(col, row)
    ),

    ColumnConfig(name = "class", width = "4%", style=None, cls=None, sortable=True, color_scale=None,
        header_fn = lambda col, row: "Class",
        renderTd_fn = lambda col, row: default_Td(col, row)
    ),

    ColumnConfig(name = "type", width = "4%", style=None, cls=None, sortable=True, color_scale=None,
        header_fn = lambda col, row: "Type",
        renderTd_fn = lambda col, row: default_Td(col, row)
    ),

    ColumnConfig(name = "constellation_abbr", width = "4%", style=None, cls=None, sortable=True, color_scale=None,
        header_fn = lambda col, row: "Cons",
        renderTd_fn = lambda col, row: default_Td(col, row)
    ),

    # ColumnConfig("mag", "Magnitude", "6%", None, True, None),

    # ColumnConfig("size", "Size", "12%", None, False, None), # nn x nn
    ColumnConfig(name="coverage", width="4%", style=None, cls=None, sortable=True, color_scale=None,
        header_fn = lambda col, row: "FOV %",
        renderTd_fn = lambda col, row: default_Td(col, row)
    ),

    # earlier code used dos['rise'] but now we use dso['rise_time']
    ColumnConfig(name="rise_time", width="4%", style="text-align:left;", cls=None, sortable=True, color_scale=None,
        header_fn = lambda col, row: "Rise",
        renderTd_fn = lambda col, row: default_Td(col, row)
    ),
    ColumnConfig(name="transit_time", width="4%", style="text-align:left;", cls=None, sortable=True, color_scale=None,
        header_fn = lambda col, row: "Trans",
        renderTd_fn = lambda col, row: default_Td(col, row)
    ),
    ColumnConfig(name="set_time", width="4%", style="text-align:left;", cls=None, sortable=True, color_scale=None,
        header_fn = lambda col, row: "Set",
        renderTd_fn = lambda col, row: default_Td(col, row)
    ),

    # ColumnConfig(name="score", width="3%", style="text-align:center;", cls=None, sortable=True, color_scale=CS_ALT,
    #     header_fn = lambda col, row: "SCR",
    #     renderTd_fn = lambda col, row: default_Td(col, row)
    # ),

    ColumnConfig(name="distance", width="3%", style="text-align:center;", cls=None, sortable=True, color_scale=None,
        header_fn = lambda col, row: "Dist",
        renderTd_fn = lambda col, row: default_Td(col, row)
    ),

    ColumnConfig(name="hours_viz", width="3%", style="text-align:center;", cls=None, sortable=True, color_scale=None,
        header_fn = lambda col, row: "Viz",
        renderTd_fn = lambda col, row: default_Td(col, row)
    ),

    # ColumnConfig("score", "4%", "text-align:center;", True, None,
    #     get_header=lambda row: "Score",
    #     render_TD=lambda row: get_TD(row, "score", "4%", "text-align:center;", color_scale=CS_ALT)),

    # # five more data / time columns algorithmically generated

    ColumnConfig(name="obsTime0", width="9%", style="text-align:center;padding:2px 2px;",
                  cls=None, sortable=False, color_scale=HSL_GREEN,
        header_fn = lambda c, row: extract_dt(row.get("obsTime0_dt")),
        renderTd_fn = lambda col, row: altAzi_Td(col, row)
    ),
    ColumnConfig(name="obsTime1", width="9%", style="text-align:center;padding:2px 2px;",
                  cls=None, sortable=False, color_scale=HSL_GREEN,
        header_fn = lambda c, row: extract_dt(row.get("obsTime1_dt")),
        renderTd_fn = lambda col, row: altAzi_Td(col, row)
    ),
    ColumnConfig(name="obsTime2", width="9%", style="text-align:center;padding:2px 2px;",
                  cls=None, sortable=False, color_scale=HSL_GREEN,
        header_fn = lambda c, row: extract_dt(row.get("obsTime2_dt")),
        renderTd_fn = lambda col, row: altAzi_Td(col, row)
    ),
    ColumnConfig(name="obsTime3", width="9%", style="text-align:center;padding:2px 2px;",
                  cls=None, sortable=False, color_scale=HSL_GREEN,
        header_fn = lambda c, row: extract_dt(row.get("obsTime3_dt")),
        renderTd_fn = lambda col, row: altAzi_Td(col, row)
    ),
    ColumnConfig(name="obsTime4", width="9%", style="text-align:center;padding:2px 2px;",
                  cls=None, sortable=False, color_scale=HSL_GREEN,
        header_fn = lambda c, row: extract_dt(row.get("obsTime4_dt")),
        renderTd_fn = lambda col, row: altAzi_Td(col, row)
    ),
    ColumnConfig(name="obsTime5", width="10%", style="text-align:center;padding:2px 2px;",
                  cls=None, sortable=False, color_scale=HSL_GREEN,
        header_fn = lambda c, row: extract_dt(row.get("obsTime5_dt")),
        renderTd_fn = lambda col, row: altAzi_Td(col, row)
    ),
]

# rename to render_sortable_header??
def sort_header(col_config: ColumnConfig, sortname: str, order: str, rows: list[dict]) -> FT:

    # sort is name of current sort column
    # order is now "asc" or "desc"
    # fake row for testing

    nxt   = "desc" if (sortname == col_config.name and order == "asc") else "asc"
    arrow = "▲" if sortname == col_config.name and order == "asc" else ("▼" if sortname == col_config.name else "&nbsp;")

    if not col_config.sortable:
        return Th(cls=col_config.hdr_cls or "nowrap",
                  style="text-align:center;"
                )(Safe(col_config.get_header(rows[0]) + "<br>&nbsp;"))  # allow <br> in header

    return Th(cls=col_config.hdr_cls or "nowrap",
              style="text-align:center;"
            )(
                Button(
                    Span(Safe(col_config.get_header(rows[0]) + "<br>" + arrow)),
                    type="button",
                    cls="linklike clicksort",
                    style="text-align:center;",
                    # hx_get=index.to(sort=col_config.name, order=nxt),
                # hx_target="#content",            hx_get=index.to(sort=col_config.name, order=nxt),
                hx_get=table.to(sortname=col_config.name, order=nxt),
                hx_target="#table",
                hx_swap="outerHTML",
                hx_push_url="true",
                hx_include="#filters-form",
                onclick="setSort(" + "'" + col_config.name + "'" + ")"
                )
            )

def render_rows(rows: list[dict], localization:dict) -> list[FT]:
    trs: list[FT] = []
    print(f"Render Rows starting with {len(rows)} rows and localization {localization}")
    
    for r in rows:
        
        # rgb = CS_ALT.as_rgb_tuple(r["score"])
        # bg  = CS_ALT.as_css_rgba(r["score"])
        
        # fg = best_text_color(rgb)

        # Row is clickable: navigate to detail page for this id
        # pass along localization info
        href = detail.to(dso_id=r["dso_id"], **localization)

        tr = Tr(
            *[col.render_Td(r) for col in COL_FIGS]
        )

        # Make the whole row clickable
        tr.attrs.update({
            'onclick': f"window.location='{href}'",
            'style': (tr.attrs.get('style','') + ' cursor:pointer;').strip()
        })
        trs.append(tr)
    # print(f"Render Rows prepared {len(trs)} rows")
    # print(f"Last row: {trs[-1] if trs else 'none'}")
    return trs

def apply_sentinel(trs: list[FT], *, next_page:int, has_more:bool, sortname:str, order:str):
    """Adds a sentinel row that uses HTMX to append the next page after itself."""
    # generates call like:
    # http://localhost:5001/rows?page=4&sort=id&order=desc&q=&region=All&active=any&min_score=0&max_score=100
    if not trs:
        return
    
    # this version adds a separate sentinel row at the end, using SWAP outerHTML to replace itself
    # but it still has the weird scroll jump when it loads so Use the GPT script fix instead

    if has_more:
        # add a new row as the sentinel
        new_row = Tr(
            Td("Loading more…", colspan=str(len(COL_FIGS)), style="text-align:center; font-style:italic;"))
        new_row.attrs.update({
            'hx-get': rows.to(page=next_page, sortname=sortname, order=order),
            # 'revealed once' is okay; 'intersect' is a bit stricter:
            # 'hx-trigger': 'intersect once threshold:0 rootMargin:0px 0px -20% 0px',
            'hx-trigger': 'intersect once threshold:0',
            'hx-swap': 'outerHTML', # replace self
            'hx-include': '#filters-form'
        })
        trs.append(
            new_row
        )
    else:
        # optional styling for end marker; keep it a plain row
        # last.attrs.update({'class': (last.attrs.get('class','') + ' end-of-results').strip()})
        trs.append(
            Tr(
                Td("End of results", colspan=str(len(COL_FIGS)), style="text-align:center; font-style:italic;")
            )
        )

def _serialize_fragments(content: Any) -> str:
    if isinstance(content, (tuple, list)):
        return "".join(to_xml(item) for item in content)
    return to_xml(content)

# ------------------------------- Routes --------------------------------------


async def ai_update_loc_and_generate_sql(loc: dict, filters: dict) -> dict:
    # in a real implementation, this would call the AI agent with the current loc and filters, and get back an updated loc and a SQL query to run
    # for now, just return the same loc and a dummy SQL query

    print(f"AI Agent called with loc: {loc} and filters: {filters}")


    updated_deps = AstroDependencies(
        # make sure these defaults are 'now' at runtime
        # note this should be CLIENT "now" not server!
        # if loc has a date and time, use that as the default for the AI agent, otherwise use current date and time
        default_time=datetime.now(ZoneInfo("America/Chicago")).strftime("%H:%M"),
        default_date=datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d"),
        default_timezone="America/Chicago",
        default_location="Powell Observatory, Kansas", # this should be findable 
        default_telescope="Astrophysics 130EDF F6.3",
        default_camera="ZWO ASI 2600MC Pro",
        default_min_altitude=20.0,
        default_max_altitude=90.0,
    )
    # if loc has specific values for these, override the defaults for the AI agent
    if loc.get("lat") and loc.get("lon"):
        updated_deps.default_latitude = loc["lat"]
        updated_deps.default_longitude = loc["lon"]
    if loc.get("date"):
        updated_deps.default_date = loc["date"]
    if loc.get("hours_start"):
        updated_deps.default_time = loc["hours_start"]
    if loc.get("site_name"):
        updated_deps.default_location = loc["site_name"]
    if loc.get("scope_name"):
        updated_deps.default_telescope = loc["scope_name"]
    if loc.get("camera_name"):
        updated_deps.default_camera = loc["camera_name"]
    if loc.get("elevation"):
        updated_deps.default_elevation = loc["elevation"]
    if loc.get("timezone"):
        updated_deps.default_timezone = loc["timezone"]
    if loc.get("min_altitude"):
        updated_deps.default_min_altitude = loc["min_altitude"]
    if loc.get("max_altitude"):
        updated_deps.default_max_altitude = loc["max_altitude"]
    
    user_query = loc['ai_text']

    if not user_query:
        print("No User AI query provided, skipping AI Agent and returning original loc with dummy SQL")
        return {**loc, 'sql_query': "SELECT * FROM dso_localized WHERE 1=1"}
    
    result = await single_agent_astro_plan.run(user_query, deps=updated_deps)
    ai_query = "" # the query we use to filter Dso, with or without AI help
    
    if isinstance(result.output, SA_Plan):
        # update loc with new info from plan
        print(f"AI Agent returned plan: {result.output}")
        if result.output.observer_context:
            loc['site_name'] = result.output.observer_context.location
            loc['lat'] = result.output.observer_context.latitude_deg
            loc['lon'] = result.output.observer_context.longitude_deg
            loc['elevation'] = result.output.observer_context.elevation_m
            loc['date'] = result.output.observer_context.observe_date
            loc['hours_start'] = result.output.observer_context.observe_time 
            loc['timezone'] = result.output.observer_context.timezone
            loc['min_altitude'] = result.output.observer_context.min_altitude
            loc['max_altitude'] = result.output.observer_context.max_altitude
            # loc['hours_end'] = result.output.observer_context.hours_end
        if result.output.equipment and result.output.equipment.telescope:
            loc['fl_mm'] = result.output.equipment.telescope.focal_length_mm
            loc['scope_name'] = result.output.equipment.telescope.name
        if result.output.equipment and result.output.equipment.camera:
            loc['px_um'] = result.output.equipment.camera.pixel_um
            loc['rows'] = result.output.equipment.camera.sensor_rows
            loc['cols'] = result.output.equipment.camera.sensor_columns
            loc['camera_name'] = result.output.equipment.camera.name
        if result.output.valid_plan:
            ai_query = result.output.sql_query
        else:
            print(f"AI Agent returned invalid plan: {result.output}")
            # FIXME return this to client via toast or popup!
            ai_query = "SELECT * FROM dso_localized WHERE 1=1"  # fallback dummy query
    else:
        print(f"AI Agent returned non-plan output: {result.output}")
        # FIXME return this to client via toast or popup!
        ai_query = "SELECT * FROM dso_localized WHERE 1=1"  # fallback dummy query
    
    updated_loc = dict(loc)
    # updated_loc["ai_text"] = ai_query 
    updated_loc['sql_query'] = ai_query #FIXME naming 

    return updated_loc

@rt('/loc/dialog')
def loc_dialog(req) -> FT:
    loc = get_loc(req)
    return loc_form(loc)

@rt('/loc/save')
async def save_loc(req):
    # this saves the localization (loc-form) but ALSO includes the current filters from the filter form
    # the fields are all merged into the req form 
    form = await req.form()
    form_data = dict(form) if form else dict(req.query_params)

    print(f"/loc/save got form data: {form_data}") # dumps all Loc and Filter fields

    loc = normalize_loc(_merge_loc(read_loc_session(req), form_data))

    print(f"Normalized loc: {loc}")

    filters = get_filters_from_mapping(form_data)

    # I think this is where the AI Agent will step in. 
    # will fale it for now

    updated_loc = await ai_update_loc_and_generate_sql(loc, filters)

    # note that table can't see the new cookie values since it's all in the same request, 
    #  so we have to pass the new loc and filtervalues directly to it as overrides
    table_content = table(
        req,
        sortname=filters.get("sortname") or "dso_id",
        order=filters.get("order") or "asc",
        update_localization=True,
        filters_override=filters, # newly updated filters from the form submission
        localization_override=updated_loc # newly updated localization from the form submission
    )

    response = Response(_serialize_fragments(table_content), media_type="text/html")
    session_id = ensure_session_id(req, response)
    if session_id:
        state = load_session_state(session_id)
        state["loc"] = updated_loc
        save_session_state(session_id, state)
    return response

@rt
async def index(req, sortname: str = "dso_id", order: str = "asc") -> FT:
    # note index handles http initial load as well as htmx table update

    # await async_func(42)  # just testing that async works in FastHTML routes

    # get localization from hidden fields in query request
    loc = get_loc(req)  # TODO: cookie fallback
    
    # get filters from query params
    filters = get_filters(req)

    # define content area that gets updates
    # pattern is FT(key-params)(children)
    content = Div(id="content", cls="container")(
        localization_bar(loc),
        filter_form(filters, loc, oob=False),  # full update when index is called
        table(req, sortname=sortname, order=order, update_localization=False)
    )

    # If this is an HTMX request, return only the inner content fragment
    # should not happen now
    if req.headers.get("HX-Request"):
        print(f"Got /index HTMX request with sort:{sortname} and returning #content fragment")
        return content

    # Otherwise return the full page
    print(f"Got FULL /index HTTP request with sort:{sortname} and returning full page")

    page = Titled(
        "FastHTML + HTMX Demo - AI version",
        Link(rel="stylesheet", href="/static/app.css?v=1"),
        content,
        Div(id="table-spinner", cls="table-spinner")(
            Div(cls="spinner"),
            Div("Updating table...", cls="spinner-text")
        ),

        # Static dialog included once; it stays in DOM between opens
        # use starlette StaticFiles mount at /static/ for scripts, css, images, etc

        Dialog(id="loc-dialog", cls="modal")(
            Div(cls="layout")(
                H2("Specify your observing plan..."),
                Div("Loading...", cls="body", id="loc-dialog-body"),
                Div(cls="footer")(
                    Button("Execute Plan", type="submit", form="loc-form", id="save-loc"),
                    Form(method="dialog")(Button("Cancel"))
                )
            )
        ),

        # use of "module" means these scripts are NOT global to window object
        # use listener in static/scripts.js to bind click events, etc.
        # or set windows.xxxx = xxxxx
        Script(src="/static/scripts.js?v=6", type="module", defer=True),
    )
    return page


@rt
def table(req, sortname: str = "dd_dec", order: str = "asc", update_localization: bool = True,
          filters_override: dict | None = None, localization_override: dict | None = None):
    """Render the FULL table with the first page and a row-sentinel at the end."""
    # sort is the name of the column to sort on
    # localization is whether to include the localization bar (oob) or not
    # filters_override and localization_override are used to pass updated values from the loc_form submission 
    #   since the session values might not be updated until the next request (??)

    filters = filters_override or get_filters(req)
    localization = localization_override or get_loc(req)
    
    # print(f"---->>>>loading raw data for /table with sortname:{sortname}, order:{order}")
    # raw_data = load_filter_localize_data(db_path, filters, localization, sortname, order)
    
    # debug test for now
    session_id = ensure_session_id(req)
    assert session_id is not None, "Session ID should not be None in /rows route"
    raw_data = load_localize_filter_expand_sort_dso_data(session_id, db_path, filters, localization, sortname, order)
    print(f"Test load_localize_filter_expand_sort_dso_data returned {len(raw_data)} rows for session {session_id}")

    trs = rows(req, page=1, sortname=sortname, order=order, raw_data=raw_data, localization=localization)  # call the route function directly to get first page

    # if there are no rows, return an empty table with no headers since we need rows to get dynamic headers
    if not trs or len(raw_data) == 0:
        return Table(id="table", cls="striped")(
            Colgroup(*[
                (Col(style=f"width:{cf.width}") if cf.width else Col())
                    for cf in COL_FIGS
            ]),
            Thead(
                Tr(Th("No results match the filter criteria.",
                       colspan=str(len(COL_FIGS)), style="text-align:center; font-style:italic;"))
            )
        )

    # maybe this DIV breaks things with hx-swap=outerHTML?
    # return
        # filter_form(get_filters(req), get_loc(req)),  # re-render filter form to preserve current values,
        # table with first page of rows and sentinel
        # filter_form(filters, localization, oob=True),  \

    table_ft =  Table(id="table", cls="striped")(
            Colgroup(*[
                (Col(style=f"width:{cf.width}") if cf.width else Col())
                    #for w in COL_WIDTHS
                    for cf in COL_FIGS
            ]),
            Thead(
                # these are the column headers, some are sortable
                # some have dynamic names (the observation time columns)
                Tr(
                    # the "'*" unpack" unpacks the tuples into individual positional args
                    *(sort_header(cf, sortname, order, raw_data) for cf in COL_FIGS)
                )
            ),
            Tbody(*trs)
            )

    if update_localization:
        return localization_bar(localization, oob=True), table_ft
    else:
        return table_ft


# called by HTMX when the sentinel row is revealed
# also called internally to get the initial tbody rows

# def rows(req, page: int = 2, sort: str = "dso_id", order: str = "asc", row_data:list[dict]|None=None) -> tuple[FT,...]:
@rt
def rows(req, page: int = 2, sortname: str = "dso_id", order: str = "asc", 
         raw_data: list[dict]|None = None, localization: dict = {}): #  -> tuple[FT,...]:
    """Return the *next page* of <tr> elements. The last <tr> becomes the new sentinel.
    Since the triggering row uses hx-swap=afterend, these rows are inserted after it.
    """
    # is this an HTMX request?
    if req.headers.get("HX-Request"):
        print(" /rows called WITH HTMX request headers;")
        print("raw data has len(raw_data) rows:", len(raw_data) if raw_data else 'None')
    else:
        print(" /rows called WITHOUT HTMX request headers;")
        print("raw data has len(raw_data) rows:", len(raw_data) if raw_data else 'None')

    print(f"Entering HTMX rows request for: page={page}, sort={sortname}, order={order}")
    # print(f"rows call has row_data: {row_data}")

    # if we already have the data (on index page fetch), use it; otherwise (htmx) load from DB
    if raw_data is not None:
        print(f"Using passed raw_data with {len(raw_data)} rows. Request: {req.query_params}")
        localization = localization or get_loc(req)  # if localization not passed in, get it from the request
        # print(f"Using localization: {localization}")
        # FIXME should sort the raw_data here using cookie values for loc??
        sorted_rows = raw_data
    else:
        # filters     = get_filters(req)
        # localization = get_loc(req) 
        # print(f"loading raw data for HTMX ROWS page {page} Request: {req.query_params} ")
        # sorted_rows = load_filter_localize_data(db_path, filters, localization, sortname, order)
        
        session_id = ensure_session_id(req)
        filters2     = get_filters(req)
        localization = get_loc(req) 
        assert session_id is not None, "Session ID should not be None in /rows route"
        sorted_rows = load_localize_filter_expand_sort_dso_data(session_id, db_path, filters2, localization, sortname, order)
        print(f"Test load_localize_filter_expand_sort_dso_data returned {len(sorted_rows)} rows for session {session_id}")

    # debug test for now
    
    start = (page - 1) * PAGE_SIZE
    end   = start + PAGE_SIZE
    chunk = sorted_rows[start:end]

    if not chunk:
        # Nothing left; return an empty tuple so nothing is inserted
        return tuple()

    trs = render_rows(chunk, localization)
    has_more  = end < len(sorted_rows)
    next_page = page + 1
    apply_sentinel(trs, next_page=next_page, has_more=has_more, sortname=sortname, order=order)

    # Defensive check: ensure trs is a list of FastHTML components
    if not isinstance(trs, list):
        raise TypeError(f"Expected trs to be a list, got {type(trs)}")
    for i, tr in enumerate(trs):
        # Check for FastHTML component by presence of 'attrs' and '__class__'
        if not hasattr(tr, 'attrs') or not hasattr(tr, '__class__'):
            raise TypeError(f"Element at index {i} in trs is not a FastHTML component: {tr}")

    print(f"Returning {len(trs)} <tr> rows for page {page} (has_more={has_more})")
    print(f"Last row: {trs[-1] if trs else 'none'}")
    return trs # this broke things: return tuple(trs)


@rt
def detail(req, dso_id: str, localization: dict = {}) -> FT:
    """Simple detail page placeholder. Normal navigation (not HTMX) so the browser's
    Back button returns to the exact table state (filters/sort preserved).

    Localization info is passed as query params to be used by detail graphics
    url ends up like this:
    GET /detail?dso_id=6459&lat=38.76918&lon=-94.65635&date=2025-10-02&hours_start=&hours_end=&fl_mm=835&px_um=3.76&rows=4176&cols=6248
    """
    print(f"Detail page request for dso_id={dso_id}, loc={localization}") # fastHTML auto unpacks into dict!
    print(f"Detail request query params: {req.query_params}") # this works as well as the localization dict
    
    row = load_dso_by_id(dso_id, db_path)
    if not row:
        return Titled("Not Found", P("No record with that id."))

    # todo add localization values for "now" and the current location
    # loc = get_loc(req)  # TODO: cookie fallback
    date = localization.get("date") or datetime.date.today().isoformat() # just 2025-10-02 eg
    alt_date = req.query_params.get("date")
    if alt_date != date:
        print(f"Surprise alt-date via query param = {alt_date}")

    lat  = localization.get("lat") or "0"
    lon  = localization.get("lon") or "0"
    tz  = localization.get("tz") or "America/Chicago"
    
    # Back button (uses history) + fallback link to the table root
    backbar = Div(
        Button("← Back to table", cls="linklike", onclick="history.back()"),
        Span(" · "),
        A("Open table", href=index),
        cls="backbar"
    )

    details = Table(cls="striped")(
        Thead(Tr(Th("Field"), Th("Value"))),
        Tbody(
            Div(
                Tr(Td("dso_id"), Td(str(row["dso_id"]))),
                Tr(Td("name"), Td(row["name"])),
                Tr(Td("catalog"), Td(row["catalog"])) ,
                Tr(Td("constellation"), Td(row["constellation"])),
                Tr(Td("class"), Td(row["class"])),
                Tr(Td("type"), Td(row["type"])),
                Tr(Td("RA Degrees"), Td(f"{row['ra_dd']:.4f}")),
                Tr(Td("DEC degrees"), Td(f"{row['dec_dd']:.4f}")),
                Tr(Td("Date"), Td(date)),
                Tr(Td("Latitude"), Td(lat)),
                Tr(Td("Longitude"), Td(lon)),
            ),
        ),
    )

    ra_dec_d3 = Div(
        Span("Chart (based on current localization):", cls="chart-label"),
        Div(
            # This method just injects a script tag that dynamically imports the chart module and runs it
            # passing the div id to render into and the parameters
            # compare to the htmx method below
            # this may be simpler for many cases!
            Div("Loading chart...", cls="loading", id="chart-container"),
            Script(f"""
                (async function() {{
                    const {{ initChartFromAPI }} = await import('/static/dso_chart_fetch.js');
                    initChartFromAPI('chart-container', '{dso_id}', {{
                        lat: {lat},
                        lon: {lon},
                        date: '{date}',
                        tz: '{tz}'
                    }});
                }})();
            """),   
            id="chart-wrapperXXX", cls="chart-container"
        )
    )

    dso_moon_d3 = Div(
        Span("DSO altitude vs Moon illumination", cls="chart-label"),
        Div(
            # This method uses htmx to trigger loading the chart data once the div is visible
            # JS code intercepts the afterRequest event to load and render the chart
            # use data-* attributes to pass parameters to the JS code
            # the /nonexistent hx-get is just a dummy to trigger the event (works either way)
            Div("Loading dso-moon chart...", id="dso-moon-container",
                hx_trigger="intersect once",  # Triggers when element becomes visible
                hx_get="/nonexistent",  # Any URL, we don't care about the response
                hx_swap="afterbegin",  # This should trigger afterRequest event
                data_dso_id=dso_id, 
                data_lat=lat, 
                data_lon=lon, 
                data_date=date, 
                data_tz=tz
            ),
            id="moon-wrapper", cls="chart-container"
        )
    )

    return Titled(
        f"Details for ID {row['name']} (id:{dso_id})",
        # add css
        Link(rel="stylesheet", href="/static/app.css?v=3"),
        Script(src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"),
        Script(src="https://cdnjs.cloudflare.com/ajax/libs/suncalc/1.9.0/suncalc.min.js"),
        Script(src="/static/scripts.js?v=5", type="module", defer=True),

        Div(backbar, details, ra_dec_d3, dso_moon_d3, cls="container")
    )

## routines for showing d3 RA/Dec chart in detail page
# Simple API endpoint for chart data
@rt('/api/dso/{dso_id}/positions')
def get_dso_positions(dso_id: str, 
                      lat: float = 38.9, 
                      lon: float = -94.6,
                      elevation: float = 300,
                      date: Optional[str] = None,
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
    
    # fetch dso data
    dso_data = load_dso_by_id(dso_id, db_path=Path("./dso_data.db"))
    if not dso_data:
        raise ValueError("DSO not found")

    # FIXME clean this up with less complex routine
    dso, data_points = calculate_dso_positions(dso_data, lat, lon, elevation, obs_date)

    # FIXME with how many hours to show
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

@rt('/api/dso-moon-chart-data/{dso_id}/localization')
def get_dso_moon_chart_data(dso_id: str, lat: float, lon: float, elevation: float, date: str, tz: str):
    """
    API endpoint that returns JSON data for the DSO moon chart
    """
    # fetch dso data
    dso_data = load_dso_by_id(dso_id, db_path=Path("./dso_data.db"))
    if not dso_data:
        raise ValueError("DSO not found")

    # Parse date string as a date in the specified timezone
    if date:
        # Parse as naive date, then make it timezone-aware at midnight
        naive_date = datetime.strptime(date, '%Y-%m-%d')
        obs_date = naive_date.replace(tzinfo=ZoneInfo(tz))
    else:
        # Get current date/time in the specified timezone
        obs_date = datetime.now(ZoneInfo(tz))

    # Fetch DSO and moon sample data for 9pm local time
    dso_moon_data = get_data_for_dso_moon_chart(dso_data, lat, lon, elevation, obs_date,
                                                sample_hour=21, tz=tz)
    if not dso_moon_data:
        raise ValueError("DSO moon data not found")
    print(f"Ready to return moon chart data {dso_moon_data}")
    return dso_moon_data

@rt('/nonexistent') # dummy endpoint for hx-get in detail page
def do_nothing():
    """This endpoint does nothing; it's just a placeholder for hx-get in the detail page."""
    print("Called /nonexistent endpoint - doing nothing")
    return ""

####
# Celestial "sky map" stuff from Claude
# @app.get("/")
# def home():
#     return Html(
#         Head(Title("Astronomy Tools")),
#         Body(
#             H1("Astronomy Tools"),
#             A("Open Sky Map", href="/sky-map", target="_blank"),
#         )
#     )

@app.get("/sky-map")
def sky_map_page():
    # Serve your HTML template
    with open('./static/sky-map.html', 'r') as f:
        return f.read()

@app.get("/api/sky-map-data")
def get_sky_map_data():
    # Load your GeoJSON files
    def load_geojson(filename):
        with open(f'./data/{filename}', 'r') as f:
            return json.load(f)
    
    def load_starnames():
        with open('./data/starnames.json', 'r') as f:
            return json.load(f)
    
    # Get observer location from session/preferences
    observer_lat = 40.0  # Default or from user prefs
    observer_long = -105.0
    
    return {
        "stars6": load_geojson("stars.6.json"),
        "constellationLines": load_geojson("constellation.lines.json"),
        "constellationBounds": load_geojson("constellation.bounds.json"),
        "messier": load_geojson("messier.json"),
        "starnames": load_starnames(),
        "observerLat": observer_lat,
        "observerLong": observer_long
    }

# ------------------------------ Run server -----------------------------------
serve()
