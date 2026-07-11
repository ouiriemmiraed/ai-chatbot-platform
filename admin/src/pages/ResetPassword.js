import React, { useState, useEffect } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ReactComponent as Logo } from '../logo.svg';
import api from '../services/api';
import { isStrongPassword } from '../utils/password';
import './Auth.css';
import { getErrorMessage } from '../utils/errorMessage';

const ResetPassword = () => {
    const [searchParams] = useSearchParams();
    const [token, setToken] = useState('');
    const [newPassword, setNewPassword] = useState('');
    const [confirmPassword, setConfirmPassword] = useState('');
    const [message, setMessage] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const { t } = useTranslation();
    const navigate = useNavigate();

    useEffect(() => {
        const tokenParam = searchParams.get('token');
        if (tokenParam) {
            setToken(tokenParam);
        } else {
            setError(t('security.passwordReset.invalidLink'));
        }
    }, [searchParams, t]);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setMessage('');

        // Validation
        if (newPassword !== confirmPassword) {
            setError(t('auth.passwordMismatch'));
            return;
        }

        if (!isStrongPassword(newPassword)) {
            setError(t('auth.passwordRequirements'));
            return;
        }

        setLoading(true);

        try {
            await api.post('/auth/reset-password', {
                token: token,
                new_password: newPassword,
            });
            setMessage(t('security.passwordReset.success'));

            // Redirect to login after 2 seconds
            setTimeout(() => {
                navigate('/login');
            }, 2000);
        } catch (err) {
            // Surface the backend's specific reason (weak password, expired/
            // invalid token, …) instead of a single misleading message.
            setError(getErrorMessage(err, t('security.passwordReset.failed')));
        } finally {
            setLoading(false);
        }
    };

    if (!token && !error) {
        return (
            <div className="auth-container">
                <div className="auth-card">
                    <div className="auth-logo">
                        <Logo width="64" height="64" />
                    </div>
                    <p>Loading...</p>
                </div>
            </div>
        );
    }

    return (
        <div className="auth-container">
            <div className="auth-card">
                <div className="auth-logo">
                    <Logo width="64" height="64" />
                </div>
                <h1>{t('security.passwordReset.title')}</h1>
                
                {message && <div className="success-message">{message}</div>}
                {error && <div className="error-message">{error}</div>}
                
                {!message && token && (
                    <form onSubmit={handleSubmit}>
                        <div className="form-group">
                            <label>{t('security.passwordReset.newPassword')}</label>
                            <input
                                type="password"
                                value={newPassword}
                                onChange={(e) => setNewPassword(e.target.value)}
                                required
                                placeholder={t('auth.passwordPlaceholder')}
                                autoFocus
                                minLength={8}
                            />
                        </div>
                        <div className="form-group">
                            <label>{t('security.passwordReset.confirmNewPassword')}</label>
                            <input
                                type="password"
                                value={confirmPassword}
                                onChange={(e) => setConfirmPassword(e.target.value)}
                                required
                                placeholder={t('auth.passwordPlaceholder')}
                                minLength={8}
                            />
                        </div>
                        <button type="submit" disabled={loading} className="submit-btn">
                            {loading ? t('security.passwordReset.resetting') : t('security.passwordReset.resetButton')}
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

export default ResetPassword;
