# app.py
"""
FastHTML + HTMX starter: filters + sortable table + **row-based infinite scroll** (single page scroll)

"""
from __future__ import annotations
import random
from datetime import date, timedelta
from faker import Faker
from fasthtml.common import *

from color_utils import ColorScale, best_text_color, MapPlotLibColorScale

# ----------------------------- App setup -------------------------------------
app, rt = fast_app()

# -------------------------- Fake dataset -------------------------------------
SEED          = 42
N_ROWS_TOTAL  = 1000
PAGE_SIZE     = 40
CATEGORIES    = ["Alpha", "Beta", "Gamma", "Delta"]
REGIONS       = ["All", "North", "South", "East", "West"]

fake = Faker()
random.seed(SEED)
Faker.seed(SEED)

DATA: list[dict] = []
start_date = date.today() - timedelta(days=365)
for i in range(1, N_ROWS_TOTAL + 1):
    DATA.append({
        "id": i,
        "name": fake.name(),
        "category": random.choice(CATEGORIES),
        "region": random.choice(REGIONS[1:]),
        "active": random.choice([True, False, True]),
        "score": random.randint(0, 100),
        "date": (start_date + timedelta(days=random.randint(0, 365))).isoformat(),
    })

# ------------------------ Helpers: filtering/sorting -------------------------

def _parse_bool(val: str|None, *, default=False) -> bool:
    if val is None:
        return default
    return val in {"1", "true", "True", "on", "yes"}


def get_filters(req) -> dict:
    # print(f"\nGet Filters Request URL: {req.url}")
    # e.g. http://localhost:5001/table?q=lisa&region=All&active=any&cat_Gamma=on&min_score=0&max_score=100
    qp = req.query_params
    q = (qp.get("q") or "").strip()
    region = qp.get("region") or "All"
    active_sel = qp.get("active") or "any"
    min_score = int(qp.get("min_score") or 0)
    max_score = int(qp.get("max_score") or 100)
    cats = [c for c in CATEGORIES if _parse_bool(qp.get(f"cat_{c}"))]
    return dict(q=q, region=region, active_sel=active_sel,
                min_score=min_score, max_score=max_score, cats=cats)


def apply_filters(rows: list[dict], f: dict) -> list[dict]:
    res = rows
    if f["q"]:
        ql = f["q"].lower()
        res = [r for r in res if ql in r["name"].lower()]
    if f["region"] != "All":
        res = [r for r in res if r["region"] == f["region"]]
    if f["active_sel"] != "any":
        want = (f["active_sel"] == "true")
        res = [r for r in res if r["active"] == want]
    res = [r for r in res if f["min_score"] <= r["score"] <= f["max_score"]]
    if f["cats"]:
        res = [r for r in res if r["category"] in f["cats"]]
    return res

VALID_SORTS = {"id", "name", "category", "region", "active", "score", "date"}

def sort_rows(rows: list[dict], sort: str, order: str) -> list[dict]:
    sort = sort if sort in VALID_SORTS else "id"
    reverse = (order == "desc")
    return sorted(rows, key=lambda r: r[sort], reverse=reverse)

# ----------------------- UI builders (FastTags) ------------------------------

def filter_form(filters: dict) -> FT:
    def cat_box(cat: str) -> FT:
        checked = (cat in filters["cats"])
        return Label(
            Input(type="checkbox", name=f"cat_{cat}", checked=checked, cls="filter-ctl"), f" {cat}", cls="chk"
        )

    # Re-render table on filter submit; resets paging/sentinel
    # note that Form(key-word-params)(children) works because FT is a builder.
    #  params first, then children FT
    return Form(
            id="filters-form", 
            hx_get=index,  # was table
            hx_target="#content",  # was #table now includes filter and table
            hx_swap="outerHTML",
            hx_push_url="true")( 
        Fieldset(
            Legend("Filters"),
            Div(
                Div(Label("Search"), Input(name="q", value=filters["q"], placeholder="name contains…", cls="filter-ctl")),
                Div(Label("Region"), Select(name="region", cls="filter-ctl")( *[Option(r, value=r, selected=(filters["region"]==r)) for r in REGIONS] )),
                Div(Label("Active"), Select(name="active", cls="filter-ctl")( 
                    Option("Any", value="any", selected=(filters["active_sel"]=="any")),
                    Option("Active", value="true", selected=(filters["active_sel"]=="true")),
                    Option("Inactive", value="false", selected=(filters["active_sel"]=="false")),
                )),
            cls="grid")
        ),
        Fieldset(
            Legend("Categories"), Div(*[cat_box(c) for c in CATEGORIES], cls="cats"),
        ),
        Fieldset(
            Legend("Score range"),
            Div(
                Label("Min"), Input(type="number", name="min_score", value=str(filters["min_score"]), min=0, max=100, step=1, cls="filter-ctl"),
                Label("Max"), Input(type="number", name="max_score", value=str(filters["max_score"]), min=0, max=100, step=1, cls="filter-ctl"),
                cls="range"
            )
        ),
        Div(
            Button("Apply", type="submit"),
            A("Reset", href=index, cls="secondary"),
            cls="actions"
        )
    )


