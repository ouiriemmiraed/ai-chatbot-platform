// Get CSRF token from cookie
export const getCsrfTokenFromCookie = () => {
    const name = 'csrf_token=';
    const decodedCookie = decodeURIComponent(document.cookie);
    const cookies = decodedCookie.split(';');

    for (let cookie of cookies) {
        cookie = cookie.trim();
        if (cookie.indexOf(name) === 0) {
            return cookie.substring(name.length);
        }
    }
    return null;
};

// Fetch CSRF token from server
export const fetchCsrfToken = async () => {
    try {
        const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';
        const response = await fetch(`${API_BASE_URL}/auth/csrf-token`, {
            credentials: 'include'  // Include cookies
        });

        if (response.ok) {
            const data = await response.json();
            return data.csrf_token;
        }
    } catch (error) {
        console.error('Failed to fetch CSRF token:', error);
    }
    return null;
};
