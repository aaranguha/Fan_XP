"""
generate_stadium.py  —  clean arena no-show heatmap
Usage: python generate_stadium.py <team_slug> <game_folder>
"""
import sys, os, csv, json, math
from collections import defaultdict
from datetime import datetime

# ── Sections ───────────────────────────────────────────────────────────────────
LOWER_SECTIONS = [str(i) for i in range(101, 119)]   # 18
UPPER_SECTIONS = [
    "201","202","204","205","206","207","208","209","210","211","212","213",
    "214","215","216","218","220","221","222","224","225","226","227","228",
    "229","230","232"
]  # 27

# ── Data ───────────────────────────────────────────────────────────────────────
def load_data(slug, folder):
    base = os.path.join("data", slug, folder)
    for fn in ("pre_game.csv","no_shows.csv","game_meta.json"):
        if not os.path.exists(os.path.join(base, fn)):
            raise FileNotFoundError(f"Missing: {os.path.join(base,fn)}")

    pre_c, pre_v, ns_c, ns_v = (defaultdict(int), defaultdict(float),
                                  defaultdict(int), defaultdict(float))
    with open(os.path.join(base,"pre_game.csv"), newline="") as f:
        for row in csv.DictReader(f):
            s = row["section"].strip()
            pre_c[s] += 1
            try: pre_v[s] += float(row["price_usd"])
            except: pass
    with open(os.path.join(base,"no_shows.csv"), newline="") as f:
        for row in csv.DictReader(f):
            s = row["section"].strip()
            ns_c[s] += 1
            try: ns_v[s] += float(row["price_usd"])
            except: pass
    with open(os.path.join(base,"game_meta.json")) as f:
        meta = json.load(f)
    return pre_c, pre_v, ns_c, ns_v, meta

def compute_stats(pre_c, pre_v, ns_c, ns_v):
    out = {}
    for s in set(pre_c)|set(ns_c):
        pre = pre_c.get(s,0); ns = ns_c.get(s,0)
        out[s] = dict(pre=pre, ns=ns,
                      rate=round(ns/pre,4) if pre else 0.0,
                      dead=round(ns_v.get(s,0.0),2))
    return out

def totals(pre_c, ns_c, ns_v):
    tp=sum(pre_c.values()); tn=sum(ns_c.values()); td=sum(ns_v.values())
    return tp, tn, round(tn/tp,4) if tp else 0.0, round(td,2)

def fdate(s):
    try: return datetime.strptime(s,"%Y-%m-%d").strftime("%b %-d")
    except: return s

# ── Color ──────────────────────────────────────────────────────────────────────
def rate_color(r):
    r = max(0.0,min(1.0,r))
    if r <= 0.5:
        t=r*2; return f"#{int(t*255):02x}{int(229-t*14):02x}{int(160-t*160):02x}"
    else:
        t=(r-0.5)*2; return f"#ff{int(215-t*138):02x}{int(t*109):02x}"

def fill(sec, stats):
    d=stats.get(sec)
    return rate_color(d["rate"]) if d and d["pre"]>0 else "#1e293b"

def dattrs(sec, stats):
    d=stats.get(sec,{}); pre=d.get("pre",0); ns=d.get("ns",0)
    r=f"{d['rate']*100:.1f}%" if d.get("pre",0)>0 else "N/A"
    v=f"${d.get('dead',0):,.0f}" if d.get("pre",0)>0 else "N/A"
    return f'data-s="{sec}" data-pre="{pre}" data-ns="{ns}" data-r="{r}" data-v="{v}"'

# ── Arc path ───────────────────────────────────────────────────────────────────
def arc(cx,cy,ia,ib,oa,ob,sd,ed,gap=0.8):
    s,e=sd-gap,ed+gap
    sr,er=math.radians(s),math.radians(e)
    def pt(a,b,ang): return cx+a*math.cos(ang),cy+b*math.sin(ang)
    ix1,iy1=pt(ia,ib,sr); ix2,iy2=pt(ia,ib,er)
    ox1,oy1=pt(oa,ob,sr); ox2,oy2=pt(oa,ob,er)
    laf=1 if abs(s-e)>180 else 0
    return (f"M{ix1:.1f},{iy1:.1f} A{ia},{ib} 0 {laf},1 {ix2:.1f},{iy2:.1f}"
            f" L{ox2:.1f},{oy2:.1f} A{oa},{ob} 0 {laf},0 {ox1:.1f},{oy1:.1f}Z")

def mid(cx,cy,ia,ib,oa,ob,sd,ed):
    a=math.radians((sd+ed)/2)
    return cx+(ia+oa)/2*math.cos(a), cy+(ib+ob)/2*math.sin(a)

