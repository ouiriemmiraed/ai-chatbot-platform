import asyncio
import hashlib
import re
import sys
import os
import logging
from collections import deque
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from core.url_safety import is_safe_public_url
from services.rag_service import rag_service
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from datetime import datetime
from bson import ObjectId

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default config – can be overridden via constructor kwargs
# ---------------------------------------------------------------------------
DEFAULT_CONCURRENCY = int(os.getenv("CRAWL_CONCURRENCY", "5"))  # Parallel browser tabs (optimized for speed)
DEFAULT_MAX_PAGES = None  # None -> no limit (crawl whole site/sitemap)
DEFAULT_MAX_DEPTH = 10           # Link-follow depth from start URL
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_DELAY = 0.1              # Polite delay between requests (seconds, reduced for faster crawling)
DEFAULT_PAGE_TIMEOUT = 30_000    # Playwright navigation timeout (ms)
DEFAULT_SELECTOR_TIMEOUT = 8_000 # Wait for content selectors (ms)

# URL patterns to always skip (login pages, API routes, query-heavy pages…)
DEFAULT_EXCLUDE_PATTERNS = [
    r'/login', r'/register', r'/signup', r'/logout',
    r'/admin', r'/api/', r'/auth/',
    r'\?sort=', r'\?order=', r'\?filter=',
    r'/tag/', r'/author/', r'/feed',
    r'#', r'javascript:',
]

# File extensions to reject
SKIP_EXTENSIONS = frozenset([
    '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp',
    '.svg', '.ico', '.css', '.js', '.zip', '.rar', '.gz',
    '.tar', '.xml', '.json', '.woff', '.woff2', '.ttf', '.eot',
    '.mp3', '.mp4', '.avi', '.mov', '.wav',
])


