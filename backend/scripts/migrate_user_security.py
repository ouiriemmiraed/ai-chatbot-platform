"""
Migration script to update existing users with new security fields.

This script adds the new security-related fields to existing users in the database:
- email_verified
- two_factor_enabled
- backup_codes
- verification_token
- verification_token_expires
- reset_password_token
- reset_password_token_expires
- two_factor_secret

Run this script after updating the backend code to ensure compatibility.
"""

import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "chatbot_db")

async def migrate_users():
    """Add new security fields to existing users."""
    
    # Connect to MongoDB
    client = AsyncIOMotorClient(MONGODB_URL)
    db = client[DATABASE_NAME]
    
    print(f"Connected to MongoDB: {MONGODB_URL}")
    print(f"Database: {DATABASE_NAME}")
    
    # Count existing users
    user_count = await db.users.count_documents({})
    print(f"\nFound {user_count} users in the database")
    
    if user_count == 0:
        print("No users to migrate.")
        client.close()
        return
    
    # Check if migration is needed
    sample_user = await db.users.find_one({})
    if sample_user and "email_verified" in sample_user:
        print("\nUsers already have the new security fields.")
        print("Migration may have already been run.")
        
        # Ask for confirmation
        response = input("\nDo you want to re-run the migration? (yes/no): ")
        if response.lower() not in ["yes", "y"]:
            print("Migration cancelled.")
            client.close()
            return
    
    print("\nStarting migration...")
    
    # Option 1: Mark all existing users as verified (recommended for existing users)
    # Option 2: Require all users to verify their email
    
    print("\nMigration options:")
    print("1. Mark all existing users as email verified (recommended)")
    print("2. Require all users to verify their email")
    
    choice = input("\nEnter your choice (1 or 2): ")
    
    email_verified = choice == "1"
    
    # Update all users
    update_result = await db.users.update_many(
        {},
        {
            "$set": {
                "email_verified": email_verified,
                "two_factor_enabled": False,
                "backup_codes": []
            },
            "$setOnInsert": {
                "verification_token": None,
                "verification_token_expires": None,
                "reset_password_token": None,
                "reset_password_token_expires": None,
                "two_factor_secret": None
            }
        }
    )
    
    print(f"\n✓ Migration completed!")
    print(f"  - Modified documents: {update_result.modified_count}")
    print(f"  - Matched documents: {update_result.matched_count}")
    
    if email_verified:
        print(f"  - All existing users marked as email verified")
    else:
        print(f"  - All users will need to verify their email on next login")
    
    # Close connection
    client.close()
    print("\n✓ Database connection closed")

def main():
    """Run the migration script."""
    print("=" * 60)
    print("User Security Fields Migration Script")
    print("=" * 60)
    
    print("\nThis script will add new security fields to existing users.")
    print("It's safe to run multiple times.\n")
    
    # Run the async migration
    asyncio.run(migrate_users())
    
    print("\n" + "=" * 60)
    print("Migration complete!")
    print("=" * 60)

if __name__ == "__main__":
    main()
