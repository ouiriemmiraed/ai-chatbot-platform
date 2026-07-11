import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from dotenv import load_dotenv
from core.logging_config import logger

load_dotenv()

# Email configuration
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", SMTP_USERNAME)
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

def send_email(to_email: str, subject: str, html_content: str, plain_content: Optional[str] = None):
    """
    Send an email using SMTP.
    
    Args:
        to_email: Recipient email address
        subject: Email subject
        html_content: HTML content of the email
        plain_content: Plain text fallback (optional)
    """
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        logger.warning("SMTP credentials not configured. Email not sent.")
        print(f"[DEV MODE] Email to {to_email}")
        print(f"Subject: {subject}")
        print(f"Content: {html_content}")
        return
    
    try:
        # Create message
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = SENDER_EMAIL
        message["To"] = to_email
        
        # Add plain text version if provided
        if plain_content:
            part1 = MIMEText(plain_content, "plain")
            message.attach(part1)
        
        # Add HTML version
        part2 = MIMEText(html_content, "html")
        message.attach(part2)
        
        # Send email
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SENDER_EMAIL, to_email, message.as_string())
        
        logger.info(f"Email sent successfully to {to_email}")
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {str(e)}")
        raise

def send_password_reset_email(to_email: str, reset_token: str):
    """Send password reset link to user."""
    reset_link = f"{FRONTEND_URL}/reset-password?token={reset_token}"
    
    subject = "Reset your password"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .button {{ 
                display: inline-block; 
                padding: 12px 24px; 
                background-color: #EF4444; 
                color: white; 
                text-decoration: none; 
                border-radius: 6px; 
                margin: 20px 0;
            }}
            .footer {{ margin-top: 30px; font-size: 12px; color: #666; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>Reset your password</h2>
            <p>We received a request to reset your password. Click the button below to create a new password:</p>
            <a href="{reset_link}" class="button">Reset Password</a>
            <p>Or copy and paste this link into your browser:</p>
            <p style="word-break: break-all; color: #EF4444;">{reset_link}</p>
            <p>This link will expire in 1 hour.</p>
            <div class="footer">
                <p>If you didn't request a password reset, please ignore this email. Your password will remain unchanged.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    plain_content = f"""
    Reset your password
    
    We received a request to reset your password. Click this link to create a new password:
    {reset_link}
    
    This link will expire in 1 hour.
    
    If you didn't request a password reset, please ignore this email. Your password will remain unchanged.
    """
    
    send_email(to_email, subject, html_content, plain_content)

def send_two_factor_enabled_email(to_email: str):
    """Send notification that 2FA has been enabled."""
    subject = "Two-Factor Authentication Enabled"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .alert {{ background-color: #10B981; color: white; padding: 12px; border-radius: 6px; margin: 20px 0; }}
            .footer {{ margin-top: 30px; font-size: 12px; color: #666; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>Two-Factor Authentication Enabled</h2>
            <div class="alert">
                ✓ Two-factor authentication has been successfully enabled on your account.
            </div>
            <p>Your account is now more secure. You'll need to enter a verification code from your authenticator app when signing in.</p>
            <p><strong>Important:</strong> Make sure to save your backup codes in a safe place. You can use them to access your account if you lose your authenticator device.</p>
            <div class="footer">
                <p>If you didn't enable 2FA, please contact support immediately.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    plain_content = """
    Two-Factor Authentication Enabled
    
    Two-factor authentication has been successfully enabled on your account.
    
    Your account is now more secure. You'll need to enter a verification code from your authenticator app when signing in.
    
    Important: Make sure to save your backup codes in a safe place. You can use them to access your account if you lose your authenticator device.
    
    If you didn't enable 2FA, please contact support immediately.
    """
    
    send_email(to_email, subject, html_content, plain_content)
