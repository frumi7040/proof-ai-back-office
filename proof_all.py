# ===== normalize.py =====
"""Turn messy customer text / voice transcripts into a structured payment claim.
Plain code first; the Muse model is only called when this fails (see pipeline.py)."""
import re

UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
         "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
        "seventy": 70, "eighty": 80, "ninety": 90}
DIGIT_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
               "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
NUMBER_TOKENS = set(UNITS) | set(TENS) | {"hundred", "thousand", "and"}


def words_to_int(tokens):
    total, current, found = 0, 0, False
    for t in tokens:
        if t in UNITS:
            current += UNITS[t]; found = True
        elif t in TENS:
            current += TENS[t]; found = True
        elif t == "hundred":
            current = max(current, 1) * 100; found = True
        elif t == "thousand":
            total += max(current, 1) * 1000; current = 0; found = True
    return total + current if found else None


def spoken_digits(text, n=4):
    """'four four one two' -> '4412' (first run of digit words)."""
    out = []
    for t in re.findall(r"[a-z]+|\d", text.lower()):
        if t in DIGIT_WORDS:
            out.append(DIGIT_WORDS[t])
        elif t.isdigit():
            out.append(t)
        elif out:
            break
        if len(out) == n:
            break
    return "".join(out) if len(out) == n else None


def _small(n):
    inv_u = {v: k for k, v in UNITS.items()}
    inv_t = {v: k for k, v in TENS.items()}
    if n < 20:
        return inv_u[n]
    t, u = divmod(n, 10)
    return inv_t[t * 10] + (" " + inv_u[u] if u else "")


def amount_to_words(n):
    parts = []
    th, rest = divmod(n, 1000)
    if th:
        parts += [_small(th), "thousand"]
    h, r = divmod(rest, 100)
    if h:
        parts += [_small(h), "hundred"]
    if r:
        parts.append(_small(r))
    return " ".join(parts)


def digits_to_words(s):
    inv = {v: k for k, v in DIGIT_WORDS.items()}
    return " ".join(inv[c] for c in s)


TXID_RE = re.compile(r"(?:tx\s?id|trx\s?id|transaction\s*id)[^A-Za-z0-9]*(?:is\s+)?([A-Za-z0-9]{8,12})", re.I)
AMT_RE_1 = re.compile(r"(\d[\d,]*)\s*(?:taka|tk|bdt)\b", re.I)
AMT_RE_2 = re.compile(r"(?:tk\.?|bdt|৳)\s*(\d[\d,]*)", re.I)
LAST4_RE = re.compile(r"(?:last\s+(?:four|4)\s+digits?|ending(?:\s+in|\s+with)?)", re.I)


def extract_claim(text):
    """Rules-only extraction. Returns {txid, amount, last4} with None for anything not found."""
    claim = {"txid": None, "amount": None, "last4": None}
    m = TXID_RE.search(text)
    if m:
        claim["txid"] = m.group(1).upper()
    m = AMT_RE_1.search(text) or AMT_RE_2.search(text)
    if m:
        claim["amount"] = int(m.group(1).replace(",", ""))
    else:
        low = text.lower()
        i = low.find("taka")
        if i > 0:
            toks = re.findall(r"[a-z]+", low[:i])
            run = []
            for t in reversed(toks):
                if t in NUMBER_TOKENS:
                    run.append(t)
                elif run:
                    break
            val = words_to_int(list(reversed(run)))
            if val:
                claim["amount"] = val
    m = LAST4_RE.search(text)
    if m:
        seg = text[m.end():]
        if claim["txid"]:
            seg = seg.replace(claim["txid"], "")
        digs = re.search(r"\b\d{4}\b", seg)
        claim["last4"] = digs.group(0) if digs else spoken_digits(seg)
    return claim

# ===== matcher.py =====
"""Payment-to-order matching in plain code. Never auto-confirms on amount alone."""
WINDOW_MIN = 180  # payment must arrive within 3 hours after the order


