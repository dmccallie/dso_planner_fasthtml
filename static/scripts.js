// /static/scripts.js
const KEY = "astro.prefs.v1";
const COOKIE = "astro_loc";

const read = () => { try { return JSON.parse(localStorage.getItem(KEY)||"{}"); } catch { return {}; } };
const write = (obj) => localStorage.setItem(KEY, JSON.stringify(obj));

function mirrorCookie(p) {
  const compact = {
    lat:p.lat, lon:p.lon, date:p.date, hstart:p.hstart, hend:p.hend,
    fl_mm:p.fl_mm, px_um:p.px_um, rows:p.rows, cols:p.cols, site_name:p.site_name
  };
  document.cookie = `${COOKIE}=${encodeURIComponent(JSON.stringify(compact))}; Max-Age=31536000; Path=/; SameSite=Lax`;
}

function applyToHidden(form, p) {
  ["lat","lon","date","hstart","hend","fl_mm","px_um","rows","cols"].forEach(n=>{
    const el = form.querySelector(`[name="${n}"]`);
    if (el) el.value = p?.[n] ?? "";
  });
}

function prefillDialog() {
  const f = document.getElementById("loc-form");
  if (!f) return;
  const p = read();
  for (const el of f.elements) {
    if (el.name && p[el.name] != null) el.value = p[el.name];
  }
}

function openLocDialog() {
  const d = document.getElementById("loc-dialog");
  prefillDialog();
  d.showModal();
}
window.openLocDialog = openLocDialog; // callable from onclick
console.log("from scripts -> openLocDialog bound");

// Save handler (delegated so it survives swaps)
document.body.addEventListener("click", (e) => {
  if (e.target?.id !== "save-loc") return;
  const d = document.getElementById("loc-dialog");
  const f = document.getElementById("loc-form");
  const prefs = Object.fromEntries(new FormData(f).entries());
  write(prefs);
  mirrorCookie(prefs);

  const filters = document.getElementById("filters-form");
  if (filters) {
    applyToHidden(filters, prefs);
    filters.requestSubmit(); // HTMX will refresh #content
  }
  d.close(); // static dialog stays in DOM; just closes
});

// Re-apply hidden fields after HTMX swaps
function bind() {
  const form = document.getElementById("filters-form");
  if (!form) return;
  applyToHidden(form, read());
}
document.addEventListener("DOMContentLoaded", bind);
if (window.htmx) {
  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (e.target.id === "content" || e.target.id === "filters-form") bind();
  });
}


// code added to persist sortname and order into hidden fields and localStorage
// so that filter changes do not reset current sort
let sort = (() => {
  try { return JSON.parse(localStorage.getItem('tableSort')) || {}; }
  catch { return {}; }
})();

function setSort(col) {
  console.log("setSort called with col=", col);
  if (sort.name === col) {
    sort.order = (sort.order === 'asc') ? 'desc' : 'asc';
  } else {
    sort.name = col;
    sort.order = 'asc';
  }
  localStorage.setItem('tableSort', JSON.stringify(sort));
  updateHiddenSort();
}
window.setSort = setSort; // callable from onclick

function updateHiddenSort() {
  const form = document.getElementById('filters-form');
  if (!form) return;
  form.elements.sortname.value = sort.name || '';
  form.elements.order.value = sort.order || 'asc';
}

document.addEventListener('DOMContentLoaded', () => {
  sort.name ||= 'name';
  sort.order ||= 'asc';
  updateHiddenSort();
});


// scroll to top *before* swapping #content, so the new sentinel starts off-screen
// to avoid auto-triggering the load-more sentinel again immediately
// Keep sort headers in view: remember where the clicked header was,
// and re-align the new header to the same viewport offset after swap.

let lastSortClick = null;

document.body.addEventListener("click", (e) => {
  const btn = e.target.closest("button.clicksort");
  if (!btn) return;
  lastSortClick = {
    col: btn.dataset.col || "",
    // where was the header relative to the viewport?
    viewportOffset: btn.getBoundingClientRect().top
  };
});

