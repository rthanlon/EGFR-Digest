"""
Daily Open-Access Research Digest
Runs two separate searches each morning:
  1. EGFR exon 20 insertion
  2. HER2 exon 20 / ERBB2 exon 20 / HER2 amp
For each search: finds ALL recent articles (open-access and paywalled),
shows open-access above the line and paywalled below,
asks Claude to pick the best open-access article and draft posts,
flags demoralizing content and clinical endpoints on all articles.
"""

import os
import json
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# ── Configuration ────────────────────────────────────────────────────────────
DAYS_BACK        = 5
TO_EMAIL         = "robertthanlon@gmail.com"
FROM_EMAIL       = "digest@resend.dev"
RESEND_API_KEY   = os.environ["RESEND_API_KEY"]
ANTHROPIC_API_KEY        = os.environ.get("ANTHROPIC_API_KEY", "")
SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")

SEARCHES = [
    {
        "label": "EGFR Exon 20 Insertion",
        "short_label": "EGFR",
        "terms": ["EGFR exon 20 insertion"],
        "seen_file": "seen_egfr.json",
        "header_color": "#1a3a5c",
    },
    {
        "label": "HER2 / ERBB2 Exon 20 & Amplification",
        "short_label": "HER2",
        "terms": ["HER2 exon 20", "ERBB2 exon 20", "HER2 amp"],
        "seen_file": "seen_her2.json",
        "header_color": "#2d6a4f",
    },
    {
        "label": "Oncology Breakthroughs — Exon 20 Opportunity Scan",
        "short_label": "OPP",
        "terms": [
            "antibody-drug conjugate lung cancer",
            "bispecific antibody NSCLC",
            "PROTAC lung cancer",
            "molecular glue cancer",
            "EGFR resistance mechanism",
            "HER2 resistance NSCLC",
            "kinase inhibitor resistance lung cancer",
            "novel targeted therapy NSCLC",
            "immune checkpoint TKI combination lung cancer",
            "EGFR structural biology",
            "CAR-T cell lung cancer",
            "tumor microenvironment NSCLC",
            "immunotherapy resistance lung cancer",
            "PD-1 PD-L1 EGFR lung cancer",
            "neoantigen lung cancer immunotherapy",
        ],
        "seen_file": "seen_opp.json",
        "header_color": "#6b3a00",
        "is_opportunity_scan": True,
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

def date_cutoff_str():
    return (datetime.utcnow() - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")

def date_cutoff_pubmed():
    return (datetime.utcnow() - timedelta(days=DAYS_BACK)).strftime("%Y/%m/%d")

def is_recent(date_str):
    if not date_str:
        return False
    try:
        if len(date_str) == 4:
            cutoff_year = (datetime.utcnow() - timedelta(days=DAYS_BACK)).year
            return int(date_str) >= cutoff_year
        pub_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
        cutoff = datetime.utcnow() - timedelta(days=DAYS_BACK)
        return pub_date >= cutoff
    except Exception:
        return True

def is_within_days(date_str, days):
    """Check if a date string falls within the last N days."""
    if not date_str:
        return True  # include if date unknown
    try:
        if len(date_str) == 4:
            cutoff_year = (datetime.utcnow() - timedelta(days=days)).year
            return int(date_str) >= cutoff_year
        pub_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
        cutoff = datetime.utcnow() - timedelta(days=days)
        return pub_date >= cutoff
    except Exception:
        return True  # include if we can't parse the date

# ── Source searches — return ALL articles with open_access flag ───────────────
def search_europe_pmc(term, days=30):
    results = []
    # Europe PMC: fetch large page sorted newest first, filter client-side.
    # API date params are unreliable; we use a 30-day client-side window
    # against firstPublicationDate to catch recent articles, with the
    # memory file as the final gate against resending old articles.
    query = urllib.parse.quote(f'"{term}"')
    url = (
        f"https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        f"?query={query}&resultType=core&pageSize=250&format=json"
        f"&sort=P_PDATE_D+desc"
    )
    data = fetch_json(url)
    if not data:
        return results
    raw_count = len(data.get("resultList", {}).get("result", []))
    print(f"    Europe PMC raw: {raw_count} total results returned")
    for item in data.get("resultList", {}).get("result", []):
        # Client-side date filter: keep articles published in last 30 days.
        # Wide window ensures we don't miss recently published articles.
        # Memory file prevents resending anything already sent.
        pub_date = item.get("firstPublicationDate", "") or item.get("firstIndexDate", "")
        if pub_date and not is_within_days(pub_date, days):
            continue
        doi = item.get("doi")
        pmcid = item.get("pmcid", "")
        # Determine if open access
        is_oa = item.get("isOpenAccess") == "Y"
        free_link = None
        for link in item.get("fullTextUrlList", {}).get("fullTextUrl", []):
            avail = (link.get("availability") or "").lower()
            if link.get("documentStyle") in ("pdf", "html") and ("open access" in avail or "free" in avail):
                free_link = link.get("url")
                break
        open_access = is_oa or bool(free_link)
        # Build best available link — for paywalled articles use DOI
        if free_link:
            article_link = free_link
        elif pmcid:
            article_link = f"https://europepmc.org/article/PMC/{pmcid}"
        elif doi:
            article_link = f"https://doi.org/{doi}"
        else:
            continue
        results.append({
            "title": item.get("title", "Untitled").rstrip("."),
            "authors": _fmt_authors_epmc(item.get("authorList", {}).get("author", [])),
            "journal": item.get("journalTitle", ""),
            "date": pub_date,
            "link": article_link,
            "source": "Europe PMC",
            "abstract": item.get("abstractText", "")[:600] if item.get("abstractText") else "",
            "open_access": open_access,
        })
    print(f"    Europe PMC: {len(results)} results ({sum(1 for a in results if a['open_access'])} open access)")
    return results

def _fmt_authors_epmc(authors):
    names = [a.get("fullName", "") for a in authors[:3] if a.get("fullName")]
    return ", ".join(names) + (" et al." if len(authors) > 3 else "")

def search_pubmed(term):
    """Run two PubMed queries: all articles + free-full-text subset to flag open access.
    Uses datetype=edat (entrez date = when PubMed indexed it) which is reliable for
    catching newly added articles. Memory file prevents resending duplicates."""
    results = []
    # PubMed: use 30-day window with datetype=edat (entrez date = when
    # PubMed indexed it). This is the most reliable PubMed date parameter.
    cutoff_30 = (datetime.utcnow() - timedelta(days=30)).strftime("%Y/%m/%d")
    today     = datetime.utcnow().strftime("%Y/%m/%d")

    # Query 1: ALL articles indexed by PubMed in last 30 days
    query_all = urllib.parse.quote(f'"{term}"[Title/Abstract]')
    data_all = fetch_json(
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        f"?db=pubmed&term={query_all}"
        f"&mindate={cutoff_30}&maxdate={today}&datetype=edat"
        f"&retmax=200&retmode=json&sort=date"
    )
    all_ids = set(data_all.get("esearchresult", {}).get("idlist", [])) if data_all else set()

    time.sleep(0.4)

    # Query 2: free full text in same window — to identify open access
    query_free = urllib.parse.quote(f'"{term}"[Title/Abstract] AND free full text[filter]')
    data_free = fetch_json(
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        f"?db=pubmed&term={query_free}"
        f"&mindate={cutoff_30}&maxdate={today}&datetype=edat"
        f"&retmax=200&retmode=json"
    )
    free_ids = set(data_free.get("esearchresult", {}).get("idlist", [])) if data_free else set()

    if not all_ids:
        print(f"    PubMed: 0 results")
        return results

    time.sleep(0.4)

    summary = fetch_json(
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        f"?db=pubmed&id={','.join(all_ids)}&retmode=json"
    )
    if not summary:
        return results

    for pmid in all_ids:
        item = summary.get("result", {}).get(pmid, {})
        if not item:
            continue
        doi = next((x["value"] for x in item.get("articleids", []) if x["idtype"] == "doi"), None)
        is_free = pmid in free_ids
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
            "open_access": is_free,
        })
    oa_count = sum(1 for a in results if a["open_access"])
    print(f"    PubMed: {len(results)} results ({oa_count} open access)")
    return results

def _search_rxiv(server, term):
    # All preprints are open access by definition
    results = []
    end_date = datetime.utcnow().strftime("%Y-%m-%d")
    start_date = date_cutoff_str()
    data = fetch_json(f"https://api.biorxiv.org/details/{server}/{start_date}/{end_date}/0/json")
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
            "open_access": True,
        })
    print(f"    {server}: {len(results)} results (all open access)")
    return results

