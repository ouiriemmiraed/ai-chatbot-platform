"""
Alert Service
Handles notifications for page changes, quality issues, and broken links
"""

import aiohttp
import logging
import os
from datetime import datetime
from typing import List, Dict, Any, Optional
from core.database import db
from bson import ObjectId

logger = logging.getLogger(__name__)


class AlertService:
    """Service for creating in-app change alerts and delivering webhooks."""

    def __init__(self):
        # Email sending removed — system uses in-app notifications and webhooks
        self.logger = logging.getLogger(self.__class__.__name__)
    
    async def send_change_alert(
        self,
        chatbot_id: str,
        page: Dict[str, Any],
        change_magnitude: str,
        change_score: int,
        changes_summary: Dict[str, int],
        old_version: int,
        new_version: int
    ) -> bool:
        """
        Send alert notification when a page changes
        
        Returns:
            bool: True if alert sent successfully
        """
        
        policy = page.get("rescrape_policy", {})
        
        # Check if alerts are enabled for this page
        if not policy.get("alert_on_change", False):
            return False
        
        # Check alert severity threshold
        alert_severity = policy.get("alert_severity", "normal")
        
        # Only send if change magnitude matches severity threshold
        should_alert = self._should_send_alert(change_magnitude, alert_severity)
        if not should_alert:
            self.logger.info(f"Change magnitude '{change_magnitude}' below threshold '{alert_severity}'")
            return False
        
        # Create alert record
        alert_data = {
            "chatbot_id": chatbot_id,
            "page_id": str(page["_id"]),
            "url": page["url"],
            "alert_type": "page_changed",
            "severity": alert_severity,
            "old_version_number": old_version,
            "new_version_number": new_version,
            "change_magnitude": change_magnitude,
            "change_summary": self._format_change_summary(changes_summary),
            "delivery_status": "pending",
            "created_at": datetime.utcnow()
        }
        
        result = await db.change_alerts.insert_one(alert_data)
        alert_id = result.inserted_id
        
        # Email sending removed: mark alert as in-app only by default
        await db.change_alerts.update_one(
            {"_id": alert_id},
            {"$set": {"delivery_status": "in_app_only", "sent_at": datetime.utcnow(), "sent_to_emails": []}}
        )
        
        # Send webhook notifications
        webhooks = policy.get("alert_webhooks", [])
        if webhooks:
            try:
                await self._send_webhook_alerts(
                    webhooks,
                    page,
                    change_magnitude,
                    changes_summary,
                    old_version,
                    new_version
                )
                self.logger.info(f"Webhook alerts sent to {len(webhooks)} endpoints")
            except Exception as e:
                self.logger.error(f"Failed to send webhook alerts: {e}")
        
        # Update alert status with webhook delivery info (email removed)
        delivery_status = "in_app_only"
        if webhooks:
            delivery_status = "in_app_and_webhooks"

        await db.change_alerts.update_one(
            {"_id": alert_id},
            {"$set": {
                "sent_at": datetime.utcnow(),
                "sent_to_emails": [],
                "sent_to_webhooks": webhooks,
                "delivery_status": delivery_status
            }}
        )
        
        # Update last alert sent timestamp
        await db.scraped_pages.update_one(
            {"_id": ObjectId(page["_id"])},
            {"$set": {"rescrape_policy.last_alert_sent": datetime.utcnow()}}
        )
        
        return True
    
    async def send_broken_link_alert(
        self,
        chatbot_id: str,
        page: Dict[str, Any],
        http_status: int,
        error_message: Optional[str] = None
    ) -> bool:
        """Send alert when a page becomes broken"""
        
        alert_data = {
            "chatbot_id": chatbot_id,
            "page_id": str(page["_id"]),
            "url": page["url"],
            "alert_type": "page_broken",
            "severity": "critical",
            "old_version_number": page.get("version", 1),
            "new_version_number": page.get("version", 1),
            "change_magnitude": "broken",
            "change_summary": f"Page returned HTTP {http_status}: {error_message or 'Unknown error'}",
            "delivery_status": "pending",
            "created_at": datetime.utcnow()
        }
        
        result = await db.change_alerts.insert_one(alert_data)
        alert_id = result.inserted_id

        # Email removed: mark as in-app notification
        await db.change_alerts.update_one(
            {"_id": alert_id},
            {"$set": {
                "sent_at": datetime.utcnow(),
                "delivery_status": "in_app_only",
                "sent_to_emails": []
            }}
        )

        return True
    
    async def send_quality_degradation_alert(
        self,
        chatbot_id: str,
        page: Dict[str, Any],
        old_score: int,
        new_score: int,
        issues: List[str]
    ) -> bool:
        """Send alert when page quality degrades significantly"""
        
        if new_score >= old_score - 10:
            # Not significant enough degradation
            return False
        
        alert_data = {
            "chatbot_id": chatbot_id,
            "page_id": str(page["_id"]),
            "url": page["url"],
            "alert_type": "quality_degraded",
            "severity": "important",
            "old_version_number": page.get("version", 1) - 1,
            "new_version_number": page.get("version", 1),
            "change_magnitude": "quality_loss",
            "change_summary": f"Quality dropped from {old_score} to {new_score}. Issues: {', '.join(issues)}",
            "delivery_status": "pending",
            "created_at": datetime.utcnow()
        }
        
        await db.change_alerts.insert_one(alert_data)
        
        return True
    
    def _should_send_alert(self, change_magnitude: str, alert_severity: str) -> bool:
        """Determine if alert should be sent based on magnitude and severity threshold"""
        
        magnitude_scores = {
            "trivial": 1,
            "minor": 2,
            "moderate": 3,
            "major": 4,
            "complete_rewrite": 5
        }
        
        severity_thresholds = {
            "critical": 5,  # Only complete rewrites
            "important": 4,  # Major changes and above
            "normal": 3,     # Moderate changes and above
            "minor": 2       # Minor changes and above
        }
        
        magnitude_score = magnitude_scores.get(change_magnitude, 3)
        threshold = severity_thresholds.get(alert_severity, 3)
        
        return magnitude_score >= threshold
    
    def _format_change_summary(self, changes: Dict[str, int]) -> str:
        """Format change summary for display"""
        return (
            f"+{changes.get('added', 0)} lines, "
            f"-{changes.get('removed', 0)} lines, "
            f"~{changes.get('modified', 0)} modified "
            f"({changes.get('change_percentage', 0):.1f}% changed)"
        )
    
    # Email sending removed — helper email methods deleted
    
    async def _send_webhook_alerts(
        self,
        webhooks: List[str],
        page: Dict[str, Any],
        change_magnitude: str,
        changes_summary: Dict[str, int],
        old_version: int,
        new_version: int
    ):
        """Send webhook notifications"""
        
        payload = {
            "event": "page.changed",
            "timestamp": datetime.utcnow().isoformat(),
            "data": {
                "url": page["url"],
                "title": page.get("title"),
                "chatbot_id": page["chatbot_id"],
                "page_id": str(page["_id"]),
                "change_magnitude": change_magnitude,
                "changes": changes_summary,
                "old_version": old_version,
                "new_version": new_version
            }
        }
        
        async with aiohttp.ClientSession() as session:
            for webhook_url in webhooks:
                try:
                    async with session.post(
                        webhook_url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as response:
                        if response.status >= 400:
                            self.logger.warning(
                                f"Webhook {webhook_url} returned {response.status}"
                            )
                        else:
                            self.logger.info(f"Webhook sent successfully to {webhook_url}")
                            
                except Exception as e:
                    self.logger.error(f"Failed to send webhook to {webhook_url}: {e}")


# Global instance
alert_service = AlertService()