class WebsiteCrawler:
    """High-performance async website crawler with concurrent tabs, retry,
    content deduplication, depth control, sitemap support, and structured
    content extraction."""

    # ------------------------------------------------------------------
    # Docker URL helper
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_url_for_docker(url: str) -> str:
        """Replace localhost/127.0.0.1 with host.docker.internal when
        running inside a Docker bridge-network container."""
        network_mode = os.getenv('DOCKER_NETWORK_MODE', 'bridge')
        if network_mode == 'host':
            return url
        parsed = urlparse(url)
        if parsed.hostname in ('localhost', '127.0.0.1'):
            new_netloc = (f"host.docker.internal:{parsed.port}"
                          if parsed.port else "host.docker.internal")
            return urlunparse(parsed._replace(netloc=new_netloc))
        return url

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------
    def __init__(
        self,
        chatbot_id: str,
        start_url: str,
        max_pages = DEFAULT_MAX_PAGES,
        url_list: list = None,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        max_depth: int = DEFAULT_MAX_DEPTH,
        delay: float = DEFAULT_DELAY,
        retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
        exclude_patterns: list = None,
        include_patterns: list = None,
        page_timeout: int = DEFAULT_PAGE_TIMEOUT,
        use_sitemap: bool = True,
        allow_subdomains: bool = False,
        force_rescrape: bool = False,
    ):
        self.chatbot_id = chatbot_id
        self.start_url = self.normalize_url_for_docker(start_url)
        self.max_pages = max_pages
        self.concurrency = concurrency
        self.max_depth = max_depth
        self.delay = delay
        self.retry_attempts = retry_attempts
        self.page_timeout = page_timeout
        self.use_sitemap = use_sitemap
        self.allow_subdomains = allow_subdomains
        self.force_rescrape = force_rescrape

        # URL filter patterns (compiled once)
        raw_exclude = exclude_patterns if exclude_patterns is not None else DEFAULT_EXCLUDE_PATTERNS
        self.exclude_re = [re.compile(p, re.IGNORECASE) for p in raw_exclude]
        self.include_re = [re.compile(p, re.IGNORECASE) for p in (include_patterns or [])]

        # Thread-safe sets / queues (only touched inside the event-loop)
        self.visited: set[str] = set()
        self.seen_hashes: set[str] = set()       # content deduplication
        self.queued_set: set[str] = set()         # fast "already queued?" check

        # Queue stores (url, depth) tuples
        self.queue: deque[tuple[str, int]] = deque()

        # Accumulated chunks for bulk embedding at the end (replaces per-page embedding)
        self.pending_chunks: list = []

        if url_list:
            for u in url_list:
                nu = self.normalize_url_for_docker(u)
                n = self._normalize(nu)
                if n not in self.queued_set:
                    self.queue.append((nu, 0))
                    self.queued_set.add(n)
            self.use_auto_discovery = False
            logger.info(f"Crawling specific URL list: {len(url_list)} URLs provided")
        else:
            n = self._normalize(self.start_url)
            self.queue.append((self.start_url, 0))
            self.queued_set.add(n)
            self.use_auto_discovery = True

        parsed = urlparse(self.start_url)
        self.base_domain = parsed.netloc
        self.scheme = parsed.scheme

        # Compute root domain for subdomain matching
        # e.g. "docs.example.com" → root_domain = "example.com"
        _host = parsed.hostname or self.base_domain
        _host_parts = _host.split('.')
        self.root_domain = '.'.join(_host_parts[-2:]) if len(_host_parts) >= 2 else _host

        self.rp = None
        self.user_agent = (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )

        # Stats
        self.stats = {
            "pages_crawled": 0,
            "pages_skipped": 0,
            "pages_failed": 0,
            "content_ingested": 0,
            "duplicates_skipped": 0,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize(url: str) -> str:
        """Normalize a URL for dedup (strip trailing slash & fragment)."""
        return url.split('#')[0].rstrip('/')

    @staticmethod
    def _content_hash(text: str) -> str:
        return hashlib.sha256(text.encode('utf-8')).hexdigest()

    @property
    def _is_dev_mode(self) -> bool:
        return any(self.base_domain.startswith(h)
                   for h in ('localhost', '127.0.0.1', 'host.docker.internal'))

    # ------------------------------------------------------------------
    # robots.txt
    # ------------------------------------------------------------------
    async def _init_robots_parser(self):
        if self._is_dev_mode:
            logger.info(f"Dev mode ({self.base_domain}). Skipping robots.txt.")
            return
        robots_url = urlunparse((self.scheme, self.base_domain, '/robots.txt', '', '', ''))
        self.rp = RobotFileParser()
        self.rp.set_url(robots_url)
        try:
            await asyncio.to_thread(self.rp.read)
            logger.info(f"Loaded robots.txt from {robots_url}")
        except Exception as e:
            logger.info(f"Could not load robots.txt: {e}. Assuming all allowed.")
            self.rp = None

    def _can_fetch(self, url: str) -> bool:
        if self.rp:
            return self.rp.can_fetch(self.user_agent, url)
        return True

    # ------------------------------------------------------------------
    # Sitemap discovery (with nested sitemapindex support)
    # ------------------------------------------------------------------
    async def _discover_sitemap(self, browser_context):
        """Try to parse /sitemap.xml and seed the queue with discovered URLs.
        Supports both flat <urlset> sitemaps and nested <sitemapindex>."""
        if not self.use_sitemap or self._is_dev_mode:
            return
        sitemap_url = urlunparse((self.scheme, self.base_domain, '/sitemap.xml', '', '', ''))
        await self._process_sitemap(browser_context, sitemap_url, depth=0)

    async def _process_sitemap(self, browser_context, sitemap_url: str, depth: int = 0):
        """Fetch and process a single sitemap URL. Recurses into
        <sitemapindex> entries up to 3 levels deep."""
        if depth > 3:
            return
        logger.info(f"Checking sitemap (depth={depth}): {sitemap_url}")
        page = await browser_context.new_page()
        try:
            resp = await page.goto(sitemap_url, timeout=15_000)
            if resp and resp.ok:
                content = await page.content()
                soup = BeautifulSoup(content, 'html.parser')

                # Check for sitemap index (nested sitemaps)
                sitemap_index = soup.find('sitemapindex')
                if sitemap_index:
                    child_sitemaps = sitemap_index.find_all('sitemap')
                    logger.info(f"Sitemap index with {len(child_sitemaps)} child sitemaps")
                    for child_sm in child_sitemaps:
                        loc = child_sm.find('loc')
                        if loc:
                            child_url = loc.get_text(strip=True)
                            await self._process_sitemap(browser_context, child_url, depth + 1)
                else:
                    # Standard urlset sitemap
                    locs = soup.find_all('loc')
                    added = 0
                    for loc in locs:
                        url_text = loc.get_text(strip=True)
                        norm = self._normalize(url_text)
                        if self._is_valid_url(url_text) and norm not in self.queued_set:
                            self.queue.append((url_text, 0))
                            self.queued_set.add(norm)
                            added += 1
                    logger.info(f"Sitemap: discovered {added} new URLs from {len(locs)} entries")
            else:
                logger.info(f"Sitemap not accessible: {sitemap_url}")
        except Exception as e:
            logger.info(f"Sitemap fetch failed: {e}")
        finally:
            await page.close()

    # ------------------------------------------------------------------
    # URL validation
    # ------------------------------------------------------------------
    def _is_valid_url(self, url: str) -> bool:
        parsed = urlparse(url)
        norm = self._normalize(url)

        if parsed.scheme not in ('http', 'https'):
            return False
        if self.allow_subdomains:
            candidate_host = parsed.hostname or parsed.netloc
            if not candidate_host.endswith(self.root_domain):
                return False
        else:
            if parsed.netloc != self.base_domain:
                return False
        if norm in self.visited:
            return False

        # Extension check
        path_lower = parsed.path.lower()
        if any(path_lower.endswith(ext) for ext in SKIP_EXTENSIONS):
            return False

        # Exclude patterns
        for pat in self.exclude_re:
            if pat.search(url):
                return False

        # Include patterns (if set, URL must match at least one)
        if self.include_re:
            if not any(pat.search(url) for pat in self.include_re):
                return False

        return True

    # ------------------------------------------------------------------
    # Page interaction: dismiss popups / cookie banners
    # ------------------------------------------------------------------
    async def _dismiss_popups(self, page):
        """Dismiss cookie consent banners, modal overlays, and notification
        bars that may block content extraction or scrolling."""
        # 1. Try clicking common "Accept" / "Close" buttons
        dismiss_selectors = [
            # Cookie consent
            'button:has-text("Accept")',
            'button:has-text("Accept all")',
            'button:has-text("Accept All")',
            'button:has-text("Accepter")',
            'button:has-text("Tout accepter")',
            'button:has-text("I agree")',
            'button:has-text("Got it")',
            'button:has-text("OK")',
            'button:has-text("Agree")',
            'button:has-text("Allow all")',
            'button:has-text("Allow All")',
            'button:has-text("Continue")',
            '[id*="cookie"] button',
            '[class*="cookie"] button',
            '[id*="consent"] button',
            '[class*="consent"] button',
            '[id*="gdpr"] button',
            '[class*="gdpr"] button',
            '#onetrust-accept-btn-handler',
            '.cc-accept',
            '.cc-dismiss',
            '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
            # Generic close / dismiss for modals & banners
            '[class*="modal"] [class*="close"]',
            '[class*="popup"] [class*="close"]',
            '[class*="banner"] [class*="close"]',
            '[class*="overlay"] [class*="close"]',
            '[class*="notification"] [class*="close"]',
            'button[aria-label="Close"]',
            'button[aria-label="Dismiss"]',
            'button[aria-label="close"]',
        ]
        for selector in dismiss_selectors:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=400):
                    await btn.click(timeout=2000)
                    await asyncio.sleep(0.3)
                    logger.debug(f"  → Dismissed popup via: {selector}")
                    break  # Usually one banner/modal at a time
            except Exception:
                continue

        # 2. Remove fixed/sticky overlays via JS so they don't block scrolling
        try:
            await page.evaluate("""
                document.querySelectorAll(
                    '[class*="overlay"], [class*="modal"], [class*="cookie"], '
                  + '[class*="consent"], [class*="gdpr"], [class*="popup"]'
                ).forEach(el => {
                    const style = window.getComputedStyle(el);
                    if (style.position === 'fixed' || style.position === 'sticky') {
                        el.remove();
                    }
                });
                // Unlock body scroll if it was locked
                document.body.style.overflow = 'auto';
                document.documentElement.style.overflow = 'auto';
            """)
        except Exception as e:
            logger.debug(f"  → Could not unlock body scroll: {e}")

    # ------------------------------------------------------------------
    # Page interaction: auto-scroll for lazy-loaded content
    # ------------------------------------------------------------------
    async def _auto_scroll(self, page, max_scrolls: int = 15, scroll_delay: float = 0.5):
        """Scroll to the bottom of the page incrementally to trigger
        lazy-loading / infinite-scroll content."""
        try:
            previous_height = await page.evaluate('document.body.scrollHeight')
            for i in range(max_scrolls):
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                await asyncio.sleep(scroll_delay)
                new_height = await page.evaluate('document.body.scrollHeight')
                if new_height == previous_height:
                    logger.debug(f"  → Scroll stopped at iteration {i + 1} (no new content)")
                    break
                previous_height = new_height
            # Scroll back to top so link discovery sees the full DOM
            await page.evaluate('window.scrollTo(0, 0)')
        except Exception as e:
            logger.debug(f"  → Auto-scroll error (non-fatal): {e}")

    # ------------------------------------------------------------------
    # Page interaction: expand hidden content
    # ------------------------------------------------------------------
    async def _expand_hidden_content(self, page):
        """Click 'Load more' buttons, open accordions / tabs / details
        elements to reveal hidden content before extraction."""
        # 1. Open all <details> elements
        try:
            await page.evaluate("""
                document.querySelectorAll('details:not([open])').forEach(d => d.setAttribute('open', ''));
            """)
        except Exception as e:
            logger.debug(f"  → Could not open details elements: {e}")

        # 2. Click "load more" / "show more" / "voir plus" buttons
        load_more_selectors = [
            'button:has-text("load more")',
            'button:has-text("show more")',
            'button:has-text("voir plus")',
            'button:has-text("read more")',
            'a:has-text("load more")',
            'a:has-text("show more")',
            '[class*="load-more"]',
            '[class*="show-more"]',
            '[data-action="load-more"]',
        ]
        for selector in load_more_selectors:
            try:
                # Click up to 5 times in case there are multiple "load more"
                for _ in range(5):
                    btn = page.locator(selector).first
                    if await btn.is_visible(timeout=500):
                        await btn.click(timeout=2000)
                        await asyncio.sleep(0.8)
                    else:
                        break
            except Exception:
                continue

        # 3. Click accordion / collapsible triggers
        accordion_selectors = [
            '[class*="accordion"] [class*="header"]',
            '[class*="accordion"] button',
            '[class*="collapse"] [data-toggle]',
            '[class*="expandable"] button',
            '[data-bs-toggle="collapse"]',
            '[data-toggle="collapse"]',
        ]
        for selector in accordion_selectors:
            try:
                elements = page.locator(selector)
                count = await elements.count()
                for i in range(min(count, 20)):  # limit to 20 clicks
                    try:
                        el = elements.nth(i)
                        if await el.is_visible(timeout=300):
                            await el.click(timeout=1000)
                            await asyncio.sleep(0.3)
                    except Exception:
                        continue
            except Exception:
                continue

        # 4. Click all tab triggers to load tab content into DOM,
        #    then FORCE-SHOW all tab panels so every panel is visible
        #    simultaneously (clicking tabs normally hides previous panels).
        tab_selectors = [
            '[role="tab"]',
            '.nav-tabs a',
            '.tab-link',
            '[data-bs-toggle="tab"]',
        ]
        for selector in tab_selectors:
            try:
                tabs = page.locator(selector)
                count = await tabs.count()
                for i in range(min(count, 10)):
                    try:
                        tab = tabs.nth(i)
                        if await tab.is_visible(timeout=300):
                            await tab.click(timeout=1000)
                            await asyncio.sleep(0.4)
                    except Exception:
                        continue
            except Exception:
                continue

        # 4.5 Hover and Click dropdown toggles
        dropdown_selectors = [
            'button[aria-haspopup="true"]',
            'button[aria-controls]',
            '[class*="dropdown"] button',
            'button.nav-link',
            '[data-bs-toggle="dropdown"]',
            '[data-toggle="dropdown"]',
            '.nav-dropdown button'
        ]
        for selector in dropdown_selectors:
            try:
                elements = page.locator(selector)
                count = await elements.count()
                for i in range(min(count, 20)):
                    try:
                        el = elements.nth(i)
                        if await el.is_visible(timeout=300):
                            try:
                                await el.hover(timeout=1000)
                                await asyncio.sleep(0.3)
                            except Exception:
                                pass
                            try:
                                await el.click(timeout=1000)
                                await asyncio.sleep(0.3)
                            except Exception:
                                pass
                    except Exception:
                        continue
            except Exception:
                continue

        # 5. Force-show ALL hidden tab panels, collapsed sections,
        #    and any content hidden via CSS (display:none, visibility:hidden,
        #    height:0, opacity:0, or off-screen clipping).
        try:
            await page.evaluate("""
                (() => {
                    // --- Force-show tab panels ---
                    const tabPanelSelectors = [
                        '[role="tabpanel"]',
                        '.tab-pane',
                        '.tab-content > *',
                        '[class*="tab-panel"]',
                        '[data-tab-content]',
                        '[class*="tabcontent"]',
                    ];
                    tabPanelSelectors.forEach(sel => {
                        document.querySelectorAll(sel).forEach(el => {
                            el.style.setProperty('display', 'block', 'important');
                            el.style.setProperty('visibility', 'visible', 'important');
                            el.style.setProperty('opacity', '1', 'important');
                            el.style.setProperty('height', 'auto', 'important');
                            el.style.setProperty('overflow', 'visible', 'important');
                            el.style.setProperty('position', 'static', 'important');
                            el.classList.remove('hide', 'hidden', 'fade', 'collapse');
                            el.classList.add('show', 'active', 'in');
                            el.removeAttribute('hidden');
                            el.removeAttribute('aria-hidden');
                        });
                    });

                    // --- Force-show collapsed / accordion content ---
                    const collapsibleSelectors = [
                        '.collapse',
                        '.accordion-collapse',
                        '[class*="collapsible"]',
                        '[class*="expandable"]',
                        '.panel-collapse',
                        '[class*="accordion-body"]',
                        '[class*="accordion-content"]',
                    ];
                    collapsibleSelectors.forEach(sel => {
                        document.querySelectorAll(sel).forEach(el => {
                            el.style.setProperty('display', 'block', 'important');
                            el.style.setProperty('visibility', 'visible', 'important');
                            el.style.setProperty('height', 'auto', 'important');
                            el.style.setProperty('overflow', 'visible', 'important');
                            el.style.setProperty('opacity', '1', 'important');
                            el.classList.remove('hide', 'hidden', 'collapse');
                            el.classList.add('show', 'in');
                            el.removeAttribute('hidden');
                        });
                    });

                    // --- Force-show any element hidden by common CSS patterns ---
                    // Scan all elements and unhide those with display:none or
                    // visibility:hidden that are likely content containers.
                    const contentTags = new Set([
                        'DIV', 'SECTION', 'ARTICLE', 'UL', 'OL', 'DL',
                        'TABLE', 'P', 'SPAN', 'MAIN', 'ASIDE',
                    ]);
                    document.querySelectorAll('*').forEach(el => {
                        if (!contentTags.has(el.tagName)) return;
                        const cs = window.getComputedStyle(el);
                        if (cs.display === 'none' || cs.visibility === 'hidden') {
                            // Only unhide if it has meaningful text content
                            // (avoids showing modal backdrops, overlays, etc.)
                            const text = el.textContent || '';
                            if (text.trim().length > 20) {
                                el.style.setProperty('display', 'block', 'important');
                                el.style.setProperty('visibility', 'visible', 'important');
                                el.style.setProperty('opacity', '1', 'important');
                                el.style.setProperty('height', 'auto', 'important');
                                el.style.setProperty('position', 'static', 'important');
                            }
                        }
                    });
                })();
            """)
            logger.debug("  → Force-showed all hidden content panels")
        except Exception as e:
            logger.debug(f"  → Force-show hidden content failed (non-fatal): {e}")

        # Small pause for content to render after interactions
        await asyncio.sleep(0.5)

    # ------------------------------------------------------------------
    # Page interaction: flatten Shadow DOM for Web Components
    # ------------------------------------------------------------------
    async def _flatten_shadow_dom(self, page):
        """Flatten Shadow DOM content into the light DOM so BeautifulSoup
        can see Web Components' internal content."""
        try:
            await page.evaluate("""
                (function flattenShadowRoots(root) {
                    const walker = document.createTreeWalker(
                        root, NodeFilter.SHOW_ELEMENT, null, false
                    );
                    const hosts = [];
                    let node;
                    while (node = walker.nextNode()) {
                        if (node.shadowRoot) {
                            hosts.push(node);
                        }
                    }
                    for (const host of hosts) {
                        flattenShadowRoots(host.shadowRoot);
                        const fragment = document.createDocumentFragment();
                        while (host.shadowRoot.firstChild) {
                            fragment.appendChild(host.shadowRoot.firstChild);
                        }
                        host.appendChild(fragment);
                    }
                })(document);
            """)
        except Exception as e:
            logger.debug(f"  → Shadow DOM flatten failed (non-fatal): {e}")

    # ------------------------------------------------------------------
    # Structured content extraction → Markdown-like output
    # ------------------------------------------------------------------
    def _extract_content(self, soup: BeautifulSoup):
        """Extract ALL visible content from the page and return it in a
        structured, markdown-like format for optimal RAG ingestion.
        Returns (clean_text, extra_metadata)."""
        metadata = {}

        # ---- Metadata extraction ----
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc:
            metadata['description'] = meta_desc.get('content', '')
        og_title = soup.find('meta', attrs={'property': 'og:title'})
        if og_title:
            metadata['og_title'] = og_title.get('content', '')
        og_desc = soup.find('meta', attrs={'property': 'og:description'})
        if og_desc:
            metadata['og_description'] = og_desc.get('content', '')
        canonical = soup.find('link', attrs={'rel': 'canonical'})
        if canonical:
            metadata['canonical_url'] = canonical.get('href', '')

        # ---- Publication date extraction (for blog posts) ----
        # Try standard HTML meta tags first
        for attr_name, attr_val in [
            ('property', 'article:published_time'),
            ('property', 'og:updated_time'),
            ('name', 'pubdate'),
            ('name', 'publish_date'),
            ('name', 'date'),
        ]:
            tag = soup.find('meta', attrs={attr_name: attr_val})
            if tag and tag.get('content', '').strip():
                metadata['published_date'] = tag.get('content', '').strip()[:10]  # keep YYYY-MM-DD
                break
        # Try <time datetime="…"> tag
        if 'published_date' not in metadata:
            time_tag = soup.find('time', attrs={'datetime': True})
            if time_tag:
                dt = time_tag.get('datetime', '').strip()
                if dt:
                    metadata['published_date'] = dt[:10]
        # Try JSON-LD structured data
        if 'published_date' not in metadata:
            import json as _json
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    ld = _json.loads(script.string or '{}')
                    items = ld if isinstance(ld, list) else [ld]
                    for item in items:
                        if isinstance(item, dict):
                            date = item.get('datePublished') or item.get('dateCreated')
                            if date:
                                metadata['published_date'] = str(date)[:10]
                                break
                    if 'published_date' in metadata:
                        break
                except Exception:
                    pass

        # ---- Extract nav link labels as context (not skipped for text) ----
        nav_labels = []
        for nav in soup.find_all('nav'):
            for a_tag in nav.find_all('a'):
                label = a_tag.get_text(strip=True)
                if label and len(label) > 1:
                    nav_labels.append(label)

        # ---- Remove non-content elements ----
        for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
            tag.decompose()
        # Remove hidden elements
        for tag in soup.find_all(attrs={'aria-hidden': 'true'}):
            tag.decompose()
        for tag in soup.find_all(style=re.compile(r'display\s*:\s*none')):
            tag.decompose()

        # ---- Pick the best content container ----
        main = (
            soup.find('main')
            or soup.find('article')
            or soup.find('div', attrs={'role': 'main'})
            or soup.find('div', id=re.compile(r'content|main', re.I))
            or soup.find('div', class_=re.compile(r'content|main|page', re.I))
            or soup.find('div', id='root')       # React SPA common root
            or soup.find('div', id='app')         # Vue SPA common root
            or soup.find('div', id='__next')      # Next.js root
            or soup.find('div', id='__nuxt')      # Nuxt.js root
            or soup.find('body')
            or soup
        )

        # ---- Recursive element-to-markdown conversion ----
        lines = self._element_to_markdown(main)

        # ---- Append nav labels as site navigation context ----
        if nav_labels:
            lines.append('')
            lines.append('Navigation du site : ' + ' | '.join(dict.fromkeys(nav_labels)))

        clean_text = '\n'.join(lines).strip()

        # Remove excessive blank lines (3+ → 2)
        clean_text = re.sub(r'\n{3,}', '\n\n', clean_text)

        return clean_text, metadata

    def _inline_to_markdown(self, element) -> str:
        """Convert inline children of a block element to a single markdown
        string, preserving inline formatting (links, bold, italic, code, spans).
        Fixes the inline text mixing issue where <p>text <a>link</a> more</p>
        was incorrectly split into separate lines."""
        from bs4 import NavigableString, Tag

        parts: list[str] = []
        for child in element.children:
            if isinstance(child, NavigableString):
                text = str(child)
                text = re.sub(r'\s+', ' ', text)
                if text:
                    parts.append(text)
            elif isinstance(child, Tag):
                tag = child.name
                if tag == 'br':
                    parts.append('\n')
                elif tag in ('strong', 'b'):
                    t = re.sub(r'\s+', ' ', child.get_text()).strip()
                    if t:
                        parts.append(f'**{t}**')
                elif tag in ('em', 'i'):
                    t = re.sub(r'\s+', ' ', child.get_text()).strip()
                    if t:
                        parts.append(f'*{t}*')
                elif tag == 'code':
                    t = child.get_text().strip()
                    if t:
                        parts.append(f'`{t}`')
                elif tag == 'a':
                    t = re.sub(r'\s+', ' ', child.get_text()).strip()
                    href = child.get('href', '')
                    if t:
                        if href and not href.startswith(('#', 'javascript:')):
                            parts.append(f'[{t}]({href})')
                        else:
                            parts.append(t)
                elif tag == 'img':
                    alt = child.get('alt', '').strip()
                    if alt:
                        parts.append(f'[Image: {alt}]')
                elif tag in ('span', 'abbr', 'mark', 'small', 'sub', 'sup', 'time'):
                    t = re.sub(r'\s+', ' ', child.get_text())
                    if t.strip():
                        parts.append(t)
                else:
                    # Unknown inline tag: get text
                    t = re.sub(r'\s+', ' ', child.get_text())
                    if t.strip():
                        parts.append(t)
        return ''.join(parts).strip()

    def _list_to_markdown(self, list_tag, indent: int = 0) -> list[str]:
        """Convert a <ul> or <ol> to markdown with proper nesting and
        indentation for sub-lists."""
        from bs4 import Tag

        lines: list[str] = []
        prefix = '  ' * indent
        is_ordered = list_tag.name == 'ol'

        for idx, li in enumerate(list_tag.find_all('li', recursive=False), 1):
            # Separate inline text from nested sub-lists
            text_parts: list[str] = []
            nested_lists: list = []
            for child in li.children:
                if isinstance(child, Tag) and child.name in ('ul', 'ol'):
                    nested_lists.append(child)
                elif isinstance(child, Tag):
                    t = child.get_text(strip=True)
                    if t:
                        text_parts.append(t)
                else:
                    t = str(child).strip()
                    if t:
                        text_parts.append(t)

            li_text = ' '.join(text_parts)
            bullet = f'{prefix}{idx}. ' if is_ordered else f'{prefix}• '
            if li_text:
                lines.append(f'{bullet}{li_text}')

            # Recurse into nested sub-lists with increased indent
            for nested in nested_lists:
                lines.extend(self._list_to_markdown(nested, indent + 1))

        return lines

    def _element_to_markdown(self, element) -> list[str]:
        """Recursively convert a BeautifulSoup element tree to
        a list of markdown-formatted text lines."""
        from bs4 import NavigableString, Tag

        lines: list[str] = []

        for child in element.children:
            if isinstance(child, NavigableString):
                text = child.strip()
                if text:
                    lines.append(text)
                continue

            if not isinstance(child, Tag):
                continue

            tag = child.name

            # --- Headings ---
            if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                level = int(tag[1])
                prefix = '#' * level
                heading_text = child.get_text(strip=True)
                if heading_text:
                    lines.append('')
                    lines.append(f'{prefix} {heading_text}')
                    lines.append('')
                continue

            # --- Paragraphs (inline-aware) ---
            if tag == 'p':
                p_text = self._inline_to_markdown(child)
                if p_text:
                    lines.append(p_text)
                    lines.append('')
                continue

            # --- Line breaks ---
            if tag == 'br':
                lines.append('')
                continue

            # --- Horizontal rule ---
            if tag == 'hr':
                lines.append('')
                lines.append('---')
                lines.append('')
                continue

            # --- Lists (with nesting support) ---
            if tag in ('ul', 'ol'):
                list_lines = self._list_to_markdown(child)
                lines.extend(list_lines)
                lines.append('')
                continue

            # --- Definition lists ---
            if tag == 'dl':
                for dt_dd in child.children:
                    if isinstance(dt_dd, Tag):
                        if dt_dd.name == 'dt':
                            lines.append(f'**{dt_dd.get_text(strip=True)}**')
                        elif dt_dd.name == 'dd':
                            lines.append(f'  {dt_dd.get_text(strip=True)}')
                lines.append('')
                continue

            # --- Blockquotes ---
            if tag == 'blockquote':
                bq_text = child.get_text(strip=True)
                if bq_text:
                    for bq_line in bq_text.splitlines():
                        lines.append(f'> {bq_line.strip()}')
                    lines.append('')
                continue

            # --- Code blocks ---
            if tag == 'pre':
                code = child.find('code')
                code_text = (code or child).get_text()
                if code_text.strip():
                    lines.append('')
                    lines.append('```')
                    lines.append(code_text.rstrip())
                    lines.append('```')
                    lines.append('')
                continue

            # --- Inline code ---
            if tag == 'code' and (not child.parent or child.parent.name != 'pre'):
                code_text = child.get_text(strip=True)
                if code_text:
                    lines.append(f'`{code_text}`')
                continue

            # --- Tables ---
            if tag == 'table':
                table_lines = self._table_to_markdown(child)
                if table_lines:
                    lines.append('')
                    lines.extend(table_lines)
                    lines.append('')
                continue

            # --- Images (extract alt text) ---
            if tag == 'img':
                alt = child.get('alt', '').strip()
                if alt and len(alt) > 3:  # skip trivial alt like "."
                    lines.append(f'[Image: {alt}]')
                continue

            # --- Details / Summary ---
            if tag == 'details':
                summary = child.find('summary')
                if summary:
                    lines.append(f'**{summary.get_text(strip=True)}**')
                inner = self._element_to_markdown(child)
                lines.extend(inner)
                lines.append('')
                continue

            # --- Figure + figcaption ---
            if tag == 'figure':
                img = child.find('img')
                if img:
                    alt = img.get('alt', '').strip()
                    if alt:
                        lines.append(f'[Image: {alt}]')
                caption = child.find('figcaption')
                if caption:
                    lines.append(f'Caption: {caption.get_text(strip=True)}')
                lines.append('')
                continue

            # --- Semantic emphasis ---
            if tag in ('strong', 'b'):
                t = child.get_text(strip=True)
                if t:
                    lines.append(f'**{t}**')
                continue
            if tag in ('em', 'i'):
                t = child.get_text(strip=True)
                if t:
                    lines.append(f'*{t}*')
                continue

            # --- Buttons: extract text (category tabs, toggles, etc.) ---
            if tag == 'button':
                btn_text = child.get_text(strip=True)
                if btn_text and len(btn_text) > 1:
                    lines.append(btn_text)
                continue

            # --- Skip form elements only ---
            if tag in ('input', 'select', 'textarea', 'label', 'form'):
                continue

            # --- Nav / Header / Footer / Aside: recurse into them
            #     to capture menu listings, sidebar content, etc. ---
            if tag in ('nav', 'header', 'footer', 'aside'):
                inner = self._element_to_markdown(child)
                # Only include if there's substantial content (not just links)
                inner_text = '\n'.join(inner).strip()
                if len(inner_text) > 50:
                    lines.extend(inner)
                continue

            # --- Generic container (div, span, section, etc.) → recurse ---
            inner = self._element_to_markdown(child)
            lines.extend(inner)

        return lines

    def _table_to_markdown(self, table_tag) -> list[str]:
        """Convert an HTML <table> to markdown table lines."""
        rows = []
        for tr in table_tag.find_all('tr'):
            cells = []
            for cell in tr.find_all(['th', 'td']):
                cells.append(cell.get_text(strip=True).replace('|', '\\|'))
            if cells:
                rows.append(cells)

        if not rows:
            return []

        # Normalise column count
        max_cols = max(len(r) for r in rows)
        for r in rows:
            while len(r) < max_cols:
                r.append('')

        result = []
        # First row as header
        result.append('| ' + ' | '.join(rows[0]) + ' |')
        result.append('| ' + ' | '.join(['---'] * max_cols) + ' |')
        for row in rows[1:]:
            result.append('| ' + ' | '.join(row) + ' |')

        return result

    # ------------------------------------------------------------------
    # Link discovery (enhanced)
    # ------------------------------------------------------------------
    def _discover_links(self, soup: BeautifulSoup, source_url: str, current_depth: int):
        """Extract and enqueue valid links from a page.
        Covers: <a href>, <link rel>, pagination patterns."""
        if current_depth + 1 > self.max_depth:
            return

        discovered = 0
        next_depth = current_depth + 1

        # 1. Standard <a href> links
        for link in soup.find_all('a', href=True):
            href = link['href']
            discovered += self._enqueue_url(href, source_url, next_depth)

        # 2. <link rel="next/prev/canonical"> for pagination / canonical
        for rel_val in ('next', 'prev', 'canonical'):
            link_tag = soup.find('link', attrs={'rel': rel_val, 'href': True})
            if link_tag:
                discovered += self._enqueue_url(link_tag['href'], source_url, next_depth)

        # 3. Pagination patterns:  ?page=N, /page/N
        #    If we find page=1 or /page/1 style links, generate page 2..10
        pagination_re = re.compile(r'(\?|&)page=(\d+)', re.IGNORECASE)
        path_page_re = re.compile(r'/page/(\d+)', re.IGNORECASE)
        for link in soup.find_all('a', href=True):
            href = link['href']
            full = urljoin(source_url, href)

            m = pagination_re.search(full)
            if m:
                base = pagination_re.sub(f'{m.group(1)}page=', full)
                for pg in range(1, 11):
                    candidate = f'{base}{pg}'
                    discovered += self._enqueue_url(candidate, source_url, next_depth)
                continue

            m = path_page_re.search(full)
            if m:
                base = path_page_re.sub('/page/', full)
                for pg in range(1, 11):
                    candidate = f'{base}{pg}'
                    discovered += self._enqueue_url(candidate, source_url, next_depth)

        if discovered:
            logger.info(f"  → Discovered {discovered} new links (depth {next_depth})")

    async def _discover_js_links(self, page, source_url: str, current_depth: int):
        """Extract links rendered by JavaScript (React Router, Next.js, etc.)
        by querying the live DOM instead of the raw HTML."""
        if current_depth + 1 > self.max_depth:
            return
        try:
            js_code = (
                "() => {"
                "  const links = new Set(window.__crawled_links || []);"
                "  document.querySelectorAll('a[href]').forEach(a => links.add(a.href));"
                "  document.querySelectorAll('[onclick]').forEach(el => {"
                "    const m = el.getAttribute('onclick').match(/[\"'](https?:[/][/][^\"']+)[\"']/);"
                "    if (m) links.add(m[1]);"
                "  });"
                "  document.querySelectorAll('[data-href]').forEach(el => links.add(el.getAttribute('data-href')));"
                "  return [...links];"
                "}"
            )
            js_hrefs = await page.evaluate(js_code)
            discovered = 0
            for href in js_hrefs:
                discovered += self._enqueue_url(href, source_url, current_depth + 1)
            if discovered:
                logger.info(f"  → JS discovery: {discovered} additional links")
        except Exception as e:
            logger.debug(f"  → JS link discovery failed (non-fatal): {e}")

    def _enqueue_url(self, href: str, source_url: str, depth: int) -> int:
        """Resolve, validate, and enqueue a URL. Returns 1 if added, 0 otherwise."""
        full_url = urljoin(source_url, href)
        norm = self._normalize(full_url)
        if norm in self.queued_set:
            return 0
        if not self._is_valid_url(full_url):
            return 0
        self.queue.append((full_url, depth))
        self.queued_set.add(norm)
        return 1

    # ------------------------------------------------------------------
    # Process a single URL (with retry)
    # ------------------------------------------------------------------
    async def _process_url(self, context, sem: asyncio.Semaphore, url: str, depth: int):
        """Process one URL using its own tab, guarded by semaphore."""
        norm = self._normalize(url)

        async with sem:
            # Double-check visited (another coroutine may have added it)
            if norm in self.visited or (self.max_pages is not None and len(self.visited) >= self.max_pages):
                return
            self.visited.add(norm)
            
            # ── Check smart rescrape policy (only during auto-rescrape, not force crawl) ──
            if not self.force_rescrape and self._db is not None:
                from services.smart_rescrape_service import smart_rescrape_engine
                db = self._db

                # Check if page exists and has rescrape policy
                existing_page = db.scraped_pages.find_one({
                    "chatbot_id": self.chatbot_id,
                    "url": url
                })

                if existing_page:
                    # Get chatbot config for rescrape engine
                    config = db.chatbot_configs.find_one({"_id": ObjectId(self.chatbot_id)})

                    if config:
                        excluded_patterns = config.get("excluded_url_patterns", [])

                        # Check if page matches exclusion patterns
                        if excluded_patterns and smart_rescrape_engine._matches_pattern(url, excluded_patterns):
                            logger.info(f"Skipped (matches exclusion pattern): {url}")
                            self.stats["pages_skipped"] += 1
                            return

                        # Check if page is pinned (never rescrape)
                        rescrape_policy = existing_page.get("rescrape_policy", {})
                        if rescrape_policy.get("is_pinned", False):
                            logger.info(f"Skipped (pinned): {url}")
                            self.stats["pages_skipped"] += 1
                            return

                        # Check if should rescrape based on smart engine logic.
                        # Fail-safe: if the smart check errors (e.g. ETag HEAD
                        # request fails), default to scraping rather than skipping.
                        try:
                            should_scrape, reason = await smart_rescrape_engine.should_rescrape_page(
                                existing_page,
                                config
                            )
                        except Exception as _se:
                            logger.warning(f"Smart re-scrape check failed for {url}; scraping anyway: {_se}")
                            should_scrape, reason = True, "smart-check-error"
                        if not should_scrape:
                            logger.info(f"Skipped ({reason}): {url}")
                            self.stats["pages_skipped"] += 1
                            # Record skip reason
                            db.scraped_pages.update_one(
                                {"chatbot_id": self.chatbot_id, "url": url},
                                {"$set": {"skip_reason": reason, "last_check": datetime.utcnow()}}
                            )
                            return

            if not self._can_fetch(url):
                logger.info(f"Blocked by robots.txt: {url}")
                self.stats["pages_skipped"] += 1
                return

            page = await context.new_page()
            try:
                await self._fetch_and_ingest(page, url, depth)
            finally:
                try:
                    await asyncio.wait_for(page.close(), timeout=180.0)
                except Exception as e:
                    logger.debug(f"  → Ignored timeout on page.close for {url}: {e}")
                # Polite delay
                if self.delay > 0:
                    await asyncio.sleep(self.delay)

    async def _fetch_and_ingest(self, page, url: str, depth: int):
        """Navigate, scroll, expand, extract, deduplicate, and ingest.
        Retries on failure with exponential backoff."""
        for attempt in range(1, self.retry_attempts + 1):
            try:
                count = len(self.visited) + 1  # +1: current URL is not yet in visited
                inf_sym = '\u221E'
                logger.info(f"[{count}/{self.max_pages or inf_sym}] (depth={depth}, attempt={attempt}) {url}")

                # SSRF guard: discovered links are not re-validated like the seed
                # was, so refuse anything that resolves to an internal/private IP.
                if not is_safe_public_url(url):
                    logger.warning(f"  \u2192 SSRF guard: refusing non-public URL: {url}")
                    self.stats["pages_skipped"] += 1
                    return

                response = await page.goto(url, wait_until='domcontentloaded', timeout=self.page_timeout)
                
                if response and response.status >= 400:
                    logger.warning(f"  → HTTP {response.status} Error - Page skipped: {url}")
                    self.stats["pages_skipped"] += 1
                    # Do not retry on 404 or other client errors, but maybe 500? 
                    # For safety, let's just abort this url if it throws HTTP >= 400
                    if response.status == 404 or response.status == 410:
                        return
                    else:
                        raise Exception(f"HTTP {response.status}")

                # Wait for JS-rendered content (SPA/React/Next.js)
                try:
                    await page.wait_for_load_state('networkidle', timeout=10_000)
                except Exception:
                    pass  # not all pages fire networkidle; continue

                try:
                    await page.wait_for_selector('body *', timeout=DEFAULT_SELECTOR_TIMEOUT)
                except Exception:
                    pass

                # ── NEW: Dismiss popups / cookie banners ──
                await self._dismiss_popups(page)

                # ── NEW: Capture dynamically mounted links ──
                try:
                    await page.evaluate("""
                        window.__crawled_links = new Set();
                        window.__recordLinks = () => {
                            document.querySelectorAll('a[href]').forEach(a => {
                                if(a.href && !a.href.startsWith('javascript:')) {
                                    window.__crawled_links.add(a.href);
                                }
                            });
                        };
                        window.__recordLinks(); // initial
                        if (!window.__linkObserver) {
                            window.__linkObserver = new MutationObserver(() => window.__recordLinks());
                            window.__linkObserver.observe(document.body, { childList: true, subtree: true });
                        }
                    """)
                except Exception as e:
                    logger.debug(f"  → Could not inject link observer: {e}")

                # ── NEW: Scroll to trigger lazy-loaded content ──
                await self._auto_scroll(page)

                # ── NEW: Expand hidden content (accordions, tabs, load-more) ──
                await self._expand_hidden_content(page)

                # ── NEW: Flatten Shadow DOM for Web Components ──
                await self._flatten_shadow_dom(page)

                # Get final rendered HTML after all interactions
                content = await page.content()
                title = await page.title()
                logger.info(f"  → '{title}' ({len(content)} chars HTML)")

                soup = BeautifulSoup(content, 'html.parser')

                # ── Link discovery (HTML + JS rendered) ──
                if self.use_auto_discovery:
                    self._discover_links(soup, url, depth)
                    await self._discover_js_links(page, url, depth)

                # ── Full structured content extraction ──
                clean_text, extra_meta = self._extract_content(soup)

                # ── Prepend page context header for RAG embedding ──
                # In SPAs, all pages may share the same <title>, so embedding
                # the URL path + title directly in the text helps the RAG
                # associate content with the correct page when queried.
                parsed_url = urlparse(url)
                url_path = parsed_url.path.strip('/') or 'accueil'
                page_label = url_path.replace('/', ' > ').replace('-', ' ').title()
                context_header = f"Page : {page_label}\nURL : {url}\n"
                if title:
                    context_header += f"Titre : {title}\n"
                context_header += "\n"
                clean_text = context_header + clean_text

                if len(clean_text) < 80:
                    logger.info(f"  → Skipped (content too short: {len(clean_text)} chars)")
                    self.stats["pages_skipped"] += 1
                    return
                    
                # ── Detect "Soft 404" (pages returning HTTP 200 but displaying "Page not found") ──
                lower_text = clean_text.lower()
                soft_404_phrases = [
                    "page introuvable",
                    "la page que vous recherchez n'existe pas",
                    "404 page not found",
                    "désolé, la page",
                    "cette page n'existe pas",
                    "page not found"
                ]
                # Combine title and beginning of text
                search_area = ((title or "").lower() + " " + lower_text[:1000])
                if any(phrase in search_area for phrase in soft_404_phrases):
                    logger.info(f"  → Skipped (Soft 404 detected): {url}")
                    self.stats["pages_skipped"] += 1
                    return

                # Debug: log first 300 chars of extracted content
                preview = clean_text[:300].replace('\n', '\\n')
                logger.info(f"  → Content preview: {preview}")

                # Content deduplication
                h = self._content_hash(clean_text)
                if h in self.seen_hashes:
                    logger.info(f"  → Skipped (duplicate content)")
                    self.stats["duplicates_skipped"] += 1
                    return
                self.seen_hashes.add(h)

                # ── Count the page as scraped immediately after HTML extraction ──
                # This is done BEFORE ingestion so that the frontend progress
                # counter reflects pages actually downloaded, not embedded.
                self.stats["pages_crawled"] += 1
                self.stats["content_ingested"] += len(clean_text)

                # Extract links and images for quality calculation
                links = [a.get('href') for a in soup.find_all('a', href=True)]
                images = [{"src": img.get('src', ''), "alt": img.get('alt', '')} 
                         for img in soup.find_all('img')]

                # Record the page in MongoDB for the details view (with content for hashing)
                await _record_scraped_page(self.chatbot_id, {
                    "url": url,
                    "title": title,
                    "char_count": len(clean_text),
                    "status": "success",
                    "depth": depth,
                    "content": clean_text,  # Pass content for hash calculation
                    "links": links,
                    "images": images,
                    "meta_description": extra_meta.get('description', ''),
                    "og_title": extra_meta.get('og_title', ''),
                    "og_description": extra_meta.get('og_description', ''),
                    "canonical_url": extra_meta.get('canonical_url', ''),
                    "h1": soup.find('h1').get_text(strip=True) if soup.find('h1') else ''
                })

                # Update progress in MongoDB every 5 pages
                if self.stats["pages_crawled"] % 5 == 0 or self.stats["pages_crawled"] == 1:
                    _update_crawl_status(self.chatbot_id, {
                        "crawl_pages_done": self.stats["pages_crawled"],
                        "crawl_current_url": url,
                    })

                # ── Prepare chunks for bulk ingestion (no embedding yet) ──
                meta = {"source": url, "title": title, **extra_meta}
                logger.info(f"  → Preparing chunks for {len(clean_text)} chars …")
                prepared = rag_service.prepare_document(clean_text, meta)
                self.pending_chunks.extend(prepared)
                logger.info(f"  → {len(prepared)} chunks prepared (total pending: {len(self.pending_chunks)})")

                return  # success → exit retry loop

            except Exception as e:
                if attempt < self.retry_attempts:
                    wait = 2 ** attempt
                    logger.warning(f"  → Attempt {attempt} failed ({e}), retrying in {wait}s …")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"  → Failed after {self.retry_attempts} attempts: {url} — {e}")
                    self.stats["pages_failed"] += 1

    # ------------------------------------------------------------------
    # Main run loop (concurrent)
    # ------------------------------------------------------------------
    async def run(self):
        logger.info(f"Starting crawl for {self.start_url} "
                     f"(max_pages={self.max_pages}, concurrency={self.concurrency}, "
                     f"max_depth={self.max_depth})")
        await self._init_robots_parser()

        # Single shared MongoDB connection for the entire crawl (reused per page)
        _mongo_client = None
        if not self.force_rescrape:
            from pymongo import MongoClient as _MongoClient
            _mongo_client = _MongoClient(
                os.getenv("MONGODB_URL", "mongodb://localhost:27017"),
                serverSelectionTimeoutMS=5000,
            )
            self._db = _mongo_client[os.getenv("DATABASE_NAME", "chatbot_db")]
        else:
            self._db = None

        async with async_playwright() as p:
            browser = await p.chromium.launch(args=[
                '--disable-gpu',
                '--no-sandbox',
                '--disable-dev-shm-usage',
            ])
            context = await browser.new_context(
                user_agent=self.user_agent,
                ignore_https_errors=True,
            )

            # Try sitemap first
            if self.use_auto_discovery:
                await self._discover_sitemap(context)

            sem = asyncio.Semaphore(self.concurrency)

            # Process queue in batches of `concurrency` size
            while self.queue and (self.max_pages is None or len(self.visited) < self.max_pages):
                # Grab a batch from the queue
                batch: list[tuple[str, int]] = []
                while self.queue and len(batch) < self.concurrency:
                    url, depth = self.queue.popleft()
                    norm = self._normalize(url)
                    if norm in self.visited or depth > self.max_depth:
                        continue
                    if self.max_pages is not None and len(self.visited) + len(batch) >= self.max_pages:
                        break
                    batch.append((url, depth))

                if not batch:
                    break

                # Run batch concurrently
                tasks = [
                    asyncio.create_task(self._process_url(context, sem, url, depth))
                    for url, depth in batch
                ]
                await asyncio.gather(*tasks, return_exceptions=True)

            try:
                await asyncio.wait_for(context.close(), timeout=180.0)
            except Exception as e:
                logger.debug(f"Ignored timeout on context.close: {e}")
                
            try:
                await asyncio.wait_for(browser.close(), timeout=180.0)
            except Exception as e:
                logger.debug(f"Ignored timeout on browser.close: {e}")

        # ── Build site index document to enable navigation queries ──
        # Example queries: "quels sont les blogs disponibles", "liste des articles"
        # Without this, the RAG has no way to list all available pages.
        if self.pending_chunks:
            # url → {title, date}
            pages_seen: dict[str, dict] = {}
            for doc in self.pending_chunks:
                meta = doc.get("metadata", {})
                url = meta.get("source", "")
                title = meta.get("title", "").strip()
                date = meta.get("published_date", "")
                if url and url not in pages_seen:
                    # Use URL path segment as fallback title when empty
                    if not title:
                        path_part = urlparse(url).path.strip("/").split("/")[-1]
                        title = path_part.replace("-", " ").replace("_", " ") or url
                    pages_seen[url] = {"title": title, "date": date}

            if pages_seen:
                base = self.base_domain

                # ── General site index (all sections) ──
                sections: dict[str, list[str]] = {}
                for url, info in sorted(pages_seen.items()):
                    path = urlparse(url).path.strip("/") or "accueil"
                    top = path.split("/")[0] if "/" in path else path
                    date_str = f" ({info['date']}" + ")" if info["date"] else ""
                    sections.setdefault(top or "accueil", []).append(
                        f"- {info['title']}{date_str} → {url}"
                    )

                # NOTE: no markdown # headings here — section-aware chunker would
                # create isolated empty header chunks which score near queries but
                # provide zero content to the LLM.  Use plain-text separators.
                index_lines = [
                    f"Répertoire complet des pages disponibles sur {base}.",
                    f"Ce site contient {len(pages_seen)} pages indexées.",
                    "",
                ]
                for section, entries in sorted(sections.items()):
                    index_lines.append(f"Section {section} :")
                    index_lines.extend(entries)
                    index_lines.append("")

                index_text = "\n".join(index_lines)
                index_meta = {
                    "source": f"https://{base}/",
                    "title": f"Répertoire des pages — {base}",
                    "is_site_index": True,
                }
                index_prepared = rag_service.prepare_document(index_text, index_meta)
                self.pending_chunks.extend(index_prepared)

                # ── Dedicated blog / news / actualités index ──
                # Detect blog-like sections by common path keywords
                BLOG_KEYWORDS = {"blog", "actualite", "actualités", "news", "article",
                                 "articles", "publication", "publications", "post",
                                 "posts", "insight", "insights", "press", "presse"}
                blog_entries: list[tuple[str, str, str]] = []  # (date, title, url)
                for url, info in pages_seen.items():
                    path = urlparse(url).path.strip("/") or ""
                    top = path.split("/")[0].lower() if path else ""
                    if top in BLOG_KEYWORDS or any(kw in path.lower() for kw in BLOG_KEYWORDS):
                        blog_entries.append((info["date"] or "", info["title"], url))

                if blog_entries:
                    # Sort by date descending (most recent first; empty dates go last)
                    blog_entries.sort(key=lambda x: x[0], reverse=True)
                    # NOTE: no markdown # headings — same reason as site index above.
                    # The preamble is repeated at the top of every chunk automatically
                    # by prepare_document so each chunk is self-contained.
                    preamble = (
                        f"Liste des articles de blog disponibles sur {base}. "
                        f"Derniers billets publiés, actualités récentes, publications du site "
                        f"({len(blog_entries)} articles au total) :"
                    )
                    blog_lines = [preamble, ""]
                    for date, title, url in blog_entries:
                        date_str = f" — publié le {date}" if date else ""
                        blog_lines.append(f"- {title}{date_str} → {url}")

                    blog_text = "\n".join(blog_lines)
                    blog_meta = {
                        "source": f"https://{base}/blog",
                        "title": f"Articles et publications — {base}",
                        "is_blog_index": True,
                    }
                    blog_prepared = rag_service.prepare_document(blog_text, blog_meta)
                    self.pending_chunks.extend(blog_prepared)
                    logger.info(
                        f"Blog index created: {len(blog_entries)} articles, "
                        f"{len(blog_prepared)} chunks added"
                    )

                logger.info(
                    f"Site index created: {len(pages_seen)} pages, "
                    f"{len(index_prepared)} general chunks added"
                )

        # ── Smart re-scrape: replace only the re-fetched pages' chunks ──
        # The whole collection was NOT wiped (unchanged pages were skipped and
        # must keep their existing chunks). Delete the old chunks of each
        # re-fetched source before re-adding the fresh ones, to avoid stale
        # duplicates. Pages that were skipped are left untouched in Chroma.
        if not self.force_rescrape and self.pending_chunks:
            sources = {
                c["metadata"]["source"]
                for c in self.pending_chunks
                if isinstance(c.get("metadata"), dict) and c["metadata"].get("source")
            }
            for src in sources:
                await asyncio.to_thread(rag_service.delete_by_source, self.chatbot_id, src)
            logger.info(
                f"Smart re-scrape: replaced chunks for {len(sources)} re-fetched source(s)."
            )

        # ── Bulk embed all accumulated chunks at once ──
        if self.pending_chunks:
            logger.info(
                f"All pages scraped. Bulk embedding {len(self.pending_chunks)} "
                f"chunks from {self.stats.get('pages_crawled', 0)} pages…"
            )
            try:
                chunk_count = await asyncio.to_thread(
                    rag_service.bulk_ingest, self.chatbot_id, self.pending_chunks
                )
                logger.info(f"Bulk ingestion complete: {chunk_count} chunks embedded and stored.")
            except Exception as e:
                logger.error(f"Bulk ingestion failed: {e}")
                self.stats["pages_ingestion_failed"] = len(self.pending_chunks)

        # Close the shared MongoDB connection now that the crawl is done
        if _mongo_client:
            _mongo_client.close()
        self._db = None

        logger.info(
            f"Crawl finished! Stats: {self.stats} | "
            f"Visited {len(self.visited)} unique URLs"
        )
        return self.stats


