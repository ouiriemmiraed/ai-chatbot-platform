import DOMPurify from 'isomorphic-dompurify';

/**
 * Sanitize text by removing all HTML tags and scripts
 * Use for user input and page content
 */
export const sanitizeText = (text) => {
    if (!text || typeof text !== 'string') return '';

    // Remove all HTML tags and scripts
    const cleaned = DOMPurify.sanitize(text, {
        ALLOWED_TAGS: [], // No HTML allowed
        ALLOWED_ATTR: [],
        KEEP_CONTENT: true, // Keep text content, remove tags
    });

    // Trim whitespace
    return cleaned.trim();
};

/**
 * Sanitize HTML allowing only safe tags
 * Use for displaying formatted content (if needed)
 */
export const sanitizeHTML = (html) => {
    if (!html || typeof html !== 'string') return '';

    // Allow only safe HTML tags
    return DOMPurify.sanitize(html, {
        ALLOWED_TAGS: ['b', 'i', 'em', 'strong', 'a', 'p', 'br', 'ul', 'ol', 'li'],
        ALLOWED_ATTR: ['href', 'target'],
        ALLOW_DATA_ATTR: false,
    });
};

/**
 * Validate message length
 */
export const validateMessageLength = (message, maxLength = 1000) => {
    if (!message || typeof message !== 'string') {
        return { valid: false, error: 'Message is required' };
    }

    const trimmed = message.trim();

    if (trimmed.length === 0) {
        return { valid: false, error: 'Message cannot be empty' };
    }

    if (trimmed.length > maxLength) {
        return {
            valid: false,
            error: `Message too long. Maximum ${maxLength} characters allowed.`
        };
    }

    return { valid: true, sanitized: sanitizeText(trimmed) };
};

/**
 * Validate chatbot ID format (MongoDB ObjectId)
 */
export const validateChatbotId = (id) => {
    if (!id || typeof id !== 'string') return false;

    // MongoDB ObjectId is 24 hex characters
    return /^[a-f\d]{24}$/i.test(id);
};
