"""
Daily Open-Access Research Digest
Runs two separate searches each morning:
  1. EGFR exon 20 insertion
  2. HER2 exon 20 / ERBB2 exon 20 / HER2 amp
For each search: finds new open-access articles, asks Claude to pick the
best one and draft an X post, flags demoralizing content and clinical
endpoints (PFS, OS, ORR, etc.), and sends a formatted email digest.
"""

import os
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

# ── Configuration ────────────────────────────────────────────────────────────
DAYS_BACK        = 7
TO_EMAIL         = "robertthanlon@gmail.com"
FROM_EMAIL       = "digest@resend.dev"
RESEND_API_KEY   = os.environ["RESEND_API_KEY"]
ANTHROPIC_API_KEY       = os.environ.get("ANTHROPIC_API_KEY", "")
SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")

# Each search is defined as a dict:
#   label        – short name used in email subject and headers
#   terms        – list of search phrases (OR'd together)
#   seen_file    – separate memory file so searches don't interfere
#   header_color – hex color for the email header bar
SEARCHES = [
    {
        "label": "EGFR Exon 20 Insertion",
        "terms": ["EGFR exon 20 insertion"],
        "seen_file": "seen_egfr.json",
        "header_color": "#1a3a5c",
    },
    {
        "label": "HER2 / ERBB2 Exon 20 & Amplification",
        "terms": ["HER2 exon 20", "ERBB2 exon 20", "HER2 amp"],
        "seen_file": "seen_her2.json",
        "header_color": "#2d6a4f",
    },
]

# ── Seen Articles Tracker ─────────────────────────────────────────────────────
def load_seen(seen_file):
    if os.path.exists(seen_file):
        with open(seen_file, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen, seen_file):
    seen_list = list(seen)[-2000:]
    with open(seen_file, "w") as f:
        json.dump(seen_list, f)

def make_key(article):
    return article["title"].lower().strip()[:80]

# ── Helpers ──────────────────────────────────────────────────────────────────
def fetch_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  Warning: fetch failed for {url[:80]}... → {e}")
        return None

