import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const MessageList = ({ messages, loading, config, onSuggestionClick, onFeedbackSubmit, messagesEndRef, isEmailCaptureActive, onEmailSubmit, currentFlowStep, onFlowAction, showInitialWelcome, feedbackBaseIndex = 0 }) => {
    const [ratings, setRatings] = React.useState({});
    const [emailInput, setEmailInput] = React.useState('');

    const userMessageColor = config?.user_message_color || '#3B82F6';
    const botMessageColor = config?.bot_message_color || '#F3F4F6';
    const messageBubbleRadius = config?.message_bubble_radius || 12;
    const botAvatarUrl = config?.bot_avatar_url || null;

    // displayIdx is the position in the rendered list (which may be prefixed by
    // local-only email-gate / flow messages). backendIdx is the position in the
    // persisted conversation, which is what the feedback API validates against.
    const handleRate = (displayIdx, backendIdx, score) => {
        setRatings(prev => ({ ...prev, [displayIdx]: score }));
        if (onFeedbackSubmit) onFeedbackSubmit(backendIdx, score);
    };

    // Custom components for ReactMarkdown
    const components = {
        // Make links clickable and styled
        a: ({ node, children, ...props }) => (
            <a
                {...props}
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: '#3B82F6', textDecoration: 'underline' }}
            >
                {children}
            </a>
        ),
        // Style tables
        table: ({ node, ...props }) => (
            <table {...props} className="markdown-table" />
        ),
        // Style blockquotes
        blockquote: ({ node, ...props }) => (
            <blockquote {...props} className="markdown-blockquote" />
        ),
    };

    return (
        <div className="chat-messages">
            {showInitialWelcome ? (
                <div className="welcome-message">
                    <p>{config?.welcome_message || 'Bonjour ! Comment puis-je vous aider aujourd’hui ?'}</p>
                    <p className="hint">Je peux répondre à vos questions sur le contenu de cette page.</p>

                    {config?.suggested_questions && config.suggested_questions.length > 0 && (
                        <div className="suggested-questions">
                            {config.suggested_questions.map((question, idx) => (
                                <button
                                    key={idx}
                                    className="suggestion-chip"
                                    onClick={() => onSuggestionClick(question)}
                                >
                                    {question}
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            ) : (
                messages.map((msg, index) => {
                    const messageStyle = {
                        borderRadius: `${messageBubbleRadius}px`,
                    };

                    if (msg.role === 'user') {
                        messageStyle.backgroundColor = userMessageColor;
                        messageStyle.color = '#FFFFFF';
                    } else {
                        messageStyle.backgroundColor = botMessageColor;
                        messageStyle.color = '#1F2937';
                    }

                    return (
                        <div key={index} className={`message ${msg.role}`} style={messageStyle}>
                            {msg.role === 'assistant' && botAvatarUrl && (
                                <img
                                    src={botAvatarUrl}
                                    alt="Bot"
                                    className="bot-avatar"
                                />
                            )}
                            <div className="message-content">
                                <ReactMarkdown
                                    remarkPlugins={[remarkGfm]}
                                    components={components}
                                >
                                    {msg.content}
                                </ReactMarkdown>
                                {msg.role === 'assistant' && index >= feedbackBaseIndex && !msg.streaming && msg.content && msg.content.trim() && (
                                    <div className="message-feedback" style={{ display: 'flex', alignItems: 'center', gap: '4px', marginTop: '8px' }}>
                                        <button
                                            type="button"
                                            aria-label="Réponse utile"
                                            title="Utile"
                                            className="feedback-btn"
                                            onClick={() => handleRate(index, index - feedbackBaseIndex, 1)}
                                            disabled={ratings[index] !== undefined}
                                            style={{ background: 'none', border: 'none', padding: '2px 4px', lineHeight: 1, fontSize: '15px', cursor: ratings[index] !== undefined ? 'default' : 'pointer', opacity: ratings[index] === undefined ? 0.55 : (ratings[index] === 1 ? 1 : 0.25), transition: 'opacity 0.15s ease' }}
                                        >👍</button>
                                        <button
                                            type="button"
                                            aria-label="Réponse pas utile"
                                            title="Pas utile"
                                            className="feedback-btn"
                                            onClick={() => handleRate(index, index - feedbackBaseIndex, -1)}
                                            disabled={ratings[index] !== undefined}
                                            style={{ background: 'none', border: 'none', padding: '2px 4px', lineHeight: 1, fontSize: '15px', cursor: ratings[index] !== undefined ? 'default' : 'pointer', opacity: ratings[index] === undefined ? 0.55 : (ratings[index] === -1 ? 1 : 0.25), transition: 'opacity 0.15s ease' }}
                                        >👎</button>
                                        {ratings[index] !== undefined && (
                                            <span style={{ fontSize: '11px', color: '#6B7280', marginLeft: '4px' }}>Merci pour votre retour</span>
                                        )}
                                    </div>
                                )}
                            </div>
                        </div>
                    );
                })
            )}

            {isEmailCaptureActive && (
                <div className="message assistant">
                    {botAvatarUrl && <img src={botAvatarUrl} alt="Bot" className="bot-avatar" />}
                    <div className="message-content" style={{ backgroundColor: botMessageColor, color: '#1F2937', borderRadius: `${messageBubbleRadius}px`, width: '100%' }}>
                        <p style={{ margin: '0 0 10px 0', fontSize: '14px', fontWeight: '600' }}>Pour continuer, veuillez indiquer votre adresse e-mail :</p>
                        <form onSubmit={(e) => { e.preventDefault(); if (emailInput.trim()) onEmailSubmit(emailInput); }} style={{ display: 'flex', gap: '8px' }}>
                            <input
                                type="email"
                                required
                                value={emailInput}
                                onChange={(e) => setEmailInput(e.target.value)}
                                placeholder="nom@entreprise.com"
                                style={{ flex: 1, padding: '8px 12px', border: '1px solid #D1D5DB', borderRadius: '6px', outline: 'none', fontSize: '13px' }}
                            />
                            <button type="submit" style={{ backgroundColor: config?.primary_color || '#3B82F6', color: 'white', border: 'none', padding: '8px 14px', borderRadius: '6px', cursor: 'pointer', fontWeight: 'bold' }}>
                                Démarrer
                            </button>
                        </form>
                    </div>
                </div>
            )}

            {!isEmailCaptureActive && currentFlowStep && (
                <div className="message assistant">
                    {botAvatarUrl && <img src={botAvatarUrl} alt="Bot" className="bot-avatar" />}
                    <div className="message-content" style={{ backgroundColor: botMessageColor, color: '#1F2937', borderRadius: `${messageBubbleRadius}px`, width: '100%' }}>
                        <p style={{ margin: '0 0 12px 0', lineHeight: '1.5' }}>{currentFlowStep.message}</p>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                            {currentFlowStep.options.map((opt, i) => (
                                <button
                                    key={i}
                                    onClick={() => onFlowAction(opt)}
                                    className="flow-action-btn"
                                    style={{
                                        padding: '10px 14px',
                                        backgroundColor: '#FFFFFF',
                                        border: `1px solid ${config?.primary_color || '#3B82F6'}`,
                                        color: config?.primary_color || '#3B82F6',
                                        borderRadius: '8px',
                                        cursor: 'pointer',
                                        textAlign: 'center',
                                        transition: 'all 0.2s ease',
                                        fontWeight: '600',
                                        fontSize: '14px'
                                    }}
                                    onMouseOver={(e) => Object.assign(e.target.style, { backgroundColor: config?.primary_color || '#3B82F6', color: '#FFFFFF' })}
                                    onMouseOut={(e) => Object.assign(e.target.style, { backgroundColor: '#FFFFFF', color: config?.primary_color || '#3B82F6' })}
                                >
                                    {opt.label}
                                </button>
                            ))}
                        </div>
                    </div>
                </div>
            )}

            {loading && (
                <div className="message assistant">
                    {botAvatarUrl && <img src={botAvatarUrl} alt="Bot" className="bot-avatar" />}
                    <div className="message-content typing-indicator">
                        <span></span>
                        <span></span>
                        <span></span>
                    </div>
                </div>
            )}
            <div ref={messagesEndRef} />
        </div>
    );
};

export default MessageList;