def search_semantic_scholar(term):
    results = []
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
        pub_date = item.get("publicationDate", "") or str(item.get("year", ""))
        if not is_recent(pub_date):
            continue
        pdf_info = item.get("openAccessPdf")
        is_oa = bool(pdf_info and pdf_info.get("url"))
        doi = (item.get("externalIds") or {}).get("DOI", "")
        link = pdf_info["url"] if is_oa else (f"https://doi.org/{doi}" if doi else "")
        if not link:
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
            "link": link,
            "source": "Semantic Scholar",
            "abstract": "",
            "open_access": is_oa,
        })
    oa_count = sum(1 for a in results if a["open_access"])
    print(f"    Semantic Scholar: {len(results)} results ({oa_count} open access)")
    return results


# ── Run all sources for a list of terms (OR logic) ───────────────────────────
def run_all_sources(terms, epmc_days=30):
    all_results = []
    for term in terms:
        print(f"  Searching for: '{term}'")
        all_results += search_europe_pmc(term, days=epmc_days)
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

# ── Claude AI: Pick Best OA Article, Draft Posts, Flag All Articles ───────────
def analyze_articles(oa_articles, all_articles, search_label):
    """
    oa_articles  — open-access only (for suggested post selection)
    all_articles — everything (for flags)
    """
    if not ANTHROPIC_API_KEY or not all_articles:
        return None, None

    is_opp_scan = search_label == "Oncology Breakthroughs — Exon 20 Opportunity Scan"

    # For standard searches, skip Claude if there are no open-access articles
    if not is_opp_scan and not oa_articles:
        print(f"  No open-access articles — skipping Claude analysis.")
        return None, None

    print(f"  Asking Claude to analyze {len(all_articles)} articles ({len(oa_articles)} open access)...")

    # Build article list — OA articles first, then paywalled
    combined = oa_articles + [a for a in all_articles if not a.get("open_access")]
    oa_indices = set(range(1, len(oa_articles) + 1))  # 1-based indices of OA articles

    article_list = ""
    for i, a in enumerate(combined[:25]):
        oa_label = "[OPEN ACCESS]" if a.get("open_access") else "[PAYWALLED]"
        article_list += f"""
Article {i+1} {oa_label}:
  Title: {a['title']}
  Journal: {a['journal']}
  Authors: {a['authors']}
  Date: {a['date']}
  Abstract: {a['abstract'] or '(no abstract available)'}
  Link: {a['link']}
"""

    # Use different prompt for opportunity scan vs standard searches
    if is_opp_scan:
        prompt = f"""You are a scientific advisor to the Exon 20 Group, a patient advocacy organization focused on solving the EGFR exon 20 insertion and HER2/ERBB2 exon 20 mutation problem in lung cancer.

The Exon 20 Group is specifically interested in breakthroughs that could:
- Overcome resistance to current EGFR exon 20 treatments (amivantamab, sunvozertinib)
- Improve selectivity of drugs targeting exon 20 insertions over wild-type EGFR
- Apply novel drug modalities (ADCs, bispecifics, PROTACs, molecular glues) to exon 20 mutations
- Leverage immunotherapy (CAR-T, checkpoint inhibitors, tumor microenvironment modulation) in combination with targeted therapy
- Illuminate new biology relevant to exon 20 insertion structural dynamics or resistance mechanisms

Here are today's new research articles from a broad oncology breakthrough scan:

{article_list}

Your tasks:

1. Screen ALL articles and identify those with GENUINE potential relevance to the exon 20 insertion problem. Be discriminating — only flag articles where you can articulate a specific, plausible mechanistic connection to exon 20 biology or treatment. Exclude articles that are only superficially related to lung cancer or oncology in general.

2. For each article you identify as potentially relevant, write:
   - A plain-English explanation (2-3 sentences) of what the finding is
   - A plain-English explanation (2-3 sentences) of WHY it might matter specifically for the exon 20 problem
   - A confidence level: HIGH, MEDIUM, or LOW (be honest — most will be LOW or MEDIUM)

3. From the relevant articles, select the single most promising one and write THREE post summaries under 240 characters each (link added separately), ending with #EGFRExon20 #LungCancer, written for a patient and advocate audience.

4. If NO articles show genuine relevance, say so honestly — do not force relevance where none exists.

Respond in this exact JSON format with no other text:
{{
  "chosen_article_index": <number, 1-based, or null if none are relevant>,
  "post_1": "<factual summary, no link, or empty string if none relevant>",
  "post_2": "<patient-centered angle, no link, or empty string if none relevant>",
  "post_3": "<broader context, no link, or empty string if none relevant>",
  "reasoning": "<one sentence explaining your top pick, or 'No articles with clear relevance to exon 20 found today'>",
  "opportunity_articles": [
    {{
      "article_index": <number, 1-based>,
      "finding": "<2-3 sentences: what the article found>",
      "relevance": "<2-3 sentences: why this might matter for exon 20>",
      "confidence": "<HIGH, MEDIUM, or LOW>"
    }}
  ],
  "sensitive_articles": [],
  "endpoint_articles": []
}}"""
    else:
        prompt = f"""You are helping the Exon 20 Group, a patient advocacy organization focused on thoracic oncology mutations including EGFR exon 20 insertion and HER2/ERBB2 alterations in lung cancer.

Today's search: {search_label}

Here are today's new research articles. Articles marked [OPEN ACCESS] are freely readable. Articles marked [PAYWALLED] require a subscription.

{article_list}

Your tasks:

1. From the [OPEN ACCESS] articles ONLY, identify the single most clinically relevant one for patients, caregivers, and advocates. Prioritize: clinical trials > new drug data > survival/response outcomes > review articles > basic science. Do NOT select a [PAYWALLED] article for this.

2. Write THREE different summaries of that chosen open-access article, all targeting the patient and advocate community in plain English. Each should be under 240 characters (link added separately), end with #EGFRExon20 #LungCancer (for EGFR searches) or #HER2LungCancer #LungCancer (for HER2 searches), and be factually accurate without overstating findings. Make each one distinct:
   - post_1: Straightforward factual summary of the key finding
   - post_2: Emphasizes what this means for patients — hopeful, patient-centered angle
   - post_3: Broader context — why this research matters for the EGFR/HER2 exon 20 community

3. Write one sentence explaining why you chose this article.

4. For EVERY article (both open access and paywalled), assess two things independently:

   a) DEMORALIZING FLAG: Flag if it contains poor survival outcomes, very low response rates, findings suggesting very limited treatment options, high toxicity with poor benefit, or conclusions likely to cause hopelessness. Err on the side of flagging. Use abstract if available; use title alone if no abstract.

   b) CLINICAL ENDPOINTS FLAG: Flag if the article reports or discusses any clinical endpoints including but not limited to: PFS, OS, ORR, DOR, TTR, EFS, RFS, DCR, CBR, TTP, or any other efficacy or survival metric. List which specific endpoints are mentioned.

Respond in this exact JSON format with no other text:
{{
  "chosen_article_index": <number, 1-based, must be an open-access article>,
  "post_1": "<factual summary, no link>",
  "post_2": "<patient-centered angle, no link>",
  "post_3": "<broader context, no link>",
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
      "endpoints": "<comma-separated list of endpoints mentioned>"
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
            chosen = combined[idx] if 0 <= idx < len(combined) else (oa_articles[0] if oa_articles else None)
            if chosen:
                print(f"  Claude chose: {chosen['title'][:60]}...")

            # Build flag dicts using combined article order
            sensitive_keys = {}
            for s in result.get("sensitive_articles", []):
                sidx = s.get("article_index", 0) - 1
                if 0 <= sidx < len(combined):
                    sensitive_keys[make_key(combined[sidx])] = s.get("reason", "")

            endpoint_keys = {}
            for e in result.get("endpoint_articles", []):
                eidx = e.get("article_index", 0) - 1
                if 0 <= eidx < len(combined):
                    endpoint_keys[make_key(combined[eidx])] = e.get("endpoints", "")

            if sensitive_keys:
                print(f"  Flagged {len(sensitive_keys)} potentially sensitive articles.")
            if endpoint_keys:
                print(f"  Flagged {len(endpoint_keys)} articles with clinical endpoints.")

            # Build opportunity_keys for opportunity scan
            opportunity_keys = {}
            for o in result.get("opportunity_articles", []):
                oidx = o.get("article_index", 0) - 1
                if 0 <= oidx < len(combined):
                    opportunity_keys[make_key(combined[oidx])] = {
                        "finding": o.get("finding", ""),
                        "relevance": o.get("relevance", ""),
                        "confidence": o.get("confidence", "LOW"),
                    }
            if opportunity_keys:
                print(f"  Identified {len(opportunity_keys)} potentially relevant opportunity articles.")

            result["sensitive_keys"] = sensitive_keys
            result["endpoint_keys"] = endpoint_keys
            result["opportunity_keys"] = opportunity_keys
            return chosen, result

    except Exception as e:
        print(f"  Warning: Claude API call failed → {e}")
        return None, None

# ── Email Builder ─────────────────────────────────────────────────────────────
def build_email_html(oa_articles, paywalled_articles, today_str, search_config,
                     chosen_article=None, ai_result=None,
                     sensitive_keys=None, endpoint_keys=None,
                     news_articles=None, opportunity_keys=None):

    sensitive_keys  = sensitive_keys  or {}
    endpoint_keys   = endpoint_keys   or {}
    opportunity_keys = opportunity_keys or {}
    header_color   = search_config["header_color"]
    label          = search_config["label"]
    short_label    = search_config.get("short_label", label)
    all_articles   = oa_articles + paywalled_articles

    # ── X Post Draft Box ──
    if chosen_article and ai_result:
        link      = chosen_article['link']
        post_1    = ai_result.get("post_1", "")
        post_2    = ai_result.get("post_2", "")
        post_3    = ai_result.get("post_3", "")
        reasoning = ai_result.get("reasoning", "")
        full_1    = f"{post_1} {link}"
        full_2    = f"{post_2} {link}"
        full_3    = f"{post_3} {link}"
        x_post_box = f"""
        <div class="x-box">
            <div class="x-label">📣 Suggested Posts — Review &amp; Post When Ready</div>
            <div class="x-reasoning"><strong>Why this article:</strong> {reasoning}</div>

            <div class="post-label">{short_label}/1</div>
            <div class="x-post">{full_1}</div>
            <div class="x-char-count">{len(full_1)} characters</div>
            <a href="https://twitter.com/intent/tweet?text={urllib.parse.quote(full_1)}"
               class="x-button">Open in X →</a>

            <div class="post-label" style="margin-top:16px;">{short_label}/2</div>
            <div class="x-post">{full_2}</div>
            <div class="x-char-count">{len(full_2)} characters</div>
            <a href="https://twitter.com/intent/tweet?text={urllib.parse.quote(full_2)}"
               class="x-button">Open in X →</a>

            <div class="post-label" style="margin-top:16px;">{short_label}/3</div>
            <div class="x-post">{full_3}</div>
            <div class="x-char-count">{len(full_3)} characters</div>
        </div>"""
    else:
        x_post_box = ""

    def make_card(a, section="oa"):
        akey = make_key(a)
        abstract_html = (
            f'<p class="abstract">{a["abstract"]}{"..." if len(a["abstract"]) == 600 else ""}</p>'
            if a["abstract"] else ""
        )
        is_chosen = chosen_article and akey == make_key(chosen_article)
        highlight = f' style="border-left: 4px solid {header_color}; padding-left: 16px;"' if is_chosen else ""
        sensitive_html = (
            f'<div class="flag flag-sensitive">⚠️ <strong>Heads up:</strong> {sensitive_keys[akey]}</div>'
            if akey in sensitive_keys else ""
        )
        endpoint_html = (
            f'<div class="flag flag-endpoint">📊 <strong>Clinical endpoints reported:</strong> {endpoint_keys[akey]}</div>'
            if akey in endpoint_keys else ""
        )
        if akey in opportunity_keys:
            opp = opportunity_keys[akey]
            conf_color = {"HIGH": "#1a5c2a", "MEDIUM": "#6b3a00", "LOW": "#555"}.get(opp["confidence"], "#555")
            opportunity_html = f'''<div class="flag flag-opportunity">
                🔬 <strong>Exon 20 Opportunity [{opp["confidence"]} confidence]</strong><br>
                <strong>Finding:</strong> {opp["finding"]}<br>
                <strong>Potential relevance:</strong> {opp["relevance"]}
            </div>'''
        else:
            opportunity_html = ""
        oa_label = ' &nbsp;<span style="color: #2d6a4f; font-size: 12px; font-weight: bold;">(open access)</span>' if section == "oa" else ""
        if section == "oa":
            action = f'<a href="{a["link"]}" class="read-link">Read full article →</a>'
        elif section == "news":
            action = f'<a href="{a["link"]}" class="read-link news-link">📰 Read full article →</a>'
        else:
            action = f'<a href="{a["link"]}" class="read-link paywall-link">🔒 View abstract (paywall)</a>'

        return f"""
        <div class="card"{highlight}>
            <div class="source-tag">{a['source']}</div>
            <h2><a href="{a['link']}">{a['title']}</a></h2>
            <p class="meta">{a['authors']}</p>
            <p class="meta journal">{a['journal']} &nbsp;·&nbsp; {a['date']}{oa_label}</p>
            {abstract_html}
            {sensitive_html}
            {endpoint_html}
            {opportunity_html}
            {action}
        </div>"""

    # ── Build body content ──
    if not all_articles:
        body_content = f"""
        <div class="no-results">
            <p>No new articles found in the past {DAYS_BACK} days for
            <strong>{label}</strong> that haven't already been sent.</p>
            <p>The search will run again tomorrow.</p>
        </div>"""
    else:
        oa_cards = "".join(make_card(a, "oa") for a in oa_articles)
        pay_cards = "".join(make_card(a, "pay") for a in paywalled_articles)

        oa_section = oa_cards if oa_cards else '<p class="no-results">No open-access articles found today.</p>'

        if pay_cards:
            divider = f"""
            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 40px 0 32px 0;">
                <tr>
                    <td bgcolor="#8b0000" style="background-color: #8b0000; padding: 16px 24px; text-align: center;">
                        <span style="color: #ffffff; font-size: 16px; font-weight: bold; font-family: Georgia, serif; letter-spacing: 2px;">
                            &#128274; &nbsp; BEHIND A PAYWALL &nbsp;&middot;&nbsp; {len(paywalled_articles)} ARTICLE{"S" if len(paywalled_articles) != 1 else ""} &nbsp;&middot;&nbsp; ABSTRACT ONLY &nbsp; &#128274;
                        </span>
                    </td>
                </tr>
            </table>"""
            pay_section = pay_cards
        else:
            divider = ""
            pay_section = ""

        # ── News section ──
        if news_articles:
            news_cards = "".join(make_card(a, "news") for a in news_articles)
            news_section = f"""
            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 40px 0 28px 0;">
                <tr>
                    <td bgcolor="#4a235a" style="background-color: #4a235a; padding: 16px 24px; text-align: center;">
                        <span style="color: #ffffff; font-size: 16px; font-weight: bold; font-family: Georgia, serif; letter-spacing: 2px;">
                            📰 &nbsp; CLINICAL NEWS &amp; CONFERENCE UPDATES &nbsp;·&nbsp; {len(news_articles)} ARTICLE{"S" if len(news_articles) != 1 else ""} &nbsp; 📰
                        </span>
                    </td>
                </tr>
            </table>
            {news_cards}"""
        else:
            news_section = ""

        body_content = oa_section + divider + pay_section + news_section

    total = len(all_articles)
    oa_count = len(oa_articles)
    pay_count = len(paywalled_articles)

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
  .post-label {{ font-size: 12px; font-weight: bold; color: #444; margin: 12px 0 6px 0; }}
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
  .flag-opportunity {{ background: #f0e8ff; border-left: 4px solid #6b3a8b; color: #3a1a5c; line-height: 1.6; }}
  .read-link {{ font-size: 13px; color: #c0392b; text-decoration: none; font-weight: bold; }}
  .paywall-link {{ font-size: 13px; color: #888; text-decoration: none; font-weight: normal; }}
  .divider {{ margin: 40px 0 32px 0; }}
  .divider-banner {{ background: #8b0000; color: white; text-align: center; padding: 16px 24px;
                    font-size: 16px; font-weight: bold; letter-spacing: 2px; border-radius: 6px;
                    border: 3px solid #5a0000; }}
  .no-results {{ color: #555; font-size: 15px; padding: 20px 0; }}
  .footer {{ font-size: 11px; color: #aaa; text-align: center; margin-top: 20px; }}
</style>
</head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>{label} — Research Digest</h1>
    <p>{today_str} &nbsp;·&nbsp; {total} new article{"s" if total != 1 else ""} — {oa_count} open access · {pay_count} paywalled</p>
  </div>
  <div class="body">
    {x_post_box}
    <p class="summary">
      <strong>{oa_count} open-access</strong> and <strong>{pay_count} paywalled</strong>
      new article{"s" if total != 1 else ""} found today.
      {f'Highlighted article (blue border) is suggested for posting.' if chosen_article and oa_articles else ''}
      {'&nbsp; ⚠️ = may be difficult for patients &nbsp; 📊 = reports clinical endpoints' if all_articles else ''}
    </p>
    {body_content}
  </div>
  <div class="footer">
    Academic: PubMed · Europe PMC · bioRxiv · medRxiv · Semantic Scholar<br>
    Clinical News: Targeted Oncology · OncLive · Medscape · NCI<br>
    Sent automatically to robertthanlon@gmail.com (forwarded to marcia@askican.org) · Exon 20 Group Research Monitor
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

    print(f"  Searching academic sources...")
    is_opp = search_config.get("is_opportunity_scan", False)
    epmc_days = 90 if is_opp else 30
    all_articles = run_all_sources(terms, epmc_days=epmc_days)
    all_articles.sort(key=lambda x: x.get("date", ""), reverse=True)

    # Filter to genuinely new articles
    new_articles = [a for a in all_articles if make_key(a) not in seen]

    # Split into open-access and paywalled
    oa_articles        = [a for a in new_articles if a.get("open_access")]
    paywalled_articles = [a for a in new_articles if not a.get("open_access")]

    print(f"  Total found: {len(all_articles)} | New: {len(new_articles)} ({len(oa_articles)} OA, {len(paywalled_articles)} paywalled)")

    news_articles = []  # RSS feeds removed (403 blocks)

    # Claude analyzes all new articles but only selects from OA academic for the post
    chosen_article, ai_result = analyze_articles(oa_articles, new_articles, label)
    sensitive_keys = ai_result.get("sensitive_keys", {}) if ai_result else {}
    endpoint_keys  = ai_result.get("endpoint_keys",  {}) if ai_result else {}

    total_new = len(oa_articles) + len(paywalled_articles)
    if total_new == 0:
        print(f"  No new articles — skipping email to avoid cluttering inbox.")
    else:
        html = build_email_html(
            oa_articles, paywalled_articles, today_str, search_config,
            chosen_article, ai_result, sensitive_keys, endpoint_keys,
            news_articles=news_articles
        )
        subject = f"{label} Digest — {today_str} ({len(oa_articles)} OA · {len(paywalled_articles)} paywalled)"
        send_email(html, subject)

    # Save sent articles to memory
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
