// fanxp-auth.js: the signed-in session shared by account.html, buy.html,
// sell.html and every NFL seat map. Accounts live behind the Fan XP API
// (fanxp_app/fanxp_accounts.py); this file only stores the session it hands
// back and attaches it to API calls.
(function () {
  const API = 'https://fanxp-api.onrender.com';
  const KEY = 'fanxp_session';

  const NFL_TEAMS = {"cardinals": "Arizona Cardinals", "falcons": "Atlanta Falcons", "ravens": "Baltimore Ravens", "bills": "Buffalo Bills", "panthers": "Carolina Panthers", "bears": "Chicago Bears", "bengals": "Cincinnati Bengals", "browns": "Cleveland Browns", "cowboys": "Dallas Cowboys", "broncos": "Denver Broncos", "lions": "Detroit Lions", "packers": "Green Bay Packers", "texans": "Houston Texans", "colts": "Indianapolis Colts", "jaguars": "Jacksonville Jaguars", "chiefs": "Kansas City Chiefs", "raiders": "Las Vegas Raiders", "chargers": "Los Angeles Chargers", "rams": "Los Angeles Rams", "dolphins": "Miami Dolphins", "vikings": "Minnesota Vikings", "patriots": "New England Patriots", "saints": "New Orleans Saints", "giants": "New York Giants", "jets": "New York Jets", "eagles": "Philadelphia Eagles", "steelers": "Pittsburgh Steelers", "49ers": "San Francisco 49ers", "seahawks": "Seattle Seahawks", "buccaneers": "Tampa Bay Buccaneers", "titans": "Tennessee Titans", "commanders": "Washington Commanders"};

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)); } catch (e) { return null; }
  }
  function save(s) {
    try { localStorage.setItem(KEY, JSON.stringify(s)); } catch (e) { /* private mode: session lasts this page only */ }
    memory = s;
  }
  function clear() {
    try { localStorage.removeItem(KEY); } catch (e) {}
    memory = null;
  }
  let memory = load();

  async function freshSession() {
    const s = memory || load();
    if (!s) return null;
    if (s.expires_at && s.expires_at * 1000 - Date.now() > 60000) return s;
    try {
      const r = await fetch(API + '/api/auth/refresh', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: s.refresh_token }),
      });
      if (!r.ok) { clear(); return null; }
      const next = await r.json();
      save(next);
      return next;
    } catch (e) {
      return s;  // offline or cold start: try the old token, the API will say if it's stale
    }
  }

  async function api(path, opts) {
    opts = opts || {};
    const s = await freshSession();
    const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    if (s) headers.Authorization = 'Bearer ' + s.access_token;
    const r = await fetch(API + path, Object.assign({}, opts, { headers }));
    let data = null;
    try { data = await r.json(); } catch (e) {}
    if (r.status === 401 && s) clear();
    if (!r.ok) {
      const err = new Error((data && data.error) || ('Something went wrong (' + r.status + ').'));
      err.status = r.status;
      throw err;
    }
    return data;
  }

  function user() { return (memory && memory.user) || null; }
  function setUser(u) { if (memory) save(Object.assign({}, memory, { user: u })); }

  // Sends people to sign in, then back here.
  function requireSignIn() {
    if (user()) return true;
    const here = location.pathname.split('/').pop() + location.search;
    location.href = 'account.html?next=' + encodeURIComponent(here);
    return false;
  }

  function signOut() { clear(); location.href = 'index.html'; }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  window.FanXP = { API, NFL_TEAMS, api, user, setUser, save, clear, requireSignIn, signOut, esc };
})();
