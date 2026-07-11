"""
Smart Re-Scrape Service
Intelligent decision engine for determining when pages should be re-scraped
"""

import aiohttp
import hashlib
import logging
import fnmatch
import random
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any, List
from urllib.parse import urlparse
from core.url_safety import is_safe_public_url
from core.database import db
from bson import ObjectId

logger = logging.getLogger(__name__)


class SmartRescrapeEngine:
    """Intelligent engine for deciding when pages should be re-scraped"""
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
    
    async def should_rescrape_page(
        self,
        page: Dict[str, Any],
        config: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        Intelligently decide if a page should be re-scraped
        
        Args:
            page: ScrapedPage document from database
            config: ChatbotConfig document from database
            
        Returns:
            Tuple of (should_rescrape: bool, reason: str)
        """
        
        chatbot_id = page["chatbot_id"]
        url = page["url"]
        policy = page.get("rescrape_policy", {})
        
        # 1. Check if page is pinned
        if policy.get("is_pinned", False):
            return False, "Page is pinned by user"
        
        # 2. Check strategy
        strategy = policy.get("strategy", "auto")
        
        if strategy == "never":
            return False, "Strategy set to never re-scrape"
        
        if strategy == "manual":
            return False, "Manual scraping only"
        
        # 3. Check if excluded by pattern
        excluded_patterns = config.get("excluded_url_patterns", [])
        if excluded_patterns and self._matches_pattern(url, excluded_patterns):
            return False, f"URL matches excluded pattern"
        
        # 4. Check included patterns (if set, ONLY these should be scraped)
        included_patterns = config.get("included_url_patterns", [])
        if included_patterns and not self._matches_pattern(url, included_patterns):
            return False, "URL does not match any included pattern"
        
        # 5. Check custom interval
        custom_interval = policy.get("custom_interval_hours")
        if custom_interval:
            hours_since_last = self._hours_since_scrape(page["scraped_at"])
            if hours_since_last < custom_interval:
                return False, f"Custom interval not reached ({hours_since_last:.1f}h < {custom_interval}h)"
        
        # 6. HTTP conditional checks (ETag, Last-Modified)
        if config.get("enable_etag_checking", True):
            try:
                has_changed = await self._check_http_freshness(page)
                if not has_changed:
                    # Update last check time
                    await db.scraped_pages.update_one(
                        {"_id": ObjectId(page["_id"])},
                        {"$set": {"last_rescrape_check": datetime.utcnow()}}
                    )
                    return False, "HTTP headers indicate no change (ETag/Last-Modified)"
            except Exception as e:
                self.logger.warning(f"HTTP freshness check failed for {url}: {e}")
                # Continue to other checks
        
        # 7. Smart scheduling - adapt frequency based on page stability
        if config.get("enable_smart_scheduling", True):
            consecutive_unchanged = policy.get("consecutive_unchanged", 0)
            
            if consecutive_unchanged >= 5:
                # Page hasn't changed in last 5 re-scrapes
                # Increase interval by 20% for each consecutive unchanged
                base_interval = config.get("rescrape_interval_hours", 24)
                adjusted_interval = base_interval * (1 + consecutive_unchanged * 0.2)
                
                hours_since_last = self._hours_since_scrape(page["scraped_at"])
                if hours_since_last < adjusted_interval:
                    return False, f"Smart scheduling: stable page, extended interval to {adjusted_interval:.1f}h"
        
        # 8. Priority-based sampling for low priority pages
        priority = policy.get("priority", 0)
        
        if priority == -1:  # Low priority
            # Only re-scrape 50% of low priority pages
            if random.random() > 0.5:
                return False, "Low priority page randomly skipped"
        
        # 9. Check global interval
        global_interval = config.get("rescrape_interval_hours")
        if global_interval:
            hours_since_last = self._hours_since_scrape(page["scraped_at"])
            if hours_since_last < global_interval:
                return False, f"Global interval not reached ({hours_since_last:.1f}h < {global_interval}h)"
        
        # 10. Check conditional rules
        if strategy == "conditional":
            conditions = policy.get("rescrape_conditions", {})
            meets_conditions, reason = self._check_custom_conditions(page, conditions)
            if not meets_conditions:
                return False, f"Conditional: {reason}"
        
        # All checks passed - ready for re-scrape
        return True, "Ready for re-scrape"
    
    async def _check_http_freshness(self, page: Dict[str, Any]) -> bool:
        """
        Check if page has changed using HTTP headers
        Returns True if page has potentially changed, False if unchanged
        """
        url = page["url"]
        stored_etag = page.get("http_etag")
        stored_last_modified = page.get("http_last_modified")
        
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = {}
                
                # Add If-None-Match header if we have an ETag
                if stored_etag:
                    headers["If-None-Match"] = stored_etag
                
                # Add If-Modified-Since header if we have Last-Modified
                if stored_last_modified:
                    headers["If-Modified-Since"] = stored_last_modified
                
                async with session.head(url, headers=headers, allow_redirects=True) as response:
                    # 304 Not Modified means content hasn't changed
                    if response.status == 304:
                        return False
                    
                    # Compare ETags
                    current_etag = response.headers.get("ETag")
                    if stored_etag and current_etag:
                        if stored_etag == current_etag:
                            return False
                    
                    # Compare Last-Modified
                    current_last_modified = response.headers.get("Last-Modified")
                    if stored_last_modified and current_last_modified:
                        if stored_last_modified == current_last_modified:
                            return False
                    
                    # If we got here, assume changed
                    return True
                    
        except Exception as e:
            self.logger.warning(f"HTTP freshness check failed for {url}: {e}")
            # On error, assume page might have changed (safe default)
            return True
    
    def _matches_pattern(self, url: str, patterns: List[str]) -> bool:
        """Check if URL matches any of the given patterns"""
        parsed = urlparse(url)
        path = parsed.path
        
        for pattern in patterns:
            # Try matching against path
            if fnmatch.fnmatch(path, pattern):
                return True
            
            # Try matching against full URL
            if fnmatch.fnmatch(url, pattern):
                return True
            
            # Try matching against path + query
            path_query = f"{path}?{parsed.query}" if parsed.query else path
            if fnmatch.fnmatch(path_query, pattern):
                return True
        
        return False
    
    def _hours_since_scrape(self, scraped_at: datetime) -> float:
        """Calculate hours since last scrape"""
        now = datetime.utcnow()
        delta = now - scraped_at
        return delta.total_seconds() / 3600
    
    def _check_custom_conditions(
        self,
        page: Dict[str, Any],
        conditions: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        Check if page meets custom conditions for re-scraping
        
        Supported conditions:
        - max_age_days: Re-scrape if older than X days
        - min_size_bytes: Only re-scrape if page is larger than X bytes
        - max_size_bytes: Only re-scrape if page is smaller than X bytes
        """
        
        # Check max age
        max_age_days = conditions.get("max_age_days")
        if max_age_days:
            age_days = self._hours_since_scrape(page["scraped_at"]) / 24
            if age_days < max_age_days:
                return False, f"Page age {age_days:.1f} days < max age {max_age_days} days"
        
        # Check min size
        min_size = conditions.get("min_size_bytes")
        if min_size:
            page_size = page.get("size_bytes", page.get("char_count", 0))
            if page_size < min_size:
                return False, f"Page size {page_size} < min size {min_size}"
        
        # Check max size
        max_size = conditions.get("max_size_bytes")
        if max_size:
            page_size = page.get("size_bytes", page.get("char_count", 0))
            if page_size > max_size:
                return False, f"Page size {page_size} > max size {max_size}"
        
        return True, "All conditions met"
    
    @staticmethod
    def compute_content_hash(content: str) -> str:
        """Compute SHA256 hash of content for change detection"""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    async def get_pages_to_rescrape(
        self,
        chatbot_id: str,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get list of pages that should be re-scraped for a chatbot
        
        Args:
            chatbot_id: Chatbot ID
            limit: Maximum number of pages to return
            
        Returns:
            List of page documents that should be re-scraped
        """
        
        # Get chatbot config
        config = await db.chatbot_configs.find_one({"_id": ObjectId(chatbot_id)})
        if not config:
            return []
        
        # Get all pages for this chatbot
        cursor = db.scraped_pages.find({"chatbot_id": chatbot_id})
        pages = await cursor.to_list(length=None)
        
        pages_to_scrape = []
        
        for page in pages:
            should_scrape, reason = await self.should_rescrape_page(page, config)
            
            if should_scrape:
                priority = page.get("rescrape_policy", {}).get("priority", 0)
                pages_to_scrape.append({
                    "page": page,
                    "priority": priority,
                    "reason": reason
                })
                
                self.logger.info(f"Page {page['url']} marked for re-scrape: {reason}")
            else:
                self.logger.debug(f"Page {page['url']} skipped: {reason}")
        
        # Sort by priority (high to low)
        pages_to_scrape.sort(key=lambda x: x["priority"], reverse=True)
        
        # Apply limit
        if limit:
            pages_to_scrape = pages_to_scrape[:limit]
        
        return [item["page"] for item in pages_to_scrape]
    
    async def update_page_after_scrape(
        self,
        page_id: str,
        new_content: str,
        new_metadata: Dict[str, Any],
        old_content: Optional[str] = None
    ) -> bool:
        """
        Update page after re-scraping and detect if content changed
        Enhanced with quality scoring, change detection, versioning, and alerts
        
        Args:
            page_id: Page ID
            new_content: New scraped content
            new_metadata: New metadata (etag, last-modified, etc.)
            old_content: Optional old content for change magnitude calculation
            
        Returns:
            True if content changed, False if unchanged
        """
        
        # Get current page
        page = await db.scraped_pages.find_one({"_id": ObjectId(page_id)})
        if not page:
            return False
        
        # Compute content hash
        new_hash = self.compute_content_hash(new_content)
        old_hash = page.get("content_hash")
        
        content_changed = (old_hash != new_hash)
        
        old_version = page.get("version", 1)
        new_version = old_version + 1
        
        # Calculate quality score
        quality_score, quality_grade, quality_issues = self.calculate_quality_score(
            new_content,
            new_metadata
        )
        
        # Calculate change magnitude if content changed
        change_magnitude = "trivial"
        change_score = 1
        changes_summary = {"added": 0, "removed": 0, "modified": 0}
        
        if content_changed and old_content:
            change_magnitude, change_score, changes_summary = self.calculate_change_magnitude(
                old_content,
                new_content,
                old_hash or "",
                new_hash
            )
        
        # Update fields
        update_fields = {
            "scraped_at": datetime.utcnow(),
            "content_hash": new_hash,
            "last_rescrape_check": datetime.utcnow(),
            "version": new_version,
            "quality_score": quality_score,
            "quality_grade": quality_grade,
            "quality_issues": quality_issues
        }
        
        # Update HTTP metadata if provided
        if "etag" in new_metadata:
            update_fields["http_etag"] = new_metadata["etag"]
        if "last_modified" in new_metadata:
            update_fields["http_last_modified"] = new_metadata["last_modified"]
        if "size_bytes" in new_metadata:
            update_fields["size_bytes"] = new_metadata["size_bytes"]
        if "links_count" in new_metadata:
            update_fields["links_count"] = new_metadata["links_count"]
        if "images_count" in new_metadata:
            update_fields["images_count"] = new_metadata["images_count"]
        if "seo_metadata" in new_metadata:
            update_fields["seo_metadata"] = new_metadata["seo_metadata"]
        
        # Update consecutive unchanged counter
        policy = page.get("rescrape_policy", {})
        if content_changed:
            update_fields["rescrape_policy.consecutive_unchanged"] = 0
            update_fields["rescrape_policy.last_change_detected"] = datetime.utcnow()
            
            # Create version snapshot
            try:
                version_id = await self.create_version_snapshot(
                    page,
                    change_magnitude,
                    change_score,
                    changes_summary,
                    quality_score,
                    quality_grade
                )
                self.logger.info(f"Created version snapshot: {version_id}")
            except Exception as e:
                self.logger.error(f"Failed to create version snapshot: {e}")
            
            # Send change alert if enabled
            if policy.get("alert_on_change", False):
                try:
                    from services.alert_service import alert_service
                    await alert_service.send_change_alert(
                        page["chatbot_id"],
                        page,
                        change_magnitude,
                        change_score,
                        changes_summary,
                        old_version,
                        new_version
                    )
                except Exception as e:
                    self.logger.error(f"Failed to send change alert: {e}")
        else:
            consecutive = policy.get("consecutive_unchanged", 0)
            update_fields["rescrape_policy.consecutive_unchanged"] = consecutive + 1
        
        # Increment scrape count
        scrape_count = policy.get("scrape_count", 0)
        update_fields["rescrape_policy.scrape_count"] = scrape_count + 1
        
        # Check for quality degradation
        old_quality_score = page.get("quality_score")
        if old_quality_score and quality_score < old_quality_score - 10:
            try:
                from services.alert_service import alert_service
                await alert_service.send_quality_degradation_alert(
                    page["chatbot_id"],
                    page,
                    old_quality_score,
                    quality_score,
                    quality_issues
                )
            except Exception as e:
                self.logger.error(f"Failed to send quality alert: {e}")
        
        # Update database
        await db.scraped_pages.update_one(
            {"_id": ObjectId(page_id)},
            {"$set": update_fields}
        )
        
        return content_changed
    
    # ══════════════════════════════════════════════════════════════════════════
    # Quality Scoring
    # ══════════════════════════════════════════════════════════════════════════
    
    def calculate_quality_score(
        self,
        page_content: str,
        metadata: Dict[str, Any]
    ) -> Tuple[int, str, List[str]]:
        """
        Calculate quality score for a page (0-100)
        
        Returns:
            Tuple of (score, grade, issues)
        """
        score = 0
        issues = []
        
        # Content richness (0-30 points)
        char_count = len(page_content)
        if char_count > 5000:
            score += 30
        elif char_count > 2000:
            score += 20
        elif char_count > 500:
            score += 10
        else:
            issues.append(f"Content too short ({char_count} chars)")
        
        # Structure quality (0-25 points)
        links_count = metadata.get("links_count", 0)
        images_count = metadata.get("images_count", 0)
        
        if links_count > 10:
            score += 10
        elif links_count > 5:
            score += 5
        else:
            issues.append(f"Few links ({links_count})")
        
        if images_count > 3:
            score += 10
        elif images_count > 0:
            score += 5
        else:
            issues.append("No images")
        
        # Check for headings
        if "<h1" in page_content or "# " in page_content:
            score += 5
        else:
            issues.append("No H1 heading")
        
        # SEO metadata (0-25 points)
        seo = metadata.get("seo_metadata", {})
        
        if seo.get("meta_description"):
            score += 10
        else:
            issues.append("Missing meta description")
        
        if seo.get("og_tags"):
            score += 10
        else:
            issues.append("Missing Open Graph tags")
        
        if seo.get("canonical_url"):
            score += 5
        else:
            issues.append("Missing canonical URL")
        
        # Freshness (0-20 points)
        scraped_at = metadata.get("scraped_at")
        if scraped_at:
            age_days = (datetime.utcnow() - scraped_at).days
            if age_days < 7:
                score += 20
            elif age_days < 30:
                score += 15
            elif age_days < 90:
                score += 10
            elif age_days < 180:
                score += 5
            else:
                issues.append(f"Content is old ({age_days} days)")
        
        # Determine grade
        if score >= 90:
            grade = "A+"
        elif score >= 80:
            grade = "A"
        elif score >= 70:
            grade = "B"
        elif score >= 60:
            grade = "C"
        elif score >= 50:
            grade = "D"
        else:
            grade = "F"
        
        return score, grade, issues
    
    def calculate_change_magnitude(
        self,
        old_content: str,
        new_content: str,
        old_hash: str,
        new_hash: str
    ) -> Tuple[str, int, Dict[str, int]]:
        """
        Calculate the magnitude of change between two versions
        
        Returns:
            Tuple of (magnitude, score, changes_summary)
            magnitude: "trivial", "minor", "moderate", "major", "complete_rewrite"
            score: 1-5
            changes_summary: {"added": X, "removed": Y, "modified": Z}
        """
        
        if old_hash == new_hash:
            return "trivial", 1, {"added": 0, "removed": 0, "modified": 0}
        
        try:
            import difflib
            
            old_lines = old_content.splitlines()
            new_lines = new_content.splitlines()
            
            diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=''))
            
            added_lines = sum(1 for line in diff if line.startswith('+') and not line.startswith('+++'))
            removed_lines = sum(1 for line in diff if line.startswith('-') and not line.startswith('---'))
            
            total_old_lines = len(old_lines) if old_lines else 1
            change_pct = (added_lines + removed_lines) / total_old_lines
            
            changes_summary = {
                "added": added_lines,
                "removed": removed_lines,
                "modified": min(added_lines, removed_lines),
                "change_percentage": round(change_pct * 100, 2)
            }
            
            # Determine magnitude
            if change_pct < 0.05:
                magnitude = "trivial"
                score = 1
            elif change_pct < 0.15:
                magnitude = "minor"
                score = 2
            elif change_pct < 0.40:
                magnitude = "moderate"
                score = 3
            elif change_pct < 0.70:
                magnitude = "major"
                score = 4
            else:
                magnitude = "complete_rewrite"
                score = 5
            
            return magnitude, score, changes_summary
            
        except Exception as e:
            logger.warning(f"Change magnitude calculation failed: {e}")
            return "unknown", 3, {"added": 0, "removed": 0, "modified": 0}
    
    async def create_version_snapshot(
        self,
        page: Dict[str, Any],
        change_magnitude: str,
        change_score: int,
        changes_summary: Dict[str, int],
        quality_score: int,
        quality_grade: str
    ) -> str:
        """
        Create a historical version snapshot
        
        Returns:
            version_id: ObjectId of created PageVersion
        """
        
        version_data = {
            "chatbot_id": page["chatbot_id"],
            "page_id": str(page["_id"]),
            "url": page["url"],
            "version_number": page.get("version", 1),
            "content_hash": page.get("content_hash"),
            "scraped_at": page.get("scraped_at", datetime.utcnow()),
            "title": page.get("title", ""),
            "char_count": page.get("char_count", 0),
            "size_bytes": page.get("size_bytes"),
            "change_magnitude": change_magnitude,
            "change_score": change_score,
            "changes_summary": changes_summary,
            "quality_score": quality_score,
            "quality_grade": quality_grade,
            "seo_metadata": page.get("seo_metadata"),
            "content_stored": False,  # Set to True if storing full content
            "created_at": datetime.utcnow()
        }
        
        result = await db.page_versions.insert_one(version_data)
        return str(result.inserted_id)
    
    # ══════════════════════════════════════════════════════════════════════════
    # Health Checking
    # ══════════════════════════════════════════════════════════════════════════
    
    async def check_page_health(self, url: str) -> Dict[str, Any]:
        """
        Check if a page is accessible and healthy
        
        Returns:
            Dict with status, is_broken, error info
        """
        # SSRF guard: this re-fetches a stored URL; refuse internal/private hosts
        # (a stored page could point at, or have been changed to, an internal IP).
        if not is_safe_public_url(url):
            logger.warning(f"check_page_health: refusing non-public URL: {url}")
            return {
                "status": 0,
                "is_broken": True,
                "redirect_count": 0,
                "final_url": url,
                "error": "blocked_non_public_url",
                "checked_at": datetime.utcnow(),
            }

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5"
        }
        
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                # Try HEAD first
                try:
                    async with session.head(
                        url,
                        timeout=aiohttp.ClientTimeout(total=10),
                        allow_redirects=True
                    ) as response:
                        # Some servers block HEAD or return 404/403/405/406. 
                        # In that case, we should fallback to GET before declaring it broken.
                        if response.status >= 400:
                            raise ValueError("Retry with GET")
                            
                        return {
                            "status": response.status,
                            "is_broken": False,
                            "redirect_count": len(response.history),
                            "final_url": str(response.url),
                            "error": None,
                            "checked_at": datetime.utcnow()
                        }
                except (ValueError, aiohttp.ClientError):
                    # Fallback to GET
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=15),
                        allow_redirects=True
                    ) as response:
                        # Consider broken only for 404 (Not Found), 410 (Gone), or Server Errors (500+)
                        # 401/403/405 usually mean the server is up but blocking automated bots.
                        is_broken = response.status in (404, 410) or response.status >= 500
                        return {
                            "status": response.status,
                            "is_broken": is_broken,
                            "redirect_count": len(response.history),
                            "final_url": str(response.url),
                            "error": None,
                            "checked_at": datetime.utcnow()
                        }
        except aiohttp.ClientError as e:
            return {
                "status": 0,
                "is_broken": True,
                "redirect_count": 0,
                "final_url": url,
                "error": str(e),
                "checked_at": datetime.utcnow()
            }
        except Exception as e:
            return {
                "status": 0,
                "is_broken": True,
                "redirect_count": 0,
                "final_url": url,
                "error": f"Unexpected error: {str(e)}",
                "checked_at": datetime.utcnow()
            }


# Global instance
smart_rescrape_engine = SmartRescrapeEngine()
