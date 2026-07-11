# AI Chatbot Platform

This is the main repository for the AI Chatbot Platform project.

## Project Structure
- `admin`: The React-based administration dashboard.
- `backend`: The FastAPI backend service.
- `widget`: The embeddable chatbot widget.

## How to Run

### 1. Backend (FastAPI)
The backend manages the database, AI logic, and API endpoints.

```bash
cd backend
# Create virtual environment (optional but recommended)
python -m venv venv
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start the server
uvicorn main:app --reload
```
Review `.env` configuration before starting.

### 2. Admin Dashboard (React)
The interface to configure chatbots and view conversations.

```bash
cd admin
# Install dependencies
npm install

# Start development server
npm start
```
Access at `http://localhost:3000`.

### 3. Chatbot Widget
The script that you embed on other websites.

```bash
cd widget

# Install dependencies
npm install

# Build the production bundle
npm run build:widget
```
The output file `widget.bundle.js` will be created in `build/`. Copy this file to your website's public folder.

## Deployment
For production deployment, ensure all environment variables are set and build both the Admin and Widget for production.
