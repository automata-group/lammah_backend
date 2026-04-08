"use client";
import { useAuth } from "@/context/AuthContext";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Loader2, BookOpen, ShieldCheck } from "lucide-react";

export default function LoginPage() {
  const { user, loading, signInWithGoogle } = useAuth();
  const router = useRouter();
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!loading && user) router.replace("/dashboard");
  }, [user, loading]);

  const handleGoogleLogin = async () => {
    setSubmitting(true);
    setError("");
    const { error } = await signInWithGoogle();
    if (error) {
      setError(error.message || "فشل تسجيل الدخول عبر جوجل.");
      setSubmitting(false);
    }
  };

  if (loading) return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 flex items-center justify-center">
      <Loader2 className="w-10 h-10 text-indigo-400 animate-spin" />
    </div>
  );

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-slate-100 flex flex-col items-center justify-center p-8" dir="rtl">
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="w-full max-w-md relative z-50">
        
        <div className="text-center mb-10">
          <img src="/lammah-logo.png" alt="Lammah Logo" className="w-24 h-24 mx-auto mb-6 object-contain drop-shadow-2xl" />
          <h1 className="text-3xl font-extrabold mb-3 bg-clip-text text-transparent bg-gradient-to-l from-indigo-300 to-purple-300">
            منصة لمّاح
          </h1>
          <p className="text-slate-400 text-sm">سجل دخولك بحساب جوجل للبدء</p>
        </div>

        <div className="bg-slate-800/60 border border-slate-700/50 rounded-3xl p-8 backdrop-blur-xl shadow-2xl">
          <div className="flex items-center justify-center mb-8 text-indigo-300">
            <ShieldCheck className="w-5 h-5 ml-2" />
            <span className="text-sm font-medium">تسجيل دخول آمن وسريع المجاني</span>
          </div>

          <div className="space-y-5">
            {error && <p className="text-red-400 text-sm bg-red-500/10 border border-red-500/30 rounded-lg p-4 text-center">{error}</p>}

            <button 
              onClick={handleGoogleLogin} 
              disabled={submitting}
              className="w-full bg-white text-slate-800 hover:bg-slate-100 font-bold py-4 rounded-xl shadow-lg hover:shadow-white/20 transition-all disabled:opacity-50 flex items-center justify-center gap-3"
            >
              {submitting ? (
                <Loader2 className="w-6 h-6 animate-spin mx-auto text-slate-400" />
              ) : (
                <>
                  <svg className="w-6 h-6" viewBox="0 0 24 24">
                    <path
                      d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                      fill="#4285F4"
                    />
                    <path
                      d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                      fill="#34A853"
                    />
                    <path
                      d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                      fill="#FBBC05"
                    />
                    <path
                      d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                      fill="#EA4335"
                    />
                  </svg>
                  المتابعة باستخدام Google
                </>
              )}
            </button>
            <p className="text-center text-xs text-slate-500 mt-6 leading-relaxed">
              بالتسجيل فإنك توافق على شروط الاستخدام وسياسة الخصوصية الخاصة بالمنصة.
            </p>
          </div>
          
          <div className="mt-8 text-center border-t border-slate-800 pt-6">
            <p className="text-slate-500 text-xs font-medium">
              تم التطوير والتصميم بواسطة <span className="text-indigo-400 font-bold">فريق لمّاح</span> &copy; {new Date().getFullYear()}
            </p>
          </div>
        </div>
      </motion.div>
    </main>
  );
}
