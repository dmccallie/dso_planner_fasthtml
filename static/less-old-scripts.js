// script to save/load filter form preferences to localStorage
// so they persist across page reloads, will be saved per browser
// uses htmx events to re-bind after htmx swaps content
// assumes form has id="filters-form"

// loads as type="module", so can use modern JS 
// BUT these are NOT WINDOWS globals, so can't call from inline onclick etc
// hence use addEventListener for clicks

const KEY = "astro.prefs.v1";
const COOKIE = "astro_loc"; // compact mirror

const read = () => { try { return JSON.parse(localStorage.getItem(KEY)||"{}"); } catch { return {}; } };
const write = (prefs) => localStorage.setItem(KEY, JSON.stringify(prefs));

function mirrorCookie(p) {
  // Keep it tiny: only what's needed for pre-render; avoid large JSON.
  const compact = {
    lat:p.lat, lon:p.lon, date:p.date, hstart:p.hstart, hend:p.hend,
    fl_mm:p.fl_mm, px_um:p.px_um, rows:p.rows, cols:p.cols
  };
  document.cookie = `${COOKIE}=${encodeURIComponent(JSON.stringify(compact))}; Max-Age=31536000; Path=/; SameSite=Lax`;
}

function applyToHidden(form, p) {
  const names = ["lat","lon","date","hstart","hend","fl_mm","px_um","rows","cols"];
  for (const n of names) {
    const el = form.querySelector(`[name="${n}"]`);
    if (el) el.value = p?.[n] ?? "";
  }
}

function bind() {
  const form = document.getElementById("filters-form");
  if (!form) return;
  const prefs = read();
  applyToHidden(form, prefs);
}

// function saveFromLocForm() {
//   const f = document.getElementById("loc-form");
//   const p = {};
//   for (const el of f.elements) if (el.name) p[el.name] = el.value;
//   write(p); mirrorCookie(p);
//   closeModal();
//   // Re-apply hidden fields & refresh content with current filters + new loc
//   const filters = document.getElementById("filters-form");
//   if (filters) { applyToHidden(filters, p); filters.requestSubmit(); }
// }

function closeModal(){ const m = document.getElementById("modal"); if (m) m.remove(); }

// boot
document.addEventListener("DOMContentLoaded", bind);
document.body.addEventListener("click", e => {
  if (e.target?.id === "save-loc") saveLocAndClose();
});
if (window.htmx) {
  document.body.addEventListener("htmx:afterSwap", e => {
    if (e.target.id === "content" || e.target.id === "filters-form" || e.target.id === "modal") bind();
  });
}

// to support the <dialog> after it closes

function applyLocToHidden(form, p){
  const names = ["lat","lon","date","hstart","hend","fl_mm","px_um","rows","cols"];
  for (const n of names){
    const el = form.querySelector(`[name="${n}"]`);
    if (el) el.value = p?.[n] ?? "";
  }
}

function saveLocAndClose(){
  const d = document.getElementById("loc-dialog");
  const f = document.getElementById("loc-form");
  const prefs = Object.fromEntries(new FormData(f).entries());
  // persist
  localStorage.setItem("astro.prefs.v1", JSON.stringify(prefs));
  document.cookie = "astro_loc="+encodeURIComponent(JSON.stringify(prefs))+"; Max-Age=31536000; Path=/; SameSite=Lax";
  // close dialog but don't call remove() here, wait for onclose handler
  // so it works with native close button or ESC key
  d.close();
  // refresh table with new prefs
  const filters = document.getElementById("filters-form");
  if (filters){
    applyLocToHidden(filters, prefs);
    filters.requestSubmit(); // HTMX will GET "/" and swap #content
  }
}