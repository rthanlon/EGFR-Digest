"""
EGFR Exon 20 Insertion - Daily Open-Access Research Digest
Searches PubMed/Europe PMC, bioRxiv, medRxiv, and Semantic Scholar.
Filters for open-access only. Sends email digest via Resend API.
"""

import os
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

# ── Configuration ────────────────────────────────────────────────────────────
SEARCH_TERM = "EGFR exon 20 insertion"
DAYS_BACK = 7
TO_EMAIL = "robertthanlon@gmail.com"
FROM_EMAIL = "digest@resend.dev"   # Resend's free sending address (no domain setup needed)
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")

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

# ── Source 1: Europe PMC ─────────────────────────────────────────────────────
def search_europe_pmc():
    print("Searching Europe PMC...")
    results = []
    query = urllib.parse.quote(f'"{SEARCH_TERM}" OPEN_ACCESS:Y')
    cutoff = date_cutoff()
    url = (
        f"https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        f"?query={query}"
        f"&resultType=core&pageSize=25&format=json"
        f"&fromDate={cutoff}"
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
            "authors": _format_authors_epmc(item.get("authorList", {}).get("author", [])),
            "journal": item.get("journalTitle", ""),
            "date": item.get("firstPublicationDate", ""),
            "link": link,
            "source": "Europe PMC",
            "abstract": item.get("abstractText", "")[:300] if item.get("abstractText") else "",
        })
    print(f"  Found {len(results)} open-access results from Europe PMC.")
    return results

def _format_authors_epmc(authors):
    names = [a.get("fullName", "") for a in authors[:3] if a.get("fullName")]
    return ", ".join(names) + (" et al." if len(authors) > 3 else "")

# ── Source 2: PubMed ─────────────────────────────────────────────────────────
def search_pubmed():
    print("Searching PubMed...")
    results = []
    cutoff = (datetime.utcnow() - timedelta(days=DAYS_BACK)).strftime("%Y/%m/%d")
    query = urllib.parse.quote(f'"{SEARCH_TERM}"[Title/Abstract] AND free full text[filter]')
    search_url = (
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        f"?db=pubmed&term={query}&mindate={cutoff}&datetype=pdat"
        f"&retmax=25&retmode=json"
    )
    data = fetch_json(search_url)
    if not data:
        return results
    ids = data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        print("  No PubMed results.")
        return results

    time.sleep(0.5)
    fetch_url = (
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        f"?db=pubmed&id={','.join(ids)}&retmode=json"
    )
    summary = fetch_json(fetch_url)
    if not summary:
        return results

    for pmid in ids:
        item = summary.get("result", {}).get(pmid, {})
        if not item:
            continue
        doi = next((id_["value"] for id_ in item.get("articleids", []) if id_["idtype"] == "doi"), None)
        link = f"https://www.ncbi.nlm.nih.gov/pmc/articles/pmid/{pmid}/" if not doi else f"https://doi.org/{doi}"
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
    print(f"  Found {len(results)} open-access results from PubMed.")
    return results

# ── Source 3 & 4: bioRxiv / medRxiv ─────────────────────────────────────────
def search_biorxiv():
    print("Searching bioRxiv...")
    return _search_rxiv("biorxiv")

def search_medrxiv():
    print("Searching medRxiv...")
    return _search_rxiv("medrxiv")

def _search_rxiv(server):
    results = []
    end_date = datetime.utcnow().strftime("%Y-%m-%d")
    start_date = date_cutoff()
    url = f"https://api.biorxiv.org/details/{server}/{start_date}/{end_date}/0/json"
    data = fetch_json(url)
    if not data:
        return results
    term_lower = SEARCH_TERM.lower()
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
            "abstract": item.get("abstract", "")[:300],
        })
    print(f"  Found {len(results)} results from {server}.")
    return results

# ── Source 5: Semantic Scholar ───────────────────────────────────────────────
def search_semantic_scholar():
    print("Searching Semantic Scholar...")
    results = []
    query = urllib.parse.quote(SEARCH_TERM)
    cutoff_year = (datetime.utcnow() - timedelta(days=DAYS_BACK)).year
    url = (
        f"https://api.semanticscholar.org/graph/v1/paper/search"
        f"?query={query}&fields=title,authors,year,publicationDate,journal,"
        f"openAccessPdf,externalIds&limit=25"
    )
    headers = {"x-api-key": SEMANTIC_SCHOLAR_API_KEY} if SEMANTIC_SCHOLAR_API_KEY else {}
    data = fetch_json(url, headers=headers)
    if not data:
        return results
    for item in data.get("data", []):
        pdf_info = item.get("openAccessPdf")
        if not pdf_info or not pdf_info.get("url"):
            continue
        pub_date = item.get("publicationDate", "") or str(item.get("year", ""))
        if pub_date and pub_date[:4].isdigit():
            if int(pub_date[:4]) < cutoff_year:
                continue
        authors = item.get("authors", [])
        author_str = ", ".join(a["name"] for a in authors[:3])
        if len(authors) > 3:
            author_str += " et al."
        journal = ""
        if item.get("journal"):
            journal = item["journal"].get("name", "")
        results.append({
            "title": item.get("title", "Untitled"),
            "authors": author_str,
            "journal": journal,
            "date": pub_date,
            "link": pdf_info["url"],
            "source": "Semantic Scholar",
            "abstract": "",
        })
    print(f"  Found {len(results)} open-access results from Semantic Scholar.")
    return results