# ── Court ──────────────────────────────────────────────────────────────────────
def court(cx,cy):
    hw,hh=105,56; L,R,T,B=cx-hw,cx+hw,cy-hh,cy+hh
    bL,bR=L+11,R-11; pD,pH=41,18; tp=52
    tpy=math.sqrt(max(0,tp**2-(bL-L)**2))
    aT,aB=cy-tpy,cy+tpy; fr=13; fxL,fxR=L+pD,R-pD
    o=[]
    # Floor
    o.append(f'<rect x="{L}" y="{T}" width="{hw*2}" height="{hh*2}" fill="#c8922a" rx="3"/>')
    # Paint
    for xl,xr in[(L,L+pD),(R-pD,R)]:
        o.append(f'<rect x="{xl}" y="{cy-pH}" width="{xr-xl}" height="{pH*2}" fill="#b8821f" stroke="rgba(255,255,255,.6)" stroke-width="1"/>')
    # Lines
    o.append(f'<line x1="{cx}" y1="{T}" x2="{cx}" y2="{B}" stroke="rgba(255,255,255,.7)" stroke-width="1.2"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="13" fill="none" stroke="rgba(255,255,255,.7)" stroke-width="1.2"/>')
    # FT circles
    for fx,s1,s2 in[(fxL,"0,1","0,0"),(fxR,"0,0","0,1")]:
        o.append(f'<path d="M{fx},{cy-fr} A{fr},{fr} 0 {s1} {fx},{cy+fr}" fill="none" stroke="rgba(255,255,255,.65)" stroke-width="1"/>')
        o.append(f'<path d="M{fx},{cy-fr} A{fr},{fr} 0 {s2} {fx},{cy+fr}" fill="none" stroke="rgba(255,255,255,.3)" stroke-width="1" stroke-dasharray="3,3"/>')
    # 3pt
    for side,sw in[(L,"1,1"),(R,"1,0")]:
        o.append(f'<line x1="{side}" y1="{T}" x2="{side}" y2="{aT:.1f}" stroke="rgba(255,255,255,.7)" stroke-width="1.2"/>')
        o.append(f'<path d="M{side},{aT:.1f} A{tp},{tp} 0 {sw} {side},{aB:.1f}" fill="none" stroke="rgba(255,255,255,.7)" stroke-width="1.2"/>')
        o.append(f'<line x1="{side}" y1="{aB:.1f}" x2="{side}" y2="{B}" stroke="rgba(255,255,255,.7)" stroke-width="1.2"/>')
    # Baskets
    for bx in(bL,bR):
        o.append(f'<circle cx="{bx}" cy="{cy}" r="8" fill="none" stroke="rgba(255,255,255,.45)" stroke-width="1"/>')
        o.append(f'<circle cx="{bx}" cy="{cy}" r="4.5" fill="#e87722" stroke="rgba(255,255,255,.8)" stroke-width="1"/>')
    # Border
    o.append(f'<rect x="{L}" y="{T}" width="{hw*2}" height="{hh*2}" fill="none" stroke="rgba(255,255,255,.8)" stroke-width="1.5" rx="3"/>')
    return "\n".join(o)

# ── Arena SVG ──────────────────────────────────────────────────────────────────
def arena_svg(stats):
    cx,cy=450,310
    BG="#07090f"
    # Radii
    OU_A,OU_B = 282,194   # upper outer
    IU_A,IU_B = 214,147   # upper inner / concourse outer
    IL_A,IL_B = 128,80    # lower inner / floor outer
    OL_A,OL_B = 206,142   # lower outer / concourse inner

    o=[]

    # ── Background & outer wall
    o.append(f'<rect width="900" height="620" fill="{BG}"/>')
    o.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{OU_A+6}" ry="{OU_B+6}" fill="#10182a"/>')

    # ── Upper bowl
    n=len(UPPER_SECTIONS); sp=360/n
    for i,sec in enumerate(UPPER_SECTIONS):
        sd=180-i*sp; ed=180-(i+1)*sp
        o.append(f'<path d="{arc(cx,cy,IU_A,IU_B,OU_A,OU_B,sd,ed,0.5)}"'
                 f' fill="{fill(sec,stats)}" stroke="{BG}" stroke-width="1.2" {dattrs(sec,stats)} class="sec"/>')

    # ── Concourse (dark ring separating upper and lower)
    o.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{IU_A}" ry="{IU_B}" fill="#0c1422"/>')
    o.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{OL_A}" ry="{OL_B}" fill="#10182a"/>')

    # ── Lower bowl
    n=len(LOWER_SECTIONS); sp=360/n
    for i,sec in enumerate(LOWER_SECTIONS):
        sd=180-i*sp; ed=180-(i+1)*sp
        o.append(f'<path d="{arc(cx,cy,IL_A,IL_B,OL_A,OL_B,sd,ed,0.8)}"'
                 f' fill="{fill(sec,stats)}" stroke="{BG}" stroke-width="1.5" {dattrs(sec,stats)} class="sec"/>')
        lx,ly=mid(cx,cy,IL_A,IL_B,OL_A,OL_B,sd,ed)
        o.append(f'<text x="{lx:.0f}" y="{ly:.0f}" text-anchor="middle" dominant-baseline="middle"'
                 f' font-size="9" font-family="Inter,sans-serif" font-weight="700"'
                 f' fill="rgba(255,255,255,0.92)" pointer-events="none">{sec}</text>')

    # ── Floor (clean dark circle hides inner section edges)
    o.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{IL_A}" ry="{IL_B}" fill="#0c1828"/>')

    # ── Court (always on top)
    o.append(court(cx,cy))

    # ── Legend label
    o.append(f'<text x="450" y="565" text-anchor="middle" font-size="9"'
             f' font-family="Inter,sans-serif" font-weight="500" letter-spacing="2"'
             f' fill="rgba(150,170,210,0.4)">UPPER DECK</text>')

    return "\n".join(o)