// Debug HTMX availability immediately
// console.log("HTMX available at script load:", !!window.htmx);
// console.log("HTMX object at script load:", window.htmx);

// Wait for HTMX to potentially load
// setTimeout(() => {
//   console.log("HTMX available after 1000ms:", !!window.htmx);
//   console.log("HTMX object after 1000ms:", window.htmx);
// }, 1000);

if (window.htmx) {
  console.log("Setting up HTMX event listeners...");
  
  // Listen to ALL HTMX events for debugging
  // document.body.addEventListener("htmx:beforeRequest", (e) => {
  //   console.log("HTMX beforeRequest:", e.target?.id, e.target);
  // });
  
  // Try both afterRequest and afterSettle events
  const chartInitHandler = async (e, eventType) => {
    console.log(`GOT htmx:${eventType} for target:`, e.target?.id, "element:", e.target);
    
    if (e.target?.id === "dso-moon-container") {
      console.log("Target matches! Container:", e.target);
      const container = e.target;
      
      // Debug: log all data attributes
      console.log("All dataset:", container.dataset);
      console.log("Individual attributes:", {
        dsoId: container.dataset.dsoId,
        lat: container.dataset.lat,
        lon: container.dataset.lon,
        date: container.dataset.date,
        tz: container.dataset.tz
      });
      
      const dsoId = container.dataset.dsoId;
      const lat = container.dataset.lat;
      const lon = container.dataset.lon;
      const date = container.dataset.date;
      const tz = container.dataset.tz;
      
      // console.log("About to call initDSOMoonChartFromAPI with:", {dsoId, lat, lon, date, tz});
      
      try {
        // Import and call the chart initialization function
        const { initDSOMoonChartFromAPI } = await import('/static/dso_moon_chart.js');
        console.log("Successfully imported initDSOMoonChartFromAPI");
        
        initDSOMoonChartFromAPI('dso-moon-container', dsoId, {
          lat: parseFloat(lat),
          lon: parseFloat(lon),
          date: date,
          tz: tz
        });
        // console.log("Called initDSOMoonChartFromAPI successfully");
      } catch (error) {
        console.error("Error in chart initialization:", error);
      }
    }
  };
  
  document.body.addEventListener("htmx:afterRequest", (e) => chartInitHandler(e, "afterRequest"));
  // document.body.addEventListener("htmx:afterSettle", (e) => chartInitHandler(e, "afterSettle"));

  document.body.addEventListener("htmx:afterSwap", (e) => {
    // Only care when #content was swapped
    if (!e.target || e.target.id !== "content") return;

    if (!lastSortClick) return;

    // Find the same header button in the new content
    const selector = `#content button.clicksort[data-col="${lastSortClick.col}"]`;
    const newBtn = document.querySelector(selector) || document.querySelector("#content thead");
    if (newBtn) {
      const nowTop = newBtn.getBoundingClientRect().top;
      const dy = nowTop - lastSortClick.viewportOffset;
      if (Math.abs(dy) > 1) {
        window.scrollBy({ top: dy+5, left: 0, behavior: "auto" });
      }
    }
    lastSortClick = null;
  });
  
} else {
  console.error("HTMX not found! Is it loaded?");
  
  // Fallback: try to initialize chart directly when DOM is ready
  document.addEventListener("DOMContentLoaded", async () => {
    console.log("DOMContentLoaded - trying direct chart init");
    const container = document.getElementById("dso-moon-container");
    if (container) {
      console.log("Found container, trying to init chart directly");
      const dsoId = container.dataset.dsoId;
      const lat = container.dataset.lat;
      const lon = container.dataset.lon;
      const date = container.dataset.date;
      const tz = container.dataset.tz;
      
      if (dsoId) {
        try {
          const { initDSOMoonChartFromAPI } = await import('/static/dso_moon_chart.js');
          initDSOMoonChartFromAPI('dso-moon-container', dsoId, {
            lat: parseFloat(lat),
            lon: parseFloat(lon),
            date: date,
            tz: tz
          });
        } catch (error) {
          console.error("Direct chart init error:", error);
        }
      }
    }
  });
}