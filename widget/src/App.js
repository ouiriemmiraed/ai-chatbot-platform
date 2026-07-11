import React, { useState } from 'react';
import FloatingButton from './components/FloatingButton';
import ChatWindow from './components/ChatWindow';
import { useChat } from './hooks/useChat';
import { useChatbotConfig } from './hooks/useChatbotConfig';
import { validateChatbotId } from './utils/sanitizer';
import './App.css';

// Get chatbot ID from script tag data attribute
const getChatbotId = () => {
  const script = document.querySelector('script[data-chatbot-id]');
  const id = script ? script.getAttribute('data-chatbot-id') : null;

  // Validate chatbot ID format (MongoDB ObjectId)
  if (id && !validateChatbotId(id)) {
    console.error('Invalid chatbot ID format');
    return null;
  }

  return id;
};

function App() {
  const [isOpen, setIsOpen] = useState(false);
  const chatbotId = getChatbotId();

  const config = useChatbotConfig(chatbotId);
  const { messages, loading, sendMessage, submitFeedback } = useChat(chatbotId);

  if (!chatbotId) {
    console.error('Chatbot Widget: data-chatbot-id attribute is required');
    return null;
  }

  return (
    <div className="chatbot-widget">
      {isOpen && (
        <ChatWindow
          messages={messages}
          onSendMessage={sendMessage}
          onFeedbackSubmit={submitFeedback}
          onClose={() => setIsOpen(false)}
          loading={loading}
          config={config}
        />
      )}
      {!isOpen && (
        <FloatingButton onClick={() => setIsOpen(true)} config={config} />
      )}
    </div>
  );
}

export default App;
