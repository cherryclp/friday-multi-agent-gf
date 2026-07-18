"""
BrowserAgent — opens URLs, does web searches, fetches page content.
Args: {action: "open"|"search"|"fetch", url: str = None, query: str = None}
"""

import webbrowser
import urllib.parse
import subprocess
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


class BrowserAgent:
    def __init__(self, llm=None):
        self.llm = llm

    def _fetch_page(self, url: str, max_chars: int = 3000) -> str:
        """Fetch and extract text from a URL."""
        if not HAS_REQUESTS:
            return f"❌ requests not installed — run: pip install requests"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()

            if HAS_BS4:
                soup = BeautifulSoup(resp.text, "html.parser")
                # Remove scripts, styles, nav
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                text = soup.get_text(separator="\n", strip=True)
                # Clean up excessive whitespace
                text = re.sub(r'\n{3,}', '\n\n', text)
                return text[:max_chars]
            else:
                # Fallback: strip HTML tags manually
                text = re.sub(r'<[^>]+>', '', resp.text)
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:max_chars]
        except requests.Timeout:
            return f"❌ Timed out fetching {url}"
        except requests.HTTPError as e:
            return f"❌ HTTP {e.response.status_code} for {url}"
        except Exception as e:
            return f"❌ Fetch error: {e}"

    def _search_web(self, query: str) -> str:
        """Open a DuckDuckGo search and optionally fetch first result."""
        encoded = urllib.parse.quote_plus(query)
        search_url = f"https://duckduckgo.com/?q={encoded}"
        webbrowser.open(search_url)

        # Also try to get top result via DDG API
        if HAS_REQUESTS:
            try:
                api_url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
                resp = requests.get(api_url, timeout=10,
                                    headers={"User-Agent": "Jarvis/1.0"})
                data = resp.json()
                abstract = data.get("AbstractText", "")
                answer   = data.get("Answer", "")
                if answer:
                    return f"✅ Opened search for: {query}\n\n🎯 Quick answer: {answer}"
                if abstract:
                    return f"✅ Opened search for: {query}\n\n📄 Summary: {abstract[:500]}"
            except Exception:
                pass
        return f"✅ Opened web search for: {query}"

    def run(self, args: dict) -> str:
        action = args.get("action", "open").lower()
        url    = args.get("url", "").strip()
        query  = args.get("query", "").strip()

        if action == "open":
            if not url:
                return "ERROR: BrowserAgent needs a 'url' for action=open"
            webbrowser.open(url)
            return f"✅ Opened: {url}"

        elif action == "search":
            if not query:
                return "ERROR: BrowserAgent needs a 'query' for action=search"
            return self._search_web(query)

        elif action == "fetch":
            if not url:
                return "ERROR: BrowserAgent needs a 'url' for action=fetch"
            content = self._fetch_page(url)
            # If LLM available, summarize
            if self.llm and len(content) > 200:
                try:
                    task = args.get("_context", "") or "Summarize this page content concisely."
                    summary = self.llm.invoke([
                        ("system", "You are a web research assistant. Extract the most relevant information."),
                        ("user", f"Task: {task}\n\nPage content:\n{content}")
                    ]).content.strip()
                    return f"✅ Fetched {url}\n\n📋 Summary:\n{summary}"
                except Exception:
                    pass
            return f"✅ Fetched {url}\n\n{content}"

        elif action == "search_and_fetch":
            if not query:
                return "ERROR: need 'query'"
            # Search DDG and fetch the top result
            encoded = urllib.parse.quote_plus(query)
            webbrowser.open(f"https://duckduckgo.com/?q={encoded}")
            if HAS_REQUESTS:
                try:
                    api_url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1"
                    resp = requests.get(api_url, timeout=10, headers={"User-Agent": "Jarvis/1.0"})
                    data = resp.json()
                    top_url = data.get("AbstractURL") or data.get("Results", [{}])[0].get("FirstURL", "")
                    if top_url:
                        content = self._fetch_page(top_url)
                        return f"✅ Fetched top result: {top_url}\n\n{content}"
                except Exception as e:
                    pass
            return f"✅ Opened search for: {query}"

        else:
            return f"ERROR: Unknown browser action '{action}'. Use: open, search, fetch, search_and_fetch"
