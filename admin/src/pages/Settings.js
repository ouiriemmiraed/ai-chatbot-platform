import React, { useState, useEffect } from 'react';
import Layout from '../components/Layout';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../context/AuthContext';
import { setup2FA, verify2FA, disable2FA } from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { copyText } from '../utils/clipboard';
import './Settings.css';

import { Lock, LockOpen, Moon, Sun } from 'lucide-react';
import { getErrorMessage } from '../utils/errorMessage';
const Settings = () => {
    const { t } = useTranslation();
    const { user, setUser } = useAuth();
    const { show: showToast } = useToast();

    // 2FA states
    const [twoFAStep, setTwoFAStep] = useState('idle'); // idle | setup | verify
    const [qrCode, setQrCode] = useState('');
    const [secret, setSecret] = useState('');
    const [backupCodes, setBackupCodes] = useState([]);
    const [verifyCode, setVerifyCode] = useState('');
    const [verifyLoading, setVerifyLoading] = useState(false);
    const [setupLoading, setSetupLoading] = useState(false);

    // Disable 2FA
    const [disableStep, setDisableStep] = useState('idle'); // idle | confirm
    const [disablePassword, setDisablePassword] = useState('');
    const [disableLoading, setDisableLoading] = useState(false);

    const handle2FASetup = async () => {
        if (setupLoading) return;   // guard double-click: a second setup would overwrite the secret
        setSetupLoading(true);
        try {
            const res = await setup2FA();
            setQrCode(res.data.qr_code);
            setSecret(res.data.secret);
            setBackupCodes(res.data.backup_codes || []);
            setTwoFAStep('setup');
        } catch (err) {
            const msg = getErrorMessage(err, t('settings.twoFA.setupError'));
            showToast(msg, { type: 'error' });
        } finally {
            setSetupLoading(false);
        }
    };

    const handle2FAVerify = async (e) => {
        e.preventDefault();
        if (!verifyCode.trim()) return;
        setVerifyLoading(true);
        try {
            await verify2FA(verifyCode.trim());
            showToast(t('settings.twoFA.enabledSuccess'), { type: 'success' });
            setUser(prev => ({ ...prev, two_factor_enabled: true }));
            setTwoFAStep('idle');
            setVerifyCode('');
            setQrCode('');
            setSecret('');
            setBackupCodes([]);
        } catch (err) {
            const msg = getErrorMessage(err, t('settings.twoFA.verifyError'));
            showToast(msg, { type: 'error' });
        } finally {
            setVerifyLoading(false);
        }
    };

    const handle2FADisable = async (e) => {
        e.preventDefault();
        if (!disablePassword) return;
        setDisableLoading(true);
        try {
            await disable2FA(disablePassword);
            showToast(t('settings.twoFA.disabledSuccess'), { type: 'success' });
            setUser(prev => ({ ...prev, two_factor_enabled: false }));
            setDisableStep('idle');
            setDisablePassword('');
        } catch (err) {
            const msg = getErrorMessage(err, t('settings.twoFA.disableError'));
            showToast(msg, { type: 'error' });
        } finally {
            setDisableLoading(false);
        }
    };

    const cancelSetup = () => {
        setTwoFAStep('idle');
        setQrCode('');
        setSecret('');
        setBackupCodes([]);
        setVerifyCode('');
    };

    const copySecret = async () => {
        const ok = await copyText(secret);
        showToast(
            ok ? t('settings.twoFA.secretCopied') : t('settings.twoFA.secretCopyFailed'),
            { type: ok ? 'success' : 'error' }
        );
    };

    // Theme state
    const [isDark, setIsDark] = useState(() => {
        try {
            const saved = localStorage.getItem('theme');
            if (saved) return saved === 'dark';
        } catch (e) { }
        return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    });

    const toggleTheme = () => {
        const nextDark = !isDark;
        setIsDark(nextDark);
        document.body.classList.toggle('theme-dark', nextDark);
        document.body.classList.toggle('theme-light', !nextDark);
        try { localStorage.setItem('theme', nextDark ? 'dark' : 'light'); } catch (e) { }
    };

    return (
        <Layout>
            <div className="settings-container">
                <header className="settings-header">
                    <h1>{t('settings.title')}</h1>
                    <p>{t('settings.subtitle')}</p>
                </header>

                {/* Appearance Card */}
                <section className="settings-card">
                    <div className="card-header">
                        <h2>{t('chatbotConfig.sections.appearance') || 'Appearance'}</h2>
                    </div>
                    <div className="appearance-settings">
                        <div className="setting-item">
                            <div className="setting-info">
                                <span className="setting-label">{t('settings.appearance.themeLabel')}</span>
                                <span className="setting-description">{t('settings.appearance.themeDescription')}</span>
                            </div>
                            <label className="theme-switch">
                                <input
                                    type="checkbox"
                                    checked={isDark}
                                    onChange={toggleTheme}
                                    aria-label={t('aria.toggleTheme')}
                                />
                                <span className="slider round">
                                    <span className="switch-icon sun"><Sun size={16} /></span>
                                    <span className="switch-icon moon"><Moon size={16} /></span>
                                </span>
                            </label>
                        </div>
                    </div>
                </section>

                {/* Account Info Card */}
                <section className="settings-card">
                    <div className="card-header">
                        <h2>{t('settings.accountInfo.title')}</h2>
                    </div>
                    <div className="account-info-grid">
                        <div className="info-field">
                            <span className="info-label">{t('settings.accountInfo.email')}</span>
                            <span className="info-value">{user?.email || '—'}</span>
                        </div>
                        <div className="info-field">
                            <span className="info-label">{t('settings.accountInfo.username')}</span>
                            <span className="info-value">{user?.username || '—'}</span>
                        </div>
                        <div className="info-field">
                            <span className="info-label">{t('settings.accountInfo.role')}</span>
                            <span className="role-badge">{user?.role || 'user'}</span>
                        </div>
                    </div>
                </section>

                {/* Two-Factor Authentication Card */}
                <section className="settings-card">
                    <div className="card-header">
                        <h2>{t('settings.twoFA.title')}</h2>
                        <span className={`twofa-status ${user?.two_factor_enabled ? 'twofa-on' : 'twofa-off'}`}>
                            {user?.two_factor_enabled ? t('settings.twoFA.enabled') : t('settings.twoFA.disabled')}
                        </span>
                    </div>
                    <p className="card-description">{t('settings.twoFA.description')}</p>

                    {/* Not enabled — idle */}
                    {!user?.two_factor_enabled && twoFAStep === 'idle' && (
                        <button className="btn-primary" onClick={handle2FASetup} disabled={setupLoading}>
                            <Lock size={16} /> {setupLoading ? t('common.loading') : t('settings.twoFA.enable')}
                        </button>
                    )}

                    {/* Setup step — show QR */}
                    {twoFAStep === 'setup' && (
                        <div className="twofa-setup">
                            <p className="setup-instruction">{t('settings.twoFA.scanInstruction')}</p>
                            {qrCode && (
                                <div className="qr-wrapper">
                                    <img src={qrCode} alt="QR Code" className="qr-image" />
                                </div>
                            )}
                            <div className="secret-row">
                                <span className="secret-label">{t('settings.twoFA.secretLabel')}</span>
                                <code className="secret-code">{secret}</code>
                                <button className="copy-btn" onClick={copySecret}>{t('settings.twoFA.copy')}</button>
                            </div>
                            {backupCodes.length > 0 && (
                                <div className="backup-codes">
                                    <p className="backup-title">{t('settings.twoFA.backupCodesTitle')}</p>
                                    <p className="backup-warning">{t('settings.twoFA.backupCodesWarning')}</p>
                                    <div className="codes-grid">
                                        {backupCodes.map((code, i) => (
                                            <code key={i} className="backup-code">{code}</code>
                                        ))}
                                    </div>
                                </div>
                            )}
                            <form onSubmit={handle2FAVerify} className="verify-form">
                                <label>{t('settings.twoFA.enterCode')}</label>
                                <div className="verify-row">
                                    <input
                                        type="text"
                                        className="code-input"
                                        value={verifyCode}
                                        onChange={e => setVerifyCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                                        placeholder="000000"
                                        maxLength={6}
                                        autoComplete="one-time-code"
                                    />
                                    <button
                                        type="submit"
                                        className="btn-primary"
                                        disabled={verifyCode.length !== 6 || verifyLoading}
                                    >
                                        {verifyLoading ? t('common.loading') : t('settings.twoFA.verifyBtn')}
                                    </button>
                                    <button type="button" className="btn-secondary" onClick={cancelSetup}>
                                        {t('common.cancel')}
                                    </button>
                                </div>
                            </form>
                        </div>
                    )}

                    {/* Enabled — disable option */}
                    {user?.two_factor_enabled && (
                        <div className="twofa-disable">
                            {disableStep === 'idle' ? (
                                <button
                                    className="btn-outline-danger"
                                    onClick={() => setDisableStep('confirm')}
                                >
                                    <LockOpen size={16} /> {t('settings.twoFA.disable')}
                                </button>
                            ) : (
                                <form onSubmit={handle2FADisable} className="disable-form">
                                    <p className="disable-warning">{t('settings.twoFA.disableWarning')}</p>
                                    <div className="verify-row">
                                        <input
                                            type="password"
                                            className="code-input"
                                            value={disablePassword}
                                            onChange={e => setDisablePassword(e.target.value)}
                                            placeholder={t('settings.twoFA.passwordPlaceholder')}
                                            autoComplete="current-password"
                                        />
                                        <button
                                            type="submit"
                                            className="btn-danger"
                                            disabled={!disablePassword || disableLoading}
                                        >
                                            {disableLoading ? t('common.loading') : t('settings.twoFA.disableConfirmBtn')}
                                        </button>
                                        <button
                                            type="button"
                                            className="btn-secondary"
                                            onClick={() => { setDisableStep('idle'); setDisablePassword(''); }}
                                        >
                                            {t('common.cancel')}
                                        </button>
                                    </div>
                                </form>
                            )}
                        </div>
                    )}
                </section>
            </div>
        </Layout>
    );
};

export default Settings;
