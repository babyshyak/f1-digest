"""Static page (docs/index.html, for GitHub Pages) and email bodies."""
import html
import json
from datetime import datetime, timezone
from pathlib import Path

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>F1 Digest</title>
<style>
:root{--bg:#f6f6f7;--card:#fff;--fg:#15151e;--mute:#6b6b78;--red:#e10600;--line:#e4e4e8}
@media(prefers-color-scheme:dark){:root{--bg:#0f0f14;--card:#1a1a22;--fg:#f2f2f5;--mute:#9a9aa8;--line:#2a2a34}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,sans-serif}
header{padding:16px 16px 8px;max-width:720px;margin:auto}h1{margin:0;font-size:22px}h1 b{color:var(--red)}
.sub{color:var(--mute);font-size:13px}
#strip{display:flex;gap:8px;overflow-x:auto;padding:8px 16px;max-width:720px;margin:auto;scrollbar-width:none}
.chip{flex:none;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:12px;padding:6px 12px;font-size:13px;cursor:pointer;text-align:center}
.chip small{display:block;color:var(--mute)}.chip.on{background:var(--red);border-color:var(--red);color:#fff}.chip.on small{color:#ffd9d7}
main{max-width:720px;margin:auto;padding:8px 16px 40px}
#q{width:100%;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--fg);font-size:15px;margin-bottom:8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:10px 0}
.card.read{opacity:.5}.top{display:flex;gap:6px;align-items:center;margin-bottom:6px;flex-wrap:wrap}
.pill{font-size:11px;padding:2px 8px;border-radius:99px;background:var(--line);color:var(--mute)}
.pill.upd{background:var(--red);color:#fff}.card h2{font-size:17px;margin:0 0 6px}.card p{margin:0 0 8px}
.meta{font-size:12px;color:var(--mute)}.links{margin-top:6px;font-size:13px}.links a{color:var(--red);margin-right:10px;text-decoration:none}
.also{margin-top:14px}.also summary{cursor:pointer;color:var(--mute);font-size:14px;padding:6px 0}
.row{border-top:1px solid var(--line);padding:8px 0;font-size:14px}.row b{display:block}
.empty{color:var(--mute);text-align:center;padding:40px 0}
</style></head><body>
<header><h1><b>F1</b> Digest</h1><div class="sub" id="gen"></div></header>
<div id="strip"></div>
<main><input id="q" type="search" placeholder="Search the last 30 days"><div id="list"></div></main>
<script>
const DATA=__DATA__;
const $=s=>document.querySelector(s);
let read={};try{read=JSON.parse(localStorage.getItem('f1read')||'{}')}catch(e){}
const save=()=>{try{localStorage.setItem('f1read',JSON.stringify(read))}catch(e){}};
const fmt=d=>new Date(d+'T12:00:00').toLocaleDateString(undefined,{weekday:'short',day:'numeric',month:'short'});
let cur=(location.hash||'').slice(1)||(DATA.days[0]&&DATA.days[0].day);
$('#gen').textContent='Updated '+new Date(DATA.generated).toLocaleString();
function el(t,c,x){const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
function card(it,day){
  const k=day+'|'+it.headline,c=el('div','card'+(read[k]?' read':''));
  const top=el('div','top');top.append(el('span','pill',it.category));
  if(it.kind==='update')top.append(el('span','pill upd','Update'));
  if(day)top.append(el('span','pill',fmt(day)));
  c.append(top,el('h2',0,it.headline),el('p',0,it.summary),el('div','meta',it.confidence));
  const l=el('div','links');it.links.forEach(x=>{const a=el('a',0,x.source);a.href=x.url;a.target='_blank';a.rel='noopener';l.append(a)});
  c.append(l);
  c.onclick=e=>{if(e.target.tagName==='A')return;read[k]=!read[k];if(!read[k])delete read[k];save();c.classList.toggle('read')};
  return c}
function render(){
  const s=$('#strip');s.replaceChildren();
  DATA.days.forEach(d=>{const b=el('button','chip'+(d.day===cur?' on':''),fmt(d.day));
    b.append(el('small',0,d.items.length+' stories'));b.onclick=()=>{cur=d.day;location.hash=cur;$('#q').value='';render()};s.append(b)});
  const q=$('#q').value.trim().toLowerCase(),L=$('#list');L.replaceChildren();
  if(q){let n=0;DATA.days.forEach(d=>d.items.forEach(it=>{
      if((it.headline+' '+it.summary).toLowerCase().includes(q)){L.append(card(it,d.day));n++}}));
    if(!n)L.append(el('div','empty','No matches'));return}
  const d=DATA.days.find(x=>x.day===cur);
  if(!d||!d.items.length){L.append(el('div','empty','Nothing new this day.'));return}
  const top=d.items.filter(i=>i.tier!=='also'),also=d.items.filter(i=>i.tier==='also');
  top.forEach(it=>L.append(card(it,null)));
  if(also.length){const det=el('details','also');det.open=!top.length;
    det.append(el('summary',0,'Also in the news ('+also.length+')'));
    also.forEach(it=>{const r=el('div','row');r.append(el('b',0,it.headline),el('span','meta',it.summary));
      const l=el('div','links');it.links.forEach(x=>{const a=el('a',0,x.source);a.href=x.url;a.target='_blank';a.rel='noopener';l.append(a)});
      r.append(l);det.append(r)});L.append(det)}}
$('#q').oninput=render;render();
</script></body></html>"""


def write_site(docs_dir: Path, days: list[dict]):
    docs_dir.mkdir(parents=True, exist_ok=True)
    payload = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "days": days}
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    (docs_dir / "index.html").write_text(PAGE.replace("__DATA__", data), encoding="utf-8")
    (docs_dir / ".nojekyll").write_text("")


def email_bodies(day: str, items: list[dict], site_url: str, warning: str = "") -> tuple[str, str, str]:
    e = html.escape
    top = [i for i in items if i.get("tier") != "also"]
    also = [i for i in items if i.get("tier") == "also"]
    subject = f"F1 Digest {day}: {len(top)} new " + ("story" if len(top) == 1 else "stories") + (
        f" + {len(also)} more" if also else "")
    parts = [f'<div style="font-family:system-ui,sans-serif;max-width:600px;margin:auto;color:#15151e">'
             f'<h2 style="margin:0 0 12px"><span style="color:#e10600">F1</span> Digest, {e(day)}</h2>']
    text = [f"F1 Digest, {day}\n"]
    if warning:
        parts.append(f'<p style="background:#fff3cd;padding:8px;border-radius:8px">&#9888; {e(warning)}</p>')
        text.append(f"WARNING: {warning}\n")

    def links_html(it):
        return " ".join(f'<a href="{e(x["url"])}" style="color:#e10600;margin-right:8px">{e(x["source"])}</a>'
                        for x in it["links"])

    def links_text(it):
        return "\n".join(f"  {x['source']}: {x['url']}" for x in it["links"])

    for it in top:
        tag = f"{it['category']}" + (" · UPDATE" if it["kind"] == "update" else "")
        parts.append(
            f'<div style="border:1px solid #e4e4e8;border-radius:12px;padding:12px;margin:10px 0">'
            f'<div style="font-size:11px;color:#6b6b78;text-transform:uppercase">{e(tag)}</div>'
            f'<div style="font-size:17px;font-weight:600;margin:4px 0">{e(it["headline"])}</div>'
            f'<div>{e(it["summary"])}</div>'
            f'<div style="font-size:12px;color:#6b6b78;margin-top:6px">{e(it["confidence"])}</div>'
            f'<div style="font-size:13px;margin-top:4px">{links_html(it)}</div></div>')
        text.append(f"[{tag}] {it['headline']}\n{it['summary']}\n({it['confidence']})\n{links_text(it)}\n")
    if also:
        parts.append('<h3 style="margin:18px 0 4px">Also in the news</h3>')
        text.append("ALSO IN THE NEWS\n")
        for it in also:
            parts.append(f'<div style="border-top:1px solid #e4e4e8;padding:8px 0;font-size:14px"><b>{e(it["headline"])}</b>'
                         f'<div style="color:#6b6b78">{e(it["summary"])}</div>'
                         f'<div style="font-size:13px">{links_html(it)}</div></div>')
            text.append(f"- {it['headline']}\n  {it['summary']}\n{links_text(it)}\n")
    if site_url:
        parts.append(f'<p><a href="{e(site_url)}" style="color:#e10600">Open the full 30-day archive</a></p>')
        text.append(f"Archive: {site_url}")
    parts.append("</div>")
    return subject, "".join(parts), "\n".join(text)
