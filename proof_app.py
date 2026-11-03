"""Proof web demo. Run:  python proof_app.py   then open http://localhost:8000
Needs proof_all.py in the same folder. For the Muse option, set MODEL_API_KEY first."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import proof_all as P

threads, feed = P.build_dataset()
by_id = {t["id"]: t for t in threads}
allocated, ledger = {}, []
LABEL = {"MATCH": "Confirmed", "MATCH_MULTI": "Confirmed: one payment covers two orders", "ASK": "Ask the customer",
         "HOLD": "Hold: payment does not fit", "FLAG": "Flagged: transaction id already used",
         "UNMATCHED": "No matching payment found", "DONE": "Already confirmed"}


def state():
    return {"threads": [{"id": t["id"], "category": t["category"], "customer": t["customer"],
                         "total": t["order"]["total"], "message": t["message"]} for t in threads],
            "feed": feed, "ledger": ledger}


def process(body):
    t = by_id[body["thread"]]
    order, msg = t["order"], body["message"]
    if any(r["order"] == order["id"] for r in ledger):
        return {"outcome": "DONE", "label": LABEL["DONE"], "reason": "This order is already in the ledger.", "state": state()}
    claim, source, note = P.extract_claim(msg), "plain code", ""
    if body.get("use_muse") and P.weak(claim):
        try:
            claim, source = P.llm_extract_claim(msg), "Muse"
        except Exception as e:
            note = "Muse call failed, used plain code only: " + str(e)[:200]
    d = P.decide(claim, order, t["open_orders"], feed, allocated)
    if d["outcome"] in ("MATCH", "MATCH_MULTI"):
        for tx in d["txids"]:
            allocated[tx] = order["id"]
        ledger.append({"order": order["id"], "customer": t["customer"], "amount": order["total"],
                       "txid": d["txids"][0], "how": d["reason"], "message": msg})
    rows = [p for p in feed if p["txid"] in d["txids"] or p["txid"] == claim.get("txid")]
    return {"outcome": d["outcome"], "label": LABEL[d["outcome"]], "reason": d["reason"], "claim": claim,
            "source": source, "note": note, "rows": rows, "order": order, "state": state()}


class H(BaseHTTPRequestHandler):
    def _send(self, code, data, ctype="application/json"):
        raw = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/api/state":
            self._send(200, state())
        else:
            self._send(200, PAGE.encode("utf-8"), "text/html")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/reset":
            allocated.clear(); ledger.clear(); self._send(200, state())
        else:
            self._send(200, process(body))

    def log_message(self, *a):
        pass


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Proof</title>
<style>
:root{--ink:#14213d;--mute:#5b6577;--paper:#f7f8fa;--card:#fff;--rule:#d5dae1;--ok:#0e7c66;--ask:#a86a00;--stop:#b3261e}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 "Segoe UI",system-ui,sans-serif}
header{padding:20px 24px 8px;max-width:1100px;margin:auto}h1{margin:0;font-size:28px;letter-spacing:-.5px}
header p{margin:4px 0 0;color:var(--mute);max-width:70ch}
.sim{margin:12px 0 0;padding:8px 12px;border-left:4px solid var(--ask);background:#fff7e6;font-size:14px}
main{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px;max-width:1100px;margin:16px auto;padding:0 24px}
@media(max-width:800px){main{grid-template-columns:1fr}}
section{background:var(--card);border:1px solid var(--rule);border-radius:6px;padding:16px}
h2{margin:0 0 10px;font-size:18px}label{display:block;font-size:14px;color:var(--mute);margin:10px 0 4px}
select,textarea{width:100%;font:inherit;padding:8px;border:1px solid var(--rule);border-radius:4px;background:#fff;color:var(--ink)}
textarea{min-height:90px;resize:vertical}.row{display:flex;gap:12px;align-items:center;margin-top:12px;flex-wrap:wrap}
button{font:inherit;padding:9px 16px;border-radius:4px;border:1px solid var(--ink);background:var(--ink);color:#fff;cursor:pointer}
button.alt{background:#fff;color:var(--ink)}:focus-visible{outline:3px solid #3b82f6;outline-offset:2px}
.badge{display:inline-block;padding:6px 12px;border-radius:4px;color:#fff;font-weight:600}
.ok{background:var(--ok)}.ask{background:var(--ask)}.stop{background:var(--stop)}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}.chip{border:1px solid var(--rule);border-radius:4px;padding:3px 8px;font-size:14px}
.num{font-variant-numeric:tabular-nums}table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--rule)}th{color:var(--mute);font-weight:600}
.scroll{overflow-x:auto;max-height:260px;overflow-y:auto}.full{grid-column:1/-1}.muted{color:var(--mute);font-size:14px}
</style></head><body>
<header><h1>Proof</h1><p>Paste what a customer sent. Proof finds the payment, decides, and shows its evidence. It only asks when it is unsure.</p>
<div class="sim">The payment feed and customers below are simulated test data.</div></header>
<main>
<section><h2>Customer message</h2>
<label for="thr">Conversation</label><select id="thr"></select>
<label for="msg">Message (edit it to try your own)</label><textarea id="msg"></textarea>
<div class="row"><label style="margin:0"><input type="checkbox" id="muse" checked> Use Muse for messy messages</label></div>
<div class="row"><button id="go">Check payment</button><button class="alt" id="reset">Clear ledger</button></div></section>
<section aria-live="polite"><h2>Decision</h2><div id="out" class="muted">Choose a conversation and press Check payment.</div></section>
<section class="full"><h2>Ledger</h2><div class="scroll"><table><thead><tr><th>Order</th><th>Customer</th><th>Amount</th><th>Transaction</th><th>Why it matched</th></tr></thead><tbody id="led"></tbody></table></div><p id="ledEmpty" class="muted">No confirmed orders yet.</p></section>
<section class="full"><h2>Simulated payment feed</h2><div class="scroll"><table><thead><tr><th>Transaction</th><th>Amount</th><th>Paid from</th><th>Last 4</th><th>Minute</th></tr></thead><tbody id="feed"></tbody></table></div></section>
</main><script>
const $=id=>document.getElementById(id);let S;
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const cls=o=>o.startsWith("MATCH")?"ok":(o=="ASK"||o=="DONE")?"ask":"stop";
function draw(st){S=st;if(!$("thr").options.length){$("thr").innerHTML=st.threads.map(t=>`<option value="${t.id}">${t.id} · ${esc(t.customer)} · ${t.total} taka</option>`).join("");pick();}
$("feed").innerHTML=st.feed.map(p=>`<tr><td>${p.txid}</td><td class=num>${p.amount}</td><td>${esc(p.sender)}</td><td>${p.last4}</td><td class=num>${p.time}</td></tr>`).join("");
$("led").innerHTML=st.ledger.map(r=>`<tr><td>${r.order}</td><td>${esc(r.customer)}</td><td class=num>${r.amount}</td><td>${r.txid}</td><td>${esc(r.how)}</td></tr>`).join("");
$("ledEmpty").style.display=st.ledger.length?"none":"block";}
function pick(){const t=S.threads.find(x=>x.id==$("thr").value);$("msg").value=t.message;}
$("thr").onchange=pick;
$("go").onclick=async()=>{$("go").disabled=true;$("out").textContent="Checking...";
const r=await(await fetch("/api/process",{method:"POST",body:JSON.stringify({thread:$("thr").value,message:$("msg").value,use_muse:$("muse").checked})})).json();
let h=`<span class="badge ${cls(r.outcome)}">${esc(r.label)}</span><p>${esc(r.reason)}</p>`;
if(r.claim){h+=`<div class="chips"><span class="chip">Transaction: ${esc(r.claim.txid||"not given")}</span><span class="chip num">Amount: ${esc(r.claim.amount??"not given")}</span><span class="chip">Last 4: ${esc(r.claim.last4||"not given")}</span><span class="chip">Read by: ${esc(r.source)}</span></div>`;
h+=`<p class="muted">Order total: <span class=num>${r.order.total}</span> taka</p>`;}
if(r.rows&&r.rows.length){h+=`<p class="muted">Evidence from the payment feed:</p>`+r.rows.map(p=>`<div class="chip num">${p.txid} · ${p.amount} taka · from ${esc(p.sender)} · minute ${p.time}</div>`).join("");}
if(r.note)h+=`<p class="muted">${esc(r.note)}</p>`;
$("out").innerHTML=h;draw(r.state);$("go").disabled=false;};
$("reset").onclick=async()=>{draw(await(await fetch("/api/reset",{method:"POST",body:"{}"})).json());$("out").textContent="Ledger cleared.";};
fetch("/api/state").then(r=>r.json()).then(draw);
</script></body></html>"""

if __name__ == "__main__":
    print("Proof is running. Open http://localhost:8000 in your browser. Press Ctrl+C to stop.")
    HTTPServer(("127.0.0.1", 8000), H).serve_forever()