def edit1(a, b):
    """True if a and b differ by exactly one insert/delete/substitution."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    s, l = (a, b) if len(a) < len(b) else (b, a)
    return any(l[:i] + l[i + 1:] == s for i in range(len(l)))


def decide(claim, order, open_orders, feed, allocated):
    """Return {outcome, reason, txids, evidence}. Outcomes:
    MATCH, MATCH_MULTI, ASK (need info), HOLD (money doesn't fit), FLAG (reused id), UNMATCHED."""
    tx, amount, l4 = claim.get("txid"), claim.get("amount"), claim.get("last4")
    cust_total = sum(o["total"] for o in open_orders)

    def res(outcome, reason, txids=(), ev=()):
        return {"outcome": outcome, "reason": reason, "txids": list(txids), "evidence": list(ev)}

    def by_amount(p, how):
        ev = [f"claim:{how}", f"feed:{p['txid']}"]
        if amount is not None and amount != p["amount"]:
            return res("HOLD", f"claimed {amount} but feed shows {p['amount']}", ev=ev)
        if p["amount"] == order["total"]:
            return res("MATCH", f"{how}; amount equals order total", [p["txid"]], ev)
        if len(open_orders) > 1 and p["amount"] == cust_total:
            return res("MATCH_MULTI", f"{how}; amount equals total of all open orders", [p["txid"]], ev)
        if p["amount"] < order["total"]:
            return res("HOLD", f"partial payment {p['amount']} of {order['total']}", ev=ev)
        return res("HOLD", f"amount {p['amount']} does not fit order {order['total']}", ev=ev)

    if tx:
        exact = [p for p in feed if p["txid"] == tx]
        if exact:
            if tx in allocated and allocated[tx] != order["id"]:
                return res("FLAG", f"transaction {tx} already used for order {allocated[tx]}", ev=[f"feed:{tx}"])
            return by_amount(exact[0], "exact transaction id")
        fuzzy = [p for p in feed if edit1(p["txid"], tx) and p["txid"] not in allocated]
        if len(fuzzy) == 1:
            return by_amount(fuzzy[0], "transaction id with one-character typo")
        if len(fuzzy) > 1:
            return res("ASK", "typo'd transaction id fits several payments")
        return res("UNMATCHED", f"no payment with id {tx} in the feed")

    if not l4 or amount is None:
        return res("ASK", "need the transaction id, or amount plus last 4 digits of the paying account")
    cands = [p for p in feed if p["txid"] not in allocated and p["last4"] == l4
             and p["amount"] == amount
             and 0 <= p["time"] - order["created"] <= WINDOW_MIN]
    if len(cands) == 1:
        return by_amount(cands[0], "amount + last 4 digits + time window")
    if len(cands) > 1:
        return res("ASK", "several payments fit amount, last 4 digits and time")
    return res("UNMATCHED", "no payment fits amount, last 4 digits and time")

# ===== data.py =====
"""Synthetic benchmark: made-up customers, numbers and payments only. No real data."""
import random, string

NAMES = ["Tanjila Ahmed", "Rafi Hasan", "Nusrat Jahan", "Imran Khan", "Sadia Islam", "Mahmud Hossain",
         "Farzana Akter", "Tareq Aziz", "Lamia Chowdhury", "Shakib Rahman", "Mitu Sultana", "Arif Billah",
         "Rumana Haque", "Jubayer Alam", "Sumaiya Noor", "Faisal Karim", "Ishrat Zaman", "Rakib Mia",
         "Tasnia Binte", "Nayeem Uddin", "Mahira Khan", "Zahid Hasan", "Anika Tabassum", "Sohel Rana",
         "Ayesha Siddika", "Kamrul Hasan", "Poly Akter", "Shuvo Dey", "Bithi Rani", "Raihan Kabir",
         "Moushumi Das", "Habib Ullah", "Dilruba Yasmin", "Nabil Sarker", "Orpita Roy", "Salman Reza",
         "Jannat Ferdous", "Emon Sheikh", "Rina Parvin", "Tuhin Mondal", "Sharmin Lata", "Alamin Seikh",
         "Keya Paul", "Robin Gazi", "Nila Sen", "Hasib Talukder", "Munia Rahim", "Sagor Biswas"]


def build_dataset(seed=7):
    rnd = random.Random(seed)
    feed, threads = [], []
    names = NAMES[:]
    rnd.shuffle(names)
    state = {"n": 0}

    def phone(): return "+8801" + "".join(rnd.choice(string.digits) for _ in range(9))
    def txid(): return "BK" + "".join(rnd.choice(string.digits) for _ in range(7)) + rnd.choice("ABCDEFGHJKLMNPQRSTUVWXYZ")
    def l4(): return "".join(rnd.choice(string.digits) for _ in range(4))
    def amt(): return rnd.choice(range(500, 9000, 50))

    def pay(amount, t, tx=None, last4=None, sender=None):
        p = {"txid": tx or txid(), "amount": amount, "time": t, "sender": sender or phone(), "last4": last4 or l4()}
        feed.append(p)
        return p

    def new_thread(cat, total=None, channel="text", msg=None, claim=None, expected=None, others=()):
        i = state["n"]; state["n"] += 1
        name = names[i % len(names)]
        order = {"id": f"O{i:03d}", "total": total or amt(), "created": i * 9, "customer": name}
        t = {"id": f"T{i:03d}", "category": cat, "customer": name, "chat_phone": phone(), "order": order,
             "open_orders": [order] + [dict(o, customer=name) for o in others], "channel": channel,
             "message": msg, "expected_claim": claim, "expected": expected}
        threads.append(t)
        return t

    def text_msg(amount, tx):
        return rnd.choice([f"Sent {amount} taka via bKash. TxID {tx}",
                           f"payment done, Tk {amount}, transaction id {tx}",
                           f"Paid {amount:,} taka. TrxID: {tx} please confirm",
                           f"bhai sent {amount} taka, transaction id is {tx}"])

    def voice_msg(amount, last4):
        return (f"I pay {amount_to_words(amount)} taka for my order, the last four digit of payment "
                f"account number is {digits_to_words(last4)}.")

    clean_txs = []
    for _ in range(8):  # clean: exact tx, same sender
        a = amt(); th = new_thread("clean", a); tx = txid()
        pay(a, th["order"]["created"] + 12, tx, sender=th["chat_phone"])
        th["message"] = text_msg(a, tx)
        th["expected_claim"] = {"txid": tx, "amount": a, "last4": None}
        th["expected"] = {"outcome": "MATCH", "txids": [tx]}
        clean_txs.append(tx)
    for _ in range(4):  # paid from a different number than the chat number
        a = amt(); th = new_thread("different_sender", a); tx = txid()
        pay(a, th["order"]["created"] + 20, tx)
        th["message"] = text_msg(a, tx)
        th["expected_claim"] = {"txid": tx, "amount": a, "last4": None}
        th["expected"] = {"outcome": "MATCH", "txids": [tx]}
    for _ in range(3):  # one-character typo in the transaction id
        a = amt(); th = new_thread("typo_txid", a); tx = txid()
        pay(a, th["order"]["created"] + 15, tx)
        pos = rnd.randrange(2, 9)
        bad = tx[:pos] + rnd.choice([c for c in string.digits if c != tx[pos]]) + tx[pos + 1:]
        th["message"] = text_msg(a, bad)
        th["expected_claim"] = {"txid": bad, "amount": a, "last4": None}
        th["expected"] = {"outcome": "MATCH", "txids": [tx]}
    for _ in range(4):  # voice note: amount + last 4 digits, no transaction id
        a = amt(); th = new_thread("voice_last4", a, "voice"); lf = l4(); tx = txid()
        pay(a, th["order"]["created"] + 25, tx, lf)
        pay(a + 700, th["order"]["created"] + 30)  # decoy, different amount
        th["message"] = voice_msg(a, lf)
        th["expected_claim"] = {"txid": None, "amount": a, "last4": lf}
        th["expected"] = {"outcome": "MATCH", "txids": [tx]}
    for _ in range(2):  # ambiguous pairs: same amount, no identifying detail
        a = amt()
        for _k in range(2):
            th = new_thread("ambiguous", a, "voice")
            pay(a, th["order"]["created"] + 10)
            th["message"] = f"I paid {amount_to_words(a)} taka just now."
            th["expected_claim"] = {"txid": None, "amount": a, "last4": None}
            th["expected"] = {"outcome": "ASK", "txids": []}
    for _ in range(3):  # one payment covers two open orders
        a, b = amt(), amt()
        extra = {"id": f"X{state['n']:03d}", "total": b, "created": state["n"] * 9 - 3}
        th = new_thread("pays_for_both", a, others=[extra]); tx = txid()
        pay(a + b, th["order"]["created"] + 14, tx)
        th["message"] = f"I paid for both orders, {a + b} taka, TxID {tx}"
        th["expected_claim"] = {"txid": tx, "amount": a + b, "last4": None}
        th["expected"] = {"outcome": "MATCH_MULTI", "txids": [tx]}
    for _ in range(2):  # partial payment
        a = rnd.choice(range(2000, 6000, 100)); paid = a - rnd.choice([500, 1000])
        th = new_thread("partial", a); tx = txid()
        pay(paid, th["order"]["created"] + 11, tx)
        th["message"] = text_msg(paid, tx)
        th["expected_claim"] = {"txid": tx, "amount": paid, "last4": None}
        th["expected"] = {"outcome": "HOLD", "txids": []}
    for _ in range(3):  # claimed payment that does not exist
        a = amt(); th = new_thread("fake_claim", a); tx = txid()
        th["message"] = text_msg(a, tx)
        th["expected_claim"] = {"txid": tx, "amount": a, "last4": None}
        th["expected"] = {"outcome": "UNMATCHED", "txids": []}
    for tx_old in clean_txs[:2]:  # someone re-uses a screenshot / transaction id
        a = amt(); th = new_thread("reused_txid", a)
        th["message"] = text_msg(a, tx_old)
        th["expected_claim"] = {"txid": tx_old, "amount": a, "last4": None}
        th["expected"] = {"outcome": "FLAG", "txids": []}
    for _ in range(3):  # messy: transaction id typed with spaces
        a = amt(); th = new_thread("messy_spaced_id", a); tx = txid()
        pay(a, th["order"]["created"] + 13, tx)
        spaced = f"{tx[:2]} {tx[2:6]} {tx[6:]}".lower()
        th["message"] = f"bhai pathaisi {a} taka, trx id {spaced}"
        th["expected_claim"] = {"txid": tx, "amount": a, "last4": None}
        th["expected"] = {"outcome": "MATCH", "txids": [tx]}
    for _ in range(2):  # messy: "4 hazar" style amount + last4 as digits
        h = rnd.choice(range(2, 9)); a = h * 1000; th = new_thread("messy_hazar", a); lf = l4(); tx = txid()
        pay(a, th["order"]["created"] + 18, tx, lf)
        th["message"] = f"{h} hazar taka pathaisi, number ending {lf}"
        th["expected_claim"] = {"txid": None, "amount": a, "last4": lf}
        th["expected"] = {"outcome": "MATCH", "txids": [tx]}
    return threads, feed

# ===== llm.py =====
"""Muse call for the messy cases, following the dashboard's own sample (POST /v1/responses).
No extra libraries needed. Set one environment variable: MODEL_API_KEY (never put the key in code)."""
import json, os, urllib.request, urllib.error

URL = os.environ.get("META_URL", "https://api.meta.ai/v1/responses")
MODEL = os.environ.get("META_MODEL", "muse-wonder-1.0")

PROMPT = ("Extract a payment CLAIM from this customer message. Reply with JSON only, no other text: "
          '{"txid": string|null, "amount": integer|null, "last4": string|null}. '
          "txid has no spaces and is uppercase. amount is in taka as an integer (4k or '4 hazar' = 4000). "
          "Never guess: use null for anything not stated.\n\nMessage: ")


def _post(text):
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        raise RuntimeError("MODEL_API_KEY is not set in this terminal window")
    payload = {"model": MODEL,
               "input": [{"role": "user", "content": PROMPT + text}],
               "stream": False, "max_output_tokens": 3000,
               "reasoning": {"effort": "low"}}
    req = urllib.request.Request(URL, data=json.dumps(payload).encode("utf-8"), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "Accept": "application/json", "User-Agent": "proof-hackathon/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:600]}")


def _text_of(resp):
    if isinstance(resp.get("output_text"), str) and resp["output_text"]:
        return resp["output_text"]
    parts = []
    for item in resp.get("output", []) or []:
        for c in item.get("content", []) or []:
            if isinstance(c, dict) and c.get("type") in ("output_text", "text") and c.get("text"):
                parts.append(c["text"])
    return "".join(parts)


def llm_extract_claim(text):
    resp = _post(text)
    out = _text_of(resp)
    if not out:
        raise RuntimeError("Could not find text in the reply. Raw reply starts: " + json.dumps(resp)[:800])
    d = json.loads(out[out.index("{"): out.rindex("}") + 1])
    tx = d.get("txid")
    return {"txid": tx.replace(" ", "").upper() if tx else None,
            "amount": int(d["amount"]) if d.get("amount") is not None else None,
            "last4": str(d["last4"]) if d.get("last4") else None}

# ===== benchmark.py =====
"""Run:  python benchmark.py          (rules only, no API needed)
        python benchmark.py --llm    (rules first, Muse only when rules find nothing useful)"""
import json, sys
from collections import defaultdict

USE_LLM = "--llm" in sys.argv
SAFE_OUTCOMES = {"MATCH", "MATCH_MULTI"}


def weak(c):  # rules found nothing we can match on
    return not c["txid"] and not (c["amount"] and c["last4"])


def run():
    threads, feed = build_dataset()
    allocated, rows = {}, []
    llm_calls = 0
    for t in threads:
        claim = extract_claim(t["message"])
        used_llm = False
        if USE_LLM and weak(claim):
            claim, used_llm = llm_extract_claim(t["message"]), True
            llm_calls += 1
        d = decide(claim, t["order"], t["open_orders"], feed, allocated)
        if d["outcome"] in SAFE_OUTCOMES:
            for tx in d["txids"]:
                allocated[tx] = t["order"]["id"]
        exp = t["expected"]
        ok = d["outcome"] == exp["outcome"] and d["txids"] == exp["txids"]
        false_confirm = d["outcome"] in SAFE_OUTCOMES and not ok
        rows.append({"id": t["id"], "category": t["category"], "message": t["message"], "claim": claim,
                     "expected_claim": t["expected_claim"], "claim_ok": claim == t["expected_claim"],
                     "expected": exp["outcome"], "got": d["outcome"], "ok": ok,
                     "false_confirm": false_confirm, "used_llm": used_llm, "reason": d["reason"]})
    return rows, llm_calls


if __name__ == "__main__":
    if "--ping" in sys.argv:
        print(llm_extract_claim("bhai pathaisi 5500 taka, trx id bk 2611 491g"))
        sys.exit()
    rows, llm_calls = run()
    n = len(rows)
    pct = lambda x: f"{100 * x / n:.1f}%"
    print(f"\nMode: {'rules + Muse fallback' if USE_LLM else 'rules only'}   threads: {n}")
    print(f"Claim extraction correct : {sum(r['claim_ok'] for r in rows)}/{n} ({pct(sum(r['claim_ok'] for r in rows))})")
    print(f"Decision correct         : {sum(r['ok'] for r in rows)}/{n} ({pct(sum(r['ok'] for r in rows))})")
    print(f"WRONG auto-confirms      : {sum(r['false_confirm'] for r in rows)}   <- the dangerous error")
    print(f"Model calls              : {llm_calls}/{n}\n")
    cats = defaultdict(list)
    for r in rows:
        cats[r["category"]].append(r)
    print(f"{'category':<18}{'correct':>9}")
    for c, rs in cats.items():
        print(f"{c:<18}{sum(r['ok'] for r in rs):>5}/{len(rs)}")
    fails = [r for r in rows if not r["ok"]]
    if fails:
        print("\nFailures:")
        for r in fails:
            print(f"  {r['id']} [{r['category']}] expected {r['expected']} got {r['got']} - {r['reason']}\n      msg: {r['message']}")
    json.dump(rows, open("results.json", "w"), indent=2, ensure_ascii=False)
    print("\nFull per-thread results saved to results.json")

