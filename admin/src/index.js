import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import reportWebVitals from './reportWebVitals';
import './i18n'; // initialise i18next

// Apply the saved (or system) theme to <body> before first render so the
// dashboard's dark/light choice persists across full page reloads — not only
// after the user toggles it on the Settings page.
try {
    const savedTheme = localStorage.getItem('theme');
    const prefersDark = savedTheme
        ? savedTheme === 'dark'
        : !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.body.classList.toggle('theme-dark', prefersDark);
    document.body.classList.toggle('theme-light', !prefersDark);
} catch (e) { /* localStorage unavailable — fall back to CSS media query */ }

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

// If you want to start measuring performance in your app, pass a function
// to log results (for example: reportWebVitals(console.log))
// or send to an analytics endpoint. Learn more: https://bit.ly/CRA-vitals
reportWebVitals();
