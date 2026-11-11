import React, { createContext, useContext, useEffect, useState } from 'react';
import { User } from '../types';
import { api } from '../services/api';

interface AuthContextType {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  isSetupRequired: boolean;
  login: (token: string, user: User) => void;
  logout: () => void;
  checkAuth: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('zaddesh_token'));
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isSetupRequired, setIsSetupRequired] = useState<boolean>(false);

  const checkAuth = async () => {
    setIsLoading(true);
    try {
      // 1. Check if setup is required
      const statusRes = await api.getAuthStatus();
      if (statusRes.setup_required) {
        setIsSetupRequired(true);
        setUser(null);
        setIsLoading(false);
        return;
      }
      setIsSetupRequired(false);

      // 2. If token exists, load current user profile
      const storedToken = localStorage.getItem('zaddesh_token');
      if (storedToken) {
        try {
          const profile = await api.getProfile();
          setUser(profile);
          setToken(storedToken);
        } catch {
          // Token invalid or expired
          localStorage.removeItem('zaddesh_token');
          setToken(null);
          setUser(null);
        }
      }
    } catch (err) {
      console.error('Failed to initialize auth:', err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    checkAuth();
    const expire = () => { setToken(null); setUser(null); };
    window.addEventListener('zaddesh:session-expired', expire);
    return () => window.removeEventListener('zaddesh:session-expired', expire);
  }, []);

  const login = (newToken: string, newUser: User) => {
    localStorage.setItem('zaddesh_token', newToken);
    setToken(newToken);
    setUser(newUser);
    setIsSetupRequired(false);
  };

  const logout = () => {
    localStorage.removeItem('zaddesh_token');
    setToken(null);
    setUser(null);
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        isLoading,
        isSetupRequired,
        login,
        logout,
        checkAuth,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
};

