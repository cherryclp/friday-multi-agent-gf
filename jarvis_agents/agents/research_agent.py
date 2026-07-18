"""
ResearchAgent — searches the web, fetches pages, summarizes findings.
Args: {query: str, depth: int = 1}
"""

import urllib.parse
import os
import re

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

JARVIS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ResearchAgent:
    def __init__(self, llm=None):
        self.llm = llm

    def _ddg_search(self, query: str) -> list[dict]:
        """Get results from DuckDuckGo instant answer API."""
        if not HAS_REQUESTS:
            return []
        try:
            encoded = urllib.parse.quote_plus(query)
            url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Jarvis/1.0"})
            data = resp.json()
            results = []
            if data.get("AbstractText"):
                results.append({
                    "title": data.get("Heading", query),
                    "url": data.get("AbstractURL", ""),
                    "snippet": data["AbstractText"],
                })
            for r in data.get("Results", [])[:3]:
                results.append({
                    "title": r.get("Text", ""),
                    "url": r.get("FirstURL", ""),
                    "snippet": r.get("Text", ""),
                })
            for r in data.get("RelatedTopics", [])[:3]:
                if isinstance(r, dict) and r.get("Text"):
                    results.append({
                        "title": r.get("Text", "")[:60],
                        "url": r.get("FirstURL", ""),
                        "snippet": r.get("Text", ""),
                    })
            return results
        except Exception as e:
            return [{"title": "Search error", "url": "", "snippet": str(e)}]

    def _fetch_page(self, url: str, max_chars: int = 2000) -> str:
        """Fetch and extract text from a URL."""
        if not HAS_REQUESTS or not url:
            return ""
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            if HAS_BS4:
                soup = BeautifulSoup(resp.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()
                text = soup.get_text(separator="\n", strip=True)
                text = re.sub(r'\n{3,}', '\n\n', text)
                return text[:max_chars]
            else:
                text = re.sub(r'<[^>]+>', '', resp.text)
                return re.sub(r'\s+', ' ', text).strip()[:max_chars]
        except Exception:
            return ""

    def run(self, args: dict) -> str:
        query = args.get("query", "").strip()
        depth = int(args.get("depth", 1))  # 1=quick, 2=fetch pages too

        if not query:
            return "ERROR: ResearchAgent needs a 'query'"

        if not HAS_REQUESTS:
            return "❌ requests not installed — run: pip install requests"

        # Search
        results = self._ddg_search(query)

        if not results:
            return f"❌ No results found for: {query}"

        # Build context
        context_parts = []
        for i, r in enumerate(results[:5]):
            snippet = r.get("snippet", "")
            url     = r.get("url", "")
            title   = r.get("title", "")

            if depth >= 2 and url and i < 2:
                # Fetch actual page for top 2 results
                page_text = self._fetch_page(url)
                if page_text:
                    snippet = page_text[:1500]

            context_parts.append(f"Source {i+1}: {title}\nURL: {url}\n{snippet}")

        context = "\n\n---\n\n".join(context_parts)

        # LLM summarize
        if self.llm:
            try:
                summary = self.llm.invoke([
                    ("system",
                     "You are a research assistant. Summarize the findings clearly and concisely. "
                     "Include key facts. If sources conflict, note it. "
                     "End with 'Sources: [URL1, URL2]' if URLs are available."),
                    ("user", f"Research query: {query}\n\nFindings:\n{context}")
                ]).content.strip()
                return f"🔍 Research: **{query}**\n\n{summary}"
            except Exception as e:
                pass

        # Fallback: return raw results
        lines = [f"🔍 Research results for: {query}\n"]
        for r in results[:4]:
            lines.append(f"• **{r['title']}**")
            lines.append(f"  {r['snippet'][:200]}")
            if r.get("url"):
                lines.append(f"  {r['url']}")
            lines.append("")
        return "\n".join(lines)
