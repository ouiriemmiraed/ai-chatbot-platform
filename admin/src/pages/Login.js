import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { loginJson, login2FA } from '../services/api';
import { useAuth } from '../context/AuthContext';
import { useTranslation } from 'react-i18next';
import { ReactComponent as Logo } from '../logo.svg';
import { getErrorMessage } from '../utils/errorMessage';
import './Auth.css';

const Login = () => {
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [show2FA, setShow2FA] = useState(false);
    const [twoFactorCode, setTwoFactorCode] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const navigate = useNavigate();
    const { login } = useAuth();
    const { t } = useTranslation();

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setLoading(true);

        try {
            if (show2FA) {
                await login2FA(email, password, twoFactorCode);
            } else {
                await loginJson(email, password);
            }
            await login(); // No token needed, cookie is set
            navigate('/dashboard');
        } catch (err) {
            const errorMessage = getErrorMessage(err, t('auth.loginFailed'));

            // Handle specific error cases
            if (errorMessage.includes('Two-factor authentication required')) {
                setError(''); // clear the error, just transition to 2FA view
                setShow2FA(true); // Switch to 2FA view
            } else {
                setError(errorMessage);
            }
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
                <h1>{t('auth.appTitle')}</h1>
                <h2>{show2FA ? t('security.2fa.login.title', 'Two-Factor Authentication') : t('auth.login')}</h2>
                {error && <div className="error-message">{error}</div>}
                <form onSubmit={handleSubmit}>
                    {!show2FA ? (
                        <>
                            <div className="form-group">
                                <label>{t('auth.email')}</label>
                                <input
                                    type="email"
                                    value={email}
                                    onChange={(e) => setEmail(e.target.value)}
                                    required
                                    placeholder={t('auth.emailPlaceholder')}
                                />
                            </div>
                            <div className="form-group">
                                <label>{t('auth.password')}</label>
                                <input
                                    type="password"
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    required
                                    placeholder={t('auth.passwordPlaceholder')}
                                />
                            </div>
                            <div className="forgot-password-link">
                                <Link to="/forgot-password">{t('auth.forgotPassword')}</Link>
                            </div>
                        </>
                    ) : (
                        <div className="form-group">
                            <label>{t('security.2fa.verifyCode')}</label>
                            <input
                                type="text"
                                value={twoFactorCode}
                                onChange={(e) => setTwoFactorCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                                required
                                placeholder="000000"
                                maxLength="6"
                                className="verification-code-input"
                                style={{ letterSpacing: '0.2em', textAlign: 'center', fontSize: '1.2rem' }}
                            />
                        </div>
                    )}
                    <button type="submit" disabled={loading} className="submit-btn">
                        {loading ? t('auth.loggingIn') : (show2FA ? t('security.2fa.verifyBtn', 'Verify Code') : t('auth.loginAction'))}
                    </button>
                    {show2FA && (
                        <button type="button" onClick={() => setShow2FA(false)} className="cancel-btn" style={{marginTop: '10px', width: '100%'}}>
                            {t('common.cancel', 'Cancel')}
                        </button>
                    )}
                </form>
            </div>
        </div>
    );
};

export default Login;
