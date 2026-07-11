import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import reportWebVitals from './reportWebVitals';

const widgetId = 'chatbot-widget-container';
let container = document.getElementById(widgetId);

if (!container) {
  container = document.createElement('div');
  container.id = widgetId;
  document.body.appendChild(container);
}

const root = ReactDOM.createRoot(container);
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

reportWebVitals();
