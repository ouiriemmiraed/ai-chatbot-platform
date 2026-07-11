const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';
const REQUEST_TIMEOUT = 90000; // 90 seconds (model loading + LLM response can be slow)

/**
 * Stream a chat response via Server-Sent Events.
 *
 * The backend emits frames of the form:
 *   data: {"type":"meta","conversation_id":"<oid>"}
 *   data: {"type":"token","content":"…"}
 *   data: {"type":"done","conversation_id":"<oid>"}
 *   data: {"type":"error","message":"…"}
 *
 * @param {string}   chatbotId
 * @param {string}   message
 * @param {string?}  conversationId
 * @param {object}   pageContent {url,title,text}
 * @param {string?}  apiKey
 * @param {object}   handlers {onMeta(id), onToken(t), onDone(id), onError(err)}
 * @returns {() => void} cancel function (aborts the stream)
 */
export const streamMessage = (
    chatbotId,
    message,
    conversationId,
    pageContent,
    apiKey,
    { onMeta, onToken, onDone, onError } = {},
) => {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);

    const headers = {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
    };
    if (apiKey) headers['X-API-Key'] = apiKey;

    (async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/chat/stream`, {
                method: 'POST',
                headers,
                body: JSON.stringify({
                    chatbot_id: chatbotId,
                    message,
                    conversation_id: conversationId,
                    page_url: pageContent.url,
                    page_title: pageContent.title,
                    page_content: pageContent.text,
                }),
                signal: controller.signal,
            });

            if (!response.ok) {
                let detail = 'Failed to send message';
                try { detail = (await response.json())?.detail || detail; } catch (_) {}
                const err = new Error(detail);
                err.status = response.status;
                throw err;
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';

            // SSE frames are separated by a blank line ("\n\n").
            // Each frame may contain multiple "data:" lines we concatenate.
            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });

                let sep;
                while ((sep = buffer.indexOf('\n\n')) !== -1) {
                    const frame = buffer.slice(0, sep);
                    buffer = buffer.slice(sep + 2);
                    const dataLines = frame
                        .split('\n')
                        .filter(l => l.startsWith('data:'))
                        .map(l => l.slice(5).trimStart());
                    if (!dataLines.length) continue;
                    const payload = dataLines.join('\n');

                    let evt;
                    try { evt = JSON.parse(payload); } catch (_) { continue; }

                    if (evt.type === 'meta') {
                        onMeta && onMeta(evt.conversation_id);
                    } else if (evt.type === 'token') {
                        onToken && onToken(evt.content);
                    } else if (evt.type === 'done') {
                        onDone && onDone(evt.conversation_id);
                    } else if (evt.type === 'error') {
                        const err = new Error(evt.message || 'Stream error');
                        onError && onError(err);
                    }
                }
            }
        } catch (error) {
            if (error.name !== 'AbortError') onError && onError(error);
        } finally {
            clearTimeout(timeoutId);
        }
    })();

    return () => {
        clearTimeout(timeoutId);
        controller.abort();
    };
};

export const sendMessage = async (chatbotId, message, conversationId, pageContent, apiKey = null) => {
    // Create abort controller for timeout
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);

    const headers = {
        'Content-Type': 'application/json',
    };
    if (apiKey) {
        headers['X-API-Key'] = apiKey;
    }

    try {
        const response = await fetch(`${API_BASE_URL}/chat/`, {
            method: 'POST',
            headers,
            body: JSON.stringify({
                chatbot_id: chatbotId,
                message,
                conversation_id: conversationId,
                page_url: pageContent.url,
                page_title: pageContent.title,
                page_content: pageContent.text,
            }),
            signal: controller.signal,
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
            let detail = 'Failed to send message';
            try {
                const errBody = await response.json();
                if (errBody?.detail) detail = errBody.detail;
            } catch (_) {}
            const err = new Error(detail);
            err.status = response.status;
            throw err;
        }

        return response.json();
    } catch (error) {
        clearTimeout(timeoutId);
        if (error.name === 'AbortError') {
            throw new Error('Request timeout - please try again');
        }
        throw error;
    }
};

export const getChatbotConfig = async (chatbotId) => {
    // Create abort controller for timeout
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);

    try {
        const response = await fetch(`${API_BASE_URL}/chatbot/public/${chatbotId}`, {
            signal: controller.signal,
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
            return null;
        }

        return response.json();
    } catch (error) {
        clearTimeout(timeoutId);
        if (error.name === 'AbortError') {
            console.warn('Config request timeout');
        }
        return null;
    }
};

export const sendFeedback = async (chatbotId, conversationId, messageIndex, rating) => {
    // The conversation turn is persisted asynchronously (Redis buffer drained
    // into Mongo every ~2s). A vote cast immediately after a reply can land
    // before the message exists in the stored document, which the server
    // rejects with 400/404. Retry with backoff so the vote isn't lost.
    const delays = [0, 1500, 3500];
    for (let attempt = 0; attempt < delays.length; attempt++) {
        if (delays[attempt]) await new Promise(r => setTimeout(r, delays[attempt]));
        try {
            const response = await fetch(`${API_BASE_URL}/conversations/${conversationId}/feedback`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    chatbot_id: chatbotId,
                    conversation_id: conversationId,
                    message_index: messageIndex,
                    rating,
                }),
            });
            if (response.ok) return response.json();
            // 400/404 = turn likely not drained yet → retry; other codes → stop.
            if (response.status !== 400 && response.status !== 404) return null;
        } catch (error) {
            console.error('Feedback failed:', error);
            return null;
        }
    }
    return null;
};

export const submitLead = async (chatbotId, email, sourceUrl = null, conversationId = null) => {
    try {
        const response = await fetch(`${API_BASE_URL}/leads/submit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                chatbot_id: chatbotId,
                email,
                source_url: sourceUrl,
                conversation_id: conversationId,
            }),
        });
        if (!response.ok) return null;
        return response.json();
    } catch (error) {
        console.error('Lead submit failed:', error);
        return null;
    }
};

