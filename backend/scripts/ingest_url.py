import requests
from bs4 import BeautifulSoup
import sys
import os

# Backend URL configuration
BACKEND_URL = "http://localhost:8000"

def scrape_and_ingest(url, chatbot_id, hf_token=None):
    """
    Scrapes the content of a URL and sends it to the chatbot's RAG ingestion endpoint.
    """
    print(f"Scraping content from: {url}")
    
    try:
        # 1. Fetch the page content
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        # 2. Parse HTML and extract text
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "header"]):
            script.decompose()
            
        # Get text
        text = soup.get_text()
        
        # Clean text (remove extra whitespace)
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        clean_text = '\n'.join(chunk for chunk in chunks if chunk)
        
        print(f"Extracted {len(clean_text)} characters of text.")
        
        if not clean_text:
            print("No text content found to ingest.")
            return

        # 3. Send to Backend Ingestion Endpoint
        ingest_url = f"{BACKEND_URL}/chatbot/{chatbot_id}/ingest"
        
        # Need to be authenticated as the owner of the chatbot
        # Ideally we'd use a token, but for this local script we might need a workaround
        # Or we instruct the user to temporarily disable auth on the endpoint for testing?
        # NO, better approach: The script is for the ADMIN.
        
        # Let's try to bypass auth verification for localhost calls from this script?
        # Actually, the endpoint requires `current_user`.
        # Simplest way for a quick test script: 
        # We can simulate the ingestion directly via python if we are on the server
        # OR we need a valid JWT token.
        
        # Let's use direct service call since we are on the backend server
        # This avoids auth issues for this specific admin script
        sys.path.append(os.getcwd())
        from services.rag_service import rag_service
        
        print(f"Ingesting into ChromaDB for chatbot {chatbot_id}...")
        count = rag_service.add_documents(chatbot_id, clean_text, metadata={"source": url})
        print(f"Successfully ingested {count} chunks from {url}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python ingest_url.py <chatbot_id> <url>")
        print("Example: python ingest_url.py 65c2... https://example.com")
        sys.exit(1)
        
    chatbot_id = sys.argv[1]
    url = sys.argv[2]
    
    scrape_and_ingest(url, chatbot_id)
