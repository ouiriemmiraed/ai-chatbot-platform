import React, { useState } from 'react';

const ChatInput = ({ onSendMessage, loading, config }) => {
    const [input, setInput] = useState('');
    const primaryColor = config?.primary_color || '#3B82F6';

    const handleSubmit = (e) => {
        e.preventDefault();
        if (input.trim() && !loading) {
            onSendMessage(input);
            setInput('');
        }
    };

    return (
        <form className="chat-input-form" onSubmit={handleSubmit}>
            <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Écrivez votre message…"
                disabled={loading}
                className="chat-input"
            />
            <button
                type="submit"
                disabled={loading || !input.trim()}
                className="send-button"
                style={{ backgroundColor: primaryColor }}
            >
                <svg
                    xmlns="http://www.w3.org/2000/svg"
                    width="20"
                    height="20"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                >
                    <line x1="22" y1="2" x2="11" y2="13"></line>
                    <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                </svg>
            </button>
        </form>
    );
};

export default ChatInput;
