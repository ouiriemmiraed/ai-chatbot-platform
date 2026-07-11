import React, { useRef, useEffect, useState } from 'react';
import Header from './Header';
import MessageList from './MessageList';
import ChatInput from './ChatInput';
import { submitLead } from '../../services/chatApi';
import '../ChatWindow.css';

const ChatWindow = ({ messages, onSendMessage, onFeedbackSubmit, onClose, loading, config }) => {
    const messagesEndRef = useRef(null);
    const customStyleRef = useRef(null);

    // State for Lead Gen & Flow
    const [localMessages, setLocalMessages] = useState([]);
    const [emailCollected, setEmailCollected] = useState(false);

    // Initialize flow state
    const initialQuestions = config?.initial_questions || [];
    const [currentFlowStepId, setCurrentFlowStepId] = useState(null);

    // Run once on mount to setup initial flow step
    useEffect(() => {
        if (initialQuestions.length > 0 && !currentFlowStepId && localMessages.length === 0 && messages.length === 0) {
            setCurrentFlowStepId(initialQuestions[0].id);
        }
    }, [initialQuestions, currentFlowStepId, localMessages.length, messages.length]);

    const isEmailRequired = config?.require_email === true;
    const isEmailCaptureActive = isEmailRequired && !emailCollected;
    const isFlowActive = currentFlowStepId !== null && !isEmailCaptureActive;

    // Derived current step object
    const currentStep = isFlowActive ? initialQuestions.find(q => q.id === currentFlowStepId) : null;

    const handleEmailSubmit = (email) => {
        setEmailCollected(true);
        setLocalMessages(prev => [...prev, { role: 'user', content: email }, { role: 'assistant', content: 'Thank you! You can now start chatting.' }]);
        // Persist the captured email as a lead for the owner dashboard.
        const cid = config?.id || config?._id;
        if (cid && email) {
            const sourceUrl = (typeof window !== 'undefined' && window.location) ? window.location.href : null;
            submitLead(cid, email, sourceUrl);
        }
    };

    const handleFlowAction = (option) => {
        if (option.action === 'start_ai') {
            setCurrentFlowStepId(null);
            onSendMessage(option.label);
        } else {
            // Record user choice for non-AI steps
            setLocalMessages(prev => [...prev, { role: 'user', content: option.label }]);
            const nextStep = initialQuestions.find(q => q.id === option.action);
            if (nextStep) {
                setCurrentFlowStepId(nextStep.id);
            } else {
                setCurrentFlowStepId(null);
                onSendMessage(option.label);
            }
        }
    };

    // Calculate window style based on config
    const getWindowStyle = () => {
        // NB: spreading first then `|| default` is a no-op ({} is always truthy),
        // so the default must live INSIDE the spread — otherwise a missing config
        // leaves the window with no bottom/right and it renders at its static
        // document position (off-screen on tall pages).
        const style = { ...(config?.position_style || { bottom: '20px', right: '20px' }) };
        const newStyle = { ...style };

        if (newStyle.bottom && newStyle.bottom.indexOf('calc') === -1) {
            newStyle.bottom = `calc(${newStyle.bottom} + 80px)`;
        }
        if (newStyle.top && newStyle.top.indexOf('calc') === -1) {
            newStyle.top = `calc(${newStyle.top} + 80px)`;
        }

        return {
            ...newStyle,
            width: `${config?.widget_width || 400}px`,
            maxHeight: `${config?.widget_height || 600}px`,
            backgroundColor: config?.background_color || (config?.theme === 'dark' ? '#1F2937' : '#FFFFFF'),
            borderRadius: `${config?.border_radius || 16}px`,
            fontSize: `${config?.font_size || 14}px`,
        };
    };

    const getThemeClass = () => {
        return `chat-theme-${config?.theme || 'light'}`;
    };

    // Determine entry animation class
    const getAnimationClass = () => {
        const anim = config?.entry_animation || 'slide';
        switch (anim) {
            case 'fade': return 'chat-anim-fade';
            case 'bounce': return 'chat-anim-bounce';
            case 'none': return 'chat-anim-none';
            case 'slide':
            default: return 'chat-anim-slide';
        }
    };

    // Inject custom CSS from config
    useEffect(() => {
        const css = config?.custom_css;
        if (css) {
            const style = document.createElement('style');
            style.setAttribute('data-chatbot-custom-css', 'true');
            style.textContent = css;
            document.head.appendChild(style);
            customStyleRef.current = style;
        }
        return () => {
            if (customStyleRef.current) {
                customStyleRef.current.remove();
                customStyleRef.current = null;
            }
        };
    }, [config?.custom_css]);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages, localMessages, loading, currentFlowStepId, isEmailCaptureActive]);

    const displayMessages = [...localMessages, ...messages];
    const isInputDisabled = isEmailCaptureActive || isFlowActive;

    return (
        <div className={`chat-window ${getThemeClass()} ${getAnimationClass()}`} style={getWindowStyle()}>
            <Header config={config} onClose={onClose} />
            <MessageList
                messages={displayMessages}
                loading={loading}
                config={config}
                onSuggestionClick={onSendMessage}
                onFeedbackSubmit={onFeedbackSubmit}
                messagesEndRef={messagesEndRef}
                isEmailCaptureActive={isEmailCaptureActive}
                onEmailSubmit={handleEmailSubmit}
                currentFlowStep={currentStep}
                onFlowAction={handleFlowAction}
                showInitialWelcome={displayMessages.length === 0 && !isEmailCaptureActive && !isFlowActive}
                feedbackBaseIndex={localMessages.length}
            />
            {/* Hide normal input bar if an email form or flow step is currently waiting for interaction */}
            {!isInputDisabled && (
                <ChatInput
                    onSendMessage={onSendMessage}
                    loading={loading}
                    config={config}
                />
            )}
        </div>
    );
};

export default ChatWindow;
