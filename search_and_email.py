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
            "antibody-drug conjugate solid tumor",
            "bispecific antibody solid tumor",
            "PROTAC kinase degrader",
            "molecular glue cancer",
            "acquired resistance targeted therapy",
            "kinase inhibitor resistance mechanism",
            "tumor microenvironment immunotherapy",
            "CAR-T solid tumor",
            "checkpoint inhibitor combination therapy",
            "neoantigen cancer immunotherapy",
            "oncolytic virus cancer",
            "cancer vaccine clinical trial",
            "synthetic lethality cancer",
            "DNA damage repair cancer therapy",
            "exon skipping cancer mutation",
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
    if not date_str:
        return True
    try:
        if len(date_str) == 4:
            cutoff_year = (datetime.utcnow() - timedelta(days=days)).year
            return int(date_str) >= cutoff_year
        pub_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
        cutoff = datetime.utcnow() - timedelta(days=days)
        return pub_date >= cutoff
    except Exception:
        return True

# ── Source searches ──────────────────────────────────────────────────────────
def search_europe_pmc(term, days=30):
    results = []
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
        pub_date = item.get("firstPublicationDate", "") or item.get("firstIndexDate", "")
        if pub_date and not is_within_days(pub_date, days):
            continue
        doi = item.get("doi")
        pmcid = item.get("pmcid", "")
        is_oa = item.get("isOpenAccess") == "Y"
        free_link = None
        for link in item.get("fullTextUrlList", {}).get("fullTextUrl", []):
            avail = (link.get("availability") or "").lower()
            if link.get("documentStyle") in ("pdf", "html") and ("open access" in avail or "free" in avail):
                free_link = link.get("url")
                break
        open_access = is_oa or bool(free_link)
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