# ── Deduplication ────────────────────────────────────────────────────────────
def deduplicate(articles):
    seen_titles = set()
    unique = []
    for a in articles:
        key = a["title"].lower().strip()[:80]
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append(a)
    return unique

# ── Email Builder ────────────────────────────────────────────────────────────
def build_email_html(articles, today_str):
    if not articles:
        body_content = """
        <div class="no-results">
            <p>No new open-access articles found in the last 7 days matching
            <strong>"EGFR exon 20 insertion"</strong>.</p>
            <p>The search will run again tomorrow.</p>
        </div>
        """
    else:
        cards = ""
        for a in articles:
            abstract_html = f'<p class="abstract">{a["abstract"]}{"..." if len(a["abstract"]) == 300 else ""}</p>' if a["abstract"] else ""
            cards += f"""
            <div class="card">
                <div class="source-tag">{a['source']}</div>
                <h2><a href="{a['link']}">{a['title']}</a></h2>
                <p class="meta">{a['authors']}</p>
                <p class="meta journal">{a['journal']} &nbsp;·&nbsp; {a['date']}</p>
                {abstract_html}
                <a href="{a['link']}" class="read-link">Read full article →</a>
            </div>
            """
        body_content = cards

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: Georgia, serif; background: #f5f5f0; margin: 0; padding: 20px; color: #222; }}
  .wrapper {{ max-width: 680px; margin: 0 auto; }}
  .header {{ background: #1a3a5c; color: white; padding: 28px 32px; border-radius: 8px 8px 0 0; }}
  .header h1 {{ margin: 0 0 4px 0; font-size: 22px; font-weight: normal; letter-spacing: 0.5px; }}
  .header p {{ margin: 0; font-size: 13px; opacity: 0.75; }}
  .body {{ background: white; padding: 24px 32px; border-radius: 0 0 8px 8px; }}
  .summary {{ font-size: 14px; color: #555; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid #eee; }}
  .card {{ margin-bottom: 28px; padding-bottom: 28px; border-bottom: 1px solid #eee; }}
  .card:last-child {{ border-bottom: none; margin-bottom: 0; padding-bottom: 0; }}
  .source-tag {{ display: inline-block; background: #e8f0f8; color: #1a3a5c; font-size: 11px;
                 font-family: monospace; padding: 2px 8px; border-radius: 3px; margin-bottom: 8px; }}
  .card h2 {{ margin: 0 0 8px 0; font-size: 16px; line-height: 1.4; }}
  .card h2 a {{ color: #1a3a5c; text-decoration: none; }}
  .card h2 a:hover {{ text-decoration: underline; }}
  .meta {{ margin: 0 0 4px 0; font-size: 13px; color: #666; }}
  .journal {{ font-style: italic; }}
  .abstract {{ font-size: 13px; color: #444; line-height: 1.6; margin: 10px 0; }}
  .read-link {{ font-size: 13px; color: #c0392b; text-decoration: none; font-weight: bold; }}
  .no-results {{ color: #555; font-size: 15px; padding: 20px 0; }}
  .footer {{ font-size: 11px; color: #aaa; text-align: center; margin-top: 20px; }}
</style>
</head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>EGFR Exon 20 Insertion — Research Digest</h1>
    <p>{today_str} &nbsp;·&nbsp; Open-access articles from the past 7 days</p>
  </div>
  <div class="body">
    <p class="summary">
      Found <strong>{len(articles)} open-access article{"s" if len(articles) != 1 else ""}</strong>
      across PubMed, Europe PMC, bioRxiv, medRxiv, and Semantic Scholar.
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
    return html

# ── Send Email via Resend ─────────────────────────────────────────────────────
def send_email(html_body, article_count, today_str):
    subject = f"EGFR Exon 20 Research Digest — {today_str} ({article_count} new articles)"
    payload = json.dumps({
        "from": FROM_EMAIL,
        "to": [TO_EMAIL],
        "subject": subject,
        "html": html_body,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    print(f"Sending email to {TO_EMAIL} via Resend...")
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode())
        print(f"Email sent successfully. ID: {result.get('id', 'unknown')}")

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    today_str = datetime.utcnow().strftime("%B %d, %Y")
    print(f"\n=== EGFR Exon 20 Daily Digest — {today_str} ===\n")

    all_articles = []
    all_articles += search_europe_pmc()
    all_articles += search_pubmed()
    all_articles += search_biorxiv()
    all_articles += search_medrxiv()
    all_articles += search_semantic_scholar()

    unique_articles = deduplicate(all_articles)
    unique_articles.sort(key=lambda x: x.get("date", ""), reverse=True)

    print(f"\nTotal unique open-access articles: {len(unique_articles)}")

    html = build_email_html(unique_articles, today_str)
    send_email(html, len(unique_articles), today_str)

if __name__ == "__main__":
    main()