# ── HTML ───────────────────────────────────────────────────────────────────────
def gen_html(slug, folder, stats, meta, tp, tn, rate, dead):
    opp   = meta.get("opponent","Unknown")
    home  = meta.get("home_team",slug).capitalize()
    arena = meta.get("arena","Arena")
    gdate = meta.get("game_date","")
    appeal= meta.get("game_appeal_score","N/A")
    owl   = f"{meta.get('opponent_wins','?')}-{meta.get('opponent_losses','?')}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{home} vs {opp} · Fan XP</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{--bg:#07090f;--b:rgba(0,245,255,.1);--c:#00f5ff;--g:#22c55e;--y:#f59e0b;--r:#ef4444;--w:#e8eeff;--d:rgba(180,205,255,.42);--f:'Inter',sans-serif}}
html,body{{font-family:var(--f);background:var(--bg);color:var(--w);height:100%;overflow:hidden;-webkit-font-smoothing:antialiased}}

/* bar */
#bar{{position:fixed;inset:0 0 auto;height:54px;display:flex;align-items:center;gap:12px;padding:0 20px;background:rgba(4,6,14,.98);border-bottom:1px solid var(--b);backdrop-filter:blur(16px);z-index:9}}
a#bk{{font-size:.73rem;font-weight:600;color:var(--c);border:1px solid rgba(0,245,255,.2);padding:5px 14px;border-radius:999px;text-decoration:none;transition:.15s}}
a#bk:hover{{background:rgba(0,245,255,.08);border-color:var(--c)}}
#ti{{font-size:.88rem;font-weight:800;letter-spacing:-.02em}}
#su{{font-size:.63rem;color:var(--d);margin-top:1px}}
.kpi{{display:flex;flex-direction:column;align-items:center;padding:4px 12px;border-radius:8px;background:rgba(255,255,255,.04);border:1px solid var(--b);min-width:70px}}
.kv{{font-size:.82rem;font-weight:800}}
.kl{{font-size:.55rem;color:var(--d);letter-spacing:.05em;margin-top:1px}}

/* arena */
#wrap{{position:fixed;top:54px;bottom:50px;inset-inline:0;display:flex;align-items:center;justify-content:center;background:var(--bg)}}
#svg{{max-width:100%;max-height:100%;display:block;filter:drop-shadow(0 0 60px rgba(0,0,0,.8))}}

/* sections */
.sec{{cursor:pointer;transition:filter .08s}}
.sec:hover,.sec.on{{filter:brightness(1.25) drop-shadow(0 0 4px rgba(255,255,255,.3));stroke:#fff!important;stroke-width:2px!important}}

/* tooltip */
#tt{{position:fixed;z-index:99;pointer-events:none;display:none;min-width:180px;
  background:rgba(3,6,16,.97);border:1px solid rgba(0,245,255,.15);border-radius:12px;
  padding:12px 15px;backdrop-filter:blur(20px);box-shadow:0 12px 40px rgba(0,0,0,.8)}}
.th{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}}
.tn{{font-size:.78rem;font-weight:800;color:var(--c)}}
.tv{{font-size:.95rem;font-weight:900}}
hr.td{{border:none;border-top:1px solid rgba(255,255,255,.06);margin:0 0 7px}}
.tr{{display:flex;justify-content:space-between;margin-bottom:2px}}
.tk{{font-size:.67rem;color:var(--d)}}
.tw{{font-size:.73rem;font-weight:600}}

/* legend */
#leg{{position:fixed;inset:auto 0 0;height:50px;display:flex;align-items:center;justify-content:center;gap:16px;
  background:rgba(4,6,14,.98);border-top:1px solid var(--b);backdrop-filter:blur(16px);z-index:9}}
