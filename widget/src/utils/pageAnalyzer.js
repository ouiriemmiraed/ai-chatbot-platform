import { sanitizeText } from './sanitizer';

// Cache for performance
let cachedContent = null;
let lastAnalyzedUrl = null;
let lastAnalyzedTime = 0;
const CACHE_DURATION = 30000; // 30 seconds

/**
 * Helper: Check if element is visible
 */
const isVisible = (element) => {
    if (!element) return false;
    const style = window.getComputedStyle(element);
    return element.offsetParent !== null &&
        style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        style.opacity !== '0';
};

/**
 * Helper: Score element importance
 */
const scoreElement = (element) => {
    let score = 0;
    const tag = element.tagName.toLowerCase();

    // Semantic importance
    const tagScores = {
        'article': 10, 'main': 10, 'h1': 8, 'h2': 6, 'h3': 4,
        'h4': 3, 'h5': 2, 'h6': 2, 'p': 3, 'li': 2, 'td': 2,
        'th': 3, 'span': 1, 'div': 1
    };
    score += tagScores[tag] || 0;

    // ARIA roles boost
    if (element.matches('[role="main"]')) score += 10;
    if (element.matches('[role="article"]')) score += 8;
    if (element.matches('[role="complementary"]')) score += 3;

    // Position (above the fold is more important)
    try {
        const rect = element.getBoundingClientRect();
        if (rect.top >= 0 && rect.top < window.innerHeight) {
            score += 5; // Visible in viewport
        }
    } catch (e) {
        // getBoundingClientRect can fail on some elements
    }

    // Text length (sweet spot for meaningful content)
    const textLength = element.textContent.trim().length;
    if (textLength > 20 && textLength < 500) score += 2;
    if (textLength >= 500 && textLength < 2000) score += 1;

    return score;
};

/**
 * Extract all content with intelligent prioritization
 */
const extractContent = () => {
    const content = {
        title: document.title,
        url: window.location.href,
        metaDescription: '',
        headings: [],
        paragraphs: [],
        lists: [],
        tables: [],
        links: [],
        images: [],
        structuredData: [],
    };

    // Meta description
    const metaDesc = document.querySelector('meta[name="description"]');
    content.metaDescription = metaDesc ? metaDesc.getAttribute('content') : '';

    // Headings with scoring
    const headingElements = [];
    document.querySelectorAll('h1, h2, h3, h4, h5, h6').forEach(h => {
        const text = h.textContent.trim();
        if (isVisible(h) && text) {
            headingElements.push({
                text: text,
                score: scoreElement(h),
                level: parseInt(h.tagName[1])
            });
        }
    });
    // Sort by score and take top 30
    content.headings = headingElements
        .sort((a, b) => b.score - a.score || a.level - b.level)
        .slice(0, 30)
        .map(h => h.text);

    // Paragraphs with scoring
    const paragraphElements = [];
    document.querySelectorAll('p').forEach(p => {
        const text = p.textContent.trim();
        if (isVisible(p) && text.length > 20) {
            paragraphElements.push({
                text: text,
                score: scoreElement(p)
            });
        }
    });
    // Sort by score and take top 30
    content.paragraphs = paragraphElements
        .sort((a, b) => b.score - a.score)
        .slice(0, 30)
        .map(p => p.text);

    // List items
    const listElements = [];
    document.querySelectorAll('li').forEach(li => {
        const text = li.textContent.trim();
        if (isVisible(li) && text && text.length > 2) {
            listElements.push({
                text: text,
                score: scoreElement(li)
            });
        }
    });
    content.lists = listElements
        .sort((a, b) => b.score - a.score)
        .slice(0, 30)
        .map(l => l.text);

    // Tables (extract meaningful table content)
    document.querySelectorAll('table').forEach(table => {
        if (isVisible(table)) {
            const tableText = table.textContent.trim();
            // Only include tables with reasonable content
            if (tableText.length > 10 && tableText.length < 1000) {
                content.tables.push(tableText);
            }
        }
    });
    content.tables = content.tables.slice(0, 5);

    // Important links (navigation, CTAs)
    const linkElements = [];
    document.querySelectorAll('a').forEach(link => {
        const text = link.textContent.trim();
        if (isVisible(link) && text && text.length > 2 && text.length < 100) {
            linkElements.push({
                text: text,
                score: scoreElement(link)
            });
        }
    });
    content.links = linkElements
        .sort((a, b) => b.score - a.score)
        .slice(0, 20)
        .map(l => l.text);

    // Image alt text (accessibility and context)
    document.querySelectorAll('img[alt]').forEach(img => {
        const alt = img.getAttribute('alt').trim();
        if (alt && alt.length > 2 && alt.length < 200) {
            content.images.push(alt);
        }
    });
    content.images = content.images.slice(0, 15);

    // Structured data (JSON-LD for rich context)
    document.querySelectorAll('script[type="application/ld+json"]').forEach(script => {
        try {
            const data = JSON.parse(script.textContent);
            if (data['@type']) {
                const parts = [];
                if (data['@type']) parts.push(`Type: ${data['@type']}`);
                if (data.name) parts.push(`Name: ${data.name}`);
                if (data.description) parts.push(`Description: ${data.description}`);
                if (parts.length > 0) {
                    content.structuredData.push(parts.join(', '));
                }
            }
        } catch (e) {
            // Invalid JSON, skip
        }
    });
    content.structuredData = content.structuredData.slice(0, 5);

    return content;
};

/**
 * Deduplicate and combine content
 */
const combineContent = (extracted) => {
    const allText = [
        extracted.metaDescription,
        ...extracted.headings,
        ...extracted.paragraphs,
        ...extracted.lists,
        ...extracted.tables,
        ...extracted.links,
        ...extracted.images,
        ...extracted.structuredData,
    ];

    // Smart deduplication
    const seen = new Set();
    const unique = allText.filter(text => {
        if (!text || text.length < 2) return false;

        // Normalize for comparison
        const normalized = text.toLowerCase().trim();

        // Skip if we've seen this exact text
        if (seen.has(normalized)) return false;

        // Skip if this is a substring of something we've already seen
        // (prevents "Home" and "Home Page" both being included)
        for (const seenText of seen) {
            if (seenText.includes(normalized) || normalized.includes(seenText)) {
                // Keep the longer version
                if (normalized.length > seenText.length) {
                    seen.delete(seenText);
                    seen.add(normalized);
                    return true;
                }
                return false;
            }
        }

        seen.add(normalized);
        return true;
    });

    return unique.join('\n\n');
};

/**
 * Main export: Analyze page content with caching
 */
export const analyzePageContent = (forceRefresh = false) => {
    const currentUrl = window.location.href;
    const now = Date.now();

    // Return cached content if valid
    if (!forceRefresh &&
        cachedContent &&
        lastAnalyzedUrl === currentUrl &&
        (now - lastAnalyzedTime) < CACHE_DURATION) {
        return cachedContent;
    }

    // Extract and process content
    const extracted = extractContent();
    const combinedText = combineContent(extracted);

    // Sanitize and limit (increased from 8000 to 12000)
    const sanitized = sanitizeText(combinedText);
    const limited = sanitized.substring(0, 12000);

    // Build result
    const result = {
        title: extracted.title,
        url: extracted.url,
        text: limited,
    };

    // Cache for performance
    cachedContent = result;
    lastAnalyzedUrl = currentUrl;
    lastAnalyzedTime = now;

    return result;
};

/**
 * Force refresh (useful for SPAs with dynamic content)
 */
export const refreshPageContent = () => {
    return analyzePageContent(true);
};
