import React, { useState, useEffect } from 'react';
import { Link, useNavigate, useLocation, useSearchParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { ReactComponent as Logo } from '../logo.svg';
import { useTranslation } from 'react-i18next';
import NotificationCenter from './NotificationCenter';
import './Layout.css';

import { BarChart3, FileText, Lock, LogOut, MessageCircle, Settings, TrendingUp, User, Users, UserPlus, X } from 'lucide-react';
const Layout = ({ children }) => {
    const { logout, user } = useAuth();
    const navigate = useNavigate();
    const location = useLocation();
    const [searchParams, setSearchParams] = useSearchParams();
    const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
    const [userMenuOpen, setUserMenuOpen] = useState(false);
    const [headerSearch, setHeaderSearch] = useState(() => searchParams.get('q') || '');
    const userMenuRef = React.useRef(null);
    const { t, i18n } = useTranslation();

    // Keep header search in sync with URL when navigating to/from /chatbot-config
    useEffect(() => {
        if (location.pathname === '/chatbot-config') {
            setHeaderSearch(searchParams.get('q') || '');
        } else {
            setHeaderSearch('');
        }
    }, [location.pathname, searchParams]);

    const onHeaderSearchChange = (e) => {
        const v = e.target.value;
        setHeaderSearch(v);
        if (location.pathname !== '/chatbot-config') {
            navigate('/chatbot-config' + (v ? '?q=' + encodeURIComponent(v) : ''));
        } else {
            setSearchParams(v ? { q: v } : {}, { replace: true });
        }
    };

    const clearHeaderSearch = () => {
        setHeaderSearch('');
        if (location.pathname === '/chatbot-config') {
            setSearchParams({}, { replace: true });
        }
    };

    useEffect(() => {
        let isDark = false;
        try {
            const saved = localStorage.getItem('theme');
            if (saved) {
                isDark = saved === 'dark';
            } else {
                isDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
            }
        } catch (e) {}
        
        document.body.classList.toggle('theme-dark', isDark);
        document.body.classList.toggle('theme-light', !isDark);
    }, []);

    const handleLogout = () => {
        logout();
        navigate('/login');
    };

    const toggleMobileMenu = () => {
        setIsMobileMenuOpen(!isMobileMenuOpen);
    };

    const closeMobileMenu = () => {
        setIsMobileMenuOpen(false);
    };

    // Close user dropdown when clicking outside
    useEffect(() => {
        const handleClickOutside = (e) => {
            if (userMenuRef.current && !userMenuRef.current.contains(e.target)) {
                setUserMenuOpen(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const toggleLanguage = () => {
        const next = i18n.language.startsWith('fr') ? 'en' : 'fr';
        i18n.changeLanguage(next);
    };

    return (
        <div className="layout">
            {/* Mobile Menu Button - Hamburger */}
            <button
                className={`mobile-menu-btn ${isMobileMenuOpen ? 'hidden' : ''}`}
                onClick={toggleMobileMenu}
                aria-label={t('aria.openMenu')}
            >
                <span className="hamburger">
                    <span></span>
                    <span></span>
                    <span></span>
                </span>
            </button>

            {/* Mobile Overlay */}
            {isMobileMenuOpen && (
                <div
                    className="mobile-overlay"
                    onClick={closeMobileMenu}
                ></div>
            )}

            <aside className={`sidebar ${isMobileMenuOpen ? 'mobile-open' : ''}`}>
                <div className="sidebar-header">
                    <span className="logo-icon">
                        <Logo width="32" height="32" />
                    </span>
                    <h1>ChatBot Manager</h1>
                    {/* Close button for mobile - inside sidebar */}
                    <button
                        className="sidebar-close-btn"
                        onClick={closeMobileMenu}
                        aria-label={t('aria.closeMenu')}
                    >
                        <X size={14} />
                    </button>
                </div>

                <nav className="sidebar-nav">
                    <div className="nav-group">
                        <p className="nav-label">{t('nav.menu')}</p>
                        <Link
                            to="/dashboard"
                            className={`nav-item ${location.pathname === '/dashboard' ? 'active' : ''}`}
                            onClick={closeMobileMenu}
                        >
                            <span className="nav-icon"><BarChart3 size={20} /></span>
                            {t('nav.dashboard')}
                        </Link>
                        <Link
                            to="/chatbot-config"
                            className={`nav-item ${location.pathname === '/chatbot-config' ? 'active' : ''}`}
                            onClick={closeMobileMenu}
                        >
                            <span className="nav-icon"><Settings size={16} /></span>
                            {t('nav.configuration')}
                        </Link>
                        <Link
                            to="/conversations"
                            className={`nav-item ${location.pathname === '/conversations' ? 'active' : ''}`}
                            onClick={closeMobileMenu}
                        >
                            <span className="nav-icon"><MessageCircle size={16} /></span>
                            {t('nav.conversations')}
                        </Link>
                        <Link
                            to="/analytics"
                            className={`nav-item ${location.pathname === '/analytics' ? 'active' : ''}`}
                            onClick={closeMobileMenu}
                        >
                            <span className="nav-icon"><TrendingUp size={16} /></span>
                            {t('nav.analytics')}
                        </Link>
                        <Link
                            to="/leads"
                            className={`nav-item ${location.pathname === '/leads' ? 'active' : ''}`}
                            onClick={closeMobileMenu}
                        >
                            <span className="nav-icon"><UserPlus size={16} /></span>
                            {t('nav.leads', 'Leads')}
                        </Link>
                        <Link
                            to="/documents"
                            className={`nav-item ${location.pathname === '/documents' ? 'active' : ''}`}
                            onClick={closeMobileMenu}
                        >
                            <span className="nav-icon"><FileText size={16} /></span>
                            {t('nav.documents', 'Documents')}
                        </Link>
                        {user && user.is_superuser && (
                            <Link
                                to="/users"
                                className={`nav-item ${location.pathname === '/users' ? 'active' : ''}`}
                                onClick={closeMobileMenu}
                            >
                                <span className="nav-icon"><Users size={16} /></span>
                                {t('nav.users', 'Users')}
                            </Link>
                        )}
                        {/* Settings & Playground accessible via avatar menu / chatbot cards */}
                    </div>
                </nav>

                <div className="sidebar-footer">
                    <button onClick={handleLogout} className="logout-btn">
                        <span className="nav-icon"><LogOut size={16} /></span>
                        {t('nav.logout')}
                    </button>
                </div>
            </aside>
            <main className="main-content">
                <header className="top-header">
                    {/* Left: Search bar */}
                    <div className="header-search">
                        <svg className="header-search-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                            <circle cx="11" cy="11" r="8" />
                            <path d="m21 21-4.35-4.35" />
                        </svg>
                        <input
                            type="text"
                            className="header-search-input"
                            placeholder={t('search.chatbotsPlaceholder', 'Search chatbots...')}
                            aria-label={t('aria.searchChatbots')}
                            value={headerSearch}
                            onChange={onHeaderSearchChange}
                        />
                        {headerSearch && (
                            <button
                                type="button"
                                className="header-search-clear"
                                onClick={clearHeaderSearch}
                                aria-label={t('aria.clearSearch')}
                                title={t('aria.clearSearch')}
                            >
                                <X size={14} />
                            </button>
                        )}
                    </div>

                    {/* Right: icon buttons + user */}
                    <div className="header-controls">
                        <div className="header-icon-group">
                            <NotificationCenter />

                            <button
                                className="header-icon-btn"
                                onClick={toggleLanguage}
                                aria-label={t('aria.switchLanguage')}
                                title={i18n.language.startsWith('fr') ? 'Switch to English' : 'Passer en Français'}
                            >
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                    <circle cx="12" cy="12" r="10" />
                                    <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
                                </svg>
                                <span className="sr-only">Switch language</span>
                            </button>
                        </div>

                        <div className="header-user-divider" />

                        <div className="user-menu-wrapper" ref={userMenuRef}>
                            <div
                                className="user-info"
                                role="button"
                                aria-label={t('aria.userMenu')}
                                aria-expanded={userMenuOpen}
                                tabIndex={0}
                                onClick={() => setUserMenuOpen(v => !v)}
                                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setUserMenuOpen(v => !v); }}
                                style={{ cursor: 'pointer' }}
                            >
                                <span className="user-name">{user && user.username ? user.username : 'Admin123'}</span>
                                <div className="avatar-wrapper">
                                    {user?.profile_picture ? (
                                        <img src={user.profile_picture} alt="Profile" className="avatar-img" />
                                    ) : (
                                        <div className="avatar-letter">
                                            {user && user.username ? user.username.charAt(0).toUpperCase() : 'A'}
                                        </div>
                                    )}
                                </div>
                            </div>
                            {userMenuOpen && (
                                <div className="user-dropdown" role="menu">
                                    <button className="user-dropdown-item" role="menuitem" onClick={() => { setUserMenuOpen(false); navigate('/profile'); }}>
                                        <span><User size={16} /></span> {t('nav.profile')}
                                    </button>
                                    <button className="user-dropdown-item" role="menuitem" onClick={() => { setUserMenuOpen(false); navigate('/settings'); }}>
                                        <span><Lock size={16} /></span> {t('nav.settings')}
                                    </button>
                                    <div className="user-dropdown-divider" />
                                    <button className="user-dropdown-item danger" role="menuitem" onClick={() => { setUserMenuOpen(false); handleLogout(); }}>
                                        <span><LogOut size={16} /></span> {t('nav.logout')}
                                    </button>
                                </div>
                            )}
                        </div>
                    </div>
                </header>
                <div className="content-scroll">
                    {children}
                </div>
            </main>
        </div>
    );
};

export default Layout;