.lg{{display:flex;align-items:center;gap:7px;font-size:.64rem;color:var(--d)}}
.lb{{width:130px;height:9px;border-radius:4px;background:linear-gradient(90deg,#16a34a,#d97706,#dc2626);opacity:.9}}
.lnd{{width:18px;height:9px;border-radius:3px;background:#1e293b}}
.lsep{{width:1px;height:22px;background:var(--b)}}
.lm{{font-size:.63rem;color:var(--d)}}
</style>
</head>
<body>

<div id="bar">
  <a id="bk" href="dashboard_{slug}.html">← Dashboard</a>
  <div style="width:1px;height:24px;background:var(--b)"></div>
  <div><div id="ti">{home} vs {opp}</div><div id="su">{fdate(gdate)} · {arena}</div></div>
  <div style="flex:1"></div>
  <div class="kpi"><span class="kv">{tp:,}</span><span class="kl">PRE-GAME</span></div>
  <div class="kpi"><span class="kv">{tn:,}</span><span class="kl">NO-SHOWS</span></div>
  <div class="kpi"><span class="kv" style="color:var(--y)">{rate*100:.1f}%</span><span class="kl">RATE</span></div>
  <div class="kpi"><span class="kv" style="color:var(--r)">${dead:,.0f}</span><span class="kl">DEAD $</span></div>
</div>

<div id="wrap">
<svg id="svg" viewBox="0 0 900 620" xmlns="http://www.w3.org/2000/svg">
{arena_svg(stats)}
</svg>
</div>

<div id="leg">
  <div class="lg"><span>0%</span><div class="lb"></div><span>100% no-show</span></div>
  <div class="lg"><div class="lnd"></div><span>No data</span></div>
  <div class="lsep"></div>
  <span class="lm">Appeal {appeal} · {opp} {owl}</span>
</div>

<div id="tt">
  <div class="th"><span class="tn" id="tn2">—</span><span class="tv" id="tv2">—</span></div>
  <hr class="td"/>
  <div class="tr"><span class="tk">Pre-game</span><span class="tw" id="pre">—</span></div>
  <div class="tr"><span class="tk">No-shows</span><span class="tw" id="ns">—</span></div>
  <div class="tr"><span class="tk">Dead value</span><span class="tw" id="dv">—</span></div>
</div>

<script>
(function(){{
const tt=document.getElementById('tt'),
      tn=document.getElementById('tn2'),tv=document.getElementById('tv2'),
      ep=document.getElementById('pre'),en=document.getElementById('ns'),ed=document.getElementById('dv');
let hi=null;
function show(el){{
  const s=el.dataset.s,pre=el.dataset.pre,ns=el.dataset.ns,r=el.dataset.r,v=el.dataset.v;
  tn.textContent='Section '+s;
  if(r==='N/A'){{tv.textContent='No data';tv.style.color='rgba(180,205,255,.35)';ep.textContent=en.textContent=ed.textContent='—';}}
  else{{
    tv.textContent=r;
    const rv=parseFloat(r)/100;
    tv.style.color=rv<.33?'#22c55e':rv<.66?'#f59e0b':'#ef4444';
    ep.textContent=parseInt(pre).toLocaleString();
    en.textContent=parseInt(ns).toLocaleString();
    ed.textContent=v;
  }}
}}
document.getElementById('svg').addEventListener('mousemove',function(e){{
  const el=e.target,ok=el&&el.classList.contains('sec');
  const W=tt.offsetWidth||190,H=tt.offsetHeight||130;
  tt.style.left=(e.clientX+14+W>innerWidth?e.clientX-W-8:e.clientX+14)+'px';
  tt.style.top=(e.clientY-12+H>innerHeight?e.clientY-H-4:e.clientY-12)+'px';
  if(ok){{if(el!==hi){{if(hi)hi.classList.remove('on');hi=el;hi.classList.add('on');show(el);}}tt.style.display='block';}}
  else{{if(hi){{hi.classList.remove('on');hi=null;}}tt.style.display='none';}}
}});
document.getElementById('svg').addEventListener('mouseleave',function(){{
  if(hi){{hi.classList.remove('on');hi=null;}}tt.style.display='none';
}});
}})();
</script>
</body>
</html>"""

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv)!=3:
        print("Usage: python generate_stadium.py <slug> <game_folder>"); sys.exit(1)
    slug,folder=sys.argv[1].lower(),sys.argv[2]
    pre_c,pre_v,ns_c,ns_v,meta=load_data(slug,folder)
    stats=compute_stats(pre_c,pre_v,ns_c,ns_v)
    tp,tn,rate,dead=totals(pre_c,ns_c,ns_v)
    html=gen_html(slug,folder,stats,meta,tp,tn,rate,dead)
    os.makedirs("Website",exist_ok=True)
    out=os.path.join("Website",f"stadium_{slug}_{folder}.html")
    with open(out,"w") as f: f.write(html)
    print(f"✓ {out}")

if __name__=="__main__": main()