@shared_task(
    bind=True,
    name="services.crawler_service.crawl_website_task",
    soft_time_limit=int(os.getenv("CRAWL_SOFT_TIME_LIMIT", "3600")),  # 1h – triggers graceful shutdown
    time_limit=int(os.getenv("CRAWL_HARD_TIME_LIMIT", "7200")),       # 2h – SIGKILL
)
def crawl_website_task(self, chatbot_id: str, url: str, max_pages = None, url_list: list = None, force_rescrape: bool = True):
    """Celery task that runs the async crawler."""
    logger.info(f"Celery task started: Crawling {url} for chatbot {chatbot_id}")
    if url_list:
        logger.info(f"Using provided URL list with {len(url_list)} URLs")

    # ── Update crawl status to 'crawling' in MongoDB ──
    _update_crawl_status(chatbot_id, {
        "crawl_status": "crawling",
        "crawl_pages_done": 0,
        "crawl_pages_total": max_pages,
        "crawl_current_url": url,
        "crawl_started_at": datetime.utcnow(),
        "crawl_completed_at": None,
        "crawl_error": None,
    })

    # ── Clear old data before re-crawling ──
    # A FULL crawl (force_rescrape=True) rebuilds the whole website index from
    # scratch, so we wipe the old crawl chunks first to avoid stale/duplicates.
    # IMPORTANT: only the crawl/web chunks are cleared — user-uploaded documents
    # (metadata type == 'document') are KEPT. They are not part of the crawl, so
    # a full delete_collection() used to silently destroy them on every re-crawl.
    # A SMART re-scrape (force_rescrape=False) must NOT wipe here: it skips
    # unchanged pages, so a full wipe would drop those pages from the RAG index.
    # In that mode the crawler replaces only the re-fetched pages' chunks
    # (per-source) before bulk re-ingestion; unchanged pages keep their chunks.
    if force_rescrape:
        try:
            cleared = rag_service.clear_non_document_chunks(chatbot_id)
            if cleared:
                logger.info(f"Cleared old crawl chunks (kept uploaded docs) for chatbot {chatbot_id}")
            else:
                logger.info(f"No existing RAG collection to clear for chatbot {chatbot_id}")
        except Exception as e:
            logger.warning(f"Failed to clear old crawl chunks (continuing): {e}")
    else:
        logger.info(
            f"Smart re-scrape: keeping RAG collection for chatbot {chatbot_id} "
            f"(per-source replacement of re-fetched pages)"
        )

    # Fetch advanced config from DB
    from pymongo import MongoClient
    import os
    from bson import ObjectId
    mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DATABASE_NAME", "chatbot_db")
    
    exclude_patterns = None
    sitemap_url = None
    try:
        client = MongoClient(mongo_url)
        db = client[db_name]
        chatbot_config = db.chatbot_configs.find_one({"_id": ObjectId(chatbot_id)})
        if chatbot_config:
            exclude_patterns = chatbot_config.get("ignored_urls", None)
            sitemap_url = chatbot_config.get("sitemap_url", None)
        client.close()
    except Exception as e:
        logger.warning(f"Failed to fetch crawler config from MongoDB: {e}")

    # If sitemap_url is configured, use it as the start URL and enforce sitemap usage.
    # Otherwise use the default URL and default sitemap usage.
    actual_url = sitemap_url if sitemap_url else url
    use_sitemap = True if sitemap_url else True

    crawler = WebsiteCrawler(
        chatbot_id, 
        actual_url, 
        max_pages, 
        url_list=url_list,
        exclude_patterns=exclude_patterns,
        use_sitemap=use_sitemap,
        force_rescrape=force_rescrape
    )

    # Report progress via Celery state
    self.update_state(state='PROGRESS', meta={
        'current': 0, 'total': max_pages, 'status': 'Starting crawl…'
    })

    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        stats = asyncio.run(crawler.run())
        # Invalidate RAG cache once after the full crawl (not after every page)
        rag_service._cache.invalidate(chatbot_id)
        logger.info(f"RAG cache invalidated once after full crawl for chatbot {chatbot_id}")
    except SoftTimeLimitExceeded:
        # 1-hour soft limit hit — save whatever was crawled so far and mark as
        # completed_partial instead of failed, so the knowledge base is still usable.
        pages_done = crawler.stats.get("pages_crawled", 0)
        logger.warning(
            f"Soft time limit reached for chatbot {chatbot_id} after {pages_done} pages. "
            f"Saving partial results."
        )
        # Still invalidate cache so partial results are usable
        rag_service._cache.invalidate(chatbot_id)
        _update_crawl_status(chatbot_id, {
            "crawl_status": "completed",
            "crawl_pages_done": pages_done,
            "crawl_pages_total": pages_done,
            "crawl_current_url": None,
            "crawl_completed_at": datetime.utcnow(),
            "crawl_error": f"Time limit reached after {pages_done} pages (partial crawl saved)",
            "last_scraped_at": datetime.utcnow(),
        })
        return {
            "message": f"Crawl time limit reached — partial results saved ({pages_done} pages)",
            "stats": crawler.stats,
        }
    except Exception as e:
        logger.error(f"Crawl failed for chatbot {chatbot_id}: {e}")
        # Record crawl_failed_at so the auto-retry scheduler can apply the
        # right backoff (5 min → 30 min → 2 h between attempts).
        _update_crawl_status(chatbot_id, {
            "crawl_status": "failed",
            "crawl_error": str(e)[:500],
            "crawl_completed_at": datetime.utcnow(),
            "crawl_failed_at": datetime.utcnow(),
        })
        raise

    # ── Remove stale pages from MongoDB (pages that no longer exist on the site) ──
    # ChromaDB was already fully rebuilt, so scraped_pages must stay in sync.
    # Only meaningful after a FULL auto-discovery crawl: a url_list crawl only
    # visits the given URLs, and a crawl stopped by max_pages may not reach
    # still-existing pages — in both cases absence from `visited` does not
    # mean the page is gone from the site.
    _cap_reached = (crawler.max_pages is not None
                    and len(crawler.visited) >= crawler.max_pages)
    if crawler.use_auto_discovery and not _cap_reached:
        try:
            from pymongo import MongoClient
            _mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
            _db_name = os.getenv("DATABASE_NAME", "chatbot_db")
            _client = MongoClient(_mongo_url)
            _db = _client[_db_name]
            # crawler.visited holds every URL that was successfully attempted,
            # in NORMALIZED form (fragment stripped, no trailing slash), while
            # scraped_pages stores the raw url. Normalize before comparing —
            # otherwise every url ending in "/" is deleted right after being
            # saved (a raw ".../carte/" never matches the visited ".../carte").
            visited_urls = set(crawler.visited)
            if visited_urls:
                stale_ids = [
                    page["_id"]
                    for page in _db.scraped_pages.find(
                        {"chatbot_id": chatbot_id}, {"url": 1})
                    if crawler._normalize(page.get("url") or "") not in visited_urls
                ]
                if stale_ids:
                    result = _db.scraped_pages.delete_many({"_id": {"$in": stale_ids}})
                    logger.info(
                        f"Removed {result.deleted_count} stale page(s) from MongoDB "
                        f"for chatbot {chatbot_id} (no longer found during crawl)"
                    )
            _client.close()
        except Exception as e:
            logger.warning(f"Failed to remove stale pages from MongoDB: {e}")

    # ── Update crawl status to 'completed' in MongoDB ──
    _update_crawl_status(chatbot_id, {
        "crawl_status": "completed",
        "crawl_pages_done": stats.get("pages_crawled", 0),
        "crawl_pages_total": stats.get("pages_crawled", 0),
        "crawl_current_url": None,
        "crawl_completed_at": datetime.utcnow(),
        "crawl_error": None,
        "last_scraped_at": datetime.utcnow(),
    })

    self.update_state(state='PROGRESS', meta={
        'current': stats.get("pages_crawled", 0),
        'total': max_pages,
        'status': 'Completed',
        'stats': stats,
    })

    return {
        "message": f"Finished crawling {url}",
        "stats": stats,
    }


