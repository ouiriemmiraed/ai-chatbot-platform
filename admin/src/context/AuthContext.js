import React, { createContext, useState, useContext, useEffect } from 'react';
import { checkAuth, logout as apiLogout } from '../services/api';
import { fetchCsrfToken } from '../utils/csrf';

const AuthContext = createContext();

export const useAuth = () => {
    const context = useContext(AuthContext);
    if (!context) {
        throw new Error('useAuth must be used within AuthProvider');
    }
    return context;
};

export const AuthProvider = ({ children }) => {
    const [user, setUser] = useState(null);
    const [loading, setLoading] = useState(true);

    const verifySession = async () => {
        try {
            const response = await checkAuth();
            setUser(response.data);
            // Fetch CSRF token if we have a valid session
            await fetchCsrfToken();
        } catch (error) {
            console.log('Session verification failed:', error);
            setUser(null);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        verifySession();
    }, []);

    // Check session periodically
    useEffect(() => {
        if (!user) return;

        const checkInterval = setInterval(async () => {
            try {
                await checkAuth();
            } catch (error) {
                console.log('Session expired');
                logout();
            }
        }, 60000); // Check every minute

        return () => clearInterval(checkInterval);
    }, [user]);

    const login = async () => {
        // After successful login API call (which sets cookie),
        // we verify session to get user data
        await verifySession();
        return true;
    };

    const logout = async () => {
        try {
            await apiLogout();
        } catch (error) {
            console.error('Logout failed:', error);
        }
        setUser(null);
        window.location.href = '/login';
    };

    const value = {
        user,
        setUser,
        login,
        logout,
        isAuthenticated: !!user,
        loading,
        isSuperuser: !!(user && user.is_superuser),
        canCreateChatbot: user ? (user.can_create_chatbot !== false) : false,
    };

    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};
