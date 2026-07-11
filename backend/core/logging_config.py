import logging
import json
from datetime import datetime
from typing import Optional

class SecurityLogger:
    """Security event logger for tracking authentication and security events"""
    
    def __init__(self):
        self.logger = logging.getLogger("security")
        self.logger.setLevel(logging.INFO)
        
        # File handler for security logs
        handler = logging.FileHandler("security.log")
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        ))
        self.logger.addHandler(handler)
        
        # Console handler for development
        console = logging.StreamHandler()
        console.setLevel(logging.WARNING)
        console.setFormatter(logging.Formatter(
            '%(levelname)s - %(message)s'
        ))
        self.logger.addHandler(console)
    
    def log_failed_login(self, email: str, ip: str, reason: str = "Invalid credentials"):
        """Log failed login attempts"""
        self.logger.warning(
            f"Failed login attempt - Email: {email}, IP: {ip}, Reason: {reason}"
        )
    
    def log_successful_login(self, email: str, ip: str):
        """Log successful logins"""
        self.logger.info(
            f"Successful login - Email: {email}, IP: {ip}"
        )
    
    def log_registration(self, email: str, ip: str):
        """Log new user registrations"""
        self.logger.info(
            f"New user registered - Email: {email}, IP: {ip}"
        )
    
    def log_rate_limit_exceeded(self, ip: str, endpoint: str):
        """Log rate limit violations"""
        self.logger.warning(
            f"Rate limit exceeded - IP: {ip}, Endpoint: {endpoint}"
        )
    
    def log_invalid_token(self, ip: str, reason: str = "Invalid token"):
        """Log invalid token attempts"""
        self.logger.warning(
            f"Invalid token attempt - IP: {ip}, Reason: {reason}"
        )
    
    def log_file_upload(self, email: str, ip: str, filename: str, size: int):
        """Log file uploads"""
        self.logger.info(
            f"File uploaded - Email: {email}, IP: {ip}, File: {filename}, Size: {size} bytes"
        )
    
    def log_file_upload_rejected(self, email: str, ip: str, reason: str):
        """Log rejected file uploads"""
        self.logger.warning(
            f"File upload rejected - Email: {email}, IP: {ip}, Reason: {reason}"
        )
    
    def log_security_event(self, event_type: str, details: dict):
        """Log generic security events"""
        self.logger.warning(
            f"Security event - Type: {event_type}, Details: {json.dumps(details)}"
        )

# Global security logger instance
security_logger = SecurityLogger()

# General application logger
logger = logging.getLogger("app")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
