import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ReactComponent as Logo } from '../logo.svg';
import api from '../services/api';
import './Auth.css';
import { getErrorMessage } from '../utils/errorMessage';

const ForgotPassword = () => {
    const [email, setEmail] = useState('');
    const [message, setMessage] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const { t } = useTranslation();

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setMessage('');
        setLoading(true);

        try {
            await api.post('/auth/forgot-password', { email });
            setMessage(t('security.passwordReset.emailSent'));
            setEmail(''); // Clear email field after success
        } catch (err) {
            setError(getErrorMessage(err, 'An error occurred. Please try again.'));
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="auth-container">
            <div className="auth-card">
                <div className="auth-logo">
                    <Logo width="64" height="64" />
                </div>
                <h1>{t('security.passwordReset.title')}</h1>
                <p className="lead">{t('security.passwordReset.subtitle')}</p>
                
                {message && <div className="success-message">{message}</div>}
                {error && <div className="error-message">{error}</div>}
                
                {!message && (
                    <form onSubmit={handleSubmit}>
                        <div className="form-group">
                            <label>{t('auth.email')}</label>
                            <input
                                type="email"
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                required
                                placeholder={t('auth.emailPlaceholder')}
                                autoFocus
                            />
                        </div>
                        <button type="submit" disabled={loading} className="submit-btn">
                            {loading ? t('security.passwordReset.requesting') : t('security.passwordReset.requestReset')}
                        </button>
                    </form>
                )}
                
                <p className="auth-link">
                    <Link to="/login">{t('auth.backToLogin')}</Link>
                </p>
            </div>
        </div>
    );
};

export default ForgotPassword;
