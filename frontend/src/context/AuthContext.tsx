"use client";
import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { createClient } from "@/lib/supabase";
import { User, Session } from "@supabase/supabase-js";

const IS_LOCAL_DEV = typeof window !== 'undefined' && 
  (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1');

interface AuthContextType {
  user: User | null;
  session: Session | null;
  loading: boolean;
  isLocalDev: boolean;
  signInWithGoogle: () => Promise<{ error: any }>;
  signInWithEmail: (email: string, password: string) => Promise<{ error: any }>;
  signUpWithEmail: (email: string, password: string) => Promise<{ error: any }>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const supabase = createClient();

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session);
      setUser(session?.user ?? null);
      setLoading(false);
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setSession(session);
      setUser(session?.user ?? null);
    });

    return () => subscription.unsubscribe();
  }, []);

  const getURL = () => {
    let url = process.env.NEXT_PUBLIC_SITE_URL;
    if (!url && typeof window !== 'undefined') url = window.location.origin;
    if (!url) url = 'http://localhost:3000';
    if (!url.startsWith('http')) url = `http://${url}`;
    return url.endsWith('/') ? url.slice(0, -1) : url;
  };

  const signInWithGoogle = async () => {
    const base = getURL();
    const isLocal = base.includes('localhost') || base.includes('127.0.0.1');
    const redirectPath = isLocal ? '/dashboard' : '/auth/callback?next=/dashboard';
    
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: `${base}${redirectPath}` },
    });
    return { error };
  };

  // ✅ تسجيل دخول محلي بالإيميل والباسوورد (لا يؤثر على الإنتاج)
  const signInWithEmail = async (email: string, password: string) => {
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    return { error };
  };

  const signUpWithEmail = async (email: string, password: string) => {
    const { data, error } = await supabase.auth.signUp({ 
      email, 
      password,
      options: { emailRedirectTo: `${getURL()}/dashboard` }
    });
    return { error };
  };

  const signOut = async () => {
    await supabase.auth.signOut();
    setUser(null);
    setSession(null);
  };

  return (
    <AuthContext.Provider value={{ user, session, loading, isLocalDev: IS_LOCAL_DEV, signInWithGoogle, signInWithEmail, signUpWithEmail, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be within AuthProvider");
  return context;
}
