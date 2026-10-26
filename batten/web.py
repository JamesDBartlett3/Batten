"""LAN web dashboard — serve Batten's UI to any computer or phone on the
same subnet, from the same stdlib-only process.

    batten serve                # http://<your-lan-ip>:8787

Endpoints:
    GET  /                 single-page UI (mobile-first, no external assets)
    GET  /api/status       storms + alerts + connectivity + supplies + model
    GET  /api/worstcase    worst-case 48-h outlook (offline engine)
    GET  /api/supplies     checklist with statuses
    POST /api/supplies     {action: have|need|na|note, item, note?}
    GET  /api/log          weather log tail
    POST /api/chat         {message, history[]} -> local model reply

There is no authentication: Batten serves read-mostly weather data to your
LAN. Set web_host to 127.0.0.1 in config.json to keep it on one machine.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import brain, config, geo, noaa, state, supplies, units, worstcase

# --- data cache (serve never writes state; it reads state + caches live fetches)

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
_STORMS_TTL = 60.0        # NHC refresh at most once a minute
_ALERTS_TTL = 300.0       # providers at most every 5 min


def _cached(key: str, ttl: float, fn):
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    try:
        val = fn()
        ok = True
    except noaa.FetchError as exc:
        val = exc
        ok = False
    with _CACHE_LOCK:
        _CACHE[key] = (now, (ok, val))
    return ok, val


def _lan_urls(port: int) -> list[str]:
    """Best-effort list of URLs a phone on the same subnet could use."""
    urls = []
    candidates = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("10.255.255.255", 1))     # no packets sent; picks a route
        candidates.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        candidates.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    for ip in sorted(candidates):
        if not ip.startswith("127."):
            urls.append(f"http://{ip}:{port}")
    return urls


# --- API ---------------------------------------------------------------------

def api_status(cfg: dict) -> dict:
    st = state.load_state()
    u = units.get_units(cfg)
    lat, lon = cfg.get("lat"), cfg.get("lon")

    ok, storms = _cached("storms", _STORMS_TTL, lambda: noaa.fetch_current_storms(cfg))
    online = bool(ok)
    outage_secs = state.outage_seconds(st)
    if outage_secs is not None:
        conn = {"online": False,
                "detail": f"offline {outage_secs / 3600:.1f} h ({st.get('outage_kind')})",
                "last_good": st.get("last_success")}
    elif online:
        conn = {"online": True, "detail": "online", "last_good": state.iso(state.utcnow())}
    else:
        conn = {"online": False, "detail": f"unreachable ({storms.kind})",
                "last_good": st.get("last_success")}
    if not online:
        storms = [s for s in (st.get("storms") or {}).values()]

    if cfg.get("alert_providers"):
        from . import providers
        got = _cached("alerts", _ALERTS_TTL, lambda: providers.fetch_all(cfg))
        if got[0]:
            alerts, alerts_note = got[1][0], "; ".join(got[1][1])
        else:
            alerts, alerts_note = [], f"alert providers unreachable ({got[1].kind})"
    elif lat is not None and lon is not None:
        got = _cached("alerts", _ALERTS_TTL,
                      lambda: noaa.fetch_alerts(lat, lon, cfg))
        if got[0]:
            alerts, alerts_note = got[1], ""
        else:
            alerts = []
            alerts_note = ("NWS alerts do not cover this location (US only)."
                           if got[1].kind == "unsupported"
                           else f"NWS alerts unreachable ({got[1].kind}).")
    else:
        alerts, alerts_note = [], "run `batten init` to set your coordinates"

    rows = []
    for s in storms:
        row = {
            "name": s["name"],
            "cat": geo.category_label(geo.saffir(s["kt"])),
            "cat_class": geo.saffir(s["kt"]).lower(),
            "speed": u.speed(s["kt"]),
            "pressure": s.get("pressure"),
            "advisory": s.get("advisory"),
            "issued": s.get("issued", ""),
        }
        if s.get("movement_dir") is not None:
            row["movement"] = f"{geo.compass_point(s['movement_dir'])} at {u.speed(s['movement_kt'])}"
        if lat is not None and lon is not None:
            d = geo.haversine_nm(s["lat"], s["lon"], lat, lon)
            b = geo.compass_point(geo.bearing_deg(lat, lon, s["lat"], s["lon"]))
            row["range"] = f"{u.distance(d)} {b} of {cfg.get('place') or 'you'}"
            ap = geo.approach(s, lat, lon)
            row["relation"] = {"toward": "toward you", "away": "away from you",
                               "passing": f"passing to your {b.lower()}"}.get(ap["relation"], "")
            row["relation_kind"] = ap["relation"]
        rows.append(row)
    rows.sort(key=lambda r: r.get("range") == "")

    sup = supplies.load_supplies()
    household = int(cfg.get("household") or 2)
    items = []
    for item in supplies.DEFAULT_SUPPLIES:
        entry = sup.get(item["id"], {})
        items.append({
            "id": item["id"], "cat": item["cat"], "label": item["label"],
            "essential": item["essential"],
            "target": supplies.scaled_quantity(item, household, u),
            "status": "have" if entry.get("have") else ("na" if entry.get("na") else "need"),
            "note": entry.get("note", ""),
        })
    model_ok, model_msg = brain.model_status(cfg)

    return {
        "place": cfg.get("place") or "unset",
        "coords": [lat, lon],
        "units": u.pref,
        "generated": state.iso(state.utcnow()),
        "connectivity": conn,
        "storms": rows,
        "alerts": alerts,
        "alerts_note": alerts_note,
        "supplies": {"items": items, "household": household,
                     "missing_essential": len(supplies.missing_essentials(household, sup))},
        "model": {"ok": model_ok, "detail": model_msg},
    }


def api_worstcase(cfg: dict) -> dict:
    st = state.load_state()
    lat, lon = cfg.get("lat"), cfg.get("lon")
    if lat is None or lon is None:
        return {"text": "Set coordinates first: batten init --lat .. --lon .."}
    storms = [s for s in (st.get("storms") or {}).values()]
    ok, live = _cached("storms", _STORMS_TTL, lambda: noaa.fetch_current_storms(cfg))
    if not storms and ok:
        storms = live
    nearest = None
    if storms:
        nearest = min(storms, key=lambda s: s.get("dist_nm") if s.get("dist_nm") is not None
                      else geo.haversine_nm(s["lat"], s["lon"], lat, lon))
    outage = (state.outage_seconds(st) or 0) / 3600
    wc = worstcase.build_worst_case(nearest, lat, lon,
                                    st.get("last_success") or "no polls yet",
                                    outage_hours=outage)
    return {"text": worstcase.render_worst_case(wc, cfg)}


def api_supplies_get(cfg: dict) -> dict:
    return api_status(cfg)["supplies"]


def api_supplies_post(cfg: dict, payload: dict) -> dict:
    sup = supplies.load_supplies()
    action = payload.get("action")
    item = payload.get("item", "")
    if action == "have":
        ok = supplies.set_item(sup, item, "have", True)
        if ok and payload.get("note"):
            supplies.set_item(sup, item, "note", str(payload["note"]))
    elif action == "need":
        ok = supplies.set_item(sup, item, "have", False)
    elif action == "na":
        ok = supplies.set_item(sup, item, "na", True)
    elif action == "note":
        ok = supplies.set_item(sup, item, "note", str(payload.get("note", "")))
    else:
        return {"error": f"unknown action {action!r}"}, 400
    if not ok:
        return {"error": f"unknown item id {item!r}"}, 400
    supplies.save_supplies(sup)
    return api_supplies_get(cfg)


def api_chat(cfg: dict, payload: dict) -> dict:
    message = str(payload.get("message") or "").strip()
    if not message:
        return {"error": "empty message"}, 400
    history = [
        {"role": "user" if h.get("role") == "user" else "assistant",
         "content": str(h.get("content", ""))[:4000]}
        for h in (payload.get("history") or [])[-10:]
        if h.get("content")
    ]
    st = state.load_state()
    ok, storms = _cached("storms", _STORMS_TTL, lambda: noaa.fetch_current_storms(cfg))
    storms = storms if ok else [s for s in (st.get("storms") or {}).values()]
    if cfg.get("alert_providers"):
        from . import providers
        aok, alerts = _cached("alerts", _ALERTS_TTL, lambda: providers.fetch_all(cfg))
        alerts, alerts_note = (alerts[0] if aok else []), ("" if aok else "; ".join(alerts[1]))
    else:
        alerts, alerts_note = [], ""
    system = brain.build_system_prompt(cfg, st, storms, alerts, alerts_note)
    try:
        b = brain.get_brain(cfg)
        reply = b.complete([{"role": "system", "content": system}] + history
                           + [{"role": "user", "content": message}])
    except noaa.FetchError as exc:
        return {"reply": None, "error": f"model unreachable ({exc})"}
    except OSError as exc:
        return {"reply": None, "error": f"model error: {exc}"}
    return {"reply": reply.strip()}


# --- HTTP plumbing --------------------------------------------------------------

class BattenHandler(BaseHTTPRequestHandler):
    cfg: dict = {}
    server_version = "batten"

    def log_message(self, fmt, *args):  # quiet unless things go wrong
        pass

    def _json(self, obj, status: int = 200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: str, status: int = 200):
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._html(INDEX_HTML)
        elif path == "/api/status":
            self._json(api_status(self.cfg))
        elif path == "/api/worstcase":
            self._json(api_worstcase(self.cfg))
        elif path == "/api/supplies":
            self._json(api_supplies_get(self.cfg))
        elif path == "/api/log":
            self._json({"text": state.tail_weather_log(8000)})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "invalid JSON"}, 400)
            return
        if self.path == "/api/supplies":
            out = api_supplies_post(self.cfg, payload)
            status = 200 if not (isinstance(out, tuple) and out[1] != 200) else out[1]
            self._json(out[0] if isinstance(out, tuple) else out, status)
        elif self.path == "/api/chat":
            out = api_chat(self.cfg, payload)
            status = 200 if not (isinstance(out, tuple) and out[1] != 200) else out[1]
            self._json(out[0] if isinstance(out, tuple) else out, status)
        else:
            self._json({"error": "not found"}, 404)


def serve(cfg: dict, host: str | None = None, port: int | None = None) -> None:
    host = host or cfg.get("web_host") or "0.0.0.0"
    port = int(port or cfg.get("web_port") or 8787)
    BattenHandler.cfg = cfg

    httpd = ThreadingHTTPServer((host, port), BattenHandler)
    print(f"🥥 batten web — serving on port {port} (Ctrl-C to stop)")
    if host == "0.0.0.0":
        for url in _lan_urls(port):
            print(f"   from any device on this network:  {url}")
    print(f"   on this machine:                  http://localhost:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nweb server stopped.")
    finally:
        httpd.server_close()


# --- the single-page UI (no external assets; works offline once loaded) ---------

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0d1220">
<title>batten</title>
<style>
  :root{
    --bg:#0d1220; --card:#161d31; --line:#26304d; --text:#e8ecf6; --dim:#93a0bf;
    --red:#ff5d5d; --green:#4fd08a; --yellow:#ffd166; --cyan:#5bc8ff; --mag:#d78cff;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font:16px/1.45 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       padding-bottom:4.5rem}
  header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);
         padding:.7rem 1rem;display:flex;align-items:center;gap:.6rem;z-index:5}
  header h1{font-size:1.05rem;margin:0;font-weight:700}
  header .sub{color:var(--dim);font-size:.78rem}
  .dot{width:.6rem;height:.6rem;border-radius:50%;display:inline-block}
  .dot.on{background:var(--green);box-shadow:0 0 8px var(--green)}
  .dot.off{background:var(--red);box-shadow:0 0 8px var(--red)}
  nav{position:fixed;bottom:0;left:0;right:0;display:flex;background:var(--card);
      border-top:1px solid var(--line);z-index:5}
  nav button{flex:1;background:none;border:0;color:var(--dim);padding:.65rem 0 .55rem;
             font-size:.72rem;cursor:pointer}
  nav button.active{color:var(--cyan);font-weight:700}
  nav .ico{display:block;font-size:1.15rem}
  main{padding:1rem;max-width:44rem;margin:0 auto}
  .card{background:var(--card);border:1px solid var(--line);border-radius:.8rem;
        padding:.9rem 1rem;margin-bottom:.9rem}
  .card h2{margin:0 0 .55rem;font-size:.8rem;letter-spacing:.08em;color:var(--dim);
           text-transform:uppercase}
  .storm{border-left:3px solid var(--cyan);padding-left:.7rem;margin-bottom:.7rem}
  .storm .name{font-weight:700;font-size:1.05rem}
  .chip{display:inline-block;border-radius:.5rem;padding:.05rem .45rem;font-size:.72rem;
        font-weight:700;margin-left:.35rem;vertical-align:middle}
  .chip.toward{background:#3a1520;color:var(--red);border:1px solid var(--red)}
  .chip.away{background:#12301f;color:var(--green);border:1px solid var(--green)}
  .chip.passing{background:#332b12;color:var(--yellow);border:1px solid var(--yellow)}
  .meta{color:var(--dim);font-size:.85rem;margin-top:.15rem}
  .alert{border-left:3px solid var(--yellow);padding-left:.7rem;margin-bottom:.6rem}
  .alert.severe{border-color:var(--red)} .alert.extreme{border-color:var(--mag)}
  .alert .ev{font-weight:700}
  .note{color:var(--dim);font-size:.85rem;font-style:italic}
  .progress{height:.5rem;background:#0a0e19;border-radius:.3rem;overflow:hidden;margin:.4rem 0 .8rem}
  .progress>div{height:100%;background:var(--green);width:0;transition:width .3s}
  .item{display:flex;align-items:flex-start;gap:.6rem;padding:.5rem 0;
        border-top:1px solid var(--line)}
  .item button{flex-shrink:0;width:1.7rem;height:1.7rem;border-radius:.45rem;border:1px solid var(--line);
               background:#0a0e19;color:var(--dim);font-size:1rem;cursor:pointer}
  .item.have button{background:#12301f;border-color:var(--green);color:var(--green)}
  .item.na button{background:#1a1f30;border-color:var(--dim)}
  .item .lbl{font-size:.92rem}
  .item.have .lbl{color:var(--dim);text-decoration:line-through}
  .item .tgt{color:var(--dim);font-size:.78rem}
  .item .essential::after{content:"essential";color:var(--red);font-size:.68rem;
                          margin-left:.4rem;font-weight:700}
  pre.wrap{white-space:pre-wrap;font:inherit;margin:0}
  button.act{background:var(--cyan);color:#04121e;border:0;border-radius:.6rem;
             padding:.6rem 1rem;font-weight:700;font-size:.95rem;cursor:pointer;width:100%}
  #chatlog{display:flex;flex-direction:column;gap:.5rem;max-height:52vh;overflow-y:auto}
  .msg{border-radius:.8rem;padding:.55rem .75rem;max-width:92%;font-size:.93rem;white-space:pre-wrap}
  .msg.me{align-self:flex-end;background:#1c3a5e}
  .msg.bot{align-self:flex-start;background:var(--card);border:1px solid var(--line)}
  .msg.err{align-self:flex-start;background:#3a1520;border:1px solid var(--red)}
  form#chat{display:flex;gap:.5rem;margin-top:.7rem}
  form#chat input{flex:1;background:#0a0e19;border:1px solid var(--line);border-radius:.6rem;
                  color:var(--text);padding:.6rem .7rem;font-size:1rem}
  .muted{color:var(--dim);font-size:.8rem}
  .banner{border-radius:.6rem;padding:.5rem .8rem;font-size:.85rem;margin-bottom:.9rem}
  .banner.off{background:#3a1520;border:1px solid var(--red)}
  .banner.on{display:none}
</style>
</head>
<body>
<header>
  <span style="font-size:1.3rem">&#127840;</span>
  <div style="flex:1">
    <h1>batten <span class="sub" id="place"></span></h1>
    <div class="sub" id="conn"></div>
  </div>
  <span class="dot off" id="dot"></span>
</header>
<nav>
  <button data-v="dash" class="active"><span class="ico">&#127788;</span>Storms</button>
  <button data-v="sup"><span class="ico">&#9986;</span>Supplies</button>
  <button data-v="worst"><span class="ico">&#9888;</span>Worst case</button>
  <button data-v="log"><span class="ico">&#128220;</span>Log</button>
  <button data-v="chat"><span class="ico">&#128172;</span>Chat</button>
</nav>
<main>
  <section id="v-dash">
    <div class="banner off" id="offline-banner"></div>
    <div class="card"><h2>Tropical cyclones</h2><div id="storms"><p class="muted">loading&hellip;</p></div></div>
    <div class="card"><h2>Alerts</h2><div id="alerts"></div></div>
    <p class="muted" id="gen"></p>
  </section>
  <section id="v-sup" hidden>
    <div class="card"><h2>Supply readiness</h2>
      <div class="progress"><div id="supbar"></div></div>
      <p class="muted" id="supcount"></p>
      <div id="supitems"></div>
    </div>
  </section>
  <section id="v-worst" hidden>
    <div class="card"><h2>Worst-case 48-hour outlook</h2>
      <p class="muted">Offline engine: persistence track from last-good data, one category of assumed
      intensification, growing uncertainty. A planning aid &mdash; never a substitute for official orders.</p>
      <button class="act" id="wcbtn">Generate outlook</button>
      <pre class="wrap" id="wctext" style="margin-top:.8rem"></pre>
    </div>
  </section>
  <section id="v-log" hidden>
    <div class="card"><h2>Weather log</h2><pre class="wrap muted" id="logtext">loading&hellip;</pre></div>
  </section>
  <section id="v-chat" hidden>
    <div class="card">
      <h2>Chat <span class="muted" id="modelstate"></span></h2>
      <div id="chatlog"></div>
      <form id="chat"><input id="chatin" placeholder="Ask about your situation&hellip;" autocomplete="off">
      <button class="act" style="width:auto">Send</button></form>
    </div>
  </section>
</main>
<script>
const $=id=>document.getElementById(id);
let view="dash", history=[], SUPCACHE=null;
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{
  view=b.dataset.v;
  document.querySelectorAll("nav button").forEach(x=>x.classList.toggle("active",x===b));
  ["dash","sup","worst","log","chat"].forEach(v=>$("v-"+v).hidden=(v!==view));
  if(view==="worst"&&!$("wctext").textContent)$("wcbtn").click();
  if(view==="log")fetch("/api/log").then(r=>r.json()).then(j=>$("logtext").textContent=j.text||"(empty)");
});
function esc(s){const d=document.createElement("div");d.textContent=s||"";return d.innerHTML}
async function refresh(){
  try{
    const j=await (await fetch("/api/status")).json();
    $("place").textContent="— "+j.place;
    const c=j.connectivity;
    $("dot").className="dot "+(c.online?"on":"off");
    $("conn").textContent=(c.online?"NOAA online":"NOAA "+c.detail)+(c.last_good?" · last good "+c.last_good:"");
    const ban=$("offline-banner");
    if(!c.online){ban.style.display="block";
      ban.textContent="Working from last-known data. Chat still works (local model); retrying NOAA in the background.";}
    else ban.style.display="none";
    $("storms").innerHTML=j.storms.length?j.storms.map(s=>`
      <div class="storm"><span class="name">${esc(s.name)}</span>
        <span class="chip ${esc(s.cat_class)}" style="background:#0a0e19;color:var(--cyan)">${esc(s.cat)}</span>
        ${s.relation?`<span class="chip ${esc(s.relation_kind)}">${esc(s.relation.toUpperCase())}</span>`:""}
        <div class="meta">${esc(s.speed)}${s.pressure?" · "+esc(s.pressure)+" mb":""}${s.movement?" · moving "+esc(s.movement):""}</div>
        ${s.range?`<div class="meta">${esc(s.range)} · advisory #${esc(s.advisory)}</div>`:""}
      </div>`).join(""):"<p class='muted'>No active storms at last good poll.</p>";
    $("alerts").innerHTML=j.alerts_note?`<p class="note">${esc(j.alerts_note)}</p>`:
      (j.alerts.length?j.alerts.map(a=>`
        <div class="alert ${esc((a.severity||"").toLowerCase())}">
          <span class="ev">${esc(a.event)}</span> <span class="muted">(${esc(a.severity)})</span>
          <div class="meta">${esc(a.area)}</div>
          ${a.headline?`<div class="meta">${esc(a.headline)}</div>`:""}
        </div>`).join(""):"<p class='muted'>No active alerts.</p>");
    $("gen").textContent="generated "+j.generated+" · units: "+j.units+
      " · model: "+(j.model.ok?"ready":"unavailable");
    renderSup(j.supplies);
  }catch(e){$("storms").innerHTML="<p class='muted'>server unreachable</p>";}
}
function renderSup(sup){
  if(!sup)return; SUPCACHE=sup;
  const items=sup.items, have=items.filter(i=>i.status==="have").length;
  $("supbar").style.width=(100*have/items.length)+"%";
  $("supcount").textContent=`${have}/${items.length} ready · ${sup.missing_essential} essential missing · household of ${sup.household}`;
  let cat="";
  $("supitems").innerHTML=items.map(i=>{
    const head=i.cat!==cat?(cat=i.cat,`<h2 style="margin:.9rem 0 .2rem">${esc(i.cat)}</h2>`):"";
    return head+`<div class="item ${i.status}" data-id="${esc(i.id)}">
      <button title="cycle status">${i.status==="have"?"&#10003;":i.status==="na"?"–":"+"}</button>
      <div><div class="lbl ${i.essential?"essential":""}">${esc(i.label)}</div>
      <div class="tgt">${esc(i.target||"")}${i.note?" — "+esc(i.note):""}</div></div></div>`;
  }).join("");
  document.querySelectorAll("#supitems .item button").forEach(b=>b.onclick=async ev=>{
    const id=ev.target.closest(".item").dataset.id;
    const cur=SUPCACHE.items.find(i=>i.id===id).status;
    const action=cur==="have"?"need":(cur==="na"?"need":"have");
    renderSup(await (await fetch("/api/supplies",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({action,item:id})})).json());
  });
}
$("wcbtn").onclick=async()=>{
  $("wcbtn").disabled=true;$("wcbtn").textContent="Computing from last-good data…";
  const j=await (await fetch("/api/worstcase")).json();
  $("wctext").textContent=j.text||"";$("wcbtn").disabled=false;$("wcbtn").textContent="Regenerate";
};
$("chat").onsubmit=async ev=>{
  ev.preventDefault();
  const inp=$("chatin"), text=inp.value.trim(); if(!text)return;
  inp.value="";
  $("chatlog").insertAdjacentHTML("beforeend",`<div class="msg me">${esc(text)}</div>`);
  const pending=document.createElement("div");pending.className="msg bot";pending.textContent="…";
  $("chatlog").appendChild(pending);
  try{
    const j=await (await fetch("/api/chat",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({message:text,history})})).json();
    pending.className="msg "+(j.reply?"bot":"err");
    pending.textContent=j.reply||("⚠ "+(j.error||"no reply"));
    if(j.reply){history.push({role:"user",content:text},{role:"assistant",content:j.reply});
      history=history.slice(-10);}
  }catch(e){pending.className="msg err";pending.textContent="⚠ server error";}
  $("chatlog").scrollTop=$("chatlog").scrollHeight;
};
const v0=new URLSearchParams(location.search).get("view");
if(v0)document.querySelector(`nav button[data-v="${v0}"]`)?.click();
refresh(); setInterval(refresh,60000);
</script>
</body>
</html>
"""