def search_pubmed(term, exact_phrase=True):
    results = []
    cutoff_60 = (datetime.utcnow() - timedelta(days=60)).strftime("%Y/%m/%d")
    today     = datetime.utcnow().strftime("%Y/%m/%d")

    if exact_phrase:
        query_all  = urllib.parse.quote(f'"{term}"[Title/Abstract]')
        query_free = urllib.parse.quote(f'"{term}"[Title/Abstract] AND free full text[filter]')
    else:
        query_all  = urllib.parse.quote(f'{term}[Title/Abstract]')
        query_free = urllib.parse.quote(f'{term}[Title/Abstract] AND free full text[filter]')

    data_all = fetch_json(
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        f"?db=pubmed&term={query_all}"
        f"&mindate={cutoff_60}&maxdate={today}&datetype=pdat"
        f"&retmax=200&retmode=json&sort=date"
    )
    all_ids = set(data_all.get("esearchresult", {}).get("idlist", [])) if data_all else set()

    time.sleep(0.4)

    data_free = fetch_json(
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        f"?db=pubmed&term={query_free}"
        f"&mindate={cutoff_60}&maxdate={today}&datetype=pdat"
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

# ── Run all sources ──────────────────────────────────────────────────────────
def run_all_sources(terms, epmc_days=30, skip_epmc=False, exact_phrase=True):
    all_results = []
    for term in terms:
        print(f"  Searching for: '{term}'")
        if not skip_epmc:
            all_results += search_europe_pmc(term, days=epmc_days)
        all_results += search_pubmed(term, exact_phrase=exact_phrase)
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

# ── Claude AI ────────────────────────────────────────────────────────────────
def analyze_articles(oa_articles, all_articles, search_label):
    if not ANTHROPIC_API_KEY or not all_articles:
        return None, None

    is_opp_scan = search_label == "Oncology Breakthroughs — Exon 20 Opportunity Scan"

    if not is_opp_scan and not oa_articles:
        print(f"  No open-access articles — skipping Claude analysis.")
        return None, None

    print(f"  Asking Claude to analyze {len(all_articles)} articles ({len(oa_articles)} open access)...")

    combined = oa_articles + [a for a in all_articles if not a.get("open_access")]

    def clean(s):
        s = str(s).replace('"', "'")
        s = s.replace('\n', ' ').replace('\r', ' ')
        return s.strip()

    article_list = ""
    for i, a in enumerate(combined[:25]):
        oa_label = "[OPEN ACCESS]" if a.get("open_access") else "[PAYWALLED]"
        if is_opp_scan:
            article_list += (
                f"Article {i+1} {oa_label}:\n"
                f"  Title: {clean(a['title'])}\n"
                f"  Journal: {clean(a['journal'])}\n"
                f"  Date: {clean(a['date'])}\n"
                f"  Link: {a['link']}\n\n"
            )
        else:
            article_list += (
                f"Article {i+1} {oa_label}:\n"
                f"  Title: {clean(a['title'])}\n"
                f"  Journal: {clean(a['journal'])}\n"
                f"  Authors: {clean(a['authors'])}\n"
                f"  Date: {clean(a['date'])}\n"
                f"  Abstract: {clean(a['abstract']) if a['abstract'] else '(no abstract available)'}\n"
                f"  Link: {a['link']}\n\n"
            )

    if is_opp_scan:
        prompt = f"""You are a scientific advisor to the Exon 20 Group focused on solving the EGFR exon 20 insertion and HER2/ERBB2 exon 20 mutation problem in lung cancer.

Areas of interest: resistance to amivantamab/sunvozertinib, novel drug modalities (ADCs, bispecifics, PROTACs, molecular glues), immunotherapy combinations, EGFR/HER2 structural biology, acquired resistance mechanisms.

Here are today's new research articles:

{article_list}

For EACH article, write a response block in EXACTLY this format. Use three dashes --- to separate blocks. Do not use JSON. Do not add extra text before or after the blocks.

ARTICLE: [number]
SUMMARY: [2-3 sentences on what the article found]
RELEVANCE: [1-2 sentences on connection to EGFR/HER2 exon 20, or write: No clear relevance to exon 20.]
CONFIDENCE: [HIGH or MEDIUM or LOW]

---

Start with ARTICLE: 1 and go through all articles in order."""

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
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
            text = data["content"][0]["text"].strip()

            if is_opp_scan:
                # Parse plain text format for opportunity scan
                result = {
                    "chosen_article_index": None,
                    "post_1": "", "post_2": "", "post_3": "",
                    "reasoning": "", "sensitive_articles": [],
                    "endpoint_articles": [], "articles": []
                }
                blocks = text.split("---")
                for block in blocks:
                    block = block.strip()
                    if not block:
                        continue
                    article_data = {}
                    for line in block.split("\n"):
                        line = line.strip()
                        if line.startswith("ARTICLE:"):
                            try:
                                article_data["article_index"] = int(line.replace("ARTICLE:", "").strip())
                            except:
                                pass
                        elif line.startswith("SUMMARY:"):
                            article_data["summary"] = line.replace("SUMMARY:", "").strip()
                        elif line.startswith("RELEVANCE:"):
                            article_data["egfr_her2_relevance"] = line.replace("RELEVANCE:", "").strip()
                        elif line.startswith("CONFIDENCE:"):
                            article_data["confidence"] = line.replace("CONFIDENCE:", "").strip()
                    if "article_index" in article_data:
                        result["articles"].append(article_data)
            else:
                # Standard JSON parsing for EGFR/HER2 searches
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                text = text.strip()
                start = text.find("{")
                end   = text.rfind("}") + 1
                if start >= 0 and end > start:
                    text = text[start:end]
                result = json.loads(text)

            raw_idx = result.get("chosen_article_index")
            if raw_idx is not None:
                idx = raw_idx - 1
                chosen = combined[idx] if 0 <= idx < len(combined) else (oa_articles[0] if oa_articles else None)
            else:
                chosen = None
            if chosen:
                print(f"  Claude chose: {chosen['title'][:60]}...")

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

            article_summaries = {}
            for a in result.get("articles", []):
                aidx = a.get("article_index", 0) - 1
                if 0 <= aidx < len(combined):
                    article_summaries[make_key(combined[aidx])] = {
                        "summary": a.get("summary", ""),
                        "exon20_relevance": a.get("exon20_relevance", ""),
                        "egfr_her2_relevance": a.get("egfr_her2_relevance", ""),
                        "confidence": a.get("confidence", ""),
                    }
            if article_summaries:
                print(f"  Generated summaries for {len(article_summaries)} articles.")

            result["sensitive_keys"] = sensitive_keys
            result["endpoint_keys"] = endpoint_keys
            result["opportunity_keys"] = {}
            result["article_summaries"] = article_summaries
            return chosen, result

    except Exception as e:
        print(f"  Warning: Claude API call failed → {e}")
        return None, None

# ── Email Builder ─────────────────────────────────────────────────────────────
def build_email_html(oa_articles, paywalled_articles, today_str, search_config,
                     chosen_article=None, ai_result=None,
                     sensitive_keys=None, endpoint_keys=None,
                     news_articles=None, opportunity_keys=None,
                     article_summaries=None):

    sensitive_keys   = sensitive_keys   or {}
    endpoint_keys    = endpoint_keys    or {}
    opportunity_keys = opportunity_keys or {}
    article_summaries = article_summaries or {}
    is_opp_scan = search_config.get("is_opportunity_scan", False)
    header_color   = search_config["header_color"]
    label          = search_config["label"]
    short_label    = search_config.get("short_label", label)
    all_articles   = oa_articles + paywalled_articles

    if chosen_article and ai_result and not is_opp_scan:
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

        if is_opp_scan:
            sensitive_html = ""
            endpoint_html = ""
            opportunity_html = ""
            if akey in article_summaries:
                s = article_summaries[akey]
                conf = s.get("confidence", "")
                conf_colors = {"HIGH": "#1a5c2a", "MEDIUM": "#6b3a00", "LOW": "#555"}
                conf_bg = conf_colors.get(conf, "#555")
                conf_label = f' <span style="font-size:11px; padding:2px 6px; border-radius:3px; background:{conf_bg}; color:white;">{conf}</span>' if conf else ""
                relevance_text = s.get("egfr_her2_relevance", s.get("exon20_relevance", ""))
                opportunity_html = (
                    f'<div class="flag flag-opportunity">'
                    f'<strong>Summary:</strong> {s["summary"]}<br><br>'
                    f'<strong>Relevance to EGFR/HER2 exon 20 community:</strong>{conf_label}<br>{relevance_text}'
                    f'</div>'
                )
        else:
            sensitive_html = (
                f'<div class="flag flag-sensitive">⚠️ <strong>Heads up:</strong> {sensitive_keys[akey]}</div>'
                if akey in sensitive_keys else ""
            )
            endpoint_html = (
                f'<div class="flag flag-endpoint">📊 <strong>Clinical endpoints reported:</strong> {endpoint_keys[akey]}</div>'
                if akey in endpoint_keys else ""
            )
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

    if not all_articles:
        body_content = f"""
        <div class="no-results">
            <p>No new articles found for <strong>{label}</strong> that haven't already been sent.</p>
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
      {'&nbsp; ⚠️ = may be difficult for patients &nbsp; 📊 = reports clinical endpoints' if all_articles and not is_opp_scan else ''}
    </p>
    {body_content}
  </div>
  <div class="footer">
    Academic: PubMed · Europe PMC · bioRxiv · medRxiv · Semantic Scholar<br>
    Sent automatically to robertthanlon@gmail.com (forwarded to marcia@askican.org) · Exon 20 Group Research Monitor
  </div>
</div>
</body>
</html>"""

# ── Send Email ────────────────────────────────────────────────────────────────
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
    all_articles = run_all_sources(terms, epmc_days=30, skip_epmc=is_opp, exact_phrase=not is_opp)
    all_articles.sort(key=lambda x: x.get("date", ""), reverse=True)

    new_articles = [a for a in all_articles if make_key(a) not in seen]
    oa_articles        = [a for a in new_articles if a.get("open_access")]
    paywalled_articles = [a for a in new_articles if not a.get("open_access")]

    print(f"  Total found: {len(all_articles)} | New: {len(new_articles)} ({len(oa_articles)} OA, {len(paywalled_articles)} paywalled)")

    if is_opp:
        oa_for_claude  = oa_articles[:25]
        all_for_claude = new_articles[:25]
    else:
        oa_for_claude  = oa_articles
        all_for_claude = new_articles

    chosen_article, ai_result = analyze_articles(oa_for_claude, all_for_claude, label)
    sensitive_keys    = ai_result.get("sensitive_keys",    {}) if ai_result else {}
    endpoint_keys     = ai_result.get("endpoint_keys",     {}) if ai_result else {}
    opportunity_keys  = ai_result.get("opportunity_keys",  {}) if ai_result else {}
    article_summaries = ai_result.get("article_summaries", {}) if ai_result else {}

    total_new = len(oa_articles) + len(paywalled_articles)
    if total_new == 0:
        print(f"  No new articles — skipping email to avoid cluttering inbox.")
    else:
        display_oa  = oa_for_claude if is_opp else oa_articles
        display_pay = [a for a in all_for_claude if not a.get("open_access")] if is_opp else paywalled_articles
        html = build_email_html(
            display_oa, display_pay, today_str, search_config,
            chosen_article, ai_result, sensitive_keys, endpoint_keys,
            opportunity_keys=opportunity_keys,
            article_summaries=article_summaries
        )
        opp_note = f" (showing top 25 of {total_new})" if is_opp and total_new > 25 else ""
        subject = f"{label} Digest — {today_str} ({len(display_oa)} OA · {len(display_pay)} paywalled{opp_note})"
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