def sort_header(label: str, col: str, sort: str, order: str) -> FT:
    nxt = "desc" if (sort == col and order == "asc") else "asc"
    arrow = " ▲" if sort == col and order == "asc" else (" ▼" if sort == col else "")
    return Th(style='width:10px')(
        A(label + arrow,
          hx_get=index.to(sort=col, order=nxt), # was #table.to()
          hx_target="#content",  
          hx_swap="outerHTML", 
          hx_push_url="true", 
          hx_include="#filters-form") # carry current filters along
    )


# Simple no dependents
SCORE_SCALE = ColorScale(
    vmin=15, vmax=50,
    colors=["#a50026", "#ffff00", "#1a9850"],
    gamma=0.5, # bias toward the upper end a bit (like your example)
    space='srgb' # interpolate in linear light for smoother blends
)

# using Matplotlib's extensive color mapping
MATPLOTLIB_COLOR_SCALE_ALTITUDE = MapPlotLibColorScale(
    model = "RdYlGn",
    vmin=20, vmax=90 # altitude numbers here for testing
)


def render_rows(rows: list[dict]) -> list[FT]:
    trs: list[FT] = []
    for r in rows:
        
        # Color the score cell based on value
        # rgb01 = SCORE_SCALE.rgb01(r["score"]) # 0..1 RGB
        # bg = SCORE_SCALE.css_rgba(r["score"]) # rgba(R,G,B,1)

        rgb = MATPLOTLIB_COLOR_SCALE_ALTITUDE.as_rgb_tuple(r["score"])
        bg = MATPLOTLIB_COLOR_SCALE_ALTITUDE.as_css_rgba(r["score"])
        
        fg = best_text_color(rgb)

        # Row is clickable: navigate to detail page for this id
        href = detail.to(id=r["id"]) # normal navigation (not HTMX)

        tr = Tr(
            Td(r["id"]),
            Td(r["name"]),
            Td(r["category"]),
            Td(r["region"]),
            Td("Yes" if r["active"] else "No"),
            Td(r["score"], style=f"background:{bg}; color:{fg};"),
            Td(r["date"], cls="wrap"),  # note the wrap for any col that should wrap, otherwise ellipsize
        )

        # Make the whole row clickable
        tr.attrs.update({
            'onclick': f"window.location='{href}'",
            'style': (tr.attrs.get('style','') + ' cursor:pointer;').strip()
        })
        trs.append(tr)
    return trs

def apply_sentinel(trs: list[FT], *, next_page:int, has_more:bool, sort:str, order:str):
    """Turn the *last* row into a sentinel that appends the next page after itself."""
    # generates call like:
    # http://localhost:5001/rows?page=4&sort=id&order=desc&q=&region=All&active=any&min_score=0&max_score=100
    if not trs:
        return
    last = trs[-1]
    if has_more:
        last.attrs.update({
            'hx-get': rows.to(page=next_page, sort=sort, order=order),
            'hx-trigger': 'revealed',
            'hx-swap': 'afterend',
            'hx-include': '#filters-form'
        })
    else:
        # optional styling for end marker; keep it a plain row
        last.attrs.update({'class': (last.attrs.get('class','') + ' end-of-results').strip()})


def initial_tbody(req, *, sort:str, order:str) -> list[FT]:
    filters     = get_filters(req)
    filtered    = apply_filters(DATA, filters)
    sorted_rows = sort_rows(filtered, sort, order)

    chunk = sorted_rows[:PAGE_SIZE]
    trs   = render_rows(chunk)

    has_more  = len(sorted_rows) > PAGE_SIZE
    next_page = 2  # since first paint shows page 1
    apply_sentinel(trs, next_page=next_page, has_more=has_more, sort=sort, order=order)
    return trs

# ------------------------------- Routes --------------------------------------