def _update_crawl_status(chatbot_id: str, fields: dict):
    """Synchronously update crawl status fields in MongoDB (for use inside Celery tasks)."""
    from pymongo import MongoClient
    import os
    mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DATABASE_NAME", "chatbot_db")
    try:
        client = MongoClient(mongo_url)
        db = client[db_name]
        db.chatbot_configs.update_one(
            {"_id": ObjectId(chatbot_id)},
            {"$set": fields}
        )
        client.close()
    except Exception as e:
        logger.warning(f"Failed to update crawl status in MongoDB: {e}")

async def _record_scraped_page(chatbot_id: str, fields: dict):
    """Record a scraped page in MongoDB using smart rescrape service."""
    from pymongo import MongoClient
    from services.smart_rescrape_service import smart_rescrape_engine
    import os
    
    mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DATABASE_NAME", "chatbot_db")
    try:
        client = MongoClient(mongo_url)
        db = client[db_name]
        
        # Get existing page to check for changes
        existing_page = db.scraped_pages.find_one({
            "chatbot_id": chatbot_id,
            "url": fields["url"]
        })
        
        # Calculate content hash if content is provided
        new_hash = None
        if "content" in fields:
            new_hash = smart_rescrape_engine.compute_content_hash(fields["content"])
            fields["content_hash"] = new_hash
            # Remove raw content from storage (we only keep the hash)
            del fields["content"]
        
        # Prepare update data - use smart rescrape engine for update logic
        if existing_page:
            # Existing page - check if content changed
            old_hash = existing_page.get("content_hash")
            content_changed = (old_hash != new_hash)
            
            # Update version
            old_version = existing_page.get("version", 1)
            fields["version"] = old_version + 1
            
            # Update consecutive_unchanged counter
            rescrape_policy = existing_page.get("rescrape_policy", {})
            if content_changed:
                rescrape_policy["consecutive_unchanged"] = 0
            else:
                consecutive = rescrape_policy.get("consecutive_unchanged", 0)
                rescrape_policy["consecutive_unchanged"] = consecutive + 1
            
            # Increment scrape count
            scrape_count = rescrape_policy.get("scrape_count", 0)
            rescrape_policy["scrape_count"] = scrape_count + 1

            fields["rescrape_policy"] = rescrape_policy

            # Record a version snapshot of the page state being superseded, so the
            # version-history feature is populated by real re-scrapes. The change
            # magnitude is estimated from the character-count delta (raw content is
            # not retained, only its hash).
            if content_changed:
                try:
                    old_cc = existing_page.get("char_count", 0) or 0
                    new_cc = fields.get("char_count", old_cc) or 0
                    pct = (abs(new_cc - old_cc) / old_cc) if old_cc else 1.0
                    if pct < 0.05:
                        _mag, _sc = "trivial", 1
                    elif pct < 0.15:
                        _mag, _sc = "minor", 2
                    elif pct < 0.40:
                        _mag, _sc = "moderate", 3
                    elif pct < 0.70:
                        _mag, _sc = "major", 4
                    else:
                        _mag, _sc = "complete_rewrite", 5
                    db.page_versions.insert_one({
                        "chatbot_id": chatbot_id,
                        "page_id": str(existing_page["_id"]),
                        "url": existing_page.get("url"),
                        "version_number": existing_page.get("version", 1),
                        "content_hash": existing_page.get("content_hash"),
                        "scraped_at": existing_page.get("scraped_at", datetime.utcnow()),
                        "title": existing_page.get("title", ""),
                        "char_count": old_cc,
                        "size_bytes": existing_page.get("size_bytes"),
                        "change_magnitude": _mag,
                        "change_score": _sc,
                        "changes_summary": {
                            "added": max(0, new_cc - old_cc),
                            "removed": max(0, old_cc - new_cc),
                            "modified": 0,
                            "change_percentage": round(pct * 100, 2),
                        },
                        "quality_score": existing_page.get("quality_score"),
                        "quality_grade": existing_page.get("quality_grade"),
                        "content_stored": False,
                        "created_at": datetime.utcnow(),
                    })
                except Exception as _ve:
                    logger.warning(f"Failed to record page version snapshot: {_ve}")

        else:
            # New page - initialize rescrape_policy
            fields["rescrape_policy"] = {
                "is_pinned": False,
                "strategy": "auto",
                "priority": 0,
                "consecutive_unchanged": 0,
                "custom_interval_hours": None,
                "conditions": {},
                "alert_on_change": False,  # Default: no alerts for new pages
                "alert_severity": "normal",
                "scrape_count": 1
            }
            fields["version"] = 1
        
        # Calculate quality score for both new and existing pages
        # Build page_content for quality calculation
        page_content = fields.get("content", "")
        
        # Build metadata dict with SEO structure
        metadata = {
            "scraped_at": datetime.utcnow(),
            "links_count": len(fields.get("links", [])),
            "images_count": len(fields.get("images", [])),
            "seo_metadata": {
                "meta_description": fields.get("meta_description", ""),
                "og_tags": {
                    "title": fields.get("og_title", ""),
                    "description": fields.get("og_description", "")
                } if fields.get("og_title") or fields.get("og_description") else None,
                "canonical_url": fields.get("canonical_url", "")
            }
        }
        
        # Note: calculate_quality_score is NOT async, so don't use await
        quality_score, quality_grade, quality_issues = smart_rescrape_engine.calculate_quality_score(page_content, metadata)
        
        fields["quality_score"] = quality_score
        fields["quality_grade"] = quality_grade
        fields["quality_issues"] = quality_issues
        
        # Update with new data
        db.scraped_pages.update_one(
            {"chatbot_id": chatbot_id, "url": fields["url"]},
            {"$set": {**fields, "scraped_at": datetime.utcnow()}},
            upsert=True
        )
        client.close()
    except Exception as e:
        logger.warning(f"Failed to record scraped page in MongoDB: {e}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Website Crawler for RAG ingestion")
    parser.add_argument("chatbot_id", help="Target chatbot ID")
    parser.add_argument("start_url", help="URL to start crawling")
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--no-sitemap", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    crawler = WebsiteCrawler(
        args.chatbot_id,
        args.start_url,
        max_pages=args.max_pages,
        concurrency=args.concurrency,
        max_depth=args.max_depth,
        delay=args.delay,
        use_sitemap=not args.no_sitemap,
    )
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(crawler.run())
