import React, { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import Layout from '../components/Layout';
import { changePassword, updateProfile } from '../services/api';
import { useTranslation } from 'react-i18next';
import { isStrongPassword } from '../utils/password';
import './Profile.css';
import { getErrorMessage } from '../utils/errorMessage';

const Profile = () => {
    const { user, setUser } = useAuth();
    const { t } = useTranslation();
    const [username, setUsername] = useState(user?.username || '');
    const [currentPassword, setCurrentPassword] = useState('');
    const [newPassword, setNewPassword] = useState('');
    const [confirmPassword, setConfirmPassword] = useState('');
    const [message, setMessage] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const handleUpdateProfile = async (e) => {
        e.preventDefault();
        if (!username.trim()) {
            setError(t('profile.usernameRequired'));
            return;
        }

        try {
            setLoading(true);
            setError('');
            setMessage('');
            const response = await updateProfile(username);
            setUser({ ...user, username: response.data.username });
            setMessage(t('profile.profileUpdateSuccess'));
        } catch (err) {
            setError(getErrorMessage(err, t('profile.profileUpdateFailed')));
        } finally {
            setLoading(false);
        }
    };

    const handleChangePassword = async (e) => {
        e.preventDefault();
        
        if (!currentPassword || !newPassword || !confirmPassword) {
            setError(t('profile.allFieldsRequired'));
            return;
        }

        if (newPassword !== confirmPassword) {
            setError(t('auth.passwordMismatch'));
            return;
        }

        if (!isStrongPassword(newPassword)) {
            setError(t('auth.passwordRequirements'));
            return;
        }

        try {
            setLoading(true);
            setError('');
            setMessage('');
            await changePassword(currentPassword, newPassword);
            setMessage(t('profile.passwordChangeSuccess'));
            setCurrentPassword('');
            setNewPassword('');
            setConfirmPassword('');
        } catch (err) {
            setError(getErrorMessage(err, t('profile.passwordChangeFailed')));
        } finally {
            setLoading(false);
        }
    };

    return (
        <Layout>
            <div className="profile-container">
                <header className="profile-header">
                    <h1>{t('profile.title')}</h1>
                    <p>{t('profile.subtitle')}</p>
                </header>

                {message && <div className="success-message">{message}</div>}
                {error && <div className="error-message">{error}</div>}

                <div className="profile-content">
                    {/* Profile Information Card */}
                    <div className="profile-card">
                        <h2>{t('profile.profileInformation')}</h2>
                        <form onSubmit={handleUpdateProfile}>
                            <div className="form-group">
                                <label htmlFor="email">{t('auth.email')}</label>
                                <input
                                    id="email"
                                    type="email"
                                    value={user?.email || ''}
                                    disabled
                                    className="form-input disabled"
                                />
                                <span className="input-hint">{t('profile.emailCannotChange')}</span>
                            </div>

                            <div className="form-group">
                                <label htmlFor="username">{t('auth.username')}</label>
                                <input
                                    id="username"
                                    type="text"
                                    value={username}
                                    onChange={(e) => setUsername(e.target.value)}
                                    placeholder={t('auth.usernamePlaceholder')}
                                    className="form-input"
                                />
                            </div>

                            <button type="submit" disabled={loading} className="submit-btn">
                                {loading ? t('common.loading') : t('profile.updateProfile')}
                            </button>
                        </form>
                    </div>

                    {/* Change Password Card */}
                    <div className="profile-card">
                        <h2>{t('profile.changePassword')}</h2>
                        <form onSubmit={handleChangePassword}>
                            <div className="form-group">
                                <label htmlFor="currentPassword">{t('profile.currentPassword')}</label>
                                <input
                                    id="currentPassword"
                                    type="password"
                                    value={currentPassword}
                                    onChange={(e) => setCurrentPassword(e.target.value)}
                                    placeholder={t('auth.passwordPlaceholder')}
                                    className="form-input"
                                />
                            </div>

                            <div className="form-group">
                                <label htmlFor="newPassword">{t('profile.newPassword')}</label>
                                <input
                                    id="newPassword"
                                    type="password"
                                    value={newPassword}
                                    onChange={(e) => setNewPassword(e.target.value)}
                                    placeholder={t('auth.passwordPlaceholder')}
                                    className="form-input"
                                />
                            </div>

                            <div className="form-group">
                                <label htmlFor="confirmPassword">{t('auth.confirmPassword')}</label>
                                <input
                                    id="confirmPassword"
                                    type="password"
                                    value={confirmPassword}
                                    onChange={(e) => setConfirmPassword(e.target.value)}
                                    placeholder={t('auth.passwordPlaceholder')}
                                    className="form-input"
                                />
                            </div>

                            <button type="submit" disabled={loading} className="submit-btn">
                                {loading ? t('common.loading') : t('profile.changePasswordBtn')}
                            </button>
                        </form>
                    </div>
                </div>
            </div>
        </Layout>
    );
};

export default Profile;
