// /static/scripts.js
if (window.htmx) {
  const spinner = document.getElementById("table-spinner");
  const setSpinner = (active) => {
    if (!spinner) return;
    spinner.classList.toggle("is-active", active);
  };

  document.body.addEventListener("htmx:beforeRequest", (e) => {
    if (e.target?.id !== "loc-form") return;
    setSpinner(true);
    const dialog = document.getElementById("loc-dialog");
    if (dialog?.open) dialog.close();
  });

  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (e.target?.id === "loc-dialog-body") {
      const dialog = document.getElementById("loc-dialog");
      if (dialog && !dialog.open) dialog.showModal();
      return;
    }

    if (e.target?.id === "table") {
      const dialog = document.getElementById("loc-dialog");
      if (dialog?.open) dialog.close();
      setSpinner(false);
    }
  });

  document.body.addEventListener("htmx:responseError", () => {
    setSpinner(false);
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