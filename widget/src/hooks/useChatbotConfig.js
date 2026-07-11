import { useState, useEffect } from 'react';
import { getChatbotConfig } from '../services/chatApi';

export const useChatbotConfig = (chatbotId) => {
    const [config, setConfig] = useState(null);

    useEffect(() => {
        const loadConfig = async () => {
            if (chatbotId) {
                try {
                    const configData = await getChatbotConfig(chatbotId);
                    setConfig(configData);
                } catch (error) {
                    if (process.env.NODE_ENV === 'development') {
                        console.error('Error loading chatbot config:', error);
                    }
                }
            }
        };
        loadConfig();
    }, [chatbotId]);

    return config;
};