@rt
def index(req, sort: str = "id", order: str = "asc"):
    # note index now handles http initial load as well as htmx table update

    filters = get_filters(req)

    # define content area that gets updates
    # pattern is FT(key-params)(children)
    content = Div(id="content", cls="container")(
        filter_form(filters),
        table(req, sort=sort, order=order)
    )

    # If this is an HTMX request, return only the inner content fragment
    if req.headers.get("HX-Request"):
        print("Got /index HTMX request")
        return content

    # Otherwise return the full page
    print("Got /index HTTP request")

    # "table.striped tbody tr:nth-child(odd){background:rgba(127,127,127,.05);}"
    # "table-layout: fixed; width:100%;"
    
    return Titled(
        "FastHTML + HTMX Demo",
        content,
        Style(
            ".container {display:flex; flex-direction:column; gap:1rem;}"
            ".grid {display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .75rem;}"
            "fieldset {border:1px solid var(--muted-border-color); padding:.75rem; border-radius:.5rem;}"
            ".cats {display:flex; gap:1rem; flex-wrap:wrap;}"
            ".range {display:flex; align-items:center; gap:.5rem;}"
            ".actions {display:flex; gap:.5rem;}"
            ".end-of-results td {color: var(--muted-color);}"
            """
                table.striped {
                    width: 100%;
                    table-layout: fixed;           /* respect widths; faster layout */
                    border-collapse: collapse;
                }
                th, td {
                    padding: .375rem .5rem;
                    overflow: hidden;
                    text-overflow: ellipsis;
                    white-space: nowrap;           /* default: ellipsize; add .wrap to allow wrapping */
                }
                table.striped tbody tr:nth-child(odd) {
                    background: rgba(127, 127, 127, .05);
                }

                .wrap { white-space: normal; }
            """
        )
    )

COL_WIDTHS = ["6%", None, "12%", "10%", "8%", "8%", "12%"]  # None = flex column

@rt
def table(req, sort: str = "id", order: str = "asc") -> FT:
    """Render the table with the first page and a row-sentinel at the end."""
    trs = initial_tbody(req, sort=sort, order=order)
    
    return Table(id="table", cls="striped")(
        Colgroup(*[
            (Col(style=f"width:{w}") if w else Col())
                for w in COL_WIDTHS
    ]),
        Thead(
            Tr(
                sort_header("ID", "id", sort, order),
                sort_header("Name", "name", sort, order),
                sort_header("Category", "category", sort, order),
                sort_header("Region", "region", sort, order),
                sort_header("Active", "active", sort, order),
                sort_header("Score", "score", sort, order),
                sort_header("Date", "date", sort, order),
            )
        ),
        Tbody(*trs)
    )

@rt
def rows(req, page: int = 2, sort: str = "id", order: str = "asc"):
    """Return the *next page* of <tr> elements. The last <tr> becomes the new sentinel.
    Since the triggering row uses hx-swap=afterend, these rows are inserted after it.
    """
    filters     = get_filters(req)
    filtered    = apply_filters(DATA, filters)
    sorted_rows = sort_rows(filtered, sort, order)

    start = (page - 1) * PAGE_SIZE
    end   = start + PAGE_SIZE
    chunk = sorted_rows[start:end]

    if not chunk:
        # Nothing left; return an empty tuple so nothing is inserted
        return tuple()

    trs = render_rows(chunk)
    has_more  = end < len(sorted_rows)
    next_page = page + 1
    apply_sentinel(trs, next_page=next_page, has_more=has_more, sort=sort, order=order)

    return tuple(trs)

@rt
def detail(req, id: int):
    """Simple detail page placeholder. Normal navigation (not HTMX) so the browser's
    Back button returns to the exact table state (filters/sort preserved).
    """
    row = next((r for r in DATA if r["id"] == int(id)), None)
    if not row:
        return Titled("Not Found", P("No record with that id."))


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
            Tr(Td("id"), Td(str(row["id"]))),
            Tr(Td("name"), Td(row["name"])),
            Tr(Td("category"), Td(row["category"])) ,
            Tr(Td("region"), Td(row["region"])) ,
            Tr(Td("active"), Td("Yes" if row["active"] else "No")),
            Tr(Td("score"), Td(str(row["score"]))),
            Tr(Td("date"), Td(row["date"]))
        )
    )

    return Titled(
        f"Details for ID {id}",
        Div(backbar, details, cls="container")
    )

# ------------------------------ Run server -----------------------------------
serve()
