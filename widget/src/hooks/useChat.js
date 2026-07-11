import { useState, useEffect, useRef } from 'react';
import {
    streamMessage,
    sendMessage,
    getChatbotConfig,
    sendFeedback,
} from '../services/chatApi';
import { validateMessageLength } from '../utils/sanitizer';
import { analyzePageContent } from '../utils/pageAnalyzer';

const MESSAGE_COOLDOWN = 2000;
const MAX_MESSAGE_LENGTH = 1000;

export const useChat = (chatbotId) => {
    const [messages, setMessages] = useState([]);
    const [loading, setLoading] = useState(false);
    const [conversationId, setConversationId] = useState(null);
    const [lastMessageTime, setLastMessageTime] = useState(0);
    const [pageContent, setPageContent] = useState(null);
    const [apiKey, setApiKey] = useState(null);
    const cancelRef = useRef(null);
    // Mirror of conversationId that updates synchronously during a stream, so
    // feedback fired right after a reply isn't blocked by a stale state closure.
    const conversationIdRef = useRef(null);

    useEffect(() => {
        setPageContent(analyzePageContent());
        if (chatbotId) {
            getChatbotConfig(chatbotId).then(config => {
                if (config && config.api_key) {
                    setApiKey(config.api_key);
                }
            });
        }
    }, [chatbotId]);

    // Cancel any in-flight stream when the component unmounts.
    useEffect(() => () => { cancelRef.current && cancelRef.current(); }, []);

    const formatError = (error) => {
        if (error.message?.includes('timeout') || error.name === 'AbortError') {
            return 'La réponse a pris trop de temps. Merci de réessayer.';
        }
        if (error.status === 503 || error.message?.toLowerCase().includes('temporarily unavailable')) {
            return 'Le service est momentanément indisponible. Merci de réessayer dans un instant.';
        }
        if (error.status === 429) {
            return 'Trop de requêtes simultanées. Merci de patienter quelques instants avant de réessayer.';
        }
        return 'Je suis momentanément indisponible. Merci de réessayer dans quelques instants. 🙏';
    };

    const handleSendMessage = async (text) => {
        if (!chatbotId) {
            console.error('Chatbot ID missing');
            return;
        }

        const now = Date.now();
        if (now - lastMessageTime < MESSAGE_COOLDOWN) {
            setMessages(prev => [...prev, { role: 'assistant', content: 'Please wait a moment before sending another message.' }]);
            return;
        }

        const validation = validateMessageLength(text, MAX_MESSAGE_LENGTH);
        if (!validation.valid) {
            setMessages(prev => [...prev, { role: 'assistant', content: validation.error }]);
            return;
        }

        const sanitizedMessage = validation.sanitized;
        setLastMessageTime(now);

        // Optimistic user message + an empty assistant placeholder we'll fill
        // token-by-token. We track its index so we can update only that slot.
        let assistantIndex = -1;
        setMessages(prev => {
            assistantIndex = prev.length + 1;  // index after we push user msg below
            return [
                ...prev,
                { role: 'user', content: sanitizedMessage },
                { role: 'assistant', content: '', streaming: true },
            ];
        });
        setLoading(true);

        // Track the in-progress stream so unmount can abort it.
        let receivedAnyToken = false;
        let resolveDone;
        const donePromise = new Promise(r => { resolveDone = r; });

        cancelRef.current = streamMessage(
            chatbotId,
            sanitizedMessage,
            conversationId,
            pageContent,
            apiKey,
            {
                onMeta: (newConvId) => {
                    if (newConvId) {
                        conversationIdRef.current = newConvId;
                        if (!conversationId) setConversationId(newConvId);
                    }
                },
                onToken: (token) => {
                    receivedAnyToken = true;
                    setMessages(prev => {
                        const next = prev.slice();
                        const last = next[assistantIndex];
                        if (last && last.role === 'assistant') {
                            next[assistantIndex] = {
                                ...last,
                                content: (last.content || '') + token,
                            };
                        }
                        return next;
                    });
                },
                onDone: (doneConvId) => {
                    if (doneConvId) {
                        conversationIdRef.current = doneConvId;
                        setConversationId(prev => prev || doneConvId);
                    }
                    setMessages(prev => {
                        const next = prev.slice();
                        const last = next[assistantIndex];
                        if (last && last.role === 'assistant') {
                            next[assistantIndex] = { ...last, streaming: false };
                        }
                        return next;
                    });
                    resolveDone();
                },
                onError: async (error) => {
                    console.error('Stream error:', error.message);

                    // Fallback to the legacy POST /chat/ when the server
                    // doesn't support streaming yet (e.g. older deployments)
                    // or the network refuses ReadableStream.
                    if (!receivedAnyToken) {
                        try {
                            const response = await sendMessage(
                                chatbotId,
                                sanitizedMessage,
                                conversationId,
                                pageContent,
                                apiKey,
                            );
                            if (response.conversation_id) {
                                conversationIdRef.current = response.conversation_id;
                                if (!conversationId) setConversationId(response.conversation_id);
                            }
                            setMessages(prev => {
                                const next = prev.slice();
                                if (next[assistantIndex]?.role === 'assistant') {
                                    next[assistantIndex] = { role: 'assistant', content: response.message };
                                }
                                return next;
                            });
                            resolveDone();
                            return;
                        } catch (fallbackErr) {
                            error = fallbackErr;
                        }
                    }

                    setMessages(prev => {
                        const next = prev.slice();
                        if (next[assistantIndex]?.role === 'assistant') {
                            next[assistantIndex] = {
                                role: 'assistant',
                                content: formatError(error),
                            };
                        }
                        return next;
                    });
                    resolveDone();
                },
            },
        );

        await donePromise;
        setLoading(false);
        cancelRef.current = null;
    };

    const handleFeedback = async (messageIndex, rating) => {
        // The conversation id is assigned mid-stream; a click landing the instant
        // the reply finishes can arrive just before it's set. Wait briefly for it
        // rather than silently dropping the vote.
        let cid = conversationIdRef.current || conversationId;
        for (let i = 0; i < 20 && !cid; i++) {
            await new Promise(r => setTimeout(r, 150));
            cid = conversationIdRef.current;
        }
        if (!cid) return;
        await sendFeedback(chatbotId, cid, messageIndex, rating);
    };

    return {
        messages,
        loading,
        sendMessage: handleSendMessage,
        submitFeedback: handleFeedback,
    };
};