def date_cutoff():
    return (datetime.utcnow() - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")

# ── Source searches (each accepts a single term string) ──────────────────────
def search_europe_pmc(term):
    results = []
    query = urllib.parse.quote(f'"{term}" OPEN_ACCESS:Y')
    url = (
        f"https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        f"?query={query}&resultType=core&pageSize=25&format=json"
        f"&fromDate={date_cutoff()}"
    )
    data = fetch_json(url)
    if not data:
        return results
    for item in data.get("resultList", {}).get("result", []):
        if item.get("isOpenAccess") != "Y":
            continue
        pdf_url = None
        for link in item.get("fullTextUrlList", {}).get("fullTextUrl", []):
            if link.get("documentStyle") in ("pdf", "html") and link.get("availability") == "Open access":
                pdf_url = link.get("url")
                break
        doi = item.get("doi")
        link = pdf_url or (f"https://doi.org/{doi}" if doi else None)
        if not link:
            continue
        results.append({
            "title": item.get("title", "Untitled").rstrip("."),
            "authors": _fmt_authors_epmc(item.get("authorList", {}).get("author", [])),
            "journal": item.get("journalTitle", ""),
            "date": item.get("firstPublicationDate", ""),
            "link": link,
            "source": "Europe PMC",
            "abstract": item.get("abstractText", "")[:600] if item.get("abstractText") else "",
        })
    return results

def _fmt_authors_epmc(authors):
    names = [a.get("fullName", "") for a in authors[:3] if a.get("fullName")]
    return ", ".join(names) + (" et al." if len(authors) > 3 else "")

def search_pubmed(term):
    results = []
    cutoff = (datetime.utcnow() - timedelta(days=DAYS_BACK)).strftime("%Y/%m/%d")
    query = urllib.parse.quote(f'"{term}"[Title/Abstract] AND free full text[filter]')
    data = fetch_json(
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        f"?db=pubmed&term={query}&mindate={cutoff}&datetype=pdat&retmax=25&retmode=json"
    )
    if not data:
        return results
    ids = data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return results
    time.sleep(0.5)
    summary = fetch_json(
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        f"?db=pubmed&id={','.join(ids)}&retmode=json"
    )
    if not summary:
        return results
    for pmid in ids:
        item = summary.get("result", {}).get(pmid, {})
        if not item:
            continue
        doi = next((x["value"] for x in item.get("articleids", []) if x["idtype"] == "doi"), None)
        link = f"https://doi.org/{doi}" if doi else f"https://www.ncbi.nlm.nih.gov/pmc/articles/pmid/{pmid}/"
        authors_raw = item.get("authors", [])
        authors = ", ".join(a["name"] for a in authors_raw[:3])
        if len(authors_raw) > 3:
            authors += " et al."
        results.append({
            "title": item.get("title", "Untitled").rstrip("."),
            "authors": authors,
            "journal": item.get("fulljournalname", item.get("source", "")),
            "date": item.get("pubdate", ""),
            "link": link,
            "source": "PubMed",
            "abstract": "",
        })
    return results

def _search_rxiv(server, term):
    results = []
    end_date = datetime.utcnow().strftime("%Y-%m-%d")
    data = fetch_json(f"https://api.biorxiv.org/details/{server}/{date_cutoff()}/{end_date}/0/json")
    if not data:
        return results
    term_lower = term.lower()
    for item in data.get("collection", []):
        text = (item.get("title", "") + " " + item.get("abstract", "")).lower()
        if term_lower not in text:
            continue
        doi = item.get("doi", "")
        results.append({
            "title": item.get("title", "Untitled"),
            "authors": item.get("authors", ""),
            "journal": f"{server} preprint",
            "date": item.get("date", ""),
            "link": f"https://doi.org/{doi}" if doi else "",
            "source": server.capitalize(),
            "abstract": item.get("abstract", "")[:600],
        })
    return results

def search_semantic_scholar(term):
    results = []
    cutoff_year = (datetime.utcnow() - timedelta(days=DAYS_BACK)).year
    query = urllib.parse.quote(term)
    headers = {"x-api-key": SEMANTIC_SCHOLAR_API_KEY} if SEMANTIC_SCHOLAR_API_KEY else {}
    data = fetch_json(
        f"https://api.semanticscholar.org/graph/v1/paper/search"
        f"?query={query}&fields=title,authors,year,publicationDate,journal,openAccessPdf,externalIds&limit=25",
        headers=headers,
    )
    if not data:
        return results
    for item in data.get("data", []):
        pdf_info = item.get("openAccessPdf")
        if not pdf_info or not pdf_info.get("url"):
            continue
        pub_date = item.get("publicationDate", "") or str(item.get("year", ""))
        if pub_date and pub_date[:4].isdigit() and int(pub_date[:4]) < cutoff_year:
            continue
        authors = item.get("authors", [])
        author_str = ", ".join(a["name"] for a in authors[:3])
        if len(authors) > 3:
            author_str += " et al."
        results.append({
            "title": item.get("title", "Untitled"),
            "authors": author_str,
            "journal": item.get("journal", {}).get("name", "") if item.get("journal") else "",
            "date": pub_date,
            "link": pdf_info["url"],
            "source": "Semantic Scholar",
            "abstract": "",
        })
    return results

# ── Run all sources for a list of terms (OR logic) ───────────────────────────
def run_all_sources(terms):
    """Search all five sources for each term, combine and deduplicate results."""
    all_results = []
    for term in terms:
        print(f"  Searching for: '{term}'")
        all_results += search_europe_pmc(term)
        all_results += search_pubmed(term)
        all_results += _search_rxiv("biorxiv", term)
        all_results += _search_rxiv("medrxiv", term)
        all_results += search_semantic_scholar(term)
    return deduplicate(all_results)

def deduplicate(articles):
    seen_titles = set()
    unique = []
    for a in articles:
        key = make_key(a)
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append(a)
    return unique

# ── Claude AI: Pick Best Article, Draft X Post, Flag Issues ──────────────────
def analyze_articles(articles, search_label):
    """Ask Claude to pick best article, draft X post, flag sensitive/endpoint articles."""
    if not ANTHROPIC_API_KEY or not articles:
        return None, None

    print(f"  Asking Claude to analyze {len(articles)} articles...")

    article_list = ""
    for i, a in enumerate(articles[:20]):
        article_list += f"""
Article {i+1}:
  Title: {a['title']}
  Journal: {a['journal']}
  Authors: {a['authors']}
  Date: {a['date']}
  Abstract: {a['abstract'] or '(no abstract available)'}
  Link: {a['link']}
"""

    prompt = f"""You are helping the Exon 20 Group, a patient advocacy organization focused on thoracic oncology mutations including EGFR exon 20 insertion and HER2/ERBB2 alterations in lung cancer.

Today's search: {search_label}

Here are today's new open-access research articles:

{article_list}

Your tasks:

1. Identify the single most clinically relevant article for patients, caregivers, and advocates. Prioritize: clinical trials > new drug data > survival/response outcomes > review articles > basic science.

2. Write a draft X (Twitter) post about that article for the @Exon20IRC account:
   - Under 240 characters (link added separately)
   - Plain English, understandable to patients and families
   - Lead with the most important finding
   - End with #EGFRExon20 #LungCancer (for EGFR searches) or #HER2LungCancer #LungCancer (for HER2 searches)
   - Factually accurate — do not overstate findings

3. Write one sentence explaining why you chose this article.

4. For EVERY article, assess two things independently:

   a) DEMORALIZING FLAG: Flag if it contains poor survival outcomes, very low response rates, findings suggesting very limited treatment options, high toxicity with poor benefit, or conclusions likely to cause hopelessness. Err on the side of flagging.

   b) CLINICAL ENDPOINTS FLAG: Flag if the article reports or discusses any clinical endpoints including but not limited to: PFS (progression-free survival), OS (overall survival), ORR (objective response rate), DOR (duration of response), TTR (time to response), EFS (event-free survival), RFS (relapse-free survival), DCR (disease control rate), CBR (clinical benefit rate), TTP (time to progression), or any other efficacy or survival metric. List which specific endpoints are mentioned.

Respond in this exact JSON format with no other text:
{{
  "chosen_article_index": <number, 1-based>,
  "x_post": "<draft post text, no link>",
  "reasoning": "<one sentence>",
  "sensitive_articles": [
    {{
      "article_index": <number, 1-based>,
      "reason": "<one plain-English sentence why this might be difficult for patients>"
    }}
  ],
  "endpoint_articles": [
    {{
      "article_index": <number, 1-based>,
      "endpoints": "<comma-separated list of endpoints mentioned, e.g. PFS, OS, ORR>"
    }}
  ]
}}"""

    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            text = data["content"][0]["text"].strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            result = json.loads(text.strip())

            idx = result["chosen_article_index"] - 1
            chosen = articles[idx] if 0 <= idx < len(articles) else articles[0]
            print(f"  Claude chose: {chosen['title'][:60]}...")

            # Build sensitive_keys: article_key -> reason
            sensitive_keys = {}
            for s in result.get("sensitive_articles", []):
                sidx = s.get("article_index", 0) - 1
                if 0 <= sidx < len(articles):
                    sensitive_keys[make_key(articles[sidx])] = s.get("reason", "")

            # Build endpoint_keys: article_key -> endpoints string
            endpoint_keys = {}
            for e in result.get("endpoint_articles", []):
                eidx = e.get("article_index", 0) - 1
                if 0 <= eidx < len(articles):
                    endpoint_keys[make_key(articles[eidx])] = e.get("endpoints", "")

            if sensitive_keys:
                print(f"  Flagged {len(sensitive_keys)} potentially sensitive articles.")
            if endpoint_keys:
                print(f"  Flagged {len(endpoint_keys)} articles with clinical endpoints.")

            result["sensitive_keys"] = sensitive_keys
            result["endpoint_keys"] = endpoint_keys
            return chosen, result

    except Exception as e:
        print(f"  Warning: Claude API call failed → {e}")
        return None, None

# ── Email Builder ─────────────────────────────────────────────────────────────
def build_email_html(articles, today_str, search_config,
                     chosen_article=None, ai_result=None,
                     sensitive_keys=None, endpoint_keys=None):

    sensitive_keys = sensitive_keys or {}
    endpoint_keys  = endpoint_keys  or {}
    header_color   = search_config["header_color"]
    label          = search_config["label"]

    # ── X Post Draft Box ──
    if chosen_article and ai_result:
        post_text  = ai_result.get("x_post", "")
        reasoning  = ai_result.get("reasoning", "")
        full_post  = f"{post_text} {chosen_article['link']}"
        x_post_box = f"""
        <div class="x-box">
            <div class="x-label">📣 Suggested X Post for @Exon20IRC — Review &amp; Post When Ready</div>
            <div class="x-post">{full_post}</div>
            <div class="x-char-count">{len(full_post)} characters</div>
            <div class="x-reasoning"><strong>Why this article:</strong> {reasoning}</div>
            <a href="https://twitter.com/intent/tweet?text={urllib.parse.quote(full_post)}"
               class="x-button">Open in X →</a>
        </div>"""
    else:
        x_post_box = ""

    # ── Article Cards ──
    if not articles:
        body_content = f"""
        <div class="no-results">
            <p>No new open-access articles found in the past 7 days for
            <strong>{label}</strong> that haven't already been sent.</p>
            <p>The search will run again tomorrow.</p>
        </div>"""
    else:
        cards = ""
        for a in articles:
            abstract_html = (
                f'<p class="abstract">{a["abstract"]}{"..." if len(a["abstract"]) == 600 else ""}</p>'
                if a["abstract"] else ""
            )
            akey = make_key(a)
            is_chosen = chosen_article and akey == make_key(chosen_article)
            highlight = ' style="border-left: 4px solid ' + header_color + '; padding-left: 16px;"' if is_chosen else ""

            # Demoralizing flag (yellow)
            sensitive_html = (
                f'<div class="flag flag-sensitive">⚠️ <strong>Heads up:</strong> {sensitive_keys[akey]}</div>'
                if akey in sensitive_keys else ""
            )
            # Clinical endpoints flag (teal)
            endpoint_html = (
                f'<div class="flag flag-endpoint">📊 <strong>Clinical endpoints reported:</strong> {endpoint_keys[akey]}</div>'
                if akey in endpoint_keys else ""
            )

            cards += f"""
            <div class="card"{highlight}>
                <div class="source-tag">{a['source']}</div>
                <h2><a href="{a['link']}">{a['title']}</a></h2>
                <p class="meta">{a['authors']}</p>
                <p class="meta journal">{a['journal']} &nbsp;·&nbsp; {a['date']}</p>
                {abstract_html}
                {sensitive_html}
                {endpoint_html}
                <a href="{a['link']}" class="read-link">Read full article →</a>
            </div>"""
        body_content = cards

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<style>
  body {{ font-family: Georgia, serif; background: #f5f5f0; margin: 0; padding: 20px; color: #222; }}
  .wrapper {{ max-width: 680px; margin: 0 auto; }}
  .header {{ background: {header_color}; color: white; padding: 28px 32px; border-radius: 8px 8px 0 0; }}
  .header h1 {{ margin: 0 0 4px 0; font-size: 22px; font-weight: normal; letter-spacing: 0.5px; }}
  .header p {{ margin: 0; font-size: 13px; opacity: 0.75; }}
  .body {{ background: white; padding: 24px 32px; border-radius: 0 0 8px 8px; }}
  .summary {{ font-size: 14px; color: #555; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid #eee; }}
  .x-box {{ background: #f0f7ff; border: 2px solid {header_color}; border-radius: 8px; padding: 20px 24px; margin-bottom: 32px; }}
  .x-label {{ font-size: 12px; font-weight: bold; color: {header_color}; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }}
  .x-post {{ font-size: 16px; line-height: 1.5; color: #111; background: white; border-radius: 6px;
             padding: 14px 16px; margin-bottom: 8px; border: 1px solid #ccd9e8; white-space: pre-wrap; word-break: break-word; }}
  .x-char-count {{ font-size: 12px; color: #888; margin-bottom: 10px; }}
  .x-reasoning {{ font-size: 13px; color: #555; font-style: italic; margin-bottom: 14px; }}
  .x-button {{ display: inline-block; background: {header_color}; color: white; padding: 8px 18px;
               border-radius: 20px; text-decoration: none; font-size: 13px; font-weight: bold; }}
  .card {{ margin-bottom: 28px; padding-bottom: 28px; border-bottom: 1px solid #eee; }}
  .card:last-child {{ border-bottom: none; margin-bottom: 0; padding-bottom: 0; }}
  .source-tag {{ display: inline-block; background: #e8f0f8; color: {header_color}; font-size: 11px;
                 font-family: monospace; padding: 2px 8px; border-radius: 3px; margin-bottom: 8px; }}
  .card h2 {{ margin: 0 0 8px 0; font-size: 16px; line-height: 1.4; }}
  .card h2 a {{ color: {header_color}; text-decoration: none; }}
  .meta {{ margin: 0 0 4px 0; font-size: 13px; color: #666; }}
  .journal {{ font-style: italic; }}
  .abstract {{ font-size: 13px; color: #444; line-height: 1.6; margin: 10px 0; }}
  .flag {{ border-radius: 4px; font-size: 13px; padding: 10px 14px; margin: 8px 0; }}
  .flag-sensitive {{ background: #fff8e1; border-left: 4px solid #f5a623; color: #7a5200; }}
  .flag-endpoint {{ background: #e8f5f0; border-left: 4px solid #2d6a4f; color: #1a4030; }}
  .read-link {{ font-size: 13px; color: #c0392b; text-decoration: none; font-weight: bold; }}
  .no-results {{ color: #555; font-size: 15px; padding: 20px 0; }}
  .footer {{ font-size: 11px; color: #aaa; text-align: center; margin-top: 20px; }}
</style>
</head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>{label} — Research Digest</h1>
    <p>{today_str} &nbsp;·&nbsp; New open-access articles only</p>
  </div>
  <div class="body">
    {x_post_box}
    <p class="summary">
      <strong>{len(articles)} new open-access article{"s" if len(articles) != 1 else ""}</strong>
      found today across PubMed, Europe PMC, bioRxiv, medRxiv, and Semantic Scholar.
      {f'The highlighted article is suggested for X.' if chosen_article and articles else ''}
      {'&nbsp; ⚠️ = may be difficult for patients &nbsp; 📊 = reports clinical endpoints' if articles else ''}
    </p>
    {body_content}
  </div>
  <div class="footer">
    Sources: PubMed · Europe PMC · bioRxiv · medRxiv · Semantic Scholar<br>
    Sent automatically to robertthanlon@gmail.com · Exon 20 Group Research Monitor
  </div>
</div>
</body>
</html>"""

# ── Send Email via Resend ─────────────────────────────────────────────────────
def send_email(html_body, subject):
    import resend
    resend.api_key = RESEND_API_KEY
    print(f"  Sending: {subject}")
    r = resend.Emails.send({
        "from": FROM_EMAIL,
        "to": [TO_EMAIL],
        "subject": subject,
        "html": html_body,
    })
    print(f"  Sent. ID: {r.get('id', 'unknown')}")

# ── Run One Search ────────────────────────────────────────────────────────────
def run_search(search_config, today_str):
    label     = search_config["label"]
    terms     = search_config["terms"]
    seen_file = search_config["seen_file"]

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    seen = load_seen(seen_file)
    print(f"  Previously seen: {len(seen)} articles")

    print(f"  Searching all sources...")
    all_articles = run_all_sources(terms)
    all_articles.sort(key=lambda x: x.get("date", ""), reverse=True)
    print(f"  Total found: {len(all_articles)}")

    new_articles = [a for a in new_articles if make_key(a) not in seen]
    print(f"  Genuinely new: {len(new_articles)}")

    chosen_article, ai_result = analyze_articles(new_articles, label)
    sensitive_keys = ai_result.get("sensitive_keys", {}) if ai_result else {}
    endpoint_keys  = ai_result.get("endpoint_keys",  {}) if ai_result else {}

    html = build_email_html(
        new_articles, today_str, search_config,
        chosen_article, ai_result, sensitive_keys, endpoint_keys
    )

    subject = f"{label} Digest — {today_str} ({len(new_articles)} new articles)"
    send_email(html, subject)

    for a in new_articles:
        seen.add(make_key(a))
    save_seen(seen, seen_file)
    print(f"  Memory updated: {len(seen)} total articles tracked.")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    today_str = datetime.utcnow().strftime("%B %d, %Y")
    print(f"\n=== Daily Research Digest — {today_str} ===")
    for search_config in SEARCHES:
        run_search(search_config, today_str)
    print(f"\n=== All searches complete ===\n")

if __name__ == "__main__":
    main()
